"""Probe: latency and correctness of each supported agent model on a critic-shaped task.

Why: the first live run's critic on gemini-3.8-flash timed out (>300s) on a trivial review, while
gemini-3.5-flash-lite finished the same shape of task in ~27s. This measures every supported model so
CRITIC_MODEL and the coder ladder are chosen from data.

    python scripts/probe_models.py [--timeout 200] [--models a,b,c]

Each model reviews a tiny solution against a unittest file in a fresh sandbox (inline sources), using
code_execution + submit_verdict. Correct = passed=false naming test_negative_rejected. Calls run concurrently.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import critic  # noqa: E402
from app.config import RunBudget  # noqa: E402
from app.interactions import Gateway  # noqa: E402
from app.keys import KeyRing  # noqa: E402
from app.routing import AgentSpec  # noqa: E402

# gemini-3.8-flash appears twice on purpose: same model on two different keys separates
# "this model is slow" from "this key/project is throttled".
MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.8-flash"]
SOLUTION = "def add(a, b):\n    return a + b\n"
TESTS = (
    "import unittest\nfrom solution import add\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_add_positive(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
    "    def test_negative_rejected(self):\n        with self.assertRaises(ValueError):\n            add(-1, 3)\n\n"
    "if __name__ == '__main__':\n    unittest.main()\n"
)
PROMPT = ("Review the solution in /workspace: run `python -m unittest test_hidden -v` with code execution, then call "
          "submit_verdict (passed=true only if every test passed).")


async def probe(model: str, timeout: float, key: str, label: str) -> dict:
    # A private single-role ring per probe: each call runs on its OWN key, so concurrent probes never share one.
    gw = Gateway(budget=replace(RunBudget(), interaction_timeout_s=timeout), keys=KeyRing(by_role={"critic": key}))
    sources = [{"type": "inline", "target": "/workspace/solution.py", "content": SOLUTION},
               {"type": "inline", "target": "/workspace/test_hidden.py", "content": TESTS}]
    res = await gw.run_agent(AgentSpec("critic", model), PROMPT, system_instruction=critic.SYSTEM_INSTRUCTION,
                             tools=critic.build_tools(), environment={"type": "remote", "sources": sources})
    verdict = critic.extract_verdict(res)
    correct = bool(verdict and verdict.passed is False and any("negative" in t for t in verdict.failing_tests))
    return {"model": model, "key": label, "status": res.status, "seconds": res.elapsed_s, "tokens": res.tokens,
            "verdict": None if verdict is None else verdict.passed, "correct": correct, "error": res.error}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeout", type=float, default=200.0)
    parser.add_argument("--models", type=str, default=",".join(MODELS))
    args = parser.parse_args()
    ring = KeyRing.from_env()
    pool = ring.all_keys()
    if not pool:
        sys.exit("No API key configured (GEMINI_API_KEY / GEMINI_API_KEYS).")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) > len(pool):
        print(f"note: {len(models)} probes but only {len(pool)} keys; some probes will share a key", flush=True)
    print(f"probing {len(models)} models concurrently, one key each (timeout {args.timeout:.0f}s)...", flush=True)
    rows = await asyncio.gather(*(
        probe(m, args.timeout, pool[i % len(pool)], ring.label_of(pool[i % len(pool)]) or "?") for i, m in enumerate(models)
    ))
    print(f"\n{'model':<24}{'key':<5}{'status':<12}{'seconds':>9}{'tokens':>9}  verdict  correct  error")
    for r in sorted(rows, key=lambda r: r["seconds"]):
        print(f"{r['model']:<24}{r['key']:<5}{r['status']:<12}{r['seconds']:>9}{r['tokens']:>9}  {str(r['verdict']):<8} {str(r['correct']):<8} {(r['error'] or '')[:70]}")


if __name__ == "__main__":
    asyncio.run(main())
