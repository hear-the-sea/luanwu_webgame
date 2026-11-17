from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from .guest_rules import build_guest_stat_block, compute_guest_attack_attribute_component
from .models import Guest


@dataclass(frozen=True)
class GuestCombatStats:
    attack: int
    defense: int
    intellect: int
    max_hp: int
    current_hp: int | None
    troop_capacity: int


def has_custom_guest_low_loyalty_penalty(guest: Any) -> bool:
    """实时门客使用当前忠诚；快照保留创建时的判定。"""
    from .services.custom_guest import is_custom_guest

    if not is_custom_guest(guest):
        return False
    if isinstance(guest, Guest):
        return guest.loyalty < 60
    return getattr(guest, "low_loyalty_penalty", False) is True


def apply_custom_guest_low_loyalty_penalty(guest: Any, stats: GuestCombatStats) -> tuple[dict[str, int], int, int]:
    """只削减四项战斗属性及其攻防贡献，保留装备、生命和带兵加成。"""
    attributes = {
        "force": getattr(guest, "force", 100),
        "intellect": getattr(guest, "intellect", 100),
        "defense_stat": getattr(guest, "defense_stat", stats.defense),
        "agility": getattr(guest, "agility", 80),
    }
    if not has_custom_guest_low_loyalty_penalty(guest):
        return attributes, stats.attack, stats.defense

    reduced = {name: int(value) * 7 // 10 for name, value in attributes.items()}
    archetype = getattr(guest, "archetype", "")
    original_attack = compute_guest_attack_attribute_component(SimpleNamespace(**attributes, archetype=archetype))
    reduced_attack = compute_guest_attack_attribute_component(SimpleNamespace(**reduced, archetype=archetype))
    attack = max(1, stats.attack - original_attack + reduced_attack)
    defense = max(1, stats.defense - attributes["defense_stat"] + reduced["defense_stat"])
    return reduced, attack, defense


def snapshot_effective_combat_stats(snapshot: dict[str, Any]) -> tuple[int, int, int]:
    """按战斗快照的冻结判定计算攻防和敏捷，供阵容实力估算使用。"""
    attack = snapshot.get("attack", 0) or 0
    defense = snapshot.get("defense", 0) or 0
    agility = snapshot.get("agility", 0) or 0
    if snapshot.get("low_loyalty_penalty") is not True:
        return attack, defense, agility
    guest = SimpleNamespace(
        template=SimpleNamespace(key=snapshot["template_key"]),
        low_loyalty_penalty=True,
        archetype=snapshot["archetype"],
        force=snapshot["force"],
        intellect=snapshot["intellect"],
        defense_stat=snapshot["defense_stat"],
        agility=agility,
    )
    stats = GuestCombatStats(
        attack=attack, defense=defense, intellect=guest.intellect, max_hp=1, current_hp=None, troop_capacity=0
    )
    attributes, attack, defense = apply_custom_guest_low_loyalty_penalty(guest, stats)
    return attack, defense, attributes["agility"]


def is_live_guest_model(guest: Any) -> bool:
    return isinstance(guest, Guest)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_min(value: Any, *, minimum: int) -> int | None:
    parsed = _coerce_int(value)
    if parsed is None:
        return None
    return max(minimum, parsed)


def resolve_guest_combat_stats(guest: Any) -> GuestCombatStats:
    raw_stats: dict[str, Any] = {}
    if is_live_guest_model(guest):
        raw_stats = build_guest_stat_block(guest)
    else:
        stat_block = getattr(guest, "stat_block", None)
        if callable(stat_block):
            payload = stat_block()
            if isinstance(payload, dict):
                raw_stats = payload

    attack = _coerce_min(raw_stats.get("attack"), minimum=1)
    if attack is None:
        attack = _coerce_min(getattr(guest, "attack", None), minimum=1) or 1

    defense = _coerce_min(raw_stats.get("defense"), minimum=1)
    if defense is None:
        defense = _coerce_min(getattr(guest, "defense", None), minimum=1) or 1

    intellect = _coerce_int(raw_stats.get("intellect"))
    if intellect is None:
        intellect = _coerce_int(getattr(guest, "intellect", None)) or 0

    max_hp = _coerce_min(raw_stats.get("hp"), minimum=1)
    if max_hp is None:
        max_hp = _coerce_min(getattr(guest, "max_hp", None), minimum=1) or 1

    current_hp = _coerce_min(getattr(guest, "current_hp", None), minimum=1)
    if current_hp is not None:
        current_hp = min(max_hp, current_hp)

    troop_capacity = _coerce_min(getattr(guest, "troop_capacity", None), minimum=0) or 0

    return GuestCombatStats(
        attack=attack,
        defense=defense,
        intellect=intellect,
        max_hp=max_hp,
        current_hp=current_hp,
        troop_capacity=troop_capacity,
    )
