"""
伤害应用逻辑
"""

from __future__ import annotations

import random
from contextlib import contextmanager
from math import ceil
from typing import TYPE_CHECKING, Any, Iterator, cast

from ..random_context import CURRENT_BATTLE_ENGINE_VERSION, DEFERRED_QIANG_WANG_COUNTER_ENGINE_VERSIONS
from .types import _DamageApplication, _UnitDamageApplication

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


_SWORD_REFLECT_PRE_HIT_HP_CAP_RATIO = 1.20
_SPEAR_COUNTER_MAX_STRENGTH = 1000
_DEFERRED_QIANG_WANG_COUNTERS_KEY = "_deferred_qiang_wang_counters"
_QIANG_WANG_COUNTER_ATTEMPTS_KEY = "_qiang_wang_counter_attempts"
_ACTION_COUNTER_ATTEMPTS_KEY = "_action_counter_attempts"


@contextmanager
def action_counter_attempts(actor: "Combatant") -> Iterator[dict[tuple[int, str], int]]:
    """限制单次主动行动内对同一目标同一被动反击来源的判定次数。"""
    attempts: dict[tuple[int, str], int] = {}
    state = actor.battle_state
    existed = _ACTION_COUNTER_ATTEMPTS_KEY in state
    previous = state.get(_ACTION_COUNTER_ATTEMPTS_KEY)
    state[_ACTION_COUNTER_ATTEMPTS_KEY] = attempts
    try:
        yield attempts
    finally:
        if existed:
            state[_ACTION_COUNTER_ATTEMPTS_KEY] = previous
        else:
            state.pop(_ACTION_COUNTER_ATTEMPTS_KEY, None)


def _claim_action_counter_attempt(
    actor: "Combatant",
    target: "Combatant",
    source_key: str,
    max_per_action: int | None,
) -> bool:
    if max_per_action is None:
        return True
    state = getattr(actor, "battle_state", None)
    if not isinstance(state, dict):
        return True
    tracker = state.get(_ACTION_COUNTER_ATTEMPTS_KEY)
    if not isinstance(tracker, dict):
        return True
    attempt_key = (id(target), source_key)
    used = int(tracker.get(attempt_key, 0) or 0)
    if used >= max_per_action:
        return False
    tracker[attempt_key] = used + 1
    return True


@contextmanager
def defer_qiang_wang_counters(actor: "Combatant") -> Iterator[list["Combatant"]]:
    """仅在本次行动中收集受击枪王，避免技能、普攻和分队逐段反击。"""
    pending: list["Combatant"] = []
    state = actor.battle_state
    existed = _DEFERRED_QIANG_WANG_COUNTERS_KEY in state
    previous = state.get(_DEFERRED_QIANG_WANG_COUNTERS_KEY)
    state[_DEFERRED_QIANG_WANG_COUNTERS_KEY] = pending
    try:
        yield pending
    finally:
        if existed:
            state[_DEFERRED_QIANG_WANG_COUNTERS_KEY] = previous
        else:
            state.pop(_DEFERRED_QIANG_WANG_COUNTERS_KEY, None)


def _claim_qiang_wang_counter_attempt(actor: "Combatant", target: "Combatant") -> bool:
    """身份仅用于战斗内比较，不写入战报；同名或同模板单位不会共享次数。"""
    round_no = target.current_round
    attempts = target.battle_state.get(_QIANG_WANG_COUNTER_ATTEMPTS_KEY)
    if not isinstance(attempts, dict) or attempts.get("round") != round_no:
        attempts = {"round": round_no, "actors": set()}
        target.battle_state[_QIANG_WANG_COUNTER_ATTEMPTS_KEY] = attempts
    actor_id = id(actor)
    if actor_id in attempts["actors"]:
        return False
    # 失败也消耗本回合机会，不能在后续追加攻击中再次掷反击概率。
    attempts["actors"].add(actor_id)
    return True


def _normalize_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(cast(Any, value)))
    except (TypeError, ValueError):
        return 0


def _finalize_damage(value: int | float) -> int:
    """Convert damage to an integer only when it reaches the HP state boundary."""

    return _normalize_non_negative_int(value)


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


