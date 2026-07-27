from __future__ import annotations

import random

from battle.combatants_pkg.core import Combatant
from battle.simulation.damage_application import apply_damage_results


def _make_guest(*, name: str, side: str, hp: int = 1000, attack: int = 300) -> Combatant:
    return Combatant(
        name=name,
        attack=attack,
        defense=100,
        hp=hp,
        max_hp=hp,
        side=side,
        rarity="purple",
        luck=50,
        agility=100,
        priority=0,
        kind="guest",
        troop_strength=0,
        initial_hp=hp,
    )


def _make_troop(*, name: str, side: str, count: int = 10, unit_hp: int = 100) -> Combatant:
    hp = count * unit_hp
    return Combatant(
        name=name,
        attack=400,
        defense=100,
        hp=hp,
        max_hp=hp,
        side=side,
        rarity="troop",
        luck=30,
        agility=80,
        priority=0,
        kind="troop",
        troop_strength=count,
        initial_troop_strength=count,
        initial_hp=hp,
        unit_attack=40,
        unit_defense=10,
        unit_hp=unit_hp,
    )


def test_fragmented_damage_matches_single_equal_total_for_troops():
    actor = _make_guest(name="攻击者", side="attacker")
    fragmented = _make_troop(name="零碎受击", side="defender")
    single = _make_troop(name="单次受击", side="defender")

    fragmented_kills = 0
    last_application = None
    for _ in range(17):
        last_application = apply_damage_results(actor, fragmented, 60, random.Random(1))
        fragmented_kills += last_application.kills

    single_application = apply_damage_results(actor, single, 17 * 60, random.Random(1))

    assert (fragmented.hp, fragmented.troop_strength) == (single.hp, single.troop_strength) == (0, 0)
    assert fragmented_kills == single_application.kills == 10
    assert last_application is not None
    assert last_application.target.applied_damage == 40
    assert last_application.target.overkill_damage == 20


def test_damage_application_clamps_overkill_and_is_idempotent_after_defeat():
    actor = _make_guest(name="攻击者", side="attacker")
    target = _make_guest(name="目标", side="defender", hp=1)

    first = apply_damage_results(actor, target, 10_000, random.Random(1))
    second = apply_damage_results(actor, target, 10_000, random.Random(1))

    assert target.hp == 0
    assert first.target.raw_damage == 10_000
    assert first.target.applied_damage == 1
    assert first.target.overkill_damage == 9_999
    assert first.kills == 1
    assert second.target.applied_damage == 0
    assert second.target.overkill_damage == 10_000
    assert second.kills == 0


def test_reflect_and_counter_skip_slaughter_but_preserve_troop_hp_strength_invariant():
    actor = _make_troop(name="进攻护院", side="attacker")
    target = _make_guest(name="反制者", side="defender", attack=400)
    target.troop_class = "jian"
    target.tech_effects = {
        "damage_reflect": 0.5,
        "counter_attack_chance": 1.0,
        "counter_attack_damage": 0.5,
    }

    application = apply_damage_results(actor, target, 100, random.Random(1))

    assert actor.hp == 750
    assert actor.troop_strength == 8
    assert application.reflect.applied_damage == 50
    assert application.counter.applied_damage == 200
    assert application.reflect.strength_after == 10
    assert application.counter.strength_before == 10
    assert application.counter.strength_after == 8
    assert application.reflect.kills + application.counter.kills == 2


def test_every_damage_transition_keeps_hp_and_strength_within_bounds():
    actor = _make_guest(name="攻击者", side="attacker")
    target = _make_troop(name="目标护院", side="defender", count=10, unit_hp=100)

    for damage in (0, 1, 99, 100, 101, 317, 10_000):
        apply_damage_results(actor, target, damage, random.Random(damage))
        assert 0 <= target.hp <= target.max_hp
        expected_strength = 0 if target.hp == 0 else min(10, (target.hp + target.unit_hp - 1) // target.unit_hp)
        assert target.troop_strength == expected_strength
