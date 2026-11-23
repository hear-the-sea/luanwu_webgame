from __future__ import annotations

from typing import Any

from core.config import GUEST


def resolve_guest_level_hp_gain(archetype: str = "") -> int:
    """按门客文/武类型解析每级固定生命成长（武门客 70，文门客 50）。"""
    normalized = str(archetype or "").strip()
    if normalized == "civil":
        return int(GUEST.CIVIL_LEVEL_HP_GAIN)
    if normalized == "military":
        return int(GUEST.MILITARY_LEVEL_HP_GAIN)
    return int(GUEST.LEVEL_HP_GAIN)


def compute_guest_level_hp_bonus(level: int, archetype: str = "") -> int:
    """所有稀有度从 1 级起按文/武类型计算固定生命成长。"""
    return max(0, int(level or 1) - 1) * resolve_guest_level_hp_gain(archetype)


def compute_guest_max_hp(guest: Any) -> int:
    template = getattr(guest, "template", None)
    base_hp = int(getattr(template, "base_hp", 0) or 0) + int(getattr(guest, "hp_bonus", 0) or 0)
    defense_hp = int(getattr(guest, "defense_stat", 0) or 0) * int(GUEST.DEFENSE_TO_HP_MULTIPLIER)
    archetype = str(getattr(guest, "archetype", "") or getattr(template, "archetype", "") or "")
    level_hp = compute_guest_level_hp_bonus(getattr(guest, "level", 1), archetype=archetype)
    return int(max(int(GUEST.MIN_HP_FLOOR), base_hp + defense_hp)) + level_hp


def compute_guest_attack_attribute_component(guest: Any) -> int:
    force = int(getattr(guest, "force", 0) or 0)
    intellect = int(getattr(guest, "intellect", 0) or 0)
    archetype = str(getattr(guest, "archetype", "") or "")
    if archetype == "civil":
        raw_attack = force * GUEST.CIVIL_FORCE_WEIGHT + intellect * GUEST.CIVIL_INTELLECT_WEIGHT
    else:
        raw_attack = force * GUEST.MILITARY_FORCE_WEIGHT + intellect * GUEST.MILITARY_INTELLECT_WEIGHT
    return int(raw_attack)


def build_guest_stat_block(guest: Any) -> dict[str, int]:
    intellect = int(getattr(guest, "intellect", 0) or 0)
    defense_stat = int(getattr(guest, "defense_stat", 0) or 0)
    attack_bonus = int(getattr(guest, "attack_bonus", 0) or 0)
    defense_bonus = int(getattr(guest, "defense_bonus", 0) or 0)

    return {
        "attack": compute_guest_attack_attribute_component(guest) + attack_bonus,
        "defense": defense_stat + defense_bonus,
        "intellect": intellect,
        "hp": compute_guest_max_hp(guest),
    }


def compute_guest_troop_capacity(guest: Any) -> int:
    base_capacity = int(GUEST.BASE_TROOP_CAPACITY)
    level = int(getattr(guest, "level", 0) or 0)
    if level >= int(GUEST.TROOP_CAPACITY_LEVEL_THRESHOLD):
        base_capacity += int(GUEST.BONUS_TROOP_CAPACITY)
    return max(0, base_capacity + int(getattr(guest, "troop_capacity_bonus", 0) or 0))


def restore_guest_full_hp(guest: Any, *, injured_status: str = "injured", idle_status: str = "idle") -> list[str]:
    guest.current_hp = compute_guest_max_hp(guest)
    update_fields = ["current_hp"]
    if getattr(guest, "status", None) == injured_status:
        guest.status = idle_status
        update_fields.append("status")
    return update_fields