def _apply_unit_damage(unit: "Combatant", damage: int | float, troop_unit_hp_fn) -> _UnitDamageApplication:
    """Apply one damage transition while keeping HP and troop strength consistent.

    Damage multipliers belong to the calculation that produced ``damage``. This
    state-transition boundary deliberately does not infer attack semantics or
    apply the guest-vs-troop slaughter multiplier.
    """

    raw_damage = _finalize_damage(damage)
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
    damage: float,
    *,
    ranged_attack: bool,
    target_hp_before: int,
    troop_unit_hp_fn,
) -> _UnitDamageApplication:
    """Apply reflected secondary damage without normal-attack multipliers."""

    from ..arena_coop import get_arena_coop_reflect_values

    reflect_ratio = (
        0 if ranged_attack and target.troop_class == "jian" else target.tech_effects.get("damage_reflect", 0)
    )
    max_reflect = float(actor.attack)
    if reflect_ratio > 0 and target.troop_class == "jian":
        max_reflect = min(max_reflect, target_hp_before * _SWORD_REFLECT_PRE_HIT_HP_CAP_RATIO)
    else:
        reflect_ratio, special_cap = get_arena_coop_reflect_values(target)
        if reflect_ratio <= 0:
            return _snapshot_zero_damage(actor, troop_unit_hp_fn)
        max_reflect = special_cap if special_cap > 0 else max_reflect

    reflect_damage = min(damage * reflect_ratio, max_reflect)
    return _apply_unit_damage(actor, reflect_damage, troop_unit_hp_fn)


