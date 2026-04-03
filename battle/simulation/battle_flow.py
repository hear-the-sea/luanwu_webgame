"""
战斗流程控制
"""

from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from django.utils import timezone

from .attack_execution import perform_attack
from .constants import MAX_ALLOWED_PRIORITY, MIN_ALLOWED_PRIORITY
from .turn_order import determine_turn_order
from .utils import alive, roll_loot, summarize_losses

if TYPE_CHECKING:
    from ..combatants_pkg.core import BattleSimulationResult, Combatant

logger = logging.getLogger(__name__)


def _clamp_min_priority(min_priority: int) -> int:
    if min_priority < MIN_ALLOWED_PRIORITY:
        logger.warning(
            "Priority value %d exceeds minimum allowed %d, clamping",
            min_priority,
            MIN_ALLOWED_PRIORITY,
        )
        return MIN_ALLOWED_PRIORITY

    if min_priority > MAX_ALLOWED_PRIORITY:
        logger.warning(
            "Priority value %d exceeds maximum allowed %d, clamping",
            min_priority,
            MAX_ALLOWED_PRIORITY,
        )
        return MAX_ALLOWED_PRIORITY

    return min_priority


def _iter_phase_attackers(
    attacker_team: List["Combatant"],
    defender_team: List["Combatant"],
    rng: random.Random,
    priority: int,
) -> list["Combatant"]:
    return [actor for actor in determine_turn_order(attacker_team, defender_team, rng) if actor.priority <= priority]


def _append_heal_event(events: List[Dict[str, Any]], heal_event: Dict[str, Any] | None) -> None:
    if not heal_event:
        return
    heal_event["order"] = len(events) + 1
    heal_event["type"] = "heal"
    events.append(heal_event)


def _append_status_event(events: List[Dict[str, Any]], status_event: Dict[str, Any] | None) -> None:
    if not status_event:
        return
    status_event["order"] = len(events) + 1
    events.append(status_event)


def _append_passive_events(events: List[Dict[str, Any]], passive_events: List[Dict[str, Any]]) -> None:
    for passive_event in passive_events:
        passive_event["order"] = len(events) + 1
        events.append(passive_event)


def _normalize_event_orders(events: List[Dict[str, Any]]) -> None:
    for idx, event in enumerate(events, start=1):
        event["order"] = idx


def _pop_attack_passive_events(event: Dict[str, Any] | None, key: str) -> List[Dict[str, Any]]:
    if not event:
        return []
    nested = event.pop(key, None) or []
    if not isinstance(nested, list):
        return []
    return [item for item in nested if isinstance(item, dict)]


def _append_attack_event(
    events: List[Dict[str, Any]], event: Dict[str, Any] | None, priority: int | None = None
) -> None:
    if not event:
        return
    for passive_event in _pop_attack_passive_events(event, "passive_events_before"):
        passive_event["order"] = len(events) + 1
        events.append(passive_event)
    event["order"] = len(events) + 1
    if priority is not None:
        event["priority_phase"] = priority
        event["preemptive"] = True
    events.append(event)
    for passive_event in _pop_attack_passive_events(event, "passive_events_after"):
        passive_event["order"] = len(events) + 1
        events.append(passive_event)


def _append_waiting_units(
    events: List[Dict[str, Any]],
    attacker_team: List["Combatant"],
    defender_team: List["Combatant"],
    priority: int,
) -> None:
    waiting_units = [
        unit for unit in alive(attacker_team) + alive(defender_team) if unit.priority > priority and unit.hp > 0
    ]
    for unit in waiting_units:
        events.append(
            {
                "actor": unit.name,
                "side": unit.side,
                "status": "charging",
                "message": "冲锋中",
                "order": len(events) + 1,
            }
        )


def _resolve_winner(attacker_units: List["Combatant"], defender_units: List["Combatant"]) -> str:
    if not alive(defender_units):
        return "attacker"
    if not alive(attacker_units):
        return "defender"
    return "defender"


def _resolve_drops(
    winner: str,
    drop_table: Dict[str, Any] | None,
    config: dict,
    rng: random.Random,
) -> Dict[str, int]:
    if winner != "attacker":
        return {}

    if drop_table is not None:
        from common.utils.loot import resolve_drop_rewards

        return resolve_drop_rewards(drop_table, rng)

    return roll_loot(config, rng)


