"""Coder specialist: attempts the fix inside the Antigravity sandbox.

STUB -- prompt and tools are written in the next step.

Intended shape:
    SYSTEM_INSTRUCTION  -- role: fix the broken script in /workspace, run it,
                           report what it changed. It does NOT see the hidden
                           tests, so a first attempt can fail for real.
    build_prompt(step, prior_verdict=None)  -- on a reroute, folds the critic's
                           failure reasons into a modified sub-task.
    build_tools()       -- ToolRegistry of client-side function tools (the
                           sandbox's own code_execution / filesystem tools are
                           built in and need no registration).
"""
from __future__ import annotations

from typing import Optional

from app.tools import ToolRegistry

SYSTEM_INSTRUCTION: str = ""  # TODO(next step)


def build_prompt(step: str, prior_verdict: Optional[dict] = None) -> str:
    raise NotImplementedError


def build_tools() -> ToolRegistry:
    raise NotImplementedError
