"""
攻击执行逻辑
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any, cast

from .damage_application import apply_damage_results
from .damage_calculation import calculate_attack_damage, process_status_effects
from .target_selection import is_ranged_attack, select_attack_targets
from .types import AttackLogEntry, AttackSkill, AttackType
from .utils import calculate_dodge_chance

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


def _trigger_attack_skills(actor: "Combatant", rng: random.Random) -> list[AttackSkill]:
    """
    触发本次攻击可用的技能集合。

    说明：
    - 该函数仅负责技能触发（含随机性），不负责目标选择、伤害计算或状态施加。
    - 为保持战斗可复现性（基于 seed），此处的 RNG 调用顺序需与历史实现一致。
    """
    from ..skills import trigger_skills

    return trigger_skills(actor, rng)


def _finalize_attack_round(actor: "Combatant", action_logs: list[AttackLogEntry]) -> AttackLogEntry | None:
    """
    完成本次行动的统一结算：
    - 标记行动完成（`has_acted_this_round` / `last_round_acted`）
    - 将多目标攻击的次要目标日志挂载到主日志 `additional_targets`
    """

    actor.has_acted_this_round = True
    actor.last_round_acted = actor.current_round
    if not action_logs:
        return None

    primary = action_logs[0]
    primary["additional_targets"] = action_logs[1:]
    return primary


def perform_attack(
    actor: "Combatant",
    attacker_team: list["Combatant"],
    defender_team: list["Combatant"],
    rng: random.Random,
    round_priority: int = 0,
) -> dict[str, Any] | None:
    """
    执行一次单位攻击行动（可能包含多目标技能）。

    返回值：
    - 返回一条主战报 `AttackLogEntry`（字典），其余目标（若存在）放在 `additional_targets`；
    - 若行动时无可攻击目标，则返回 None，但仍会标记 `has_acted_this_round` / `last_round_acted`。

    随机性兼容性：
    - 本函数严格维护 RNG 的消耗顺序，以确保历史 seed 的战斗回放一致。
    """

    selection = select_attack_targets(actor, attacker_team, defender_team, rng, _trigger_attack_skills)
    if selection is None:
        return cast(dict[str, Any] | None, _finalize_attack_round(actor, []))

    action_logs: list[AttackLogEntry] = []
    actor_defeated = False
    for idx, current_target in enumerate(selection.engaged_targets):
        dodge_chance = calculate_dodge_chance(current_target)
        if rng.random() < dodge_chance:
            dodge_entry: AttackLogEntry = {
                "actor": actor.name,
                "target": current_target.name,
                "damage": 0,
                "is_dodge": True,
                "is_crit": False,
                "side": actor.side,
                "skills": [skill["name"] for skill in selection.skills],
                "agility": actor.agility,
                "kind": actor.kind,
                "priority": actor.priority,
                "status_inflicted": [],
                "index": idx,
                "kills": 0,
                "target_defeated": False,
                "actor_guest_id": actor.guest_id,
                "actor_owner_entry_id": actor.owner_entry_id,
                "actor_combatant_slot": actor.combatant_slot,
                "target_guest_id": current_target.guest_id,
                "target_owner_entry_id": current_target.owner_entry_id,
                "target_combatant_slot": current_target.combatant_slot,
                "target_template_key": current_target.template_key,
                "target_is_boss": current_target.is_boss,
            }
            action_logs.append(dodge_entry)
            continue

        damage_calc = calculate_attack_damage(
            actor,
            current_target,
            selection.skills,
            rng,
            round_priority=round_priority,
        )

        applied = apply_damage_results(actor, current_target, damage_calc.damage, rng)
        actor_defeated = actor_defeated or applied.actor_defeated

        attack_type: AttackType = "ranged" if is_ranged_attack(actor, round_priority) else "melee"
        entry: AttackLogEntry = {
            "actor": actor.name,
            "target": current_target.name,
            "damage": applied.display_damage,
            "is_crit": damage_calc.is_crit,
            "is_dodge": False,
            "side": actor.side,
            "skills": [skill["name"] for skill in selection.skills],
            "agility": actor.agility,
            "kind": actor.kind,
            "priority": actor.priority,
            "status_inflicted": [],
            "index": idx,
            "kills": applied.kills,
            "target_defeated": applied.target_defeated,
            "is_double_strike": damage_calc.is_double_strike,
            "reflect_damage": applied.reflect_damage,
            "reflect_kills": applied.reflect_kills,
            "reflect_defeated": applied.reflect_defeated,
            "counter_damage": applied.counter_damage,
            "counter_kills": applied.counter_kills,
            "counter_defeated": applied.counter_defeated,
            "attack_type": attack_type,
            "actor_defeated": actor_defeated,
            "actor_guest_id": actor.guest_id,
            "actor_owner_entry_id": actor.owner_entry_id,
            "actor_combatant_slot": actor.combatant_slot,
            "target_guest_id": current_target.guest_id,
            "target_owner_entry_id": current_target.owner_entry_id,
            "target_combatant_slot": current_target.combatant_slot,
            "target_template_key": current_target.template_key,
            "target_is_boss": current_target.is_boss,
        }

        entry["status_inflicted"] = process_status_effects(
            actor, current_target, selection.skills, rng, phase="inflict"
        )
        action_logs.append(entry)

        if actor_defeated:
            break

    return cast(dict[str, Any] | None, _finalize_attack_round(actor, action_logs))
