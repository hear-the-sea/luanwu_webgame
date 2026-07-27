from __future__ import annotations

import random

import pytest

from battle.modifier_lifecycle import clear_action_modifiers, resolve_modifier_scope
from battle.passives import run_passives_for_timing
from battle.status_manager import prepare_combatants_for_round
from tests.battle_passives.support import make_unit


def _passive_skill(*, timing: str, effect: dict) -> dict:
    return {
        "key": f"{timing}_modifier",
        "name": "生命周期测试",
        "kind": "passive",
        "passive_config": {"triggers": [{"timing": timing, "effects": [effect]}]},
    }


def _run_timing(actor, timing: str) -> None:
    run_passives_for_timing(
        timing,
        actor=actor,
        target=None,
        attacker_team=[actor],
        defender_team=[],
        round_no=1,
        event_sink=[],
        rng=random.Random(1),
    )


def test_modifier_scope_is_inferred_only_when_not_provided():
    assert (
        resolve_modifier_scope(
            timing="battle_start",
            effect_type="modify_outgoing_damage",
        )
        == "battle"
    )
    assert (
        resolve_modifier_scope(
            timing="hit_taken",
            effect_type="modify_outgoing_damage",
            explicit_scope="round",
        )
        == "round"
    )


@pytest.mark.parametrize("explicit_scope", ["", "   ", False, 0, [], {}])
def test_modifier_scope_rejects_explicit_invalid_values(explicit_scope):
    with pytest.raises(AssertionError, match="invalid passive modifier scope"):
        resolve_modifier_scope(
            timing="battle_start",
            effect_type="modify_outgoing_damage",
            explicit_scope=explicit_scope,
        )


def test_battle_start_modifier_survives_round_cleanup():
    actor = make_unit(
        battle_modifiers={},
        battle_state={},
        skills=[_passive_skill(timing="battle_start", effect={"type": "modify_outgoing_damage", "value": 1.5})],
    )

    _run_timing(actor, "battle_start")
    prepare_combatants_for_round([actor], [], 1)
    prepare_combatants_for_round([actor], [], 2)

    assert actor.battle_modifiers["outgoing_damage_multiplier"] == 1.5


def test_round_modifier_expires_before_next_round():
    actor = make_unit(
        battle_modifiers={},
        battle_state={},
        skills=[_passive_skill(timing="round_start", effect={"type": "modify_outgoing_damage", "value": 1.5})],
    )

    prepare_combatants_for_round([actor], [], 1)
    _run_timing(actor, "round_start")
    assert actor.battle_modifiers["outgoing_damage_multiplier"] == 1.5

    prepare_combatants_for_round([actor], [], 2)
    assert actor.battle_modifiers == {}


def test_action_modifier_expires_after_action_cleanup():
    actor = make_unit(
        battle_modifiers={},
        battle_state={},
        skills=[_passive_skill(timing="attack_before", effect={"type": "modify_outgoing_damage", "value": 1.5})],
    )

    _run_timing(actor, "attack_before")
    assert actor.battle_modifiers["outgoing_damage_multiplier"] == 1.5

    clear_action_modifiers([actor])
    assert actor.battle_modifiers == {}


def test_hit_taken_modifier_can_explicitly_live_for_the_round():
    actor = make_unit(
        battle_modifiers={},
        battle_state={},
        skills=[
            _passive_skill(
                timing="hit_taken",
                effect={"type": "set_reflect", "ratio": 0.1, "cap": 8000, "scope": "round"},
            )
        ],
    )

    _run_timing(actor, "hit_taken")
    clear_action_modifiers([actor])
    assert actor.battle_modifiers["reflect_ratio"] == 0.1

    prepare_combatants_for_round([actor], [], 2)
    assert actor.battle_modifiers == {}


def test_same_skill_source_can_coexist_across_battle_and_round_scopes():
    actor = make_unit(
        battle_modifiers={},
        battle_state={},
        skills=[
            {
                "key": "shared_scope_skill",
                "name": "跨域气势",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "battle_start",
                            "effects": [{"type": "modify_outgoing_damage", "value": 2.0}],
                        },
                        {
                            "timing": "round_start",
                            "effects": [{"type": "modify_outgoing_damage", "value": 1.5}],
                        },
                    ]
                },
            }
        ],
    )

    _run_timing(actor, "battle_start")
    _run_timing(actor, "round_start")
    assert actor.battle_modifiers["outgoing_damage_multiplier"] == 3.0

    prepare_combatants_for_round([actor], [], 2)
    assert actor.battle_modifiers["outgoing_damage_multiplier"] == 2.0


def test_action_scope_cleanup_removes_modifiers_from_all_affected_allies():
    actor = make_unit(battle_modifiers={}, battle_state={})
    ally = make_unit(side=actor.side, battle_modifiers={}, battle_state={})
    actor.skills = [
        _passive_skill(
            timing="action_before",
            effect={"type": "modify_outgoing_damage", "value": 1.2, "target_scope": "allies"},
        )
    ]

    run_passives_for_timing(
        "action_before",
        actor=actor,
        target=None,
        attacker_team=[actor, ally],
        defender_team=[],
        round_no=1,
        event_sink=[],
        rng=random.Random(1),
    )
    assert actor.battle_modifiers["outgoing_damage_multiplier"] == 1.2
    assert ally.battle_modifiers["outgoing_damage_multiplier"] == 1.2

    clear_action_modifiers([actor, ally])
    assert actor.battle_modifiers == {}
    assert ally.battle_modifiers == {}


def test_reflect_projection_uses_last_write_even_when_source_repeats():
    actor = make_unit(battle_modifiers={}, battle_state={})
    context = {
        "actor": actor,
        "target": None,
        "attacker_team": [actor],
        "defender_team": [],
        "event_sink": [],
        "timing": "round_start",
    }

    from battle.passive_effects import apply_effect

    apply_effect({"type": "set_reflect", "ratio": 0.1, "cap": 100}, {**context, "skill_key": "a"})
    apply_effect({"type": "set_reflect", "ratio": 0.2, "cap": 200}, {**context, "skill_key": "b"})
    apply_effect({"type": "set_reflect", "ratio": 0.3, "cap": 300}, {**context, "skill_key": "a"})

    assert actor.battle_modifiers["reflect_ratio"] == 0.3
    assert actor.battle_modifiers["reflect_cap"] == 300
