"""Shared ranking math -- the ONE place the Bayesian smoothing formula used
across model rankings lives.

Lifted VERBATIM from RoundtableCI: shared/ranking_math.py (docstring trimmed).
Keep the body identical so `diff` against the source stays a one-glance check.
"""
from __future__ import annotations

SMOOTHING_C: float = 15.0
GLOBAL_PRIOR_M: float = 0.5


def bayesian_smooth(
    win_rate: float, num_races: float, *, c: float = SMOOTHING_C, m: float = GLOBAL_PRIOR_M,
) -> float:
    """Shrink a raw win_rate toward a fixed global prior (m) as evidence
    (num_races) accumulates. A model with 0 races scores exactly m; a model
    with many races converges toward its raw win_rate. c controls how many
    races it takes to trust the raw rate over the prior -- higher c means
    slower convergence (more skepticism of small samples).
    """
    n = float(num_races or 0.0)
    r = float(win_rate or 0.0)
    return (n * r + c * m) / (n + c)
