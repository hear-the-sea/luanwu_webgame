"""
装备工具模块

提供装备套装加成计算等功能。
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Dict

from ..models import GearSlot, GearTemplate
from .set_bonus_parser import iter_set_bonus_stat_pairs, parse_set_bonus_entries

if TYPE_CHECKING:
    pass

# 装备槽位映射
EQUIP_SLOT_MAP = {
    "equip_helmet": GearSlot.HELMET.value,
    "equip_armor": GearSlot.ARMOR.value,
    "equip_weapon": GearSlot.WEAPON.value,
    "equip_shoes": GearSlot.SHOES.value,
    "equip_mount": GearSlot.MOUNT.value,
    "equip_jewelry": GearSlot.ORNAMENT.value,
    "equip_ornament": GearSlot.ORNAMENT.value,
    "equip_special": GearSlot.DEVICE.value,
    "equip_device": GearSlot.DEVICE.value,
}

# 套装属性映射
SET_STAT_FIELD_MAP = {
    "attack": "attack_bonus",
    "defense": "defense_stat",
    "hp": "hp_bonus",
    "force": "force",
    "intellect": "intellect",
    "agility": "agility",
    "luck": "luck",
    "troop_capacity": "troop_capacity_bonus",
}


def _normalize_set_bonus_definitions(raw_bonus) -> list[tuple[int | None, dict]]:
    return iter_set_bonus_stat_pairs(raw_bonus)


def _collect_set_info(gear_items) -> Dict[str, Dict[str, object]]:
    gear_items = list(gear_items)
    base_keys = set()
    for gear in gear_items:
        tpl = getattr(gear, "template", None)
        if not tpl:
            continue
        base_key = getattr(tpl, "base_key", "") or ""
        set_key = getattr(tpl, "set_key", "") or ""
        set_bonus = getattr(tpl, "set_bonus", None)
        if base_key and base_key != getattr(tpl, "key", "") and (not set_key or not set_bonus):
            base_keys.add(base_key)

    base_set_info = {
        key: (set_key, set_description, set_bonus)
        for key, set_key, set_description, set_bonus in GearTemplate.objects.filter(key__in=base_keys).values_list(
            "key", "set_key", "set_description", "set_bonus"
        )
    }

    sets: Dict[str, Dict[str, object]] = {}
    for gear in gear_items:
        tpl = getattr(gear, "template", None)
        if not tpl:
            continue
        set_key = getattr(tpl, "set_key", "") or ""
        set_description = getattr(tpl, "set_description", "") or ""
        raw_set_bonus = getattr(tpl, "set_bonus", None)
        base_key = getattr(tpl, "base_key", "") or ""
        inherited_set_info = base_set_info.get(base_key)
        if inherited_set_info:
            set_key = set_key or inherited_set_info[0]
            set_description = set_description or inherited_set_info[1]
            raw_set_bonus = raw_set_bonus or inherited_set_info[2]
        if not set_key:
            continue
        parsed_entries = parse_set_bonus_entries(raw_set_bonus)
        bonus_definitions = [(entry.get("pieces"), entry["bonus"]) for entry in parsed_entries]
        if not bonus_definitions:
            continue

        info = sets.setdefault(
            set_key,
            {
                "count": 0,
                "description": set_description,
                "definitions": bonus_definitions,
                "entries": parsed_entries,
            },
        )
        if info.get("entries") != parsed_entries:
            raise AssertionError(f"inconsistent equipment set bonus definition: {set_key!r}")
        if not info.get("description") and set_description:
            info["description"] = set_description
        info["count"] = int(info.get("count") or 0) + 1  # type: ignore[arg-type, call-overload]
    return sets


def _accumulate_active_set_bonuses(sets: Dict[str, Dict[str, object]]) -> Dict[str, int]:
    bonuses_out: Dict[str, int] = {}
    for info in sets.values():
        count = int(info.get("count") or 0)  # type: ignore[arg-type, call-overload]
        definitions = info.get("definitions") or []
        if not isinstance(definitions, list):
            continue
        for pieces, bonus_map in definitions:
            required = int(pieces or count or 0)
            if count < required or not isinstance(bonus_map, dict):
                continue
            for stat, value in bonus_map.items():
                try:
                    bonuses_out[stat] = bonuses_out.get(stat, 0) + int(value)
                except (TypeError, ValueError):
                    continue
    return bonuses_out


def compute_set_bonus(gear_items) -> Dict[str, int]:
    """
    计算装备列表提供的套装加成。

    Args:
        gear_items: 装备对象列表（需包含template属性）

    Returns:
        加成属性字典 {"attack": 10, "defense": 5}
    """
    sets = _collect_set_info(gear_items)
    return _accumulate_active_set_bonuses(sets)


def compute_set_combat_effects(gear_items) -> list[dict[str, Any]]:
    """Return synthetic battle passive definitions for active set combat_effects tiers."""
    sets = _collect_set_info(gear_items)
    passives: list[dict[str, Any]] = []
    for set_key in sorted(sets):
        info = sets[set_key]
        count = int(info.get("count") or 0)  # type: ignore[arg-type, call-overload]
        set_description = str(info.get("description") or "").strip()
        entries = info.get("entries") or []
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            combat_effects = entry.get("combat_effects")
            if not isinstance(combat_effects, list) or not combat_effects:
                continue
            pieces = entry.get("pieces")
            required = int(pieces or count or 0)
            if count < required:
                continue
            passives.append(
                {
                    "key": f"set_bonus:{set_key}:{pieces or index + 1}",
                    "name": set_description or set_key,
                    "kind": "passive",
                    "passive_config": {
                        "triggers": [
                            {
                                "timing": "battle_start",
                                "effects": copy.deepcopy(combat_effects),
                            }
                        ]
                    },
                }
            )
    return passives
