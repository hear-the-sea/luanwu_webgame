"""城门普通回合限制独立于远程、先锋阶段，普通战斗保持原规则。"""

import random

from battle.combatants_pkg.core import Combatant
from battle.simulation.battle_flow import simulate_battle


def simulate(config):
    archer = Combatant(
        name="弓手",
        attack=1,
        defense=100000,
        hp=100000,
        max_hp=100000,
        side="attacker",
        priority=-2,
        luck=0,
        agility=0,
        rarity="black",
        kind="guest",
        troop_strength=1,
    )
    gate = Combatant(
        name="城门",
        attack=0,
        defense=0,
        hp=1500000,
        max_hp=1500000,
        side="defender",
        priority=0,
        luck=0,
        agility=0,
        kind="city_defense",
        rarity="city_defense",
        troop_strength=1,
        initial_troop_strength=1,
        initial_hp=1500000,
        unit_hp=1500000,
        template_key="wall",
        battle_modifiers={"skip_turn": True},
    )
    return simulate_battle(
        attacker_units=[archer], defender_units=[gate], rng=random.Random(7), seed=7, travel_seconds=0, config=config
    )


def test_gate_has_eight_normal_rounds_in_addition_to_both_priority_rounds():
    result = simulate({"max_rounds": 24, "normal_round_limit": 8})
    assert [row["priority"] for row in result.rounds if row.get("priority", 0) < 0] == [-2, -1]
    assert len([row for row in result.rounds if row.get("priority", 0) >= 0]) == 8
    assert len(result.rounds) == 10


def test_existing_total_round_limit_remains_unchanged_for_other_battles():
    result = simulate({"max_rounds": 8})
    assert len(result.rounds) == 8
    assert len([row for row in result.rounds if row.get("priority", 0) >= 0]) == 6
