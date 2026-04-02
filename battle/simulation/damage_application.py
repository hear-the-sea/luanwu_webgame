"""
伤害应用逻辑
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .types import _DamageApplication

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


def _mark_actor_troop_damage(
    actor: "Combatant",
    damage: int,
    source: "Combatant",
    troop_unit_hp_fn,
    calculate_slaughter_multiplier_fn,
) -> tuple[int, bool]:
    per_unit_hp_actor = troop_unit_hp_fn(actor)
    slaughter_mult = calculate_slaughter_multiplier_fn(source, actor)
    kills = int(damage * slaughter_mult / per_unit_hp_actor)
    kills = max(0, min(actor.troop_strength, kills))
    actor.troop_strength = max(0, actor.troop_strength - kills)
    defeated = actor.troop_strength <= 0
    if defeated:
        actor.hp = min(actor.hp, 0)
    return kills, defeated


def _mark_actor_guest_damage(actor: "Combatant") -> tuple[int, bool]:
    if actor.hp <= 0:
        return 1, True
    return 0, False


def _apply_reflect(
    actor: "Combatant",
    target: "Combatant",
    damage: int,
    troop_unit_hp_fn,
    calculate_slaughter_multiplier_fn,
) -> tuple[int, int, bool]:
    from ..arena_coop import get_arena_coop_reflect_values

    reflect_ratio = target.tech_effects.get("damage_reflect", 0)
    max_reflect = int(actor.attack * 1.0)
    if reflect_ratio <= 0 or target.troop_class != "jian":
        reflect_ratio, special_cap = get_arena_coop_reflect_values(target)
        if reflect_ratio <= 0:
            return 0, 0, False
        max_reflect = special_cap if special_cap > 0 else max_reflect

    reflect_damage = min(int(damage * reflect_ratio), max_reflect)
    actor.hp -= reflect_damage

    if actor.kind == "troop":
        reflect_kills, reflect_defeated = _mark_actor_troop_damage(
            actor,
            reflect_damage,
            target,
            troop_unit_hp_fn,
            calculate_slaughter_multiplier_fn,
        )
    else:
        reflect_kills, reflect_defeated = _mark_actor_guest_damage(actor)

    return reflect_damage, reflect_kills, reflect_defeated


def _apply_counter(
    actor: "Combatant",
    target: "Combatant",
    rng: random.Random,
    effective_attack_value_fn,
    troop_unit_hp_fn,
    calculate_slaughter_multiplier_fn,
) -> tuple[int, int, bool]:
    counter_chance = target.tech_effects.get("counter_attack_chance", 0)
    if counter_chance <= 0 or target.hp <= 0 or rng.random() >= counter_chance:
        return 0, 0, False

    counter_mult = target.tech_effects.get("counter_attack_damage", 0.30)
    counter_attack_value = effective_attack_value_fn(target, actor)
    counter_damage = int(counter_attack_value * counter_mult)
    actor.hp -= counter_damage

    if actor.kind == "troop":
        counter_kills, counter_defeated = _mark_actor_troop_damage(
            actor,
            counter_damage,
            target,
            troop_unit_hp_fn,
            calculate_slaughter_multiplier_fn,
        )
    else:
        counter_kills, counter_defeated = _mark_actor_guest_damage(actor)

    return counter_damage, counter_kills, counter_defeated


def _apply_target_damage(target: "Combatant", damage: int, troop_unit_hp_fn) -> tuple[int, bool, int]:
    target.hp -= damage
    target_defeated = target.hp <= 0

    if target.kind == "troop":
        per_unit_hp = troop_unit_hp_fn(target)
        kills = int(damage / per_unit_hp)
        kills = max(0, min(target.troop_strength, kills))
        target.troop_strength = max(0, target.troop_strength - kills)
        if target_defeated or target.troop_strength <= 0:
            target_defeated = True
            target.hp = min(target.hp, 0)
        return kills, target_defeated, damage

    kills = 1 if target_defeated else 0
    return kills, target_defeated, damage


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
    from ..combat_math import calculate_slaughter_multiplier, effective_attack_value, troop_unit_hp

    kills, target_defeated, display_damage = _apply_target_damage(target, damage, troop_unit_hp)
    reflect_damage, reflect_kills, reflect_defeated = _apply_reflect(
        actor,
        target,
        damage,
        troop_unit_hp,
        calculate_slaughter_multiplier,
    )
    counter_damage, counter_kills, counter_defeated = _apply_counter(
        actor,
        target,
        rng,
        effective_attack_value,
        troop_unit_hp,
        calculate_slaughter_multiplier,
    )

    actor_defeated = actor.hp <= 0
    if actor_defeated:
        actor.hp = min(actor.hp, 0)

    return _DamageApplication(
        display_damage=display_damage,
        kills=kills,
        target_defeated=target_defeated,
        actor_defeated=actor_defeated,
        reflect_damage=reflect_damage,
        reflect_kills=reflect_kills,
        reflect_defeated=reflect_defeated,
        counter_damage=counter_damage,
        counter_kills=counter_kills,
        counter_defeated=counter_defeated,
    )
