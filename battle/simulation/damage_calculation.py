"""
伤害计算逻辑
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Callable, List, Literal, overload

from .constants import (
    ARROW_TOWER_VS_GUEST_DAMAGE_MULTIPLIER,
    ARROW_TOWER_VS_TROOP_DAMAGE_MULTIPLIER,
    BASE_CRIT_CHANCE,
    COUNTER_DAMAGE_MULTIPLIER,
    CRIT_DAMAGE_MULTIPLIER,
    DAMAGE_VARIANCE_MAX,
    DAMAGE_VARIANCE_MIN,
    DEFAULT_DEFENSE_CONSTANT,
    GUEST_SKILL_VS_GUEST_MULTIPLIER,
    GUEST_SKILL_VS_TROOP_MULTIPLIER,
    GUEST_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER,
    GUEST_VS_GUEST_DAMAGE_MULTIPLIER,
    GUEST_VS_GUEST_DEFENSE_CONSTANT,
    GUEST_VS_TROOP_DEFENSE_CONSTANT,
    HARDCAP,
    PREEMPTIVE_DAMAGE_REDUCTION,
    SOFTCAP_THRESHOLD,
    TROOP_COUNTERS,
    TROOP_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER,
    TROOP_VS_GUEST_DEFENSE_CONSTANT,
)
from .target_selection import is_ranged_attack
from .types import AttackSkill, _DamageCalculation

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


def _finalize_damage(value: float) -> int:
    """Round once at the boundary where damage becomes an HP state change."""

    return max(1, int(value))


def _calculate_defense_value(
    actor: "Combatant",
    target: "Combatant",
    effective_defense_value_fn: Callable[["Combatant", "Combatant"], float],
) -> float:
    if target.kind != "troop":
        return float(target.defense)
    if actor.kind == "guest":
        return float(target.unit_defense)
    return effective_defense_value_fn(target, actor)


def _apply_attack_and_defense_tech_effects(
    actor: "Combatant",
    target: "Combatant",
    round_priority: int,
    attack_value: float,
    defense_value: float,
) -> tuple[float, float]:
    ranged_attack = is_ranged_attack(actor, round_priority)

    if ranged_attack:
        ranged_def = target.tech_effects.get("ranged_defense", 0)
        if ranged_def > 0:
            defense_value = defense_value * (1 + ranged_def)

    if actor.troop_class == "gong" and not ranged_attack:
        melee_bonus = actor.tech_effects.get("melee_attack_bonus", 0)
        if melee_bonus > 0:
            attack_value = attack_value * (1 + melee_bonus)

    return attack_value, defense_value


def _apply_troop_counter_bonus(actor: "Combatant", target: "Combatant", attack_value: float) -> float:
    countered_class = TROOP_COUNTERS.get(actor.troop_class)
    if countered_class and target.troop_class == countered_class:
        return attack_value * COUNTER_DAMAGE_MULTIPLIER
    return attack_value


def _apply_softcap(base_reduction: float) -> float:
    if base_reduction > SOFTCAP_THRESHOLD:
        excess = base_reduction - SOFTCAP_THRESHOLD
        return SOFTCAP_THRESHOLD + excess * 0.5
    return base_reduction


def _calculate_damage_reduction(actor: "Combatant", target: "Combatant", defense_value: float) -> float:
    pair = (actor.kind, target.kind)
    if pair == ("guest", "troop"):
        base_reduction = defense_value / (defense_value + GUEST_VS_TROOP_DEFENSE_CONSTANT)
        return min(base_reduction, HARDCAP)

    constants = {
        ("guest", "guest"): GUEST_VS_GUEST_DEFENSE_CONSTANT,
        ("troop", "guest"): TROOP_VS_GUEST_DEFENSE_CONSTANT,
    }
    defense_constant = constants.get(pair, DEFAULT_DEFENSE_CONSTANT)
    base_reduction = defense_value / (defense_value + defense_constant)
    return min(HARDCAP, _apply_softcap(base_reduction))


def _calculate_base_damage(
    actor: "Combatant",
    target: "Combatant",
    attack_value: float,
    damage_reduction: float,
    attack_multiplier: float,
) -> float:
    base_damage = attack_value * attack_multiplier * (1 - damage_reduction)
    if actor.kind == "guest" and target.kind == "guest":
        base_damage *= GUEST_VS_GUEST_DAMAGE_MULTIPLIER
    if actor.kind == "guest" and target.kind == "city_defense":
        base_damage *= GUEST_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER
    return base_damage


def _city_defense_damage_multiplier(actor: "Combatant", target: "Combatant") -> float:
    from core.config import BUILDING_KEYS

    if target.kind == "city_defense":
        if actor.kind == "troop":
            return TROOP_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER

    if actor.kind == "city_defense" and actor.template_key == BUILDING_KEYS.ARROW_TOWER:
        if target.kind == "guest":
            return ARROW_TOWER_VS_GUEST_DAMAGE_MULTIPLIER
        if target.kind == "troop":
            return ARROW_TOWER_VS_TROOP_DAMAGE_MULTIPLIER

    return 1.0


def _apply_round_and_tech_damage_modifiers(actor: "Combatant", round_priority: int, damage: float) -> float:
    if actor.kind == "guest" and actor.priority == -1:
        damage = damage * PREEMPTIVE_DAMAGE_REDUCTION

    if actor.troop_class == "jian" and round_priority == -1:
        preempt_mult = actor.tech_effects.get("preemptive_damage", 0)
        if preempt_mult > 0:
            damage = damage * preempt_mult

    if actor.troop_class == "gong" and round_priority == -2:
        extra_range_mult = actor.tech_effects.get("extra_range_damage", 0)
        if extra_range_mult > 0:
            damage = damage * extra_range_mult

    return damage


def _roll_double_strike(actor: "Combatant", rng: random.Random) -> bool:
    double_strike_chance = actor.tech_effects.get("double_strike_chance", 0)
    if double_strike_chance > 0 and rng.random() < double_strike_chance:
        return True
    return False


def _apply_post_damage_modifiers(
    actor: "Combatant",
    target: "Combatant",
    skills: List[AttackSkill],
    *,
    round_priority: int,
    damage: float,
    is_double_strike: bool,
    rng: random.Random,
) -> float:
    damage = _apply_round_and_tech_damage_modifiers(actor, round_priority, damage)
    if is_double_strike:
        damage *= 2
    return process_status_effects(actor, target, skills, rng, phase="damage_penalty", damage=damage)


def _apply_slaughter_multiplier(
    actor: "Combatant",
    target: "Combatant",
    damage: float,
    calculate_slaughter_multiplier_fn: Callable[["Combatant", "Combatant"], float],
) -> float:
    if target.kind != "troop":
        return damage
    slaughter_mult = calculate_slaughter_multiplier_fn(actor, target)
    if slaughter_mult == 1.0:
        return damage
    return damage * slaughter_mult


def _apply_guest_vs_troop_split_scaling(
    actor: "Combatant",
    target: "Combatant",
    *,
    base_damage: float,
    total_damage: float,
    calculate_slaughter_multiplier_fn: Callable[["Combatant", "Combatant"], float],
) -> float:
    slaughter_mult = calculate_slaughter_multiplier_fn(actor, target)
    if slaughter_mult == 1.0:
        return total_damage

    skill_damage = total_damage - base_damage
    return base_damage * slaughter_mult + skill_damage * GUEST_SKILL_VS_TROOP_MULTIPLIER


def _apply_guest_vs_guest_split_scaling(*, base_damage: float, total_damage: float) -> float:
    skill_damage = total_damage - base_damage
    return base_damage + skill_damage * GUEST_SKILL_VS_GUEST_MULTIPLIER


def _apply_passive_true_damage(actor: "Combatant", target: "Combatant", damage: float) -> float:
    modifiers = getattr(actor, "battle_modifiers", None)
    if not isinstance(modifiers, dict):
        return damage
    sources = modifiers.get("true_damage_ratio_sources")
    if not isinstance(sources, dict) or not sources:
        return damage

    max_hp = max(0, int(getattr(actor, "max_hp", 0) or 0))
    if max_hp <= 0:
        return damage

    extra_damage = 0.0
    for payload in sources.values():
        if not isinstance(payload, dict):
            continue
        ratio = float(payload.get("value") or 0)
        if ratio <= 0:
            continue
        multiplier = float(payload.get("troop_value_multiplier") or 1.0) if target.kind == "troop" else 1.0
        extra_damage += max(0.0, max_hp * ratio * multiplier)

    if extra_damage <= 0:
        return damage
    return damage + extra_damage


@overload
def process_status_effects(
    actor: "Combatant",
    target: "Combatant",
    skills: List[AttackSkill],
    rng: random.Random,
    *,
    phase: Literal["damage_penalty"],
    damage: float,
) -> float: ...


@overload
def process_status_effects(
    actor: "Combatant",
    target: "Combatant",
    skills: List[AttackSkill],
    rng: random.Random,
    *,
    phase: Literal["inflict"],
    damage: None = None,
) -> List[str]: ...


def process_status_effects(
    actor: "Combatant",
    target: "Combatant",
    skills: List[AttackSkill],
    rng: random.Random,
    *,
    phase: Literal["damage_penalty", "inflict"],
    damage: float | None = None,
) -> float | List[str]:
    """
    状态效果处理（保持战斗日志与 RNG 调用顺序向后兼容）。

    phase:
    - "damage_penalty": 仅处理攻击者身上的伤害惩罚（如士气低落降低伤害）；不消耗 RNG。
    - "inflict": 处理技能对目标施加的状态；会消耗 RNG（施加状态是概率性的）。

    注意：
    - 为避免改变随机序列，"inflict" 必须在所有命中结算（含反击概率判定）完成后调用。
    """
    from ..skills import apply_skill_statuses
    from ..utils.status_effects import get_damage_penalty

    if phase == "damage_penalty":
        if damage is None:
            raise AssertionError("damage_penalty phase requires 'damage'")
        damage_penalty = get_damage_penalty(actor)
        if damage_penalty > 0:
            damage = damage * (1 - damage_penalty)
        return damage

    return apply_skill_statuses(skills, target, rng)


def calculate_attack_damage(
    actor: "Combatant",
    target: "Combatant",
    skills: List[AttackSkill],
    rng: random.Random,
    *,
    round_priority: int,
) -> _DamageCalculation:
    """
    计算本次命中对目标造成的最终伤害。

    覆盖内容（与历史实现保持一致的顺序）：
    1) 计算有效攻击
    2) 计算目标防御（按小兵/门客、攻击者类型区分）
    3) 应用武艺技术影响（远程防御、弓近战加成）
    4) 应用五行相克固定倍率
    5) 按战斗双方类型计算防御减伤（含软/硬上限）
    6) 伤害随机波动
    7) 暴击判定
    8) 技能伤害加成
    9) 先手回合调整 + 特定武艺倍率
    10) 双倍打击
    11) 状态惩罚（伤害降低）
    12) 门客对小兵/门客的最终倍率：普攻倍率 + 技能伤害独立倍率
    13) 非门客来源的城防相关最终伤害倍率

    该函数不直接修改 actor/target 的血量或兵力，专注于"伤害数值"的计算。
    """
    from ..combat_math import calculate_slaughter_multiplier, effective_attack_value, effective_defense_value
    from ..skills import skill_damage_bonus

    attack_value: float = effective_attack_value(actor, target)

    defense_value = _calculate_defense_value(actor, target, effective_defense_value)
    attack_value, defense_value = _apply_attack_and_defense_tech_effects(
        actor, target, round_priority, attack_value, defense_value
    )
    attack_value = _apply_troop_counter_bonus(actor, target, attack_value)
    damage_reduction = _calculate_damage_reduction(actor, target, defense_value)

    attack_multiplier = rng.uniform(DAMAGE_VARIANCE_MIN, DAMAGE_VARIANCE_MAX)
    base_damage = _calculate_base_damage(actor, target, attack_value, damage_reduction, attack_multiplier)

    crit_chance = BASE_CRIT_CHANCE
    is_crit = rng.random() < crit_chance
    if is_crit:
        base_damage *= CRIT_DAMAGE_MULTIPLIER

    bonus = skill_damage_bonus(skills, actor, target)
    base_damage_value = base_damage
    total_damage_value = base_damage + bonus

    is_double_strike = _roll_double_strike(actor, rng)
    base_damage_value = _apply_post_damage_modifiers(
        actor,
        target,
        skills,
        round_priority=round_priority,
        damage=base_damage_value,
        is_double_strike=is_double_strike,
        rng=rng,
    )
    total_damage_value = _apply_post_damage_modifiers(
        actor,
        target,
        skills,
        round_priority=round_priority,
        damage=total_damage_value,
        is_double_strike=is_double_strike,
        rng=rng,
    )

    if actor.kind == "guest" and target.kind == "troop":
        damage = _apply_guest_vs_troop_split_scaling(
            actor,
            target,
            base_damage=base_damage_value,
            total_damage=total_damage_value,
            calculate_slaughter_multiplier_fn=calculate_slaughter_multiplier,
        )
    elif actor.kind == "guest" and target.kind == "guest":
        damage = _apply_guest_vs_guest_split_scaling(
            base_damage=base_damage_value,
            total_damage=total_damage_value,
        )
    else:
        damage = _apply_slaughter_multiplier(actor, target, total_damage_value, calculate_slaughter_multiplier)

    city_defense_multiplier = _city_defense_damage_multiplier(actor, target)
    if city_defense_multiplier != 1.0:
        damage = damage * city_defense_multiplier

    from ..arena_coop import adjust_arena_coop_damage

    damage = adjust_arena_coop_damage(actor, target, damage)
    damage = _apply_passive_true_damage(actor, target, damage)
    damage = _finalize_damage(damage)

    return _DamageCalculation(damage=damage, is_crit=is_crit, is_double_strike=is_double_strike)
