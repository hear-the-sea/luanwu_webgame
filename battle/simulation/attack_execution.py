"""
攻击执行逻辑
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any, cast

from ..passive_effects import consume_pending_extra_target_true_damage
from ..passives import run_passives_for_timing
from .damage_application import apply_damage_results
from .damage_calculation import calculate_attack_damage, calculate_passive_true_damage, process_status_effects
from .report_state import snapshot_unit_state
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
    - 将本次行动的额外目标或追加命中日志挂载到主日志 `additional_targets`
    """

    actor.has_acted_this_round = True
    actor.last_round_acted = actor.current_round
    if not action_logs:
        return None

    primary = action_logs[0]
    primary["additional_targets"] = action_logs[1:]
    return primary


def _read_damage_contract(application: Any, target: "Combatant") -> dict[str, int]:
    """Read the extended damage contract while tolerating legacy test/adaptor results."""

    target_application = getattr(application, "target", None)
    raw_damage = int(getattr(target_application, "raw_damage", getattr(application, "display_damage", 0)) or 0)
    hp_after = max(0, int(getattr(target_application, "hp_after", getattr(target, "hp", 0)) or 0))
    applied_damage = int(getattr(target_application, "applied_damage", raw_damage) or 0)
    strength_after = max(
        0,
        int(getattr(target_application, "strength_after", getattr(target, "troop_strength", 0)) or 0),
    )
    kills = max(0, int(getattr(application, "kills", 0) or 0))
    return {
        "raw_damage": raw_damage,
        "applied_damage": applied_damage,
        "overkill_damage": int(
            getattr(target_application, "overkill_damage", max(0, raw_damage - applied_damage)) or 0
        ),
        "hp_before": min(
            max(0, int(getattr(target, "max_hp", hp_after + applied_damage) or 0)),
            int(getattr(target_application, "hp_before", hp_after + applied_damage) or 0),
        ),
        "hp_after": hp_after,
        "strength_before": int(getattr(target_application, "strength_before", strength_after + kills) or 0),
        "strength_after": strength_after,
    }


def _read_secondary_damage(application: Any, name: str) -> tuple[int, int]:
    nested = getattr(application, name, None)
    raw_damage = int(getattr(nested, "raw_damage", getattr(application, f"{name}_damage", 0)) or 0)
    applied_damage = int(getattr(nested, "applied_damage", raw_damage) or 0)
    overkill_damage = int(getattr(nested, "overkill_damage", max(0, raw_damage - applied_damage)) or 0)
    return applied_damage, overkill_damage


