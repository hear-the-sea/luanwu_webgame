"""
伤害计算逻辑
"""

from __future__ import annotations

import random
from functools import partial
from typing import TYPE_CHECKING, Callable, List, Literal, overload

from ..random_context import (
    CURRENT_BATTLE_ENGINE_VERSION,
    GONG_MELEE_BASE_RATIO_ENGINE_VERSIONS,
    GUEST_ACTION_CONSISTENCY_ENGINE_VERSIONS,
    GUEST_TROOP_BALANCE_ENGINE_VERSIONS,
    STAT_BALANCE_V11_ENGINE_VERSIONS,
)
from .constants import (
    ARROW_TOWER_VS_GUEST_DAMAGE_MULTIPLIER,
    ARROW_TOWER_VS_TROOP_DAMAGE_MULTIPLIER,
    BASE_CRIT_CHANCE,
    COUNTER_DAMAGE_MULTIPLIER,
    CRIT_DAMAGE_MULTIPLIER,
    DAMAGE_VARIANCE_MAX,
    DAMAGE_VARIANCE_MIN,
    DEFAULT_DEFENSE_CONSTANT,
    GONG_MELEE_BASE_DAMAGE_RATIO,
    GUEST_SKILL_VS_GUEST_MULTIPLIER,
    GUEST_SKILL_VS_TROOP_MULTIPLIER,
    GUEST_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER,
    GUEST_VS_GUEST_DAMAGE_MULTIPLIER,
    GUEST_VS_GUEST_DEFENSE_CONSTANT,
    GUEST_VS_TROOP_DEFENSE_CONSTANT,
    HARDCAP,
    INTELLECT_SKILL_DR_CONSTANT,
    INTELLECT_SKILL_DR_HARDCAP,
    INTELLECT_STATUS_RESIST_CONSTANT,
    INTELLECT_STATUS_RESIST_HARDCAP,
    LEGACY_GUEST_SKILL_VS_TROOP_MULTIPLIER,
    PREEMPTIVE_DAMAGE_REDUCTION,
    PREEMPTIVE_STATUS_CHANCE_MULTIPLIER,
    SOFTCAP_THRESHOLD,
    TROOP_COUNTERS,
    TROOP_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER,
    TROOP_VS_GUEST_DEFENSE_CONSTANT,
    V11_GUEST_VS_GUEST_DAMAGE_MULTIPLIER,
    V11_PREEMPTIVE_DAMAGE_REDUCTION,
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
    from ..utils.status_effects import get_defense_multiplier

    if target.kind != "troop":
        defense = float(target.defense)
    elif actor.kind == "guest":
        defense = float(target.unit_defense)
    else:
        defense = effective_defense_value_fn(target, actor)
    return defense * get_defense_multiplier(target)


def _apply_attack_and_defense_tech_effects(
    actor: "Combatant",
    target: "Combatant",
    round_priority: int,
    attack_value: float,
    defense_value: float,
    *,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> tuple[float, float]:
    ranged_attack = is_ranged_attack(actor, round_priority)

    if ranged_attack:
        ranged_def = target.tech_effects.get("ranged_defense", 0)
        if ranged_def > 0:
            defense_value = defense_value * (1 + ranged_def)

    if actor.troop_class == "gong" and not ranged_attack:
        if battle_engine_version in GONG_MELEE_BASE_RATIO_ENGINE_VERSIONS:
            attack_value = attack_value * GONG_MELEE_BASE_DAMAGE_RATIO
        melee_bonus = actor.tech_effects.get("melee_attack_bonus", 0)
        if melee_bonus > 0:
            attack_value = attack_value * (1 + melee_bonus)

    if actor.kind == "troop" and actor.troop_class == "dao":
        defense_ignore = max(0.0, min(1.0, actor.tech_effects.get("defense_ignore", 0)))
        if defense_ignore > 0:
            defense_value = defense_value * (1 - defense_ignore)
        if target.kind == "troop" and target.troop_class == "jian":
            sword_bonus = max(0.0, actor.tech_effects.get("sword_damage_bonus", 0))
            if sword_bonus > 0:
                attack_value = attack_value * (1 + sword_bonus)

    return attack_value, defense_value


def _apply_ranged_damage_reduction(
    actor: "Combatant", target: "Combatant", round_priority: int, damage: float
) -> float:
    if not is_ranged_attack(actor, round_priority):
        return damage
    reduction = target.tech_effects.get("ranged_damage_reduction", 0)
    return damage * (1 - reduction) if reduction > 0 else damage


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
    *,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> float:
    base_damage = attack_value * attack_multiplier * (1 - damage_reduction)
    if actor.kind == "guest" and target.kind == "guest":
        pvp_mult = (
            V11_GUEST_VS_GUEST_DAMAGE_MULTIPLIER
            if battle_engine_version in STAT_BALANCE_V11_ENGINE_VERSIONS
            else GUEST_VS_GUEST_DAMAGE_MULTIPLIER
        )
        base_damage *= pvp_mult
    if actor.kind == "guest" and target.kind == "city_defense":
        base_damage *= GUEST_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER
    return base_damage


def _city_defense_damage_multiplier(
    actor: "Combatant", target: "Combatant", *, enable_siege_damage_bonus: bool = True
) -> float:
    from core.config import BUILDING_KEYS

    if target.kind == "city_defense":
        if actor.kind == "troop":
            siege_bonus = actor.tech_effects.get("siege_damage", 0) if enable_siege_damage_bonus else 0
            return TROOP_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER * (1 + siege_bonus)

    if actor.kind == "city_defense" and actor.template_key == BUILDING_KEYS.ARROW_TOWER:
        if target.kind == "guest":
            return ARROW_TOWER_VS_GUEST_DAMAGE_MULTIPLIER
        if target.kind == "troop":
            return ARROW_TOWER_VS_TROOP_DAMAGE_MULTIPLIER

    return 1.0


def _target_intellect(target: "Combatant") -> float:
    raw = getattr(target, "intellect_attr", None)
    if raw is None:
        raw = getattr(target, "intellect", 0)
    return max(0.0, float(raw or 0))


def _intellect_skill_damage_reduction(target: "Combatant", battle_engine_version: str) -> float:
    if battle_engine_version not in STAT_BALANCE_V11_ENGINE_VERSIONS or target.kind != "guest":
        return 0.0
    intellect = _target_intellect(target)
    if intellect <= 0:
        return 0.0
    return min(INTELLECT_SKILL_DR_HARDCAP, intellect / (intellect + INTELLECT_SKILL_DR_CONSTANT))


def _status_chance_multiplier(actor: "Combatant", target: "Combatant", battle_engine_version: str) -> float:
    if battle_engine_version not in STAT_BALANCE_V11_ENGINE_VERSIONS:
        return 1.0
    multiplier = 1.0
    if actor.kind == "guest" and actor.priority == -1:
        multiplier *= PREEMPTIVE_STATUS_CHANCE_MULTIPLIER
    if target.kind == "guest":
        intellect = _target_intellect(target)
        if intellect > 0:
            resist = min(INTELLECT_STATUS_RESIST_HARDCAP, intellect / (intellect + INTELLECT_STATUS_RESIST_CONSTANT))
            multiplier *= 1.0 - resist
    return multiplier


def _apply_round_and_tech_damage_modifiers(
    actor: "Combatant",
    round_priority: int,
    damage: float,
    *,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> float:
    if actor.kind == "guest" and actor.priority == -1:
        preemptive_mult = (
            V11_PREEMPTIVE_DAMAGE_REDUCTION
            if battle_engine_version in STAT_BALANCE_V11_ENGINE_VERSIONS
            else PREEMPTIVE_DAMAGE_REDUCTION
        )
        damage = damage * preemptive_mult

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
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> float:
    damage = _apply_round_and_tech_damage_modifiers(
        actor, round_priority, damage, battle_engine_version=battle_engine_version
    )
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
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> float:
    slaughter_mult = calculate_slaughter_multiplier_fn(actor, target)
    if slaughter_mult == 1.0:
        return total_damage

    skill_damage = total_damage - base_damage
    skill_multiplier = (
        GUEST_SKILL_VS_TROOP_MULTIPLIER
        if battle_engine_version in GUEST_TROOP_BALANCE_ENGINE_VERSIONS
        else LEGACY_GUEST_SKILL_VS_TROOP_MULTIPLIER
    )
    return base_damage * slaughter_mult + skill_damage * skill_multiplier


def _apply_guest_vs_guest_split_scaling(
    *,
    base_damage: float,
    total_damage: float,
    skill_damage_reduction: float = 0.0,
) -> float:
    skill_damage = total_damage - base_damage
    return base_damage + skill_damage * GUEST_SKILL_VS_GUEST_MULTIPLIER * (1.0 - skill_damage_reduction)


def calculate_passive_true_damage(
    actor: "Combatant",
    target: "Combatant",
    *,
    value: float,
    troop_value_multiplier: float = 1.0,
) -> float:
    max_hp = max(0, int(getattr(actor, "max_hp", 0) or 0))
    if max_hp <= 0 or value <= 0:
        return 0.0

    multiplier = troop_value_multiplier if target.kind == "troop" else 1.0
    return max(0.0, max_hp * value * multiplier)


def _apply_passive_true_damage(actor: "Combatant", target: "Combatant", damage: float) -> float:
    modifiers = getattr(actor, "battle_modifiers", None)
    if not isinstance(modifiers, dict):
        return damage
    sources = modifiers.get("true_damage_ratio_sources")
    if not isinstance(sources, dict) or not sources:
        return damage

    extra_damage = 0.0
    for payload in sources.values():
        if not isinstance(payload, dict):
            continue
        ratio = float(payload.get("value") or 0)
        if ratio <= 0:
            continue
        multiplier = float(payload.get("troop_value_multiplier") or 1.0) if target.kind == "troop" else 1.0
        extra_damage += calculate_passive_true_damage(
            actor,
            target,
            value=ratio,
            troop_value_multiplier=multiplier,
        )

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
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> List[str]: ...


def process_status_effects(
    actor: "Combatant",
    target: "Combatant",
    skills: List[AttackSkill],
    rng: random.Random,
    *,
    phase: Literal["damage_penalty", "inflict"],
    damage: float | None = None,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
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

    status_mult = _status_chance_multiplier(actor, target, battle_engine_version)
    return apply_skill_statuses(skills, target, rng, status_chance_multiplier=status_mult)


def _apply_final_damage_modifiers(
    actor: "Combatant",
    target: "Combatant",
    damage: float,
    *,
    round_priority: int,
    troop_damage_multiplier: float,
    enable_siege_damage_bonus: bool,
    enable_ranged_damage_reduction: bool,
    include_passive_true_damage: bool,
) -> float:
    """共用同一末端数值链，暴击标记比较也计入减伤、软上限及最终取整。"""
    from ..arena_coop import adjust_arena_coop_damage

    city_defense_multiplier = _city_defense_damage_multiplier(
        actor, target, enable_siege_damage_bonus=enable_siege_damage_bonus
    )
    if city_defense_multiplier != 1.0:
        damage = damage * city_defense_multiplier
    if actor.kind == "troop":
        damage *= troop_damage_multiplier
    damage = adjust_arena_coop_damage(actor, target, damage)
    if enable_ranged_damage_reduction:
        damage = _apply_ranged_damage_reduction(actor, target, round_priority, damage)
    if include_passive_true_damage:
        damage = _apply_passive_true_damage(actor, target, damage)
    return damage


def calculate_attack_damage(
    actor: "Combatant",
    target: "Combatant",
    skills: List[AttackSkill],
    rng: random.Random,
    *,
    round_priority: int,
    troop_attack_strength: int | None = None,
    troop_damage_multiplier: float = 1.0,
    enable_ranged_damage_reduction: bool = True,
    enable_siege_damage_bonus: bool = True,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
    damage_component: Literal["combined", "burst"] = "combined",
) -> _DamageCalculation:
    """
    计算本次命中对目标造成的最终伤害。

    覆盖内容（与历史实现保持一致的顺序）：
    1) 计算有效攻击
    2) 计算目标防御（按小兵/门客、攻击者类型区分）
    3) 应用武艺技术影响（远程防御值、弓近战加成、刀系破甲与对剑增伤）
    4) 应用五行相克固定倍率
    5) 按战斗双方类型计算防御减伤（含软/硬上限）
    6) 伤害随机波动
    7) 暴击判定
    8) 技能伤害加成
    9) 先手回合调整 + 特定武艺倍率
    10) 双倍打击
    11) 状态惩罚（伤害降低）
    12) 门客对小兵/门客的最终倍率：普攻倍率 + 技能伤害独立倍率
    13) 非门客来源的城防相关最终伤害倍率与攻坚科技加成
    14) 远程直接减伤（不影响附加真伤）

    该函数不直接修改 actor/target 的血量或兵力，专注于"伤害数值"的计算。
    """
    from ..combat_math import calculate_slaughter_multiplier, effective_attack_value, effective_defense_value
    from ..skills import skill_damage_bonus

    versioned_slaughter_multiplier = partial(
        calculate_slaughter_multiplier, battle_engine_version=battle_engine_version
    )

    attack_value: float = effective_attack_value(actor, target, troop_attack_strength=troop_attack_strength)

    defense_value = _calculate_defense_value(actor, target, effective_defense_value)
    attack_value, defense_value = _apply_attack_and_defense_tech_effects(
        actor,
        target,
        round_priority,
        attack_value,
        defense_value,
        battle_engine_version=battle_engine_version,
    )
    attack_value = _apply_troop_counter_bonus(actor, target, attack_value)
    damage_reduction = _calculate_damage_reduction(actor, target, defense_value)

    attack_multiplier = rng.uniform(DAMAGE_VARIANCE_MIN, DAMAGE_VARIANCE_MAX)
    base_damage = _calculate_base_damage(
        actor,
        target,
        attack_value,
        damage_reduction,
        attack_multiplier,
        battle_engine_version=battle_engine_version,
    )
    base_damage_without_crit = base_damage

    crit_chance = BASE_CRIT_CHANCE
    is_crit = rng.random() < crit_chance
    if is_crit:
        base_damage *= CRIT_DAMAGE_MULTIPLIER

    bonus = skill_damage_bonus(skills, actor, target)
    base_damage_value = base_damage
    total_damage_value = base_damage + bonus
    skill_base_multiplier = 1.0
    for skill in skills:
        if "damage_multiplier" in skill:
            multiplier = max(0.0, float(skill.get("damage_multiplier") or 0.0))
            total_damage_value *= multiplier
            skill_base_multiplier *= multiplier
        execute = (
            target.hp / max(1, target.max_hp) < skill["execute_hp_ratio_lt"]
            if "execute_hp_ratio_lt" in skill
            else "execute_hp_ratio" in skill and target.hp / max(1, target.max_hp) <= skill["execute_hp_ratio"]
        )
        if execute:
            multiplier = float(skill.get("execute_multiplier", 1.0))
            total_damage_value *= multiplier
            skill_base_multiplier *= multiplier

    is_double_strike = _roll_double_strike(actor, rng)
    base_damage_value = _apply_post_damage_modifiers(
        actor,
        target,
        skills,
        round_priority=round_priority,
        damage=base_damage_value,
        is_double_strike=is_double_strike,
        rng=rng,
        battle_engine_version=battle_engine_version,
    )
    total_damage_value = _apply_post_damage_modifiers(
        actor,
        target,
        skills,
        round_priority=round_priority,
        damage=total_damage_value,
        is_double_strike=is_double_strike,
        rng=rng,
        battle_engine_version=battle_engine_version,
    )

    skill_dr = _intellect_skill_damage_reduction(target, battle_engine_version)
    noncritical_burst_damage = None
    if damage_component == "burst":
        # 倍率和斩杀造成的额外伤害也属于技能增量，不能把普攻基础重复计入。
        damage = max(0.0, total_damage_value - base_damage_value)
        if target.kind == "troop":
            damage *= GUEST_SKILL_VS_TROOP_MULTIPLIER
        elif target.kind == "guest":
            damage *= GUEST_SKILL_VS_GUEST_MULTIPLIER * (1.0 - skill_dr)
        if battle_engine_version in GUEST_ACTION_CONSISTENCY_ENGINE_VERSIONS and is_crit:
            # 纯加法技能的基础暴击会在 total-base 中抵消；只给实际增加技能伤害的暴击标记。
            crit_increment = _apply_post_damage_modifiers(
                actor,
                target,
                skills,
                round_priority=round_priority,
                damage=(base_damage - base_damage_without_crit) * (skill_base_multiplier - 1.0),
                is_double_strike=is_double_strike,
                rng=rng,
                battle_engine_version=battle_engine_version,
            )
            noncritical_burst_damage = max(0.0, total_damage_value - base_damage_value - crit_increment)
            if target.kind == "troop":
                noncritical_burst_damage *= GUEST_SKILL_VS_TROOP_MULTIPLIER
            elif target.kind == "guest":
                noncritical_burst_damage *= GUEST_SKILL_VS_GUEST_MULTIPLIER * (1.0 - skill_dr)
    elif actor.kind == "guest" and target.kind == "troop":
        damage = _apply_guest_vs_troop_split_scaling(
            actor,
            target,
            base_damage=base_damage_value,
            total_damage=total_damage_value,
            calculate_slaughter_multiplier_fn=versioned_slaughter_multiplier,
            battle_engine_version=battle_engine_version,
        )
    elif actor.kind == "guest" and target.kind == "guest":
        damage = _apply_guest_vs_guest_split_scaling(
            base_damage=base_damage_value,
            total_damage=total_damage_value,
            skill_damage_reduction=skill_dr,
        )
    else:
        damage = _apply_slaughter_multiplier(actor, target, total_damage_value, versioned_slaughter_multiplier)

    finalize_modifiers = partial(
        _apply_final_damage_modifiers,
        actor,
        target,
        round_priority=round_priority,
        troop_damage_multiplier=troop_damage_multiplier,
        enable_siege_damage_bonus=enable_siege_damage_bonus,
        enable_ranged_damage_reduction=enable_ranged_damage_reduction,
        include_passive_true_damage=damage_component != "burst",
    )
    damage = finalize_modifiers(damage)
    # 辅助技能允许零伤害；普攻和历史混合攻击保留原有伤害下限。
    damage = max(0, int(damage)) if damage_component == "burst" else _finalize_damage(damage)
    if noncritical_burst_damage is not None:
        is_crit = damage > max(0, int(finalize_modifiers(noncritical_burst_damage)))

    return _DamageCalculation(damage=damage, is_crit=is_crit, is_double_strike=is_double_strike)
