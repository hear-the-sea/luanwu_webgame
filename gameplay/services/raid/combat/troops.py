"""Raid troop helpers: normalization and pure calculation utilities.

Functions that need direct ORM access (PlayerTroop) or that tests monkeypatch
through ``combat_runs`` remain in ``runs.py`` and import from here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict

from core.utils import safe_positive_int

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ....models import Manor, PlayerTroop


# ============ Normalization helpers ============


def _normalize_mapping(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def _normalize_positive_int_mapping(raw: Any) -> Dict[str, int]:
    data = _normalize_mapping(raw)
    normalized: Dict[str, int] = {}
    for key, value in data.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        normalized_value = safe_positive_int(value, 0)
        if normalized_value > 0:
            normalized[normalized_key] = normalized_value
    return normalized


# ============ Pure troop calculations ============


def _normalize_troops_for_addition(troops_to_add: Dict[str, int]) -> Dict[str, int]:
    return _normalize_positive_int_mapping(troops_to_add)


def _extract_raid_troops_lost(
    loadout: Dict[str, int],
    battle_report: Any,
) -> Dict[str, int]:
    """Extract troop losses from a battle report, capped by the original loadout."""
    if not battle_report:
        return {}

    normalized_loadout = _normalize_positive_int_mapping(loadout)
    if not normalized_loadout:
        return {}

    losses = _normalize_mapping(getattr(battle_report, "losses", {}))
    attacker_losses = _normalize_mapping(losses.get("attacker"))
    casualties = attacker_losses.get("casualties")
    if not isinstance(casualties, list):
        return {}

    from battle.troops import load_troop_templates

    troop_definitions = load_troop_templates()
    troops_lost: Dict[str, int] = {}
    for entry in casualties:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if key not in normalized_loadout or key not in troop_definitions:
            continue
        lost = safe_positive_int(entry.get("lost", 0), 0)
        if lost > 0:
            troops_lost[key] = troops_lost.get(key, 0) + lost
    return troops_lost


def _calculate_surviving_raid_troops(
    loadout: Dict[str, int],
    troops_lost: Dict[str, int],
) -> Dict[str, int]:
    surviving_troops: Dict[str, int] = {}
    for troop_key, original_count in loadout.items():
        surviving = max(0, original_count - troops_lost.get(troop_key, 0))
        if surviving > 0:
            surviving_troops[troop_key] = surviving
    return surviving_troops


def _collect_troop_upserts(
    manor: Manor,
    troops_to_add: Dict[str, int],
    templates: Dict[str, Any],
    existing: Dict[str, PlayerTroop],
    now: datetime,
) -> tuple[list[PlayerTroop], list[PlayerTroop]]:
    """Prepare update/create lists for troop batch upsert.

    Returns ``(to_update, to_create)`` lists of ``PlayerTroop`` instances.
    Import of ``PlayerTroop`` is deferred to avoid circular imports;
    callers in ``runs.py`` pass existing ``PlayerTroop`` instances directly.
    """
    from ....models import PlayerTroop

    to_update: list[PlayerTroop] = []
    to_create: list[PlayerTroop] = []
    for key, count in troops_to_add.items():
        template = templates.get(key)
        if not template:
            logger.warning("Unknown troop template: %s", key)
            continue
        if key in existing:
            existing[key].count += count
            existing[key].updated_at = now
            to_update.append(existing[key])
        else:
            to_create.append(PlayerTroop(manor=manor, troop_template=template, count=count))
    return to_update, to_create
