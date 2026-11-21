"""Django-aware arena power settings.

Kept out of ``virtual_lineups`` because that module is intentionally
framework-independent (see the architecture gate test).
"""

from __future__ import annotations

from django.conf import settings


def current_arena_power_formula_version() -> int:
    """Return the configured arena power formula version (1 or 2)."""

    try:
        version = int(getattr(settings, "ARENA_POWER_FORMULA_VERSION", 1))
    except (TypeError, ValueError):
        return 1
    return version if version in {1, 2} else 1
