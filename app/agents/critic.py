"""Critic specialist: independently judges the coder's work, pass/fail.

Lineage: the judge role from classic Roundtable (race -> judge -> improve), here with a single racer.

Isolation is what makes a failure real. The critic runs in its OWN fresh sandbox, seeded (environment sources)
with a copy of the coder's workspace plus, on the gate step, a hidden verification suite the coder never sees.
It must finish by calling the submit_verdict function tool, so the verdict is structured data rather than
parsed prose. Verified live: code_execution + a custom function tool work together in one interaction.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import ValidationError

from app.agents.coder import SANDBOX_ROOT
from app.interactions import AgentResult
from app.schemas import VERDICT_PARAMETERS, PlanStep, Verdict
from app.tools import ToolContext, ToolRegistry

VERDICT_TOOL = "submit_verdict"
VERDICT_OK = "verdict recorded"

SYSTEM_INSTRUCTION = f"""You are the Critic in a two-agent team. You independently verify another agent's work; you are not its collaborator.

{SANDBOX_ROOT} holds a copy of the Coder's workspace. Verify by actually running things with code execution; never judge
from reading alone. Do not modify the Coder's files or any test file. Be strict: passed is true only if every check
passed. Finish by calling {VERDICT_TOOL} exactly once. In it, describe problems in behavioural terms (what the program
does wrong), never by quoting test source, and list failing test names separately."""


def build_prompt(task: str, step: PlanStep, index: int, total: int, *, gate: bool, suite: Optional[str]) -> str:
    """The user turn for one review. On the gate step `suite` names the verification file to run."""
    lines: List[str] = [
        f"Overall task: {task}",
        f"Step under review ({index} of {total}): {step.title}",
        f"The step's instruction was: {step.instruction}",
        f"It counts as done when: {step.acceptance}",
        "",
    ]
    if gate and suite:
        lines += [
            f"This is the final gate. In {SANDBOX_ROOT}, run the verification suite: `python -m unittest {suite.removesuffix('.py')} -v`.",
            f"Also check the step's acceptance criteria. Then call {VERDICT_TOOL}.",
        ]
    else:
        lines += [
            "Verify the step's acceptance criteria by running the code where relevant "
            "(later steps will do more work, so judge only this step).",
            f"Then call {VERDICT_TOOL}.",
        ]
    return "\n".join(lines)


def build_tools() -> ToolRegistry:
    registry = ToolRegistry()

    async def submit_verdict(args: dict, ctx: ToolContext) -> str:
        try:
            Verdict.model_validate(args)
        except ValidationError as e:
            # The agent sees this text as the function result and can correct itself.
            return f"error: invalid verdict ({str(e)[:200]})"
        return VERDICT_OK

    registry.register(
        VERDICT_TOOL,
        "Submit your final pass/fail verdict for the step under review. Call exactly once, after verifying.",
        VERDICT_PARAMETERS,
        submit_verdict,
    )
    return registry


def extract_verdict(result: AgentResult) -> Optional[Verdict]:
    """The last valid submit_verdict call, or None (critic crashed, timed out, or never submitted)."""
    for call in reversed(result.tool_calls):
        if call["name"] == VERDICT_TOOL and call["result"] == VERDICT_OK:
            try:
                return Verdict.model_validate(call["arguments"])
            except ValidationError:
                continue
    return None
