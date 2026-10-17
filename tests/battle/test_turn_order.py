from __future__ import annotations

import random

import pytest

from battle.combatants_pkg.core import Combatant
from battle.combatants_pkg.guest_builder import assign_agility_based_priorities
from battle.simulation.battle_flow import simulate_battle
from battle.simulation.turn_order import determine_turn_order


def make_combatant(name: str, side: str, agility: int) -> Combatant:
    return Combatant(
        name=name,
        attack=100,
        defense=50,
        hp=100,
        max_hp=100,
        side=side,
        rarity="green",
        luck=10,
        agility=agility,
        priority=0,
        kind="guest",
        troop_strength=1,
        initial_hp=100,
    )


class NoRandomRng:
    def uniform(self, _start, _end):
        raise AssertionError("turn order should not use random initiative bonuses")

    def random(self):
        raise AssertionError("turn order should not use random tiebreakers")


def test_determine_turn_order_ignores_rng_and_sorts_by_agility():
    attacker = make_combatant("甲", "attacker", 110)
    defender = make_combatant("乙", "defender", 140)

    ordered = determine_turn_order([attacker], [defender], NoRandomRng())

    assert [unit.name for unit in ordered] == ["乙", "甲"]


def test_determine_turn_order_prefers_defender_when_agility_matches():
    attacker = make_combatant("攻方", "attacker", 120)
    defender = make_combatant("守方", "defender", 120)

    ordered = determine_turn_order([attacker], [defender], NoRandomRng())

    assert [unit.name for unit in ordered] == ["守方", "攻方"]


def test_determine_turn_order_keeps_original_order_within_same_side_and_agility():
    attacker_first = make_combatant("攻一", "attacker", 120)
    attacker_second = make_combatant("攻二", "attacker", 120)
    defender_first = make_combatant("守一", "defender", 120)
    defender_second = make_combatant("守二", "defender", 120)

    ordered = determine_turn_order(
        [attacker_first, attacker_second],
        [defender_first, defender_second],
        NoRandomRng(),
    )

    assert [unit.name for unit in ordered] == ["守一", "守二", "攻一", "攻二"]


def test_assign_agility_based_priorities_prefers_defender_when_cutoff_ties():
    attacker = make_combatant("攻方", "attacker", 120)
    defender = make_combatant("守方", "defender", 120)

    assign_agility_based_priorities([attacker], [defender])

    assert defender.priority == -1
    assert attacker.priority == 0


@pytest.mark.parametrize(
    ("attacker_agilities", "defender_agilities", "expected_sides"),
    [
        ([100, 200], [140], ["attacker", "defender"]),
        ([140], [100, 200], ["defender", "attacker"]),
        ([100, 200], [150], ["defender", "attacker"]),
        ([100, 101], [100], ["attacker", "defender"]),
        ([], [], ["defender", "attacker"]),
        ([100], [], ["attacker", "defender"]),
        ([], [100], ["defender", "attacker"]),
    ],
)
def test_tied_troops_use_deployed_guest_average(attacker_agilities, defender_agilities, expected_sides):
    attacker = make_combatant("攻方护院", "attacker", 120)
    defender = make_combatant("守方护院", "defender", 120)
    attacker.kind = defender.kind = "troop"
    attacker_guests = [
        make_combatant(f"攻方门客{idx}", "attacker", agility) for idx, agility in enumerate(attacker_agilities)
    ]
    defender_guests = [
        make_combatant(f"守方门客{idx}", "defender", agility) for idx, agility in enumerate(defender_agilities)
    ]

    ordered = determine_turn_order([attacker, *attacker_guests], [defender, *defender_guests], NoRandomRng())

    assert [unit.side for unit in ordered if unit.kind == "troop"] == expected_sides


def test_guest_average_includes_dead_and_skipping_deployed_guests():
    attacker = make_combatant("攻方护院", "attacker", 120)
    defender = make_combatant("守方护院", "defender", 120)
    attacker.kind = defender.kind = "troop"
    dead_guest = make_combatant("阵亡门客", "attacker", 200)
    dead_guest.hp = 0
    skipping_guest = make_combatant("跳过行动门客", "attacker", 200)
    skipping_guest.battle_modifiers["skip_turn"] = True
    defender_guest = make_combatant("守方门客", "defender", 150)

    ordered = determine_turn_order([attacker, dead_guest, skipping_guest], [defender, defender_guest], NoRandomRng())

    assert [unit.name for unit in ordered] == ["攻方护院", "守方护院", "守方门客"]


