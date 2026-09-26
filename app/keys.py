"""API key assignment: one dedicated key per role, spares for failover.

Environment (all optional; each source only fills gaps left by the one above it):
    GEMINI_API_KEY_ORCHESTRATOR / _CODER / _CRITIC   explicit key for that role
    GEMINI_API_KEYS                                  comma-separated pool: the first three fill orchestrator,
                                                     coder, critic in that order; the rest are spares
    GEMINI_API_KEY                                   single key: fallback for any role still without one

Why a dedicated key per role: each agent's traffic is attributable (the `key` label lands on its events), and one
role hitting a limit does not starve the others -- IF the keys belong to different Google projects. Rate limits are
applied per project, not per key, so keys from one project share a single quota.

Failover rules (enforced in Gateway, not here): a role's key is swapped for a spare only at a fresh interaction
boundary, because interaction chains (previous_interaction_id) and sandbox environments are project-scoped and cannot
follow a role onto another key. The swap is sticky for the rest of the run so later calls stay on the key that owns
the coder's environment.

Nothing here ever logs or returns key material except key_for()/all_keys(); use label_of() for anything displayed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import app.config  # noqa: F401  (loads .env as a side effect)

ROLES = ("orchestrator", "coder", "critic")


@dataclass
class KeyRing:
    by_role: Dict[str, str] = field(default_factory=dict)
    spares: List[str] = field(default_factory=list)
    _labels: Dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        # Stable, non-secret labels (k1, k2, ...) in role order then spare order.
        ordered = [self.by_role[r] for r in ROLES if r in self.by_role] + list(self.spares)
        for key in ordered:
            self._labels.setdefault(key, f"k{len(self._labels) + 1}")

    @classmethod
    def from_env(cls, environ: Optional[Dict[str, str]] = None) -> "KeyRing":
        env = os.environ if environ is None else environ
        pool = [k.strip() for k in (env.get("GEMINI_API_KEYS") or "").split(",") if k.strip()]
        by_role: Dict[str, str] = {}
        for role in ROLES:
            explicit = (env.get(f"GEMINI_API_KEY_{role.upper()}") or "").strip()
            if explicit:
                by_role[role] = explicit
        # Pool fills the roles still empty, in role order, skipping keys already assigned explicitly.
        unassigned = [k for k in pool if k not in by_role.values()]
        for role in ROLES:
            if role not in by_role and unassigned:
                by_role[role] = unassigned.pop(0)
        single = (env.get("GEMINI_API_KEY") or "").strip()
        for role in ROLES:
            if role not in by_role and single:
                by_role[role] = single
        used = set(by_role.values())
        spares = [k for k in unassigned if k not in used]
        if single and single not in used and single not in spares:
            spares.append(single)
        return cls(by_role=by_role, spares=spares)

    @classmethod
    def single(cls, key: str) -> "KeyRing":
        return cls(by_role={role: key for role in ROLES})

    def key_for(self, role: str) -> Optional[str]:
        return self.by_role.get(role) or next(iter(self.by_role.values()), None)

    def any_key(self) -> Optional[str]:
        return next(iter(self.by_role.values()), None) or next(iter(self.spares), None)

    def all_keys(self) -> List[str]:
        seen: List[str] = []
        for key in list(self.by_role.values()) + self.spares:
            if key not in seen:
                seen.append(key)
        return seen

    def label_of(self, key: Optional[str]) -> Optional[str]:
        return self._labels.get(key) if key else None

    def has_spare(self) -> bool:
        return bool(self.spares)

    def rotate(self, role: str) -> Optional[str]:
        """Give `role` the next spare; the key it gives up goes to the back so it can be retried later.

        Returns the new key, or None when there is no spare (a shared single key has nothing to rotate to).
        """
        if not self.spares:
            return None
        old = self.by_role.get(role)
        new = self.spares.pop(0)
        self.by_role[role] = new
        if old and old != new and old not in self.by_role.values() and old not in self.spares:
            self.spares.append(old)
        self._labels.setdefault(new, f"k{len(self._labels) + 1}")
        return new

    def scrub(self, text: str) -> str:
        for key in sorted(self.all_keys(), key=len, reverse=True):
            text = text.replace(key, "***")
        return text