def _build_result_timestamps(travel_seconds: int | None) -> tuple:
    now = timezone.now()
    travel = timedelta(seconds=travel_seconds if travel_seconds is not None else 5)
    starts_at = now + travel
    completed_at = starts_at
    return starts_at, completed_at


def _resolve_standard_round(
    attacker_units: List["Combatant"],
    defender_units: List["Combatant"],
    rng: random.Random,
    round_no: int,
) -> Dict[str, Any]:
    from ..arena_coop import sync_arena_coop_combat_state, try_trigger_arena_coop_pre_action_heal
    from ..passives import run_passives_for_timing
    from ..status_manager import prepare_combatants_for_round, try_trigger_battle_heal_on_action
    from ..utils.status_effects import handle_pre_action_status

    prepare_combatants_for_round(attacker_units, defender_units, round_no, promote_pending=True)
    events: List[Dict[str, Any]] = []
    for status_event in sync_arena_coop_combat_state(attacker_units, defender_units, round_no):
        _append_status_event(events, status_event)
    for unit in alive(attacker_units) + alive(defender_units):
        passive_events: List[Dict[str, Any]] = []
        run_passives_for_timing(
            "round_start",
            actor=unit,
            target=None,
            attacker_team=attacker_units,
            defender_team=defender_units,
            round_no=round_no,
            event_sink=passive_events,
            rng=rng,
        )
        _append_passive_events(events, passive_events)

    for actor in determine_turn_order(attacker_units, defender_units, rng):
        if actor.hp <= 0:
            continue
        if handle_pre_action_status(actor, events):
            continue
        passive_events = []
        run_passives_for_timing(
            "action_before",
            actor=actor,
            target=None,
            attacker_team=attacker_units,
            defender_team=defender_units,
            round_no=round_no,
            event_sink=passive_events,
            rng=rng,
        )
        _append_passive_events(events, passive_events)

        _append_heal_event(events, try_trigger_arena_coop_pre_action_heal(actor))
        _append_heal_event(events, try_trigger_battle_heal_on_action(actor, rng))
        _append_attack_event(events, perform_attack(actor, attacker_units, defender_units, rng, round_priority=0))
        passive_events = []
        run_passives_for_timing(
            "action_end",
            actor=actor,
            target=None,
            attacker_team=attacker_units,
            defender_team=defender_units,
            round_no=round_no,
            event_sink=passive_events,
            rng=rng,
        )
        _append_passive_events(events, passive_events)

        if not alive(defender_units) or not alive(attacker_units):
            break

    return {"round": round_no, "events": events}


