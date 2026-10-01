from __future__ import annotations

import random
from copy import deepcopy
from types import SimpleNamespace

import pytest

from battle.combatants_pkg.core import Combatant
from battle.random_context import PREVIOUS_BATTLE_ENGINE_VERSION
from battle.simulation.attack_execution import perform_attack
from battle_debugger.simulator import patch_battle_params


class FixedRng(random.Random):
    def choice(self, sequence):
        return sequence[0]

    def uniform(self, _minimum, _maximum):
        return 1.0

    def random(self):
        return 0.99


def _combatants(count: int) -> tuple[Combatant, Combatant]:
    actor = Combatant(
        name="箭神",
        attack=count * 10,
        defense=0,
        hp=count * 10,
        max_hp=count * 10,
        side="attacker",
        kind="troop",
        troop_class="gong",
        troop_strength=count,
        initial_troop_strength=count,
        unit_attack=10,
        unit_defense=1,
        unit_hp=10,
        agility=100,
        luck=30,
        priority=0,
        rarity="troop",
    )
    target = Combatant(
        name="目标门客",
        attack=1,
        defense=0,
        hp=1_000_000,
        max_hp=1_000_000,
        side="defender",
        kind="guest",
        troop_strength=0,
        agility=1,
        luck=30,
        priority=0,
        rarity="guest",
    )
    return actor, target


@pytest.mark.parametrize("target_kind,expected", [("guest", 2500), ("troop", 1000), ("city_defense", 2500)])
def test_debugger_attack_override_uses_requested_group_strength_and_target_divisor(target_kind, expected):
    import battle.combat_math as combat_math

    actor, _target = _combatants(1000)
    target = SimpleNamespace(kind=target_kind)
    original_attack = combat_math.effective_attack_value
    with patch_battle_params({"troop_attack_divisor_vs_guest": 2.0, "troop_attack_divisor_vs_troop": 5.0}):
        assert combat_math.effective_attack_value(actor, target, troop_attack_strength=500) == expected
        assert combat_math.effective_attack_value(actor, target) == expected * 2
    assert combat_math.effective_attack_value is original_attack


@pytest.mark.parametrize("count,expected_damage", [(999, [4995]), (1000, [3000, 3000]), (1501, [4506, 4500])])
def test_debugger_attack_override_runs_real_split_attacks(count, expected_damage):
    actor, target = _combatants(count)

    with patch_battle_params({"troop_attack_divisor_vs_guest": 2.0}):
        event = perform_attack(actor, [actor], [target], FixedRng())

    assert event is not None
    assert [attack["damage"] for attack in [event, *event.get("additional_attacks", [])]] == expected_damage


def test_debugger_attack_override_preserves_previous_engine_single_attack():
    actor, target = _combatants(1000)
    with patch_battle_params({"troop_attack_divisor_vs_guest": 2.0}):
        event = perform_attack(
            actor, [actor], [target], FixedRng(), battle_engine_version=PREVIOUS_BATTLE_ENGINE_VERSION
        )

    assert event is not None
    assert event["damage"] == 5000
    assert "additional_attacks" not in event


def test_debugger_attack_override_allows_guest_attack_and_preserves_fractional_value():
    import battle.combat_math as combat_math

    actor, target = _combatants(1)
    actor.kind = "guest"
    actor.attack = 123.5
    baseline_actor, baseline_target = deepcopy(actor), deepcopy(target)
    baseline = perform_attack(baseline_actor, [baseline_actor], [baseline_target], FixedRng())
    with patch_battle_params({"troop_attack_divisor_vs_guest": 2.0}):
        assert combat_math.effective_attack_value(actor, target, troop_attack_strength=None) == 123.5
        event = perform_attack(actor, [actor], [target], FixedRng())

    assert event is not None
    assert baseline is not None
    assert event["damage"] == baseline["damage"]
