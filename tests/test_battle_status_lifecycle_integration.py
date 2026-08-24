from __future__ import annotations

import random

from battle.simulation.battle_flow import _resolve_standard_round, resolve_priority_phases
from battle.utils.status_effects import apply_status_effect, get_damage_penalty
from tests.battle_passives.support import make_unit


def _mark_action_complete(actor) -> None:
    actor.has_acted_this_round = True
    actor.last_round_acted = actor.current_round


def test_standard_round_consumes_status_inflicted_before_target_action(monkeypatch):
    inflictor = make_unit(name="施术者", side="attacker")
    target = make_unit(name="目标", side="defender")
    observed_penalties: list[float] = []

    monkeypatch.setattr(
        "battle.simulation.battle_flow.determine_turn_order",
        lambda *_args, **_kwargs: [inflictor, target],
    )

    def _perform_attack(actor, *_args, **_kwargs):
        if actor is inflictor:
            apply_status_effect(target, "weakened", 1, defer=target.has_acted_this_round)
        else:
            observed_penalties.append(get_damage_penalty(target))
        _mark_action_complete(actor)
        return None

    monkeypatch.setattr("battle.simulation.battle_flow.perform_attack", _perform_attack)

    _resolve_standard_round([inflictor], [target], random.Random(1), round_no=1)

    assert observed_penalties == [0.3]
    assert "weakened" not in target.status_effects


def test_standard_round_defers_status_inflicted_after_target_action_until_next_round(monkeypatch):
    inflictor = make_unit(name="施术者", side="attacker")
    target = make_unit(name="目标", side="defender")
    observed_penalties: list[float] = []
    status_inflicted = False

    monkeypatch.setattr(
        "battle.simulation.battle_flow.determine_turn_order",
        lambda *_args, **_kwargs: [target, inflictor],
    )

    def _perform_attack(actor, *_args, **_kwargs):
        nonlocal status_inflicted
        if actor is target:
            observed_penalties.append(get_damage_penalty(target))
        elif not status_inflicted:
            apply_status_effect(target, "weakened", 1, defer=target.has_acted_this_round)
            status_inflicted = True
        _mark_action_complete(actor)
        return None

    monkeypatch.setattr("battle.simulation.battle_flow.perform_attack", _perform_attack)

    _resolve_standard_round([inflictor], [target], random.Random(1), round_no=1)

    assert observed_penalties == [0.0]
    assert target.status_effects["weakened"] == {"active": 0, "pending": 1}

    _resolve_standard_round([inflictor], [target], random.Random(2), round_no=2)

    assert observed_penalties == [0.0, 0.3]
    assert "weakened" not in target.status_effects


def test_priority_unit_consumes_duration_across_each_action_opportunity(monkeypatch):
    fast_actor = make_unit(
        name="先攻单位",
        side="attacker",
        priority=-2,
        status_effects={"weakened": {"active": 2, "pending": 0}},
    )
    defender = make_unit(name="防守单位", side="defender", priority=0)
    observed_penalties: list[float] = []

    monkeypatch.setattr(
        "battle.simulation.battle_flow.determine_turn_order",
        lambda *_args, **_kwargs: [fast_actor, defender],
    )

    def _perform_attack(actor, *_args, **_kwargs):
        observed_penalties.append(get_damage_penalty(actor))
        _mark_action_complete(actor)
        return None

    monkeypatch.setattr("battle.simulation.battle_flow.perform_attack", _perform_attack)

    rounds, next_round_no = resolve_priority_phases([fast_actor], [defender], random.Random(3))

    assert [battle_round["priority"] for battle_round in rounds] == [-2, -1]
    assert next_round_no == 3
    assert observed_penalties == [0.3, 0.3]
    assert "weakened" not in fast_actor.status_effects


def test_next_round_lineup_snapshot_reflects_previous_round_losses(monkeypatch):
    attacker = make_unit(name="进攻门客", side="attacker", template_key="attacker_guest")
    defender_guest = make_unit(name="防守门客", side="defender", template_key="defender_guest")
    defender_troop = make_unit(
        name="刀圣",
        side="defender",
        kind="troop",
        template_key="dao_sheng",
        hp=5000,
        max_hp=5000,
        troop_strength=500,
        initial_troop_strength=500,
    )

    monkeypatch.setattr(
        "battle.simulation.battle_flow.determine_turn_order",
        lambda *_args, **_kwargs: [attacker],
    )

    def _perform_attack(*_args, **_kwargs):
        defender_guest.hp = 0
        defender_troop.troop_strength = 450
        return None

    monkeypatch.setattr("battle.simulation.battle_flow.perform_attack", _perform_attack)

    first_round = _resolve_standard_round(
        [attacker],
        [defender_guest, defender_troop],
        random.Random(4),
        round_no=1,
    )
    second_round = _resolve_standard_round(
        [attacker],
        [defender_guest, defender_troop],
        random.Random(5),
        round_no=2,
    )

    assert [guest["name"] for guest in first_round["lineups"]["defender"]["guests"]] == ["防守门客"]
    assert first_round["lineups"]["defender"]["troops"][0]["count"] == 500
    assert second_round["lineups"]["defender"]["guests"] == []
    assert second_round["lineups"]["defender"]["troops"][0]["count"] == 450
