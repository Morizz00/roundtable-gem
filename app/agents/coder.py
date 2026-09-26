"""Coder specialist: attempts each plan step inside the Antigravity sandbox.

The coder needs no custom function tools: the sandbox's own code execution and filesystem tools are all it
uses, so run_agent is called without a ToolRegistry. Its work persists across steps and retries because the
orchestrator reuses one environment for the whole run (verified live: files survive across interactions
that share an environment_id).

It is never shown the hidden verification suite. It only ever sees the critic's behavioural feedback (what
failed and which checks), the way a developer sees a code review.
"""
from __future__ import annotations

from typing import Optional

from app.schemas import PlanStep, Verdict

SANDBOX_ROOT = "/workspace"

SYSTEM_INSTRUCTION = f"""You are the Coder in a two-agent team. Another agent, the Critic, will independently review your work.

Work only inside {SANDBOX_ROOT}. Before editing, read the relevant files, including any SPEC.md, so you follow the real
requirements rather than guessing. After editing, run the code yourself to check it works. Keep changes focused on the
current step. When you finish, reply with a short summary of what you changed and what you verified."""


def build_prompt(
    task: str,
    step: PlanStep,
    index: int,
    total: int,
    *,
    feedback: Optional[Verdict] = None,
    failure_note: Optional[str] = None,
    revision: Optional[str] = None,
) -> str:
    """The user turn for one coder attempt. `feedback`/`failure_note`/`revision` are set on a retry.

    `revision` is the orchestrator's model-written rewrite of the sub-task (see app/reflection.py).
    """
    parts = [
        f"Overall task: {task}",
        f"Current step ({index} of {total}): {step.title}",
        f"Instruction: {step.instruction}",
        f"This step is done when: {step.acceptance}",
        f"The workspace ({SANDBOX_ROOT}) keeps the results of earlier steps and attempts.",
    ]
    if feedback is not None:
        lines = ["", "A previous attempt at this step failed independent review."]
        if feedback.reasons:
            lines.append("Problems found:")
            lines += [f"- {r}" for r in feedback.reasons]
        if feedback.failing_tests:
            lines.append("Failing checks: " + ", ".join(feedback.failing_tests))
        lines.append("Re-read the requirements and fix the underlying behaviour, not only these symptoms.")
        parts.append("\n".join(lines))
    elif failure_note:
        parts.append(f"\nA previous attempt at this step did not finish cleanly ({failure_note}). Continue from the current workspace state.")
    if revision:
        parts.append(f"\nRevised instruction from the orchestrator, based on why the last attempt failed:\n{revision}")
    return "\n".join(parts)
