"""Model-generated reflection: after a failed review, the orchestrator reasons about WHY and rewrites the sub-task.

This is what makes the retry a decision rather than a template. A plain-model call on the Interactions API
(structured JSON, chained onto the planning interaction with previous_interaction_id so the orchestrator
"remembers" the plan and earlier failures server-side) returns a Decision:

    summary          the root cause, in behavioural terms
    modified_subtask a complete, self-contained instruction for the coder's next attempt
    escalate         whether a stronger model should take it, or a same-model retry will do

The orchestrator applies it, bounded: escalate is honoured on the first retry, and every later retry escalates
regardless (a model may not talk us into repeating the same failure forever). If the reflection call fails or
returns something unusable, the run falls back to the deterministic retry (the critic's findings appended to
the original instruction) and records the fallback -- recovery never depends on the reflection succeeding.

Lineage note: this is NOT the RoundtableCI Reflection System. That is an asynchronous validated-knowledge
pipeline (observations -> compiler -> validation -> store -> genome -> retrieval) and stays on the roadmap. What
is shared is the principle: the orchestrator learns from a failure; the model does not change.
"""
from __future__ import annotations

from typing import Sequence

from app.schemas import PlanStep, Verdict

SYSTEM_INSTRUCTION = (
    "You are the orchestrator of a Coder and Critic team. A step failed independent review. Decide how the next "
    "attempt should differ. Explain the root cause in one or two sentences, in terms of behaviour, not blame. Write "
    "modified_subtask as a complete, self-contained instruction for the coder's next attempt: restate the goal, list "
    "the concrete behaviours that must hold, and point to files to re-read (such as SPEC.md). Never invent test code "
    "or assertions you were not shown. Set escalate to true if the failure suggests the attempt lacked capability "
    "(it missed several requirements or misread the spec); set it to false for a small slip a retry on the same model "
    "would fix."
)


def build_prompt(
    task: str,
    step: PlanStep,
    index: int,
    total: int,
    *,
    attempt: int,
    coder_model: str,
    coder_output: str,
    verdict: Verdict,
    ladder: Sequence[str],
) -> str:
    reasons = "\n".join(f"- {r}" for r in verdict.reasons) or "- (the critic gave no reasons)"
    failing = ", ".join(verdict.failing_tests) or "(none named)"
    output = " ".join((coder_output or "").split())[:1200] or "(no summary)"
    return "\n".join([
        f"Overall task: {task}",
        f"Step {index} of {total}: {step.title}",
        f"Instruction given to the coder: {step.instruction}",
        f"The step counts as done when: {step.acceptance}",
        "",
        f"Attempt {attempt} ran on {coder_model} and failed independent review.",
        f"The coder reported: {output}",
        "The critic found:",
        reasons,
        f"Failing checks: {failing}",
        f"Evidence: {verdict.evidence or '(none)'}",
        "",
        f"Models available, weakest to strongest: {', '.join(ladder)}.",
    ])
