"""Critic specialist: judges the coder's output, pass/fail.

STUB -- prompt and tools are written in the next step.

Lineage: the judge role from classic Roundtable (race -> judge -> improve),
here with a single racer.

Intended shape:
    SYSTEM_INSTRUCTION  -- role: run the hidden tests against the coder's
                           result and decide. Must finish by calling the
                           submit_verdict function tool -- a structured verdict
                           beats parsing free text.
    build_tools()       -- ToolRegistry with submit_verdict(passed: bool,
                           reasons: list[str], failing_tests: list[str]).
    The hidden tests are mounted into the critic's environment only
    (environment sources), never shown to the coder -- that is what makes the
    first-attempt failure real rather than scripted.
"""
from __future__ import annotations

from app.tools import ToolRegistry

SYSTEM_INSTRUCTION: str = ""  # TODO(next step)


def build_tools() -> ToolRegistry:
    raise NotImplementedError