def _apply_extra_target_true_damage(
    actor: "Combatant",
    target: "Combatant",
    effect: dict[str, Any],
    *,
    attacker_team: list["Combatant"],
    defender_team: list["Combatant"],
    rng: random.Random,
    round_priority: int,
    index: int,
    fallback_to_selected_target: bool = False,
) -> AttackLogEntry:
    passive_events_before: list[dict[str, Any]] = [
        {
            "type": "passive",
            "side": actor.side,
            "unit": actor.name,
            "effect": str(effect.get("log_name") or "被动技能"),
            "message": "当前目标追加伤害" if fallback_to_selected_target else "随机追加目标",
            "unit_state": snapshot_unit_state(actor),
        }
    ]
    run_passives_for_timing(
        "attack_before",
        actor=actor,
        target=target,
        attacker_team=attacker_team,
        defender_team=defender_team,
        round_no=actor.current_round,
        event_sink=passive_events_before,
        rng=rng,
    )

    damage = calculate_passive_true_damage(
        actor,
        target,
        value=float(effect.get("value") or 0),
        troop_value_multiplier=float(effect.get("troop_value_multiplier") or 1.0),
    )
    application = apply_damage_results(actor, target, damage, rng)
    damage_contract = _read_damage_contract(application, target)
    reflect_applied_damage, reflect_overkill_damage = _read_secondary_damage(application, "reflect")
    counter_applied_damage, counter_overkill_damage = _read_secondary_damage(application, "counter")
    actor_state = snapshot_unit_state(actor)
    target_state = snapshot_unit_state(target)
    passive_events_after: list[dict[str, Any]] = []
    run_passives_for_timing(
        "hit_taken",
        actor=target,
        target=actor,
        attacker_team=attacker_team,
        defender_team=defender_team,
        round_no=actor.current_round,
        event_sink=passive_events_after,
        rng=rng,
    )
    run_passives_for_timing(
        "attack_after",
        actor=actor,
        target=target,
        attacker_team=attacker_team,
        defender_team=defender_team,
        round_no=actor.current_round,
        event_sink=passive_events_after,
        rng=rng,
    )

    return cast(
        AttackLogEntry,
        {
            "actor": actor.name,
            "target": target.name,
            "damage": application.display_damage,
            "is_crit": False,
            "is_dodge": False,
            "side": actor.side,
            "skills": [],
            "agility": int(actor.agility),
            "kind": actor.kind,
            "priority": actor.priority,
            "status_inflicted": [],
            "index": index,
            "kills": application.kills,
            "target_defeated": application.target_defeated,
            "raw_damage": damage_contract["raw_damage"],
            "applied_damage": damage_contract["applied_damage"],
            "overkill_damage": damage_contract["overkill_damage"],
            "target_hp_before": damage_contract["hp_before"],
            "target_hp_after": damage_contract["hp_after"],
            "target_strength_before": damage_contract["strength_before"],
            "target_strength_after": damage_contract["strength_after"],
            "reflect_damage": application.reflect_damage,
            "reflect_applied_damage": reflect_applied_damage,
            "reflect_overkill_damage": reflect_overkill_damage,
            "reflect_kills": application.reflect_kills,
            "reflect_defeated": application.reflect_defeated,
            "counter_damage": application.counter_damage,
            "counter_applied_damage": counter_applied_damage,
            "counter_overkill_damage": counter_overkill_damage,
            "counter_kills": application.counter_kills,
            "counter_defeated": application.counter_defeated,
            "actor_defeated": application.actor_defeated,
            "actor_guest_id": actor.guest_id,
            "actor_owner_entry_id": actor.owner_entry_id,
            "actor_combatant_slot": actor.combatant_slot,
            "target_guest_id": target.guest_id,
            "target_owner_entry_id": target.owner_entry_id,
            "target_combatant_slot": target.combatant_slot,
            "target_template_key": target.template_key,
            "target_is_boss": target.is_boss,
            "actor_state": actor_state,
            "target_state": target_state,
            "passive_events_before": passive_events_before,
            "passive_events_after": passive_events_after,
            "current_target_bonus": fallback_to_selected_target,
            "attack_type": "ranged" if is_ranged_attack(actor, round_priority) else "melee",
        },
    )


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
    - 返回一条主战报 `AttackLogEntry`（字典），本次行动的附加命中记录放在 `additional_targets`；
    - 若行动时无可攻击目标，则返回 None，但仍会标记 `has_acted_this_round` / `last_round_acted`。

    随机性兼容性：
    - 本函数严格维护 RNG 的消耗顺序，以确保历史 seed 的战斗回放一致。
    """

    extra_target_effects = consume_pending_extra_target_true_damage(actor)
    selection = select_attack_targets(actor, attacker_team, defender_team, rng, _trigger_attack_skills)
    if selection is None:
        return cast(dict[str, Any] | None, _finalize_attack_round(actor, []))

    opposing_team = defender_team if actor.side == "attacker" else attacker_team
    selected_target_ids = {id(target) for target in selection.engaged_targets}
    has_unselected_alive_target = any(
        int(getattr(target, "hp", 0) or 0) > 0 and id(target) not in selected_target_ids for target in opposing_team
    )
    current_target_bonus_effects: list[dict[str, Any]] = [] if has_unselected_alive_target else extra_target_effects
    merged_extra_effect_ids: set[int] = set()

    action_logs: list[AttackLogEntry] = []
    actor_defeated = False
    for idx, current_target in enumerate(selection.engaged_targets):
        passive_events_before: list[dict[str, Any]] = []
        run_passives_for_timing(
            "attack_before",
            actor=actor,
            target=current_target,
            attacker_team=attacker_team,
            defender_team=defender_team,
            round_no=actor.current_round,
            event_sink=passive_events_before,
            rng=rng,
        )
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
                "agility": int(actor.agility),
                "kind": actor.kind,
                "priority": actor.priority,
                "status_inflicted": [],
                "index": idx,
                "kills": 0,
                "target_defeated": False,
                "raw_damage": 0,
                "applied_damage": 0,
                "overkill_damage": 0,
                "target_hp_before": max(0, int(current_target.hp)),
                "target_hp_after": max(0, int(current_target.hp)),
                "target_strength_before": max(0, int(current_target.troop_strength)),
                "target_strength_after": max(0, int(current_target.troop_strength)),
                "actor_guest_id": actor.guest_id,
                "actor_owner_entry_id": actor.owner_entry_id,
                "actor_combatant_slot": actor.combatant_slot,
                "target_guest_id": current_target.guest_id,
                "target_owner_entry_id": current_target.owner_entry_id,
                "target_combatant_slot": current_target.combatant_slot,
                "target_template_key": current_target.template_key,
                "target_is_boss": current_target.is_boss,
                "actor_state": snapshot_unit_state(actor),
                "target_state": snapshot_unit_state(current_target),
            }
            if passive_events_before:
                dodge_entry["passive_events_before"] = passive_events_before
            action_logs.append(dodge_entry)
            continue

        damage_calc = calculate_attack_damage(
            actor,
            current_target,
            selection.skills,
            rng,
            round_priority=round_priority,
        )

        damage_to_apply: int | float = damage_calc.damage
        if idx == 0 and current_target_bonus_effects:
            for effect in current_target_bonus_effects:
                bonus_damage = calculate_passive_true_damage(
                    actor,
                    current_target,
                    value=float(effect.get("value") or 0),
                    troop_value_multiplier=float(effect.get("troop_value_multiplier") or 1.0),
                )
                if bonus_damage <= 0:
                    continue
                damage_to_apply += bonus_damage
                merged_extra_effect_ids.add(id(effect))
                passive_events_before.append(
                    {
                        "type": "passive",
                        "side": actor.side,
                        "unit": actor.name,
                        "effect": str(effect.get("log_name") or "被动技能"),
                        "message": "本次攻击追加真实伤害",
                        "unit_state": snapshot_unit_state(actor),
                    }
                )

        applied = apply_damage_results(actor, current_target, damage_to_apply, rng)
        damage_contract = _read_damage_contract(applied, current_target)
        reflect_applied_damage, reflect_overkill_damage = _read_secondary_damage(applied, "reflect")
        counter_applied_damage, counter_overkill_damage = _read_secondary_damage(applied, "counter")
        actor_state = snapshot_unit_state(actor)
        target_state = snapshot_unit_state(current_target)
        actor_defeated = actor_defeated or applied.actor_defeated
        passive_events_after: list[dict[str, Any]] = []
        run_passives_for_timing(
            "hit_taken",
            actor=current_target,
            target=actor,
            attacker_team=attacker_team,
            defender_team=defender_team,
            round_no=actor.current_round,
            event_sink=passive_events_after,
            rng=rng,
        )

        attack_type: AttackType = "ranged" if is_ranged_attack(actor, round_priority) else "melee"
        entry: AttackLogEntry = {
            "actor": actor.name,
            "target": current_target.name,
            "damage": applied.display_damage,
            "is_crit": damage_calc.is_crit,
            "is_dodge": False,
            "side": actor.side,
            "skills": [skill["name"] for skill in selection.skills],
            "agility": int(actor.agility),
            "kind": actor.kind,
            "priority": actor.priority,
            "status_inflicted": [],
            "index": idx,
            "kills": applied.kills,
            "target_defeated": applied.target_defeated,
            "raw_damage": damage_contract["raw_damage"],
            "applied_damage": damage_contract["applied_damage"],
            "overkill_damage": damage_contract["overkill_damage"],
            "target_hp_before": damage_contract["hp_before"],
            "target_hp_after": damage_contract["hp_after"],
            "target_strength_before": damage_contract["strength_before"],
            "target_strength_after": damage_contract["strength_after"],
            "is_double_strike": damage_calc.is_double_strike,
            "reflect_damage": applied.reflect_damage,
            "reflect_applied_damage": reflect_applied_damage,
            "reflect_overkill_damage": reflect_overkill_damage,
            "reflect_kills": applied.reflect_kills,
            "reflect_defeated": applied.reflect_defeated,
            "counter_damage": applied.counter_damage,
            "counter_applied_damage": counter_applied_damage,
            "counter_overkill_damage": counter_overkill_damage,
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
            "actor_state": actor_state,
            "target_state": target_state,
        }
        if passive_events_before:
            entry["passive_events_before"] = passive_events_before

        entry["status_inflicted"] = process_status_effects(
            actor, current_target, selection.skills, rng, phase="inflict"
        )
        run_passives_for_timing(
            "attack_after",
            actor=actor,
            target=current_target,
            attacker_team=attacker_team,
            defender_team=defender_team,
            round_no=actor.current_round,
            event_sink=passive_events_after,
            rng=rng,
        )
        if passive_events_after:
            entry["passive_events_after"] = passive_events_after
        action_logs.append(entry)

        if actor_defeated:
            break

    remaining_extra_target_effects = [
        effect for effect in extra_target_effects if id(effect) not in merged_extra_effect_ids
    ]
    if not actor_defeated and int(getattr(actor, "hp", 0) or 0) > 0 and remaining_extra_target_effects:
        additional_target_ids: set[int] = set()
        for effect in remaining_extra_target_effects:
            candidates = [
                target
                for target in opposing_team
                if int(getattr(target, "hp", 0) or 0) > 0
                and id(target) not in selected_target_ids
                and id(target) not in additional_target_ids
            ]
            fallback_to_selected_target = not candidates
            if not candidates:
                candidates = [
                    target
                    for target in selection.engaged_targets
                    if int(getattr(target, "hp", 0) or 0) > 0 and id(target) not in additional_target_ids
                ]
                if not candidates:
                    break
                extra_target = candidates[0]
            else:
                extra_target = rng.choice(candidates)
            additional_target_ids.add(id(extra_target))
            extra_entry = _apply_extra_target_true_damage(
                actor,
                extra_target,
                effect,
                attacker_team=attacker_team,
                defender_team=defender_team,
                rng=rng,
                round_priority=round_priority,
                index=len(action_logs),
                fallback_to_selected_target=fallback_to_selected_target,
            )
            action_logs.append(extra_entry)
            if extra_entry["actor_defeated"]:
                actor_defeated = True
                break

    return cast(dict[str, Any] | None, _finalize_attack_round(actor, action_logs))