def test_fixed_first_takes_precedence_over_guest_average():
    attacker = make_combatant("攻方护院", "attacker", 120)
    defender = make_combatant("守方护院", "defender", 120)
    attacker.kind = defender.kind = "troop"
    defender.battle_modifiers["fixed_first"] = True
    attacker_guest = make_combatant("攻方门客", "attacker", 200)

    ordered = determine_turn_order([attacker, attacker_guest], [defender], NoRandomRng())

    assert ordered[0] is defender


def test_guest_average_excludes_troops_and_does_not_override_unit_agility():
    attacker = make_combatant("攻方护院", "attacker", 120)
    defender = make_combatant("守方护院", "defender", 120)
    faster_troop = make_combatant("守方快护院", "defender", 1000)
    attacker.kind = defender.kind = faster_troop.kind = "troop"
    attacker_guest = make_combatant("攻方门客", "attacker", 200)
    defender_guest = make_combatant("守方门客", "defender", 150)

    ordered = determine_turn_order([attacker, attacker_guest], [defender, faster_troop, defender_guest], NoRandomRng())

    assert [unit.name for unit in ordered] == ["守方快护院", "攻方护院", "守方护院", "攻方门客", "守方门客"]


@pytest.mark.parametrize("version", ["legacy", "2", "3", "4"])
@pytest.mark.parametrize("priority", [-2, -1, 0])
def test_troops_precede_faster_guests_only_in_new_standard_rounds(version, priority):
    guest = make_combatant("高速门客", "attacker", 300)
    troop = make_combatant("低速护院", "defender", 80)
    troop.kind = "troop"

    ordered = determine_turn_order(
        [guest], [troop], NoRandomRng(), round_priority=priority, battle_engine_version=version
    )

    expected = [troop, guest] if version == "4" and priority == 0 else [guest, troop]
    assert ordered == expected


def test_fixed_first_precedes_troops_and_dead_or_skipping_units_are_excluded():
    troop = make_combatant("护院", "attacker", 100)
    troop.kind = "troop"
    guest = make_combatant("门客", "defender", 300)
    tower = make_combatant("箭塔", "defender", 0)
    tower.kind = "city_defense"
    tower.battle_modifiers["fixed_first"] = True
    dead = make_combatant("阵亡护院", "attacker", 1000)
    dead.kind = "troop"
    dead.hp = 0
    wall = make_combatant("城墙", "defender", 0)
    wall.kind = "city_defense"
    wall.battle_modifiers["skip_turn"] = True

    assert determine_turn_order([troop, dead], [guest, tower, wall], NoRandomRng()) == [tower, troop, guest]


@pytest.mark.parametrize("version", ["3", "4"])
def test_simulation_preserves_opening_order_and_versions_standard_round_order(version):
    guest = make_combatant("高速门客", "attacker", 300)
    troop = make_combatant("低速护院", "defender", 80)
    troop.kind = "troop"
    for unit in (guest, troop):
        unit.priority = -1
        unit.hp = unit.max_hp = unit.initial_hp = 100000
    troop.initial_troop_strength = 1
    troop.unit_attack = 100
    troop.unit_defense = 50
    troop.unit_hp = 100000

    result = simulate_battle(
        [guest],
        [troop],
        random.Random(7),
        seed=7,
        travel_seconds=0,
        config={"max_rounds": 2},
        battle_engine_version=version,
    )

    assert result.rounds[0]["priority"] == -1
    opening_attacks = [event["actor"] for event in result.rounds[0]["events"] if "damage" in event]
    standard_attacks = [event["actor"] for event in result.rounds[1]["events"] if "damage" in event]
    assert opening_attacks == [guest.name, troop.name]
    assert standard_attacks == ([troop.name, guest.name] if version == "4" else [guest.name, troop.name])


def test_guest_killed_by_troop_before_action_does_not_act():
    guest = make_combatant("高速门客", "attacker", 300)
    troop = make_combatant("低速护院", "defender", 80)
    troop.kind = "troop"
    troop.initial_troop_strength = 1
    troop.unit_attack = 1000000
    troop.unit_defense = 50
    troop.unit_hp = 100

    result = simulate_battle([guest], [troop], random.Random(7), seed=7, travel_seconds=0, config={"max_rounds": 1})

    assert result.winner == "defender"
    assert guest.hp == 0
    assert [event["actor"] for event in result.rounds[0]["events"] if "damage" in event] == [troop.name]
