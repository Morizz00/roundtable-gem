"""Run the demo task end to end against the live API and record it to runs/<run_id>.jsonl.

    python scripts/run_demo.py [--max-attempts N]

Needs GEMINI_API_KEY (env or .env). Prints each state-log event as it happens, then a summary. The recorded
run is what the timeline UI replays (uvicorn app.server:app). A full run makes ~8 agent calls (~150k tokens);
individual calls take 6-200s, so expect several minutes.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RunBudget, api_key  # noqa: E402
from app.demo import load_demo_task  # noqa: E402
from app.interactions import Gateway  # noqa: E402
from app.orchestrator import run_task  # noqa: E402
from app.state_log import StateLog, StepEvent, new_run_id, register_log  # noqa: E402


def one_line(e: StepEvent) -> str:
    p = e.payload
    if e.kind == "run_started":
        return p.get("task", "")
    if e.kind == "step_started":
        return f"{p.get('title', '')}{'  [GATE: hidden suite]' if p.get('gate') else ''}"
    if e.kind == "agent_call":
        return (p.get("purpose") or "call") + ": " + " ".join(str(p.get("prompt", "")).split())[:110]
    if e.kind == "agent_result":
        tail = p.get("error") or " ".join(str(p.get("output", "")).split())[:100]
        return f"{p.get('status')} {p.get('tokens', 0)} tok {p.get('elapsed_s', 0)}s [{p.get('key', '-')}] | {tail}"
    if e.kind in ("tool_called", "tool_returned"):
        return f"{p.get('tool')}"
    if e.kind == "verdict":
        return ("PASS" if p.get("passed") else "FAIL " + "; ".join(p.get("reasons", []))[:140]) + f"  (judging {p.get('subject_model')})"
    if e.kind == "reroute":
        return f"{p.get('from_model')} -> {p.get('to_model')}"
    if e.kind in ("step_succeeded", "step_failed", "reflection"):
        return str(p.get("attempts") or p.get("reason") or p.get("summary") or "")
    if e.kind == "run_finished":
        return f"succeeded={p.get('succeeded')} steps={p.get('steps_succeeded')}/{p.get('steps_total')} attempts={p.get('attempts_used')} tokens={p.get('tokens')} {p.get('error') or ''}"
    return ""


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-attempts", type=int, default=RunBudget().max_attempts)
    args = parser.parse_args()
    if not api_key():
        sys.exit("GEMINI_API_KEY is not set (put it in .env or the environment).")

    budget = replace(RunBudget(), max_attempts=args.max_attempts)
    log = register_log(StateLog(new_run_id()))
    t0: list = []

    async def printer() -> None:
        async for e in log.subscribe():
            t0.append(e.ts) if not t0 else None
            who = f"{e.agent or '':<14}{(e.model or ''):<24}"
            step = f"s{e.step}a{e.attempt}" if e.step else "     "
            print(f"+{e.ts - t0[0]:7.1f}s {step:<6} {e.kind:<15}{who}{one_line(e)}", flush=True)

    print(f"run {log.run_id} -> {log.path}")
    show = asyncio.create_task(printer())
    result = await run_task(load_demo_task(), log, Gateway(budget=budget), budget)
    await show
    print(f"\n{'SUCCEEDED' if result.succeeded else 'FAILED'}: {result}")
    print(f"recorded: {log.path}")
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
