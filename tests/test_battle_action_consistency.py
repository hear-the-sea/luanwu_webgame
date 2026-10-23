from __future__ import annotations

import pytest

from battle.simulation.damage_calculation import calculate_attack_damage
from tests.test_guest_burst_attack import DirectedRng, attack, burst_skill, passive, unit


class CountingRng(DirectedRng):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.random_calls = 0

    def random(self):
        self.random_calls += 1
        return super().random()


def burst_damage(skill, *, version="8", crit=True, target_hp=1000):
    actor = unit("门客", "attacker")
    enemy = unit(hp=target_hp)
    rng = CountingRng(rolls=[0.0 if crit else 0.99])
    result = calculate_attack_damage(
        actor,
        enemy,
        [skill],
        rng,
        round_priority=0,
        damage_component="burst",
        battle_engine_version=version,
    )
    return result, rng.random_calls


@pytest.mark.parametrize(
    "overrides,expected_damage,expected_crit",
    [
        ({}, 200, False),
        ({"damage_multiplier": 2.0}, 4900, True),
        ({"execute_hp_ratio_lt": 0.5, "execute_multiplier": 1.5}, 2550, True),
        ({"execute_hp_ratio_lt": 0.05, "execute_multiplier": 1.5}, 200, False),
        (
            {"damage_multiplier": 2.0, "execute_hp_ratio_lt": 0.5, "execute_multiplier": 0.5},
            200,
            False,
        ),
        (
            {"damage_multiplier": 0.5, "execute_hp_ratio_lt": 0.5, "execute_multiplier": 2.0},
            200,
            False,
        ),
        ({"damage_multiplier": 0.0}, 0, False),
        ({"damage_formula": {"base": -10000}, "damage_multiplier": 2.0}, 0, False),
    ],
)
def test_burst_only_marks_critical_when_crit_increases_positive_skill_increment(
    overrides, expected_damage, expected_crit
):
    result, calls = burst_damage(burst_skill(**overrides))

    assert result.damage == expected_damage
    assert result.is_crit is expected_crit
    assert calls == 1


def test_burst_multiplier_without_crit_roll_does_not_claim_critical_hit():
    result, calls = burst_damage(burst_skill(damage_multiplier=2.0), crit=False)

    assert result.damage == 3400
    assert not result.is_crit
    assert calls == 1


@pytest.mark.parametrize("version", ["6", "7"])
def test_legacy_burst_keeps_old_critical_flag_but_same_damage_and_random_consumption(version):
    historical, historical_calls = burst_damage(burst_skill(), version=version)
    current, current_calls = burst_damage(burst_skill())

    assert historical.damage == current.damage == 200
    assert historical.is_crit
    assert not current.is_crit
    assert historical_calls == current_calls == 1


def healing_passive(*timings):
    return passive(
        *[
            {
                "timing": timing,
                "effects": [{"type": "heal_ratio", "value": 0.1, "max_hp_based": True}],
            }
            for timing in timings
        ]
    )


@pytest.mark.parametrize(
    "multiplier,modifiers,expected_damage",
    [
        (1.000001, {}, 200),
        (2.0, {"incoming_damage_multiplier": 0.0000001}, 0),
        (2.0, {"burst_softcap_sources": {"test": {"threshold": 100, "overflow_ratio": 0.00001}}}, 100),
    ],
)
def test_rounding_and_softcap_do_not_claim_a_crit_without_actual_extra_damage(multiplier, modifiers, expected_damage):
    actor = unit("门客", "attacker")
    enemy = unit(battle_modifiers=modifiers)
    result = calculate_attack_damage(
        actor,
        enemy,
        [burst_skill(damage_multiplier=multiplier)],
        CountingRng(rolls=[0.0]),
        round_priority=0,
        damage_component="burst",
        battle_engine_version="8",
    )
    assert result.damage == expected_damage
    assert not result.is_crit


@pytest.mark.parametrize("triggered", [False, True])
def test_actor_attack_passives_run_once_even_with_skill_and_pending_extra_hit(triggered):
    actor = unit(
        "门客",
        "attacker",
        hp=100,
        max_hp=1000,
        skills=[burst_skill(trigger_chance=int(triggered)), healing_passive("attack_before", "attack_after")],
        battle_state={"_pending_extra_target_true_damage": [{"value": 0.1}]},
    )

    event = attack(actor, [unit(), unit("乙")], version="8")

    assert actor.hp == 300
    assert "_pending_extra_target_true_damage" not in actor.battle_state
    assert event["attack_phase"] == ("burst" if triggered else "normal")
    normal = event["additional_attacks"][0] if triggered else event
    assert normal["additional_targets"][0]["damage"] == 100


@pytest.mark.parametrize("version", ["6", "7"])
def test_legacy_untriggered_skill_keeps_attack_passives_on_each_extra_hit(version):
    actor = unit(
        "门客",
        "attacker",
        hp=100,
        max_hp=1000,
        skills=[burst_skill(trigger_chance=0), healing_passive("attack_before", "attack_after")],
        battle_state={"_pending_extra_target_true_damage": [{"value": 0.1}]},
    )

    attack(actor, [unit(), unit("乙")], version=version)

    assert actor.hp == 500


@pytest.mark.parametrize("triggered", [False, True])
def test_ordinary_attack_after_heal_does_not_revive_actor_defeated_by_reflect(triggered):
    actor = unit(
        "门客",
        "attacker",
        hp=10,
        max_hp=1000,
        skills=[burst_skill(trigger_chance=int(triggered)), healing_passive("attack_after")],
    )
    mirror = unit(troop_class="jian", tech_effects={"damage_reflect": 1.0})

    event = attack(actor, [mirror], version="8")

    assert actor.hp == 0
    assert event["actor_defeated"]
    assert event["actor_state"]["current"] == 0
    assert not event.get("additional_attacks")


@pytest.mark.parametrize("version", ["6", "7"])
def test_legacy_attack_after_heal_keeps_historical_post_reflect_behavior(version):
    actor = unit(
        "门客",
        "attacker",
        hp=10,
        max_hp=1000,
        skills=[burst_skill(), healing_passive("attack_after")],
    )
    mirror = unit(troop_class="jian", tech_effects={"damage_reflect": 1.0})

    event = attack(actor, [mirror], version=version)

    assert actor.hp == 100
    assert event["actor_defeated"]
