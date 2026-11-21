"""Central resolver for the arena power formula version.

Kept separate from ``projection`` because that module must stay free of
Django imports; Django-aware call sites import this helper instead.
"""

from __future__ import annotations

from django.conf import settings


def arena_power_formula_version() -> int:
    try:
        version = int(getattr(settings, "ARENA_POWER_FORMULA_VERSION", 1))
    except (TypeError, ValueError):
        return 1
    return version if version in {1, 2} else 1
