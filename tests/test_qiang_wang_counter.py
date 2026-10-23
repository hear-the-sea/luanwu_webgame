from __future__ import annotations

import pytest

from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION
from battle.simulation.attack_execution import perform_attack
from tests.test_guest_burst_attack import DirectedRng, burst_skill, unit


def spear(name="枪王", count=1000, **overrides):
    values = dict(
        kind="troop",
        template_key="qiang_wang",
        troop_class="qiang",
        troop_strength=count,
        initial_troop_strength=count,
        attack=count * 10,
        defense=count,
        hp=count * 1000,
        max_hp=count * 1000,
        initial_hp=count * 1000,
        unit_attack=10,
        unit_defense=1,
        unit_hp=1000,
        tech_effects={"counter_attack_chance": 1.0, "counter_attack_damage": 0.3},
    )
    values.update(overrides)
    return unit(name, **values)


def hits(event):
    yield event
    for nested in event.get("additional_targets", []):
        yield from hits(nested)
    for nested in event.get("additional_attacks", []):
        yield from hits(nested)


def reactions(event):
    return [hit for hit in hits(event) if hit.get("reaction_type") == "counter"]


def fire(actor, enemies, *, rng=None, priority=0, version=CURRENT_BATTLE_ENGINE_VERSION):
    return perform_attack(actor, [actor], enemies, rng or DirectedRng(), priority, battle_engine_version=version)


def test_burst_and_normal_on_same_spear_only_counter_after_entire_action():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    enemy = spear()

    event = fire(actor, [enemy])

    (counter,) = reactions(event)
    assert counter is event["additional_attacks"][-1]
    assert event["attack_phase"] == "burst"
    assert event["additional_attacks"][0]["attack_phase"] == "normal"
    assert all(not hit.get("counter_damage") for hit in hits(event))
    assert counter["actor"] == enemy.name
    assert counter["target"] == actor.name
    assert counter["damage"] == int(min(1000, enemy.troop_strength) * enemy.unit_attack * 0.3)
    assert counter["applied_damage"] == counter["damage"]
    assert actor.hp == 10000 - counter["damage"]


def test_spear_hit_by_burst_still_counters_when_normal_reselects_other_target():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    enemy, other = spear(), unit("乙")

    event = fire(actor, [enemy, other], rng=DirectedRng(targets=["枪王", "乙"]))

    assert event["target"] == enemy.name
    assert event["additional_attacks"][0]["target"] == other.name
    (counter,) = reactions(event)
    assert counter["actor"] == enemy.name
    assert counter["target"] == actor.name
    assert counter is event["additional_attacks"][-1]


def test_two_same_named_spears_have_independent_counter_opportunities():
    actor = unit("门客", "attacker", skills=[burst_skill(targets=2)], hp=100000, max_hp=100000)
    first, second = spear(), spear()

    event = fire(actor, [first, second])

    assert len(reactions(event)) == 2
    assert all(counter["actor"] == "枪王" for counter in reactions(event))
    assert actor.hp == 100000 - sum(counter["applied_damage"] for counter in reactions(event))


def test_two_same_named_attackers_each_allow_one_counter():
    first, second = unit("同名门客", "attacker"), unit("同名门客", "attacker")
    enemy = spear()

    first_event = fire(first, [enemy])
    second_event = fire(second, [enemy])

    assert len(reactions(first_event)) == len(reactions(second_event)) == 1
    assert first.hp < 10000
    assert second.hp < 10000


def test_counter_opportunity_resets_only_after_round_changes():
    actor, enemy = unit("门客", "attacker", hp=100000, max_hp=100000), spear()
    actor.current_round = enemy.current_round = 1

    first = fire(actor, [enemy])
    second = fire(actor, [enemy])
    actor.current_round = enemy.current_round = 2
    third = fire(actor, [enemy])

    assert len(reactions(first)) == 1
    assert not reactions(second)
    assert len(reactions(third)) == 1


def test_failed_probability_roll_also_consumes_pair_opportunity():
    actor = unit("门客", "attacker")
    enemy = spear(tech_effects={"counter_attack_chance": 0.5, "counter_attack_damage": 0.3})
    actor.current_round = enemy.current_round = 1

    first = fire(actor, [enemy])  # 定向 RNG 为 0.99，首次概率判定失败。
    enemy.tech_effects["counter_attack_chance"] = 1.0
    second = fire(actor, [enemy])
    actor.current_round = enemy.current_round = 2
    third = fire(actor, [enemy])

    assert not reactions(first)
    assert not reactions(second)
    assert len(reactions(third)) == 1


