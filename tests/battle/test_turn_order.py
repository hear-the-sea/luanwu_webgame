from __future__ import annotations

import pytest

from battle.combatants_pkg.core import Combatant
from battle.combatants_pkg.guest_builder import assign_agility_based_priorities
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

    assert [unit.name for unit in ordered] == ["守方门客", "攻方护院", "守方护院"]


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

    assert [unit.name for unit in ordered] == ["守方快护院", "攻方门客", "守方门客", "攻方护院", "守方护院"]
