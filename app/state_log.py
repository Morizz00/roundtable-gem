"""State log: every step's input / output / verdict, recorded and streamable.

This is what makes the demo legible -- "step 2 failed -> rerouted -> passed"
is read straight off this log, both live (SSE) and on replay from a recorded
run.

Lineage (RoundtableCI):
- StepEvent's fields follow MissionEvent / MissionResult in
  roundtable-service/app/services/engineer_runtime.py (kind / turn / success /
  stopped_reason) and the Observation Layer's emit_observation shape
  (component / event_type / payload) in reflection_observation_layer.py.
- Backlog + live-subscribe + monotonic `seq` follows workflow_event_bus.py's
  in-process fallback (_fallback_backlog / _fallback_subscribers /
  _fallback_seq). No Redis here: single process, single run at a time.

Like the source it mirrors, recording never raises: a JSONL write failure or an
unknown event kind degrades to a logged warning, never a broken run.

Payload conventions (the UI in ui/index.html and the orchestrator both rely on
these; extra keys are fine, missing ones are tolerated):
    run_started    {task, category, budget, fixture?}   fixture=true marks synthetic data
    step_started   {title}
    agent_call     {prompt}
    agent_result   {status, tokens, elapsed_s, output, key?, error?}   key = non-secret label (k1..) of the API key used
    tool_called    {tool, arguments}
    tool_returned  {tool, result}
    verdict        {passed, reasons[], failing_tests[], category}
    reflection     {summary}
    reroute        {from_model, to_model, modified_subtask}
    step_succeeded {attempts}
    step_failed    {reason}
    run_finished   {succeeded, steps_total, steps_succeeded, attempts_used}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from app.config import RUNS_DIR

logger = logging.getLogger(__name__)

KINDS = frozenset(
    {
        "run_started",
        "step_started",
        "agent_call",  # an Antigravity interaction was issued (payload: prompt summary)
        "agent_result",  # interaction returned (payload: status, output summary, tokens)
        "tool_called",
        "tool_returned",
        "verdict",  # critic's pass/fail (payload: passed, reasons, category)
        "reflection",  # orchestrator's read of why a step failed
        "reroute",  # step re-issued with a different model / modified sub-task
        "step_succeeded",
        "step_failed",
        "run_finished",  # terminal: closes the stream
    }
)

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_run_id(run_id: object) -> bool:
    """Run ids can arrive from URLs, so they are validated before touching disk."""
    return isinstance(run_id, str) and _RUN_ID_RE.match(run_id) is not None


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


@dataclass
class StepEvent:
    run_id: str
    seq: int
    ts: float
    kind: str
    step: Optional[int] = None  # 1-based index of the plan step
    attempt: int = 1
    agent: Optional[str] = None  # "orchestrator" | "coder" | "critic"
    model: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepEvent":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class StateLog:
    """Append-only event log for one run.

    append() is synchronous (safe to call from async orchestrator code on the
    same event loop); subscribe() is the async stream the SSE endpoint drains.
    """

    def __init__(self, run_id: str, runs_dir: Optional[Path] = None, persist: bool = True):
        if not is_valid_run_id(run_id):
            raise ValueError(f"invalid run_id {run_id!r}: expected 1-64 chars of [A-Za-z0-9_-]")
        self.run_id = run_id
        self._events: List[StepEvent] = []
        self._subscribers: List["asyncio.Queue[Optional[StepEvent]]"] = []
        self._closed = False
        self._path: Optional[Path] = None
        if persist:
            self._path = (Path(runs_dir) if runs_dir else RUNS_DIR) / f"{run_id}.jsonl"

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def events(self) -> List[StepEvent]:
        return list(self._events)

    def append(
        self,
        kind: str,
        *,
        step: Optional[int] = None,
        attempt: int = 1,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> StepEvent:
        if kind not in KINDS:
            logger.warning("state_log: unknown event kind %r (expected one of %s), recording anyway", kind, sorted(KINDS))
        event = StepEvent(
            run_id=self.run_id,
            seq=len(self._events) + 1,
            ts=time.time(),
            kind=kind,
            step=step,
            attempt=attempt,
            agent=agent,
            model=model,
            payload=dict(payload or {}),
        )
        self._events.append(event)
        self._persist(event)
        for queue in self._subscribers:
            queue.put_nowait(event)
        if kind == "run_finished":
            self.close()
        return event

    def _persist(self, event: StepEvent) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("state_log: could not persist event %s for run %s: %s", event.seq, self.run_id, e)

    def backlog(self, since_seq: int = 0) -> List[StepEvent]:
        return [e for e in self._events if e.seq > since_seq]

    def close(self) -> None:
        """Ends every live subscription. Idempotent."""
        if self._closed:
            return
        self._closed = True
        for queue in self._subscribers:
            queue.put_nowait(None)

    async def subscribe(self, since_seq: int = 0) -> AsyncIterator[StepEvent]:
        """Yield backlog events after `since_seq`, then live ones, until the log closes.

        Snapshotting the backlog and registering the queue happen with no await
        between them, so an event is delivered exactly once: either it was
        already in the backlog, or it lands in the queue.
        """
        queue: "asyncio.Queue[Optional[StepEvent]]" = asyncio.Queue()
        backlog = self.backlog(since_seq)
        live = not self._closed
        if live:
            self._subscribers.append(queue)
        try:
            for event in backlog:
                yield event
            while live:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    @classmethod
    def load(cls, path: Path) -> "StateLog":
        """Rebuild a closed, read-only log from a recorded JSONL run (for replay)."""
        p = Path(path)
        log = cls(p.stem, runs_dir=p.parent, persist=False)
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                log._events.append(StepEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("state_log: skipping malformed line in %s: %s", p, e)
        log._closed = True
        return log


# Logs of runs in this process, by run_id. The orchestrator registers each new
# log so GET /stream/{run_id} can serve a run that is still in flight; finished
# runs are also on disk, so this is only ever the fast path.
ACTIVE_LOGS: Dict[str, StateLog] = {}


def register_log(log: StateLog) -> StateLog:
    ACTIVE_LOGS[log.run_id] = log
    return log


def get_active_log(run_id: str) -> Optional[StateLog]:
    return ACTIVE_LOGS.get(run_id)
