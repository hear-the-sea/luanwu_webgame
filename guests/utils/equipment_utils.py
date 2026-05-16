"""
装备工具模块

提供装备套装加成计算等功能。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from ..models import GearSlot

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
    "defense": "defense_bonus",
    "hp": "hp_bonus",
    "force": "force",
    "intellect": "intellect",
    "agility": "agility",
    "luck": "luck",
    "troop_capacity": "troop_capacity_bonus",
}


def _normalize_set_bonus_definitions(raw_bonus) -> list[tuple[int | None, dict]]:
    bonus_def = raw_bonus or {}
    if isinstance(bonus_def, (list, tuple)):
        definitions: list[tuple[int | None, dict]] = []
        for entry in bonus_def:
            definitions.extend(_normalize_set_bonus_definitions(entry))
        return definitions
    if not isinstance(bonus_def, dict):
        return []

    pieces = bonus_def.get("pieces")
    bonuses = bonus_def.get("bonus") or bonus_def.get("bonuses") or bonus_def
    if not isinstance(bonuses, (dict, list, tuple)):
        if hasattr(bonuses, "get"):
            bonuses = [bonuses]
        else:
            bonuses = {}
    if not isinstance(bonuses, dict):
        return []
    return [(pieces, bonuses)]


def _collect_set_info(gear_items) -> Dict[str, Dict[str, object]]:
    sets: Dict[str, Dict[str, object]] = {}
    for gear in gear_items:
        tpl = getattr(gear, "template", None)
        if not tpl:
            continue
        set_key = getattr(tpl, "set_key", "") or ""
        if not set_key:
            continue
        bonus_definitions = _normalize_set_bonus_definitions(getattr(tpl, "set_bonus", None))
        if not bonus_definitions:
            continue

        info = sets.setdefault(set_key, {"count": 0, "definitions": bonus_definitions})
        info["count"] = int(info.get("count") or 0) + 1  # type: ignore[arg-type, call-overload]
        if not info.get("definitions"):
            info["definitions"] = bonus_definitions
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