def resolve_priority_phases(
    attacker_team: List["Combatant"],
    defender_team: List["Combatant"],
    rng: random.Random,
) -> Tuple[List[Dict[str, Any]], int]:
    from ..arena_coop import sync_arena_coop_combat_state, try_trigger_arena_coop_pre_action_heal
    from ..passives import run_passives_for_timing
    from ..status_manager import prepare_combatants_for_round, try_trigger_battle_heal_on_action
    from ..utils.status_effects import handle_pre_action_status

    participants = alive(attacker_team) + alive(defender_team)
    priority_values = sorted({c.priority for c in participants if c.priority < 0})
    if not priority_values:
        return [], 1
    rounds: List[Dict[str, Any]] = []
    next_round_no = 1
    min_priority = _clamp_min_priority(min(priority_values))

    staged_priorities = list(range(min_priority, 0))
    for priority in staged_priorities:
        events: List[Dict[str, Any]] = []
        prepare_combatants_for_round(attacker_team, defender_team, next_round_no, promote_pending=True)
        for status_event in sync_arena_coop_combat_state(attacker_team, defender_team, next_round_no):
            _append_status_event(events, status_event)
        for unit in alive(attacker_team) + alive(defender_team):
            passive_events: List[Dict[str, Any]] = []
            run_passives_for_timing(
                "round_start",
                actor=unit,
                target=None,
                attacker_team=attacker_team,
                defender_team=defender_team,
                round_no=next_round_no,
                event_sink=passive_events,
                rng=rng,
            )
            _append_passive_events(events, passive_events)
        phase_attackers = _iter_phase_attackers(attacker_team, defender_team, rng, priority)
        for actor in phase_attackers:
            if actor.hp <= 0:
                continue
            # 先判定控制状态（眩晕/冻结等）
            if handle_pre_action_status(actor, events):
                continue
            passive_events = []
            run_passives_for_timing(
                "action_before",
                actor=actor,
                target=None,
                attacker_team=attacker_team,
                defender_team=defender_team,
                round_no=next_round_no,
                event_sink=passive_events,
                rng=rng,
            )
            _append_passive_events(events, passive_events)
            # 未被控制的单位，在行动前尝试触发五气朝元（拳类武艺科技）
            _append_heal_event(events, try_trigger_arena_coop_pre_action_heal(actor))
            _append_heal_event(events, try_trigger_battle_heal_on_action(actor, rng))
            _append_attack_event(
                events,
                perform_attack(actor, attacker_team, defender_team, rng, round_priority=priority),
                priority,
            )
            passive_events = []
            run_passives_for_timing(
                "action_end",
                actor=actor,
                target=None,
                attacker_team=attacker_team,
                defender_team=defender_team,
                round_no=next_round_no,
                event_sink=passive_events,
                rng=rng,
            )
            _append_passive_events(events, passive_events)
        _append_waiting_units(events, attacker_team, defender_team, priority)
        rounds.append({"round": next_round_no, "events": events, "priority": priority})
        next_round_no += 1
        if not alive(attacker_team) or not alive(defender_team):
            break
    return rounds, next_round_no


def simulate_battle(
    attacker_units: List["Combatant"],
    defender_units: List["Combatant"],
    rng: random.Random,
    seed: int,
    travel_seconds: int | None,
    config: dict,
    drop_table: Dict[str, Any] | None = None,
    max_rounds: int | None = None,
) -> "BattleSimulationResult":
    from ..combatants_pkg.core import BattleSimulationResult
    from ..constants import MAX_ROUNDS
    from ..passives import run_passives_for_timing

    if max_rounds is None:
        max_rounds = int(config.get("max_rounds", MAX_ROUNDS))

    # 安全修复：验证回合数范围，防止负数和过大值导致异常
    max_rounds = max(1, min(max_rounds, MAX_ROUNDS * 2))
    rounds: List[Dict[str, Any]] = []
    battle_start_events: List[Dict[str, Any]] = []
    for unit in alive(attacker_units) + alive(defender_units):
        passive_events: List[Dict[str, Any]] = []
        run_passives_for_timing(
            "battle_start",
            actor=unit,
            target=None,
            attacker_team=attacker_units,
            defender_team=defender_units,
            round_no=1,
            event_sink=passive_events,
            rng=rng,
        )
        _append_passive_events(battle_start_events, passive_events)
    priority_rounds, next_round_start = resolve_priority_phases(attacker_units, defender_units, rng)
    rounds.extend(priority_rounds)
    round_no = next_round_start
    remaining_rounds = max(0, max_rounds - len(priority_rounds))
    while remaining_rounds > 0 and alive(attacker_units) and alive(defender_units):
        rounds.append(_resolve_standard_round(attacker_units, defender_units, rng, round_no))
        round_no += 1
        remaining_rounds -= 1
    if battle_start_events:
        if not rounds:
            rounds.append({"round": 1, "events": []})
        first_round_events = list(battle_start_events) + list(rounds[0].get("events") or [])
        _normalize_event_orders(first_round_events)
        rounds[0]["events"] = first_round_events

    winner = _resolve_winner(attacker_units, defender_units)
    starts_at, completed_at = _build_result_timestamps(travel_seconds)

    losses = summarize_losses(attacker_units, defender_units, winner, rng)
    drops = _resolve_drops(winner, drop_table, config, rng)

    return BattleSimulationResult(
        rounds=rounds,
        winner=winner,
        losses=losses,
        drops=drops,
        seed=seed,
        starts_at=starts_at,
        completed_at=completed_at,
    )
