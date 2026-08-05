"""
门客装备属性与套装结算。
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import GearItem, GearSlot, GearTemplate, Guest
from ..utils.equipment_utils import SET_STAT_FIELD_MAP, compute_set_bonus
from .equipment_payloads import GEAR_EXTRA_STAT_FIELDS, normalize_active_set_bonus, normalize_extra_stats, require_int


def slot_capacity(slot: str) -> int:
    return {
        GearSlot.DEVICE: 3,
        GearSlot.ORNAMENT: 3,
    }.get(
        slot, 1
    )  # type: ignore[call-overload]


def apply_template_stats_to_guest(guest: Guest, template: GearTemplate, sign: int, updates: set[str]) -> None:
    guest.attack_bonus += sign * template.attack_bonus
    guest.defense_bonus += sign * template.defense_bonus
    extra_stats = normalize_extra_stats(template.extra_stats)
    for key, field in GEAR_EXTRA_STAT_FIELDS.items():
        value = extra_stats.get(key)
        if value:
            setattr(guest, field, getattr(guest, field) + sign * value)
            updates.add(field)


def apply_set_bonuses(
    guest: Guest,
    *,
    gear_items: Iterable[GearItem] | None = None,
    persist: bool = True,
) -> dict[str, int]:
    """
    重新计算套装效果，并将其数值写回门客属性。上一轮套装效果会被先撤销。
    """
    previous = normalize_active_set_bonus(guest.gear_set_bonus)
    current = compute_set_bonus(gear_items if gear_items is not None else guest.gear_items.select_related("template"))
    if previous == current:
        return current

    updates = set()
    for stat, field in SET_STAT_FIELD_MAP.items():
        prev_value = previous.get(stat, 0)
        if prev_value:
            setattr(guest, field, getattr(guest, field) - prev_value)
            updates.add(field)

    for stat, value in current.items():
        set_bonus_field = SET_STAT_FIELD_MAP.get(stat)
        if not set_bonus_field:
            continue
        normalized_value = require_int(value, field_name=f"computed set_bonus[{stat}]")
        if normalized_value:
            setattr(guest, set_bonus_field, getattr(guest, set_bonus_field) + normalized_value)
            updates.add(set_bonus_field)

    guest.gear_set_bonus = current
    updates.add("gear_set_bonus")
    if updates and persist:
        guest.save(update_fields=list(updates))
    return current
