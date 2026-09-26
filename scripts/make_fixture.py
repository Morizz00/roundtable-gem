"""Writes runs/sample_fixture.jsonl: a SYNTHETIC run for building the UI offline.

This is NOT a real run and must never be presented as one. run_started carries fixture=true; the UI shows a
FIXTURE badge for it, /runs lists fixtures after real runs, and routing.history_from_runs ignores it.

It mirrors the REAL event shape the orchestrator emits (app/orchestrator.py): a model-generated plan, the critic
running on its own fixed model with the coder's model in verdict.payload.subject_model, the hidden-suite gate on
the last code-changing step, non-secret key labels (k1 orchestrator, k2 coder, k3 critic), and a run_finished
with token totals. The story is the demo's target shape: step 2's first attempt fails the gate on the cheap
model, the orchestrator reroutes to the stronger model with the critic's findings, and the retry passes.

    python scripts/make_fixture.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CRITIC_MODEL, MODEL_LADDER, ORCHESTRATOR_MODEL, RUNS_DIR, RunBudget  # noqa: E402
from app.demo import CATEGORY, TASK  # noqa: E402
from app.state_log import StepEvent  # noqa: E402

RUN_ID = "sample_fixture"
CHEAP, STRONG = MODEL_LADDER[0], MODEL_LADDER[-1]
BASE_TS = 1_790_400_000.0  # arbitrary fixed epoch so the file is reproducible
KEY = {"orchestrator": "k1", "coder": "k2", "critic": "k3"}

events: List[StepEvent] = []


def ev(t: float, kind: str, *, step: Optional[int] = None, attempt: int = 1,
       agent: Optional[str] = None, model: Optional[str] = None, **payload: Any) -> None:
    events.append(StepEvent(run_id=RUN_ID, seq=len(events) + 1, ts=BASE_TS + t, kind=kind, step=step,
                            attempt=attempt, agent=agent, model=model, payload=payload))


def coder_round(t: float, step: int, attempt: int, model: str, prompt: str, output: str, tokens: int, secs: float) -> float:
    ev(t, "agent_call", step=step, attempt=attempt, agent="coder", model=model, prompt=prompt)
    ev(t + secs, "agent_result", step=step, attempt=attempt, agent="coder", model=model, status="completed",
       tokens=tokens, elapsed_s=secs, key=KEY["coder"], output=output)
    return t + secs + 6.0  # + workspace snapshot


def critic_round(t: float, step: int, attempt: int, subject_model: str, gate: bool, passed: bool,
                 reasons: List[str], failing: List[str], evidence: str, prompt: str, tokens: int, secs: float) -> float:
    ev(t, "agent_call", step=step, attempt=attempt, agent="critic", model=CRITIC_MODEL, prompt=prompt, gate=gate)
    verdict = {"passed": passed, "reasons": reasons, "failing_tests": failing, "evidence": evidence}
    ev(t + secs - 6.0, "tool_called", step=step, attempt=attempt, agent="critic", model=CRITIC_MODEL,
       tool="submit_verdict", arguments=verdict)
    ev(t + secs - 5.9, "tool_returned", step=step, attempt=attempt, agent="critic", model=CRITIC_MODEL,
       tool="submit_verdict", result="verdict recorded")
    ev(t + secs, "agent_result", step=step, attempt=attempt, agent="critic", model=CRITIC_MODEL, status="completed",
       tokens=tokens, elapsed_s=secs, key=KEY["critic"], output="Verdict submitted.")
    ev(t + secs + 0.1, "verdict", step=step, attempt=attempt, agent="critic", model=CRITIC_MODEL, category=CATEGORY,
       subject_model=subject_model, gate=gate, **verdict)
    return t + secs + 0.2


steps = [
    ("Reproduce the crash and inspect the spec", False),
    ("Fix inventory_report.py", True),
    ("Verify the report against the spec", False),
]
titles = " | ".join(t for t, _ in steps)
gate_step = max(i for i, (_, modifies) in enumerate(steps, 1) if modifies)

ev(0.0, "run_started", agent="orchestrator", fixture=True, task=TASK, category=CATEGORY, budget=asdict(RunBudget()),
   coder_ladder=list(MODEL_LADDER), critic_model=CRITIC_MODEL, orchestrator_model=ORCHESTRATOR_MODEL)
ev(0.1, "agent_call", agent="orchestrator", model=ORCHESTRATOR_MODEL, purpose="plan",
   prompt=f"Task: {TASK}\n\nFiles in the workspace:\n- SPEC.md (14 lines)\n- inventory_report.py (33 lines)\n- sample_stock.csv (8 lines)")
ev(9.1, "agent_result", agent="orchestrator", model=ORCHESTRATOR_MODEL, status="completed", tokens=834, elapsed_s=9.0,
   key=KEY["orchestrator"], output=titles)

t = 9.2
total_tokens = 834
attempts = 0
for i, (title, modifies) in enumerate(steps, 1):
    gate = i == gate_step
    ev(t, "step_started", step=i, agent="orchestrator", title=title, modifies_code=modifies, gate=gate,
       instruction=f"{title}. Read SPEC.md first and follow it exactly.",
       acceptance="Observable behaviour matches the spec." if modifies else "The finding is explained and verified by running the code.")
    t += 0.2
    if i == 1:
        attempts += 1
        t = coder_round(t, 1, 1, CHEAP, f"Overall task: {TASK}\nCurrent step (1 of 3): {title}", "Reproduced a ValueError on line 4 (quantity 'N/A'); SPEC.md lists 7 requirements.", 11473, 24.8)
        t = critic_round(t, 1, 1, CHEAP, False, True, [], [], "Reran the script; traceback matches the diagnosis.", "Verify step 1 acceptance by running the code.", 6320, 58.0)
        total_tokens += 11473 + 6320
        ev(t + 0.2, "step_succeeded", step=1, agent="orchestrator", attempts=1)
        t += 1.0
    elif i == 2:
        attempts += 1
        t = coder_round(t, 2, 1, CHEAP, f"Overall task: {TASK}\nCurrent step (2 of 3): {title}", "Skips rows with non-numeric quantity; script now exits 0 on the sample.", 14210, 41.5)
        reasons = ["Negative quantities are summed instead of rejected", "A warehouse with no valid rows is missing from the report"]
        failing = ["test_negative_quantity_is_rejected", "test_warehouse_with_no_valid_rows_is_still_reported"]
        t = critic_round(t, 2, 1, CHEAP, gate, False, reasons, failing, "Ran 8 tests: FAILED (failures=2)",
                         "This is the final gate. Run: python -m unittest test_hidden -v", 7680, 71.0)
        ev(t + 0.3, "reroute", step=2, attempt=1, agent="orchestrator", from_model=CHEAP, to_model=STRONG,
           modified_subtask=f"Current step (2 of 3): {title}\n\nA previous attempt failed independent review.\nProblems found:\n- " + "\n- ".join(reasons) + "\nFailing checks: " + ", ".join(failing))
        t += 0.6
        attempts += 1
        t = coder_round(t, 2, 2, STRONG, f"Current step (2 of 3): {title}\n(retry with the critic's findings)", "Rejects negative rows, keeps rows-less warehouses, logs SKIPPED lines. All spec items handled.", 18470, 47.0)
        t = critic_round(t, 2, 2, STRONG, gate, True, [], [], "Ran 8 tests: OK", "This is the final gate. Run: python -m unittest test_hidden -v", 7104, 64.0)
        total_tokens += 14210 + 7680 + 18470 + 7104
        ev(t + 0.2, "step_succeeded", step=2, attempt=2, agent="orchestrator", attempts=2)
        t += 1.0
    else:
        attempts += 1
        t = coder_round(t, 3, 1, CHEAP, f"Overall task: {TASK}\nCurrent step (3 of 3): {title}", "Ran the script on sample_stock.csv; output matches SPEC.md.", 9020, 20.0)
        t = critic_round(t, 3, 1, CHEAP, False, True, [], [], "Reran the script; output matches the spec.", "Verify step 3 acceptance by running the code.", 6110, 52.0)
        total_tokens += 9020 + 6110
        ev(t + 0.2, "step_succeeded", step=3, agent="orchestrator", attempts=1)
        t += 1.0

ev(t, "run_finished", agent="orchestrator", succeeded=True, steps_total=3, steps_succeeded=3,
   attempts_used=attempts, tokens=total_tokens, error=None)

out: Path = RUNS_DIR / f"{RUN_ID}.jsonl"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("".join(json.dumps(e.to_dict(), ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
print(f"wrote {out} ({len(events)} events, {attempts} coder attempts, {total_tokens} tokens)")
