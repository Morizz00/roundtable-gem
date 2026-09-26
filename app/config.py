"""Config: env loading, model ladder, run budgets.

Budget shape follows RoundtableCI's MissionRuntimeConfig
(roundtable-service/app/services/engineer_runtime.py: max turns / wall-clock /
hard ceiling). The turn loop itself is NOT ported -- the Antigravity agent runs
its own loop server-side -- so the budgets map onto what the Interactions API
exposes instead: agent_config.max_total_tokens, the per-interaction execution
timeout, and status == "incomplete" when a token budget is exceeded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:  # .env is optional; plain env vars work too
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"

# The one base agent the Interactions API exposes for managed agents.
AGENT_ID = "antigravity-preview-09-2026"

# Models the Antigravity harness supports (per the Gemini API docs), cheapest ->
# strongest. Named managed agents cannot change model per interaction, so the
# orchestrator uses the inline base agent + agent_config.model to reroute
# (verified live: a reused environment and a chained continuation both accept a
# different model).
#
# Measured with scripts/probe_models.py (a critic-shaped task, one key per call, 2026-09-26):
#   3.5-flash 42s / 10.6k tok | 3.6-flash 102s / 19.5k | 3.7-flash 102s / 10.3k
#   3.8-flash 156s and 162s / 23.8k (two different keys: the slowness is the model, not a throttled key)
#   3.5-flash-lite 241s timeout once, but 27-81s in earlier runs (high variance)
# So the ladder climbs lite -> 3.5-flash -> 3.8-flash: each retry is stronger, and the slow, costly 3.8 is only
# reached on a third attempt.
MODEL_LADDER = ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.8-flash")

# The coder is routed up the ladder on failure. The critic is a judge: it is never routed down, and it runs on
# the fastest model that judged correctly in the probe (a 3.8 critic timed out at 300s in the first live run).
# The orchestrator's own reasoning (plan, later reflection) is a plain model call on the same Interactions API:
# no ~12k-token sandbox overhead, schema-validated JSON, ~9s.
CRITIC_MODEL = "gemini-3.5-flash"
ORCHESTRATOR_MODEL = "gemini-3.8-flash"


@dataclass(frozen=True)
class RunBudget:
    max_attempts: int = 3  # orchestrator-level tries per plan step
    # Per-interaction agent_config.max_total_tokens. A trivial call already costs ~12k tokens
    # (an env-read ~19k), so the old 50k default left almost no room for real work.
    max_total_tokens: int = 120_000
    max_run_tokens: int = 600_000  # ceiling over every call in one run (a full run is ~150k)
    # Agent calls range from ~6s to well over 300s: the gate step (implement every spec item) hit the old 300s
    # ceiling on three runs in a row, then a 3.5-flash retry COMPLETED in 165s using 168k tokens, so those "0 token"
    # timeouts were cancelled long-running calls, not hangs. 420s gives that step room while still failing a truly
    # stuck call faster than the docs' 600s default. Plain-model calls take ~7-10s.
    interaction_timeout_s: float = 420.0
    run_wall_clock_s: float = 2700.0  # hard ceiling for a whole run


def api_key() -> Optional[str]:
    """Any configured key (single key or the first of the pool), or None. Never log or print the value.

    Used for "is a key configured?" checks; per-role assignment lives in app.keys.KeyRing.
    """
    from app.keys import KeyRing  # local import: keys imports config for its .env side effect

    return KeyRing.from_env().any_key()
