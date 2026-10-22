"""Engines for the slow tests that the production code must never build."""

from __future__ import annotations

from typing import Any

from decimer_chem.engine import OrthonymEngine
from decimer_chem.settings import Settings


class DefaultTierEngine(OrthonymEngine):
    """Orthonym's *default* tier. It ships carve-out names (`scyllo-inositol`, phane names, ...)
    as `pin_unverified` with `verified: "unverified"`; the policy must withhold every one."""

    @staticmethod
    def _build_namer() -> Any:
        import orthonym

        return orthonym.Orthonym()


def make_default_tier_engine(settings: Settings) -> DefaultTierEngine:
    return DefaultTierEngine(settings)