def _collect_counter_candidates(
    target: "Combatant",
    *,
    ranged_attack: bool,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    tech_chance = float(target.tech_effects.get("counter_attack_chance", 0) or 0)
    if tech_chance > 0 and not (ranged_attack and target.troop_class == "qiang"):
        candidates.append(
            {
                "source_key": "__tech__",
                "chance": tech_chance,
                "damage_multiplier": float(target.tech_effects.get("counter_attack_damage", 0.30) or 0),
                "scale_stat": "attack",
                "use_defense": False,
                "max_per_action": None,
                "name": "",
            }
        )
    for source_key, source in getattr(target, "battle_modifiers", {}).get("counter_sources", {}).items():
        if not isinstance(source, dict):
            continue
        if ranged_attack and source.get("melee_only"):
            continue
        chance = float(source.get("chance") or 0)
        if chance <= 0:
            continue
        candidates.append(
            {
                "source_key": str(source_key),
                "chance": chance,
                "damage_multiplier": float(source.get("damage_multiplier") or 0),
                "scale_stat": str(source.get("scale_stat") or "attack"),
                "use_defense": bool(source.get("use_defense", False)),
                "max_per_action": source.get("max_per_action"),
                "name": str(source.get("name") or ""),
            }
        )
    candidates.sort(key=lambda item: float(item["chance"]), reverse=True)
    return candidates


def _apply_counter_with_effect(
    actor: "Combatant",
    target: "Combatant",
    rng: random.Random,
    effective_attack_value_fn,
    troop_unit_hp_fn,
    *,
    ranged_attack: bool,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> tuple[_UnitDamageApplication, str]:
    """Apply counter secondary damage without normal-attack multipliers."""

    if ranged_attack and target.troop_class == "qiang":
        return _snapshot_zero_damage(actor, troop_unit_hp_fn), ""

    limited_counter = (
        battle_engine_version in DEFERRED_QIANG_WANG_COUNTER_ENGINE_VERSIONS
        and target.kind == "troop"
        and target.template_key == "qiang_wang"
    )
    if limited_counter:
        if actor.hp <= 0 or target.hp <= 0:
            return _snapshot_zero_damage(actor, troop_unit_hp_fn), ""
        pending = actor.battle_state.get(_DEFERRED_QIANG_WANG_COUNTERS_KEY)
        if isinstance(pending, list):
            if not any(unit is target for unit in pending):
                pending.append(target)
            return _snapshot_zero_damage(actor, troop_unit_hp_fn), ""

    if target.hp <= 0:
        return _snapshot_zero_damage(actor, troop_unit_hp_fn), ""

    candidates = _collect_counter_candidates(target, ranged_attack=ranged_attack)
    chosen = None
    for candidate in candidates:
        if _claim_action_counter_attempt(actor, target, candidate["source_key"], candidate["max_per_action"]):
            chosen = candidate
            break
    if chosen is None:
        return _snapshot_zero_damage(actor, troop_unit_hp_fn), ""

    if limited_counter and not _claim_qiang_wang_counter_attempt(actor, target):
        return _snapshot_zero_damage(actor, troop_unit_hp_fn), ""
    if rng.random() >= float(chosen["chance"]):
        return _snapshot_zero_damage(actor, troop_unit_hp_fn), ""

    counter_mult = float(chosen["damage_multiplier"])
    if chosen["scale_stat"] == "force":
        counter_attack_value = float(getattr(target, "force_attr", 0) or getattr(target, "force", 0) or 0)
    elif target.kind == "troop" and target.troop_class == "qiang":
        counter_strength = min(target.troop_strength, _SPEAR_COUNTER_MAX_STRENGTH)
        counter_attack_value = effective_attack_value_fn(target, actor, troop_attack_strength=counter_strength)
    else:
        counter_attack_value = effective_attack_value_fn(target, actor)

    counter_damage = counter_attack_value * counter_mult
    if chosen["use_defense"]:
        from ..combat_math import effective_defense_value
        from .damage_calculation import _calculate_damage_reduction, _calculate_defense_value

        defense_value = _calculate_defense_value(target, actor, effective_defense_value)
        damage_reduction = _calculate_damage_reduction(target, actor, defense_value)
        counter_damage *= 1.0 - damage_reduction

    return _apply_unit_damage(actor, counter_damage, troop_unit_hp_fn), str(chosen["name"] or "")


def _apply_counter(
    actor: "Combatant",
    target: "Combatant",
    rng: random.Random,
    effective_attack_value_fn,
    troop_unit_hp_fn,
    *,
    ranged_attack: bool,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> _UnitDamageApplication:
    application, _effect_name = _apply_counter_with_effect(
        actor,
        target,
        rng,
        effective_attack_value_fn,
        troop_unit_hp_fn,
        ranged_attack=ranged_attack,
        battle_engine_version=battle_engine_version,
    )
    return application


def resolve_deferred_qiang_wang_counters(
    actor: "Combatant",
    pending: list["Combatant"],
    rng: random.Random,
    *,
    battle_engine_version: str,
) -> Iterator[tuple["Combatant", _UnitDamageApplication]]:
    from ..combat_math import effective_attack_value, troop_unit_hp

    for target in pending:
        if actor.hp <= 0:
            break
        application = _apply_counter(
            actor,
            target,
            rng,
            effective_attack_value,
            troop_unit_hp,
            ranged_attack=False,
            battle_engine_version=battle_engine_version,
        )
        if application.applied_damage > 0:
            yield target, application


def apply_damage_results(
    actor: "Combatant",
    target: "Combatant",
    damage: int | float,
    rng: random.Random,
    *,
    ranged_attack: bool = False,
    trigger_reactions: bool = True,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> _DamageApplication:
    """
    将伤害应用到目标，并处理命中后结算：
    - 目标 HP/兵力扣减与击杀数计算
    - 技术效果：近战命中可触发反伤（剑系）、反击（枪系）
    - 检查攻击者是否被反伤/反击击败

    该函数会直接修改 `actor` 和 `target` 的状态（HP、兵力等）。
    """
    from ..combat_math import effective_attack_value, troop_unit_hp

    target_application = _apply_unit_damage(target, damage, troop_unit_hp)
    if not trigger_reactions:
        return _DamageApplication(
            target=target_application,
            reflect=_snapshot_zero_damage(actor, troop_unit_hp),
            counter=_snapshot_zero_damage(actor, troop_unit_hp),
        )
    reflect_application = _apply_reflect(
        actor,
        target,
        damage,
        ranged_attack=ranged_attack,
        target_hp_before=target_application.hp_before,
        troop_unit_hp_fn=troop_unit_hp,
    )
    counter_application, counter_effect_name = _apply_counter_with_effect(
        actor,
        target,
        rng,
        effective_attack_value,
        troop_unit_hp,
        ranged_attack=ranged_attack,
        battle_engine_version=battle_engine_version,
    )

    return _DamageApplication(
        target=target_application,
        reflect=reflect_application,
        counter=counter_application,
        reflect_effect=(
            (
                "护身剑罡"
                if not ranged_attack
                and target.troop_class == "jian"
                and target.tech_effects.get("damage_reflect", 0) > 0
                else str(target.battle_modifiers.get("reflect_name") or "反震")
            )
            if reflect_application.raw_damage > 0
            else ""
        ),
        counter_effect=counter_effect_name if counter_application.raw_damage > 0 else "",
    )
