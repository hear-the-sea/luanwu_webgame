"""Zero-ORM parser and formatter helpers for equipment set_bonus definitions."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


def extract_raw_set_bonus_map(raw_bonus: Mapping[str, Any]) -> Any:
    """Return the stat-bonus map inside a set_bonus tier without falling back on explicit empty {}."""
    if "bonus" in raw_bonus:
        return raw_bonus["bonus"]
    if "bonuses" in raw_bonus:
        return raw_bonus["bonuses"]
    if any(key in raw_bonus for key in ("pieces", "combat_effects", "effect_description")):
        return {}
    return dict(raw_bonus)


def parse_set_bonus_entries(raw_bonus: Any) -> list[dict[str, Any]]:
    """Normalize dict/list/shorthand set_bonus into uniform tier dictionaries."""
    if not raw_bonus:
        return []
    if isinstance(raw_bonus, (list, tuple)):
        entries: list[dict[str, Any]] = []
        for item in raw_bonus:
            entries.extend(parse_set_bonus_entries(item))
        return entries
    if not isinstance(raw_bonus, dict):
        return []

    pieces = raw_bonus.get("pieces")
    raw_map = extract_raw_set_bonus_map(raw_bonus)
    stat_map = dict(raw_map) if isinstance(raw_map, dict) else {}
    entry: dict[str, Any] = {
        "pieces": pieces,
        "bonus": stat_map,
    }
    if "combat_effects" in raw_bonus and isinstance(raw_bonus["combat_effects"], list):
        entry["combat_effects"] = copy.deepcopy(raw_bonus["combat_effects"])
    if isinstance(raw_bonus.get("effect_description"), str) and raw_bonus["effect_description"].strip():
        entry["effect_description"] = raw_bonus["effect_description"].strip()
    return [entry]


def iter_set_bonus_stat_pairs(raw_bonus: Any) -> list[tuple[int | None, dict[str, Any]]]:
    """Return legacy (pieces, bonus_map) pairs for stat-focused templates and helpers."""
    return [(entry.get("pieces"), entry["bonus"]) for entry in parse_set_bonus_entries(raw_bonus)]


def format_set_bonus_entry_texts(
    entry: dict[str, Any],
    *,
    stat_labels: dict[str, str],
) -> list[str]:
    """Render readable Chinese text fragments for one parsed set_bonus tier."""
    parts: list[str] = []
    bonus_map = entry.get("bonus") or {}
    if isinstance(bonus_map, dict):
        for key, value in bonus_map.items():
            if key in {"pieces", "bonus", "bonuses"}:
                continue
            if value is None or value == 0:
                continue
            parts.append(f"{stat_labels.get(key, '未知属性')}+{value}")
    effect_description = entry.get("effect_description")
    if isinstance(effect_description, str) and effect_description.strip():
        parts.append(effect_description.strip())
    return parts
