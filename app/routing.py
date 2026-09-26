"""Routing policy: pick the model for a specialist, and reroute after a failure.

Lineage (RoundtableCI): Sage's query routing
(sage-service/app/services/query_routing_service.py) -- a canonical category
maps to a ranked candidate list, ranking uses Bayesian-smoothed win rates
(app/ranking_math.py, verbatim from shared/ranking_math.py), ties break by
ascending name so the same evidence always yields the same pick, and there is
a fallback when nothing is left.

What is NOT ported: the rankings themselves. Sage's come from Postgres race
outcomes across OpenRouter families; here the "races" are critic verdicts
recorded in runs/*.jsonl, and the candidates are only the Gemini models the
Antigravity harness supports. With no history the policy is a plain escalation
ladder: cheapest model first, stronger one after a failure.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from app.config import MODEL_LADDER, RUNS_DIR
from app.ranking_math import bayesian_smooth

logger = logging.getLogger(__name__)

# category -> candidates, cheapest -> strongest. Category labels reuse Sage's
# canonical names so the lineage is visible; unknown categories use the default.
CATEGORY_LADDERS: Dict[str, Tuple[str, ...]] = {
    "coding_python": MODEL_LADDER,
    "general": MODEL_LADDER,
}

# category -> model -> (win_rate, num_races)
History = Mapping[str, Mapping[str, Tuple[float, int]]]


@dataclass(frozen=True)
class AgentSpec:
    role: str  # "coder" | "critic"
    model: str


def pick(
    category: str,
    failed_models: Sequence[str] = (),
    history: Optional[History] = None,
    role: str = "coder",
) -> AgentSpec:
    """Choose a model for `role` on `category`.

    1. Order candidates: by smoothed win rate (ties -> ascending name) when
       there is history for this category, else the static cheap->strong ladder.
    2. Drop models that already failed this step.
    3. If nothing is left, fall back to the strongest model rather than dying.
    """
    candidates = list(CATEGORY_LADDERS.get(category, MODEL_LADDER))
    stats = (history or {}).get(category) or {}
    if stats:
        def smoothed(model: str) -> float:
            win_rate, num_races = stats.get(model, (0.0, 0))
            return bayesian_smooth(win_rate, num_races)

        candidates.sort(key=lambda m: (-smoothed(m), m))

    failed = set(failed_models)
    remaining = [m for m in candidates if m not in failed]
    model = remaining[0] if remaining else MODEL_LADDER[-1]
    return AgentSpec(role=role, model=model)


def history_from_runs(runs_dir: Optional[Path] = None) -> Dict[str, Dict[str, Tuple[float, int]]]:
    """Tally critic verdicts in recorded runs into per-(category, model) win rates.

    A `verdict` event counts when it carries a model and payload["category"] +
    payload["passed"]. Malformed lines are skipped, never raised on.
    """
    directory = Path(runs_dir) if runs_dir else RUNS_DIR
    tally: Dict[str, Dict[str, list]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            logger.warning("routing: could not read %s: %s", path, e)
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("kind") != "verdict":
                continue
            payload = event.get("payload") or {}
            model, category = event.get("model"), payload.get("category")
            if not model or not category or "passed" not in payload:
                continue
            wins_and_total = tally.setdefault(category, {}).setdefault(model, [0, 0])
            wins_and_total[0] += 1 if payload["passed"] else 0
            wins_and_total[1] += 1
    return {
        category: {model: (wins / total, total) for model, (wins, total) in models.items()}
        for category, models in tally.items()
    }
