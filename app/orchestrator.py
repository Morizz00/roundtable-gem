"""Orchestrator: plan -> delegate -> verdict -> reflect -> reroute.

STUB -- implemented in the next step. This is the part that is genuinely NEW
code (not ported): the in-run retry loop.

Contract:
    run_task(task, category, log, budget) -> RunResult. NEVER raises; every
    failure path ends in step_failed / run_finished events on the StateLog.

    1. Plan: split the task into steps (a single Antigravity call to the
       coder-less "planner" prompt, or a fixed plan for the demo task).
    2. For each step, up to budget.max_attempts times:
         spec = routing.pick(category, failed_models=tried, history=...)
         coder  -> interactions.run_agent(...)   # attempts the fix in the sandbox
         critic -> interactions.run_agent(...)   # runs hidden tests, calls submit_verdict
         verdict passed?  -> step_succeeded, next step
         verdict failed?  -> reflection event (why), reroute event (new model
                             and/or a modified sub-task carrying the critic's
                             reasons), loop
    3. run_finished carries the overall outcome and closes the stream.

Guards: budget.run_wall_clock_s is a hard ceiling for the whole run (asyncio
wait_for), mirroring the hard-ceiling pattern in RoundtableCI's
engineer_runtime.run_engineer. Lineage for the detect -> bounded fix ->
re-verify shape: roundtable-service/app/services/runtime_healing_service.py.

Honest lineage note: this loop is NOT "the Reflection System, ported". The
Reflection subsystems in RoundtableCI are an asynchronous validated-knowledge
pipeline; engineering_reflect_hook is observation-only. What carries over is
the *observation* idea -- one recorded fact per attempt (see app/state_log.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config import RunBudget
from app.state_log import StateLog


@dataclass
class RunResult:
    succeeded: bool
    steps_total: int
    steps_succeeded: int
    attempts_used: int
    error: Optional[str] = None


async def run_task(task: str, category: str, log: StateLog, budget: Optional[RunBudget] = None) -> RunResult:
    raise NotImplementedError("implemented in the next step, after the smoke test")