def test_troop_groups_finish_before_a_single_counter_uses_final_survivors():
    actor = unit(
        "刀圣",
        "attacker",
        kind="troop",
        troop_class="dao",
        troop_strength=2000,
        initial_troop_strength=2000,
        unit_attack=10,
        unit_defense=1,
        unit_hp=1000,
        attack=20000,
        defense=2000,
        hp=2000000,
        max_hp=2000000,
    )
    enemy = spear(count=2000)

    event = fire(actor, [enemy])

    outgoing = [hit for hit in hits(event) if not hit.get("reaction_type")]
    assert len(outgoing) == 3
    (counter,) = reactions(event)
    assert counter is event["additional_attacks"][-1]
    assert enemy.troop_strength > 1000
    assert counter["damage"] == 3000


def test_pending_extra_true_damage_can_trigger_deferred_spear_counter():
    actor = unit(
        "门客",
        "attacker",
        battle_state={"_pending_extra_target_true_damage": [{"value": 0.1, "log_name": "嗜血狂怒"}]},
    )
    other, enemy = unit("乙"), spear()

    event = fire(actor, [other, enemy], rng=DirectedRng(targets=["乙", "枪王"]))

    assert event["target"] == "乙"
    assert event["additional_targets"][0]["target"] == "枪王"
    assert len(reactions(event)) == 1


def test_spear_killed_by_later_normal_attack_cannot_counter():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    enemy = spear(count=1, hp=1000, max_hp=1000)

    event = fire(actor, [enemy])

    assert event["damage"] < 1000
    assert enemy.hp == 0
    assert not reactions(event)
    assert actor.hp == 10000


def test_actor_killed_by_later_reflect_does_not_receive_deferred_counter():
    actor = unit("门客", "attacker", hp=50, skills=[burst_skill()])
    enemy = spear()
    mirror = unit("剑客", troop_class="jian", tech_effects={"damage_reflect": 1.0})

    event = fire(actor, [enemy, mirror], rng=DirectedRng(targets=["枪王", "剑客"]))

    assert actor.hp == 0
    assert event["additional_attacks"][0]["reflect_applied_damage"] == 50
    assert not reactions(event)


def test_ranged_spear_hit_does_not_schedule_a_counter():
    actor = unit("箭神", "attacker", troop_class="gong")
    enemy = spear()

    event = fire(actor, [enemy], priority=-1)

    assert event["attack_type"] == "ranged"
    assert not reactions(event)
    assert actor.hp == 10000


def test_version_six_preserves_immediate_counter_on_both_guest_hits():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    enemy = spear()

    event = fire(actor, [enemy], version="6")

    assert not reactions(event)
    assert event["counter_damage"] > 0
    assert event["additional_attacks"][0]["counter_damage"] > 0


def test_deferred_counter_has_independent_source_and_capped_applied_damage():
    actor = unit("门客", "attacker", hp=100, skills=[burst_skill()])
    enemy = spear()

    event = fire(actor, [enemy])

    (counter,) = reactions(event)
    assert counter["actor_state"]["kind"] == "troop"
    assert counter["actor_state"]["side"] == enemy.side
    assert counter["target_state"]["kind"] == "guest"
    assert counter["target_state"]["side"] == actor.side
    assert counter["damage"] > counter["applied_damage"] == 100
    assert counter["target_hp_before"] == 100
    assert counter["target_hp_after"] == 0
    assert counter["overkill_damage"] == counter["damage"] - 100
    assert counter["target_defeated"]
    assert actor.hp == 0


def test_remaining_spears_do_not_counter_an_actor_already_defeated_by_first_counter():
    actor = unit("门客", "attacker", hp=100, skills=[burst_skill(targets=2)])

    event = fire(actor, [spear(), spear()])

    assert len(reactions(event)) == 1
    assert actor.hp == 0


@pytest.mark.parametrize("kind,template_key", [("troop", "qiang_ba"), ("guest", "qiang_wang")])
def test_other_spear_units_and_guest_counters_keep_original_timing(kind, template_key):
    actor = unit("门客", "attacker", skills=[burst_skill()])
    enemy = spear(kind=kind, template_key=template_key)

    event = fire(actor, [enemy])

    assert not reactions(event)
    assert event["counter_damage"] > 0
    assert event["additional_attacks"][0]["counter_damage"] > 0
