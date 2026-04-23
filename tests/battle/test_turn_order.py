from __future__ import annotations

from battle.combatants_pkg.core import Combatant
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
        troop_strength=0,
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
