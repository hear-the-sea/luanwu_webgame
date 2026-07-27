"""
伤害应用逻辑
"""

from __future__ import annotations

import random
from math import ceil
from typing import TYPE_CHECKING, Any, cast

from .types import _DamageApplication, _UnitDamageApplication

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


def _normalize_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(cast(Any, value)))
    except (TypeError, ValueError):
        return 0


def _strength_for_hp(unit: "Combatant", hp: int, troop_unit_hp_fn) -> int:
    if unit.kind != "troop" or hp <= 0:
        return 0
    per_unit_hp = troop_unit_hp_fn(unit)
    initial_strength = _normalize_non_negative_int(getattr(unit, "initial_troop_strength", 0))
    derived_strength = ceil(hp / per_unit_hp)
    return min(initial_strength, derived_strength) if initial_strength > 0 else derived_strength


def _snapshot_zero_damage(unit: "Combatant", troop_unit_hp_fn) -> _UnitDamageApplication:
    maximum_hp = _normalize_non_negative_int(getattr(unit, "max_hp", 0))
    hp = min(maximum_hp, _normalize_non_negative_int(getattr(unit, "hp", 0)))
    strength = _strength_for_hp(unit, hp, troop_unit_hp_fn)
    return _UnitDamageApplication(
        raw_damage=0,
        applied_damage=0,
        overkill_damage=0,
        kills=0,
        defeated=hp <= 0,
        hp_before=hp,
        hp_after=hp,
        strength_before=strength,
        strength_after=strength,
    )


def _apply_unit_damage(unit: "Combatant", damage: int, troop_unit_hp_fn) -> _UnitDamageApplication:
    """Apply one damage transition while keeping HP and troop strength consistent.

    Damage multipliers belong to the calculation that produced ``damage``. This
    state-transition boundary deliberately does not infer attack semantics or
    apply the guest-vs-troop slaughter multiplier.
    """

    raw_damage = _normalize_non_negative_int(damage)
    maximum_hp = _normalize_non_negative_int(getattr(unit, "max_hp", 0))
    hp_before = min(maximum_hp, _normalize_non_negative_int(getattr(unit, "hp", 0)))
    strength_before = _strength_for_hp(unit, hp_before, troop_unit_hp_fn)
    applied_damage = min(raw_damage, hp_before)
    hp_after = hp_before - applied_damage
    strength_after = _strength_for_hp(unit, hp_after, troop_unit_hp_fn)

    unit.hp = hp_after
    if unit.kind == "troop":
        unit.troop_strength = strength_after

    kills = strength_before - strength_after if unit.kind == "troop" else int(hp_before > 0 and hp_after == 0)
    return _UnitDamageApplication(
        raw_damage=raw_damage,
        applied_damage=applied_damage,
        overkill_damage=max(0, raw_damage - applied_damage),
        kills=max(0, kills),
        defeated=hp_after <= 0,
        hp_before=hp_before,
        hp_after=hp_after,
        strength_before=strength_before,
        strength_after=strength_after,
    )


def _apply_reflect(
    actor: "Combatant",
    target: "Combatant",
    damage: int,
    troop_unit_hp_fn,
) -> _UnitDamageApplication:
    """Apply reflected secondary damage without normal-attack multipliers."""

    from ..arena_coop import get_arena_coop_reflect_values

    reflect_ratio = target.tech_effects.get("damage_reflect", 0)
    max_reflect = int(actor.attack * 1.0)
    if reflect_ratio <= 0 or target.troop_class != "jian":
        reflect_ratio, special_cap = get_arena_coop_reflect_values(target)
        if reflect_ratio <= 0:
            return _snapshot_zero_damage(actor, troop_unit_hp_fn)
        max_reflect = special_cap if special_cap > 0 else max_reflect

    reflect_damage = min(int(damage * reflect_ratio), max_reflect)
    return _apply_unit_damage(actor, reflect_damage, troop_unit_hp_fn)


def _apply_counter(
    actor: "Combatant",
    target: "Combatant",
    rng: random.Random,
    effective_attack_value_fn,
    troop_unit_hp_fn,
) -> _UnitDamageApplication:
    """Apply counter secondary damage without normal-attack multipliers."""

    counter_chance = target.tech_effects.get("counter_attack_chance", 0)
    if counter_chance <= 0 or target.hp <= 0 or rng.random() >= counter_chance:
        return _snapshot_zero_damage(actor, troop_unit_hp_fn)

    counter_mult = target.tech_effects.get("counter_attack_damage", 0.30)
    counter_attack_value = effective_attack_value_fn(target, actor)
    counter_damage = int(counter_attack_value * counter_mult)
    return _apply_unit_damage(actor, counter_damage, troop_unit_hp_fn)


def apply_damage_results(
    actor: "Combatant",
    target: "Combatant",
    damage: int,
    rng: random.Random,
) -> _DamageApplication:
    """
    将伤害应用到目标，并处理命中后结算：
    - 目标 HP/兵力扣减与击杀数计算
    - 技术效果：反伤（剑系）、反击（枪系）
    - 检查攻击者是否被反伤/反击击败

    该函数会直接修改 `actor` 和 `target` 的状态（HP、兵力等）。
    """
    from ..combat_math import effective_attack_value, troop_unit_hp

    target_application = _apply_unit_damage(target, damage, troop_unit_hp)
    reflect_application = _apply_reflect(
        actor,
        target,
        damage,
        troop_unit_hp,
    )
    counter_application = _apply_counter(
        actor,
        target,
        rng,
        effective_attack_value,
        troop_unit_hp,
    )

    return _DamageApplication(
        target=target_application,
        reflect=reflect_application,
        counter=counter_application,
    )
