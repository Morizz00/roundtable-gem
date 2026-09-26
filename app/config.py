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
# orchestrator uses the inline base agent + agent_config.model to reroute.
MODEL_LADDER = ("gemini-3.5-flash-lite", "gemini-3.8-flash")


@dataclass(frozen=True)
class RunBudget:
    max_attempts: int = 3  # orchestrator-level tries per plan step
    max_total_tokens: int = 50_000  # agent_config.max_total_tokens, per interaction
    interaction_timeout_s: float = 600.0  # docs' default execution timeout
    run_wall_clock_s: float = 900.0  # hard ceiling for a whole run


def api_key() -> Optional[str]:
    """Returns the key or None. Never log or print the value."""
    return os.environ.get("GEMINI_API_KEY") or None
