"""Orchestrator: plan -> delegate -> verdict -> reroute.

This is the part that is genuinely NEW code (not ported): the in-run recovery loop.

    plan      a plain-model call (Interactions API, structured JSON) turns the task into 2-5 steps
    delegate  per step: the Coder (Antigravity agent) attempts it in a fresh sandbox seeded from the orchestrator's
              workspace copy; the resulting snapshot becomes the new workspace and is copied into the Critic's own
              fresh sandbox (Antigravity agent), which reviews it
    verdict   the Critic calls submit_verdict (structured). Only steps that change code (and the gate) are reviewed;
              the others are recorded as reviewed=false. On the GATE step -- the last step that modifies code --
              the hidden verification suite is mounted for the Critic only; the Coder never sees it
    reflect   on a failed verdict the orchestrator reasons about WHY (a structured plain-model call chained onto the
              plan, see app/reflection.py) and rewrites the sub-task; if that call fails the deterministic retry is used
    reroute   the step is retried: routing.pick climbs the Coder one rung up the model ladder and the prompt carries
              the Critic's findings (test names / behavioural reasons, never test source) plus the revised sub-task

Every decision is recorded on the StateLog (payload conventions in state_log.py), which is what the timeline UI
and the recorded replay read. Failure is a first-class outcome: run_task NEVER raises and always ends the log
with exactly one run_finished.

Guards: RunBudget.max_attempts per step, max_run_tokens over the whole run, and run_wall_clock_s as a hard ceiling
(asyncio.wait_for), mirroring the hard-ceiling pattern of RoundtableCI's engineer_runtime.run_engineer. The
detect -> bounded fix -> re-verify shape follows runtime_healing_service.py.

Honest lineage note: this loop is NOT "the Reflection System, ported". RoundtableCI's Reflection subsystems are an
asynchronous validated-knowledge pipeline and engineering_reflect_hook is observation-only. What carries over is the
observation idea: one recorded fact per attempt (see app/state_log.py).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app import reflection, routing
from app.agents import coder, critic
from app.config import CRITIC_MODEL, MODEL_LADDER, ORCHESTRATOR_MODEL, RunBudget
from app.interactions import AgentResult, Gateway, SnapshotError
from app.routing import AgentSpec
from app.schemas import Decision, Plan, PlanStep, Verdict, validate_plan
from app.state_log import StateLog

logger = logging.getLogger(__name__)

PLANNER_SYSTEM = (
    "You are the planner of a multi-agent coding team made of a Coder and a Critic. Break the task into 2 to 5 "
    "ordered steps. Each step must be small, self-contained, and verifiable by running code. Later steps build on "
    "earlier ones in the same workspace. Prefer a first step that reproduces or diagnoses the problem. Set "
    "modifies_code to true only for steps that change project code. Only mention files that are listed."
)

_EVENT_TEXT_LIMIT = 2000


@dataclass
class TaskSpec:
    name: str
    task: str  # what the user asks for; goes to the planner and the coder
    category: str  # routing category (Sage-style canonical label)
    workspace: Dict[str, str]  # files mounted for the coder: relative path -> text
    hidden_tests: Dict[str, str]  # mounted ONLY for the critic, on the gate step
    workspace_root: str = coder.SANDBOX_ROOT

    @property
    def suite(self) -> Optional[str]:
        """The verification file the critic runs on the gate step."""
        return next((name for name in sorted(self.hidden_tests) if name.startswith("test")), None)


@dataclass
class RunResult:
    succeeded: bool
    steps_total: int = 0
    steps_succeeded: int = 0
    attempts_used: int = 0  # coder attempts across the whole run
    tokens: int = 0
    error: Optional[str] = None


def _clip(text: Any, limit: int = _EVENT_TEXT_LIMIT) -> str:
    text = str(text if text is not None else "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def to_sources(files: Dict[str, str], root: str) -> List[Dict[str, str]]:
    """Inline environment sources that materialise `files` under `root` in a sandbox."""
    return [{"type": "inline", "target": f"{root}/{path}", "content": text} for path, text in sorted(files.items())]


def fallback_plan(task: str) -> Plan:
    return Plan(steps=[PlanStep(
        title="Complete the task",
        instruction=task,
        acceptance="The behaviour the task asks for is implemented and verified by running the code.",
        modifies_code=True,
    )])


class _Run:
    def __init__(self, spec: TaskSpec, log: StateLog, gateway: Gateway, budget: RunBudget, history: Optional[routing.History]):
        self.spec, self.log, self.gateway, self.budget, self.history = spec, log, gateway, budget, history
        self.tokens = 0
        self.attempts = 0
        self.steps_total = 0
        self.steps_ok = 0
        self.current_step: Optional[int] = None
        self.orchestrator_interaction_id: Optional[str] = None  # chain for Phase B's reflection calls
        # The workspace is OUR state, not the sandbox's: every coder attempt gets a fresh sandbox seeded from the
        # latest snapshot. This is a design choice (explicit state, and no binding to one API key's project, so key
        # failover works for the coder), NOT a proven fix for the step-2 timeouts: that step timed out at 300s in
        # reused AND fresh sandboxes -- it is a heavy step and API latency varies (see RunBudget).
        self.workspace: Dict[str, str] = dict(spec.workspace)
        self.env_id: Optional[str] = None  # the environment the latest coder call ran in; used only to snapshot it

    # ---- small helpers ----
    def emit(self, kind: str, *, step: Optional[int] = None, attempt: int = 1, agent: Optional[str] = None,
             model: Optional[str] = None, **payload: Any) -> None:
        self.log.append(kind, step=step, attempt=attempt, agent=agent, model=model, payload=payload)

    def bind(self, step: int, attempt: int, agent: str, model: str):
        return lambda kind, payload: self.log.append(kind, step=step, attempt=attempt, agent=agent, model=model, payload=payload)

    @staticmethod
    def result_payload(res: AgentResult) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"status": res.status, "tokens": res.tokens, "elapsed_s": res.elapsed_s, "output": _clip(res.output_text, 600)}
        if res.key:
            payload["key"] = res.key  # non-secret label (k1, k2, ...): which dedicated key served this call
        if res.error:
            payload["error"] = res.error
        return payload

    def over_budget(self) -> bool:
        return self.tokens > self.budget.max_run_tokens

    def result(self, succeeded: bool, error: Optional[str] = None) -> RunResult:
        return RunResult(succeeded, self.steps_total, self.steps_ok, self.attempts, self.tokens, error)

    # ---- phases ----
    async def make_plan(self) -> Plan:
        listing = "\n".join(f"- {path} ({len(text.splitlines())} lines)" for path, text in sorted(self.spec.workspace.items()))
        prompt = f"Task: {self.spec.task}\n\nFiles in the workspace:\n{listing}"
        self.emit("agent_call", agent="orchestrator", model=ORCHESTRATOR_MODEL, purpose="plan", prompt=_clip(prompt))
        res = await self.gateway.run_model(ORCHESTRATOR_MODEL, prompt, Plan, system_instruction=PLANNER_SYSTEM)
        self.tokens += res.tokens
        self.orchestrator_interaction_id = res.interaction_id
        problem = res.error if not res.ok else validate_plan(res.value)
        if problem is None:
            self.emit("agent_result", agent="orchestrator", model=ORCHESTRATOR_MODEL, status="completed", tokens=res.tokens,
                      elapsed_s=res.elapsed_s, key=res.key, output=" | ".join(s.title for s in res.value.steps))
            return res.value
        # Never silent: the fallback is recorded so a recording can't pass off a fallback as a real plan.
        logger.warning("orchestrator: planner unusable (%s); using a single-step fallback plan", problem)
        self.emit("agent_result", agent="orchestrator", model=ORCHESTRATOR_MODEL, status="fallback", tokens=res.tokens,
                  elapsed_s=res.elapsed_s, error=problem, output="single-step fallback plan")
        return fallback_plan(self.spec.task)

    async def reflect(self, i: int, total: int, attempt: int, step: PlanStep, coder_model: str, coder_output: str,
                      verdict: Verdict) -> Optional[Decision]:
        """The orchestrator reasons about a failed review. None means 'use the deterministic retry' (recorded)."""
        prompt = reflection.build_prompt(
            self.spec.task, step, i, total, attempt=attempt, coder_model=coder_model, coder_output=coder_output,
            verdict=verdict, ladder=MODEL_LADDER,
        )
        res = await self.gateway.run_model(
            ORCHESTRATOR_MODEL, prompt, Decision, system_instruction=reflection.SYSTEM_INSTRUCTION,
            previous_interaction_id=self.orchestrator_interaction_id,  # the plan and earlier failures live server-side
        )
        self.tokens += res.tokens
        decision = res.value if res.ok else None
        problem = res.error if not res.ok else (
            None if decision and decision.summary.strip() and decision.modified_subtask.strip() else "empty reflection"
        )
        if problem is not None:
            logger.warning("orchestrator: reflection unusable (%s); using the deterministic retry", problem)
            self.emit("reflection", step=i, attempt=attempt, agent="orchestrator", model=ORCHESTRATOR_MODEL,
                      status="fallback", error=problem, tokens=res.tokens, elapsed_s=res.elapsed_s, key=res.key,
                      summary="; ".join(verdict.reasons)[:300] or "The critic rejected the work.")
            return None
        self.orchestrator_interaction_id = res.interaction_id or self.orchestrator_interaction_id
        self.emit("reflection", step=i, attempt=attempt, agent="orchestrator", model=ORCHESTRATOR_MODEL,
                  status="completed", summary=decision.summary, modified_subtask=_clip(decision.modified_subtask),
                  escalate=decision.escalate, tokens=res.tokens, elapsed_s=res.elapsed_s, key=res.key)
        return decision

    async def review(self, i: int, total: int, attempt: int, step: PlanStep, files: Dict[str, str], gate: bool) -> Optional[Verdict]:
        """One critic review in a fresh sandbox; one retry if it never submits a verdict."""
        spec = self.spec
        critic_spec = AgentSpec(role="critic", model=CRITIC_MODEL)
        sources = to_sources({p: t for p, t in files.items() if not (gate and p in spec.hidden_tests)}, spec.workspace_root)
        if gate:
            sources += to_sources(spec.hidden_tests, spec.workspace_root)
        for _ in range(2):
            prompt = critic.build_prompt(spec.task, step, i, total, gate=gate, suite=spec.suite)
            self.emit("agent_call", step=i, attempt=attempt, agent="critic", model=CRITIC_MODEL, prompt=_clip(prompt), gate=gate)
            res = await self.gateway.run_agent(
                critic_spec, prompt, system_instruction=critic.SYSTEM_INSTRUCTION, tools=critic.build_tools(),
                environment={"type": "remote", "sources": sources}, on_event=self.bind(i, attempt, "critic", CRITIC_MODEL),
            )
            self.tokens += res.tokens
            self.emit("agent_result", step=i, attempt=attempt, agent="critic", model=CRITIC_MODEL, **self.result_payload(res))
            verdict = critic.extract_verdict(res)
            if verdict is not None:
                return verdict
            if self.over_budget():
                return None
        return None

    async def run_step(self, i: int, total: int, step: PlanStep, gate: bool) -> Optional[str]:
        """Returns None when the step passed, else the reason it failed."""
        spec = self.spec
        failed_models: List[str] = []
        feedback: Optional[Verdict] = None
        failure_note: Optional[str] = None
        decision: Optional[Decision] = None  # the orchestrator's reflection on the last failed review
        last_model: Optional[str] = None
        last_reason = "no attempt completed"
        for attempt in range(1, self.budget.max_attempts + 1):
            if self.over_budget():
                return f"run token budget exhausted ({self.tokens} > {self.budget.max_run_tokens})"
            coder_spec = routing.pick(spec.category, failed_models, self.history, role="coder")
            revision = decision.modified_subtask if decision else None
            prompt = coder.build_prompt(spec.task, step, i, total, feedback=feedback, failure_note=failure_note, revision=revision)
            if attempt > 1:
                self.emit("reroute", step=i, attempt=attempt - 1, agent="orchestrator", from_model=last_model,
                          to_model=coder_spec.model, escalated=coder_spec.model != last_model,
                          modified_subtask=_clip(revision or prompt))
            last_model = coder_spec.model
            self.attempts += 1
            self.emit("agent_call", step=i, attempt=attempt, agent="coder", model=coder_spec.model, prompt=_clip(prompt))
            res = await self.gateway.run_agent(
                coder_spec, prompt, system_instruction=coder.SYSTEM_INSTRUCTION,
                environment={"type": "remote", "sources": to_sources(self.workspace, spec.workspace_root)},
                on_event=self.bind(i, attempt, "coder", coder_spec.model),
            )
            self.tokens += res.tokens
            self.emit("agent_result", step=i, attempt=attempt, agent="coder", model=coder_spec.model, **self.result_payload(res))
            if res.environment_id:
                self.env_id = res.environment_id
            if not res.ok:
                failed_models.append(coder_spec.model)
                decision = None
                feedback, failure_note = None, f"{res.status}: {res.error or 'no detail'}"
                last_reason = failure_note
                continue

            try:
                files = await self.gateway.download_workspace(self.env_id or "")
            except SnapshotError as e:  # infrastructure, not the model's fault: don't blame the model
                self.emit("agent_result", step=i, attempt=attempt, agent="orchestrator", status="snapshot_error", error=str(e))
                decision = None
                feedback, failure_note = None, f"workspace snapshot failed: {e}"
                last_reason = failure_note
                continue

            if files:
                self.workspace = files  # the next attempt or step continues from what the coder actually left behind
            if not step.modifies_code and not gate:
                # Nothing to verify with the suite and no code changed: a critic sandbox would cost ~3 minutes and
                # ~30k tokens for little. Recorded as unreviewed rather than passed off as a verdict.
                self.emit("step_succeeded", step=i, attempt=attempt, agent="orchestrator", attempts=attempt,
                          reviewed=False, note="no code changes planned; not sent to the critic")
                return None
            verdict = await self.review(i, total, attempt, step, files, gate)
            if verdict is None:
                return "critic produced no verdict"
            self.emit("verdict", step=i, attempt=attempt, agent="critic", model=CRITIC_MODEL, passed=verdict.passed,
                      reasons=verdict.reasons, failing_tests=verdict.failing_tests, evidence=verdict.evidence,
                      category=spec.category, subject_model=coder_spec.model, gate=gate)
            if verdict.passed:
                self.emit("step_succeeded", step=i, attempt=attempt, agent="orchestrator", attempts=attempt)
                return None
            decision = None
            if attempt < self.budget.max_attempts and not self.over_budget():  # no point reflecting with no retry left
                decision = await self.reflect(i, total, attempt, step, coder_spec.model, res.output_text, verdict)
            # The model's escalate flag is honoured for the first retry only; after that every retry climbs a rung.
            if decision is None or decision.escalate or attempt >= 2:
                failed_models.append(coder_spec.model)
            feedback, failure_note = verdict, None
            last_reason = "; ".join(verdict.reasons)[:300] or "the critic rejected the work"
        return f"exhausted {self.budget.max_attempts} attempts: {last_reason}"

    async def execute(self) -> RunResult:
        plan = await self.make_plan()
        steps = plan.steps
        total = self.steps_total = len(steps)
        # The hidden suite covers the whole spec, so it gates only the last step that changes code.
        gate_index = max((n for n, s in enumerate(steps, 1) if s.modifies_code), default=total)
        for i, step in enumerate(steps, 1):
            self.current_step = i
            self.emit("step_started", step=i, agent="orchestrator", title=step.title, instruction=_clip(step.instruction),
                      acceptance=step.acceptance, modifies_code=step.modifies_code, gate=(i == gate_index))
            reason = await self.run_step(i, total, step, gate=(i == gate_index))
            if reason is not None:
                self.emit("step_failed", step=i, agent="orchestrator", reason=reason)
                return self.result(False, f"step {i} failed: {reason}")
            self.steps_ok += 1
        return self.result(True)


async def run_task(
    spec: TaskSpec,
    log: StateLog,
    gateway: Gateway,
    budget: Optional[RunBudget] = None,
    *,
    history: Optional[routing.History] = None,
) -> RunResult:
    """Run one task end to end. Never raises; always closes the log with a single run_finished."""
    budget = budget or RunBudget()
    run = _Run(spec, log, gateway, budget, history)
    log.append("run_started", agent="orchestrator", payload={
        "task": spec.task, "category": spec.category, "budget": asdict(budget),
        "coder_ladder": list(MODEL_LADDER), "critic_model": CRITIC_MODEL, "orchestrator_model": ORCHESTRATOR_MODEL,
    })
    try:
        result = await asyncio.wait_for(run.execute(), timeout=budget.run_wall_clock_s)
    except asyncio.TimeoutError:
        reason = f"run exceeded the {budget.run_wall_clock_s:.0f}s wall-clock ceiling"
        if run.current_step is not None:
            run.emit("step_failed", step=run.current_step, agent="orchestrator", reason=reason)
        result = run.result(False, reason)
    except Exception as e:  # noqa: BLE001 -- the never-raises contract
        logger.exception("orchestrator: unexpected error")
        result = run.result(False, f"orchestrator error: {type(e).__name__}: {e}")
    run.emit("run_finished", agent="orchestrator", succeeded=result.succeeded, steps_total=result.steps_total,
             steps_succeeded=result.steps_succeeded, attempts_used=result.attempts_used, tokens=result.tokens,
             error=result.error)
    return result
