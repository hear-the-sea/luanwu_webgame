from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any


def _iter_nested_damage_events(events: Iterable[object]) -> Iterator[Mapping[str, Any]]:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        yield event
        additional_targets = event.get("additional_targets")
        if isinstance(additional_targets, list):
            yield from _iter_nested_damage_events(additional_targets)
        additional_attacks = event.get("additional_attacks")
        if isinstance(additional_attacks, list):
            yield from _iter_nested_damage_events(additional_attacks)


def iter_damage_events(rounds: Iterable[object] | None) -> Iterator[Mapping[str, Any]]:
    """Yield all attack hits, including secondary targets and troop attacks."""

    for battle_round in rounds or []:
        if not isinstance(battle_round, Mapping):
            continue
        events = battle_round.get("events")
        if isinstance(events, list):
            yield from _iter_nested_damage_events(events)


def troop_phase_skill(attack: object, action: object = None, round_priority: object = None) -> str:
    """Identify troop technology attacks; follow-up hits inherit the action phase."""
    if not isinstance(attack, Mapping) or attack.get("current_target_bonus"):
        return ""
    action = action if isinstance(action, Mapping) else attack
    if attack.get("kind", action.get("kind")) != "troop":
        return ""
    phase = attack.get("priority_phase", action.get("priority_phase", round_priority))
    priority = attack.get("priority", action.get("priority"))
    # Bow technology grants priority -2, while sword technology grants -1.
    # Units retain priority in ordinary rounds, which are not technology phases.
    if priority == -2 and phase in (-2, -1):
        return "凤舞九天"
    if priority == -1 and phase == -1:
        return "驭剑之术"
    return ""


def report_event_side(event: object, action: object = None, *, target: bool = False) -> str:
    """Resolve battle sides from immutable event snapshots, including older logs."""
    event = event if isinstance(event, Mapping) else {}
    action = action if isinstance(action, Mapping) else {}
    if target:
        state = event.get("target_state")
        if isinstance(state, Mapping) and state.get("side") in ("attacker", "defender"):
            return str(state["side"])
    for source in (event, action):
        side = source.get("side")
        for state_key in ("actor_state", "unit_state"):
            state = source.get(state_key)
            if side not in ("attacker", "defender") and isinstance(state, Mapping):
                side = state.get("side")
        if side in ("attacker", "defender"):
            if target:
                return "defender" if side == "attacker" else "attacker"
            return str(side)
    return ""
