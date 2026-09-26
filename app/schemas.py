"""Structured shapes exchanged with the models.

Plan / PlanStep are the orchestrator's plan (a plain-model call with response_format). They are kept free of
JSON-schema length/count constraints on purpose: not every keyword is guaranteed to be honoured by the API, so
bounds are enforced afterwards in validate_plan(), where a violation becomes a logged fallback rather than a
rejected request. Verdict is what the critic submits through the submit_verdict function tool.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

MIN_STEPS, MAX_STEPS = 2, 5


class PlanStep(BaseModel):
    title: str = Field(description="Short imperative title, e.g. 'Reproduce the crash'.")
    instruction: str = Field(description="What the coder must do in this step, specific and self-contained.")
    acceptance: str = Field(description="How the critic can verify the step is done, in observable terms.")
    modifies_code: bool = Field(description="True if this step changes the project's code.")


class Plan(BaseModel):
    steps: List[PlanStep]


def validate_plan(plan: Plan) -> Optional[str]:
    """None if usable, else why not. Bounds live here, not in the schema (see module docstring)."""
    if not (MIN_STEPS <= len(plan.steps) <= MAX_STEPS):
        return f"plan has {len(plan.steps)} steps, expected {MIN_STEPS}-{MAX_STEPS}"
    for i, step in enumerate(plan.steps, 1):
        if not step.title.strip() or not step.instruction.strip() or not step.acceptance.strip():
            return f"step {i} has an empty title, instruction or acceptance"
    return None


class Decision(BaseModel):
    """The orchestrator's reflection after a failed review (a plain-model call with response_format)."""

    summary: str = Field(description="Why the attempt failed, in one or two sentences of behaviour, not blame.")
    modified_subtask: str = Field(description="A complete, self-contained instruction for the coder's next attempt.")
    escalate: bool = Field(description="True if a stronger model should take the next attempt, false for a small slip.")


class Verdict(BaseModel):
    passed: bool
    reasons: List[str] = Field(default_factory=list)
    failing_tests: List[str] = Field(default_factory=list)
    evidence: str = ""


# JSON-schema parameters for the submit_verdict function tool (flat, no $refs).
VERDICT_PARAMETERS = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean", "description": "True only if every check passed."},
        "reasons": {
            "type": "array", "items": {"type": "string"},
            "description": "What is wrong, in behavioural terms (not test source). Empty when passed.",
        },
        "failing_tests": {
            "type": "array", "items": {"type": "string"},
            "description": "Names of failing tests or checks. Empty when passed.",
        },
        "evidence": {"type": "string", "description": "The summary line of the verification run."},
    },
    "required": ["passed", "reasons", "failing_tests"],
}
