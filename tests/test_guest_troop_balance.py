from __future__ import annotations

import random

import pytest

from battle.combatants_pkg.core import Combatant
from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION, current_replay_metadata
from battle.simulation.attack_execution import perform_attack
from battle.simulation.damage_calculation import calculate_attack_damage
from battle.simulation.target_selection import select_target_with_priority
from battle_debugger.simulator import patch_battle_params


def unit(name="门客", kind="guest", side="defender", **overrides):
    values = dict(
        name=name,
        kind=kind,
        side=side,
        attack=1000,
        defense=100,
        hp=1_000_000,
        max_hp=1_000_000,
        initial_hp=1_000_000,
        rarity="purple",
        luck=0,
        agility=100,
        priority=0,
        troop_strength=20 if kind == "troop" else 0,
        initial_troop_strength=20 if kind == "troop" else 0,
        unit_attack=10,
        unit_defense=50,
        unit_hp=10,
    )
    values.update(overrides)
    return Combatant(**values)


class FixedRng(random.Random):
    def __init__(self, *values):
        super().__init__(0)
        self.values = iter(values)
        self.random_calls = 0

    def random(self):
        self.random_calls += 1
        return next(self.values, 0.99)

    def choice(self, sequence):
        return sequence[0]

    def shuffle(self, sequence):
        pass

    def uniform(self, _minimum, _maximum):
        return 1.0


@pytest.mark.parametrize("side", ["attacker", "defender"])
@pytest.mark.parametrize("roll,screened", [(0.0, True), (0.749999, True), (0.75, False), (0.99, False)])
def test_screening_threshold_applies_to_both_sides(side, roll, screened):
    opponent_side = "defender" if side == "attacker" else "attacker"
    actor = unit(kind="troop", side=side)
    guest = unit(side=opponent_side)
    guard = unit("护院", "troop", opponent_side)
    rng = FixedRng(roll)

    selected = select_target_with_priority(actor, [guest, guard], rng)

    assert selected is (guard if screened else guest)
    assert rng.random_calls == 1


@pytest.mark.parametrize("version", ["legacy", "2", "3", "4"])
def test_historical_selection_never_screens_or_consumes_extra_randomness(version):
    actor = unit(kind="troop", side="attacker")
    guest, guard = unit(), unit("护院", "troop")
    rng = FixedRng(0.0)

    assert select_target_with_priority(actor, [guest, guard], rng, battle_engine_version=version) is guest
    assert rng.random_calls == 0


@pytest.mark.parametrize("include_dead_guard", [False, True])
def test_no_living_guard_means_no_screening_or_extra_randomness(include_dead_guard):
    actor, guest = unit(kind="troop", side="attacker"), unit()
    opponents = [guest]
    if include_dead_guard:
        opponents.append(unit("阵亡护院", "troop", hp=0))
    rng = FixedRng(0.0)

    assert select_target_with_priority(actor, opponents, rng) is guest
    assert rng.random_calls == 0


def test_screening_does_not_change_guest_targeting_or_wall_interception():
    guest = unit()
    guard = unit("护院", "troop")
    guest_rng = FixedRng(0.0)
    assert select_target_with_priority(unit(side="attacker"), [guest, guard], guest_rng) is guest
    assert guest_rng.random_calls == 1  # 原有的门客优先选敌判定。

    wall = unit("城墙", "city_defense", template_key="wall", battle_modifiers={"wall_intercept_chance": 1.0})
    wall_rng = FixedRng(0.0)
    assert select_target_with_priority(unit(kind="troop", side="attacker"), [guest, guard, wall], wall_rng) is wall
    assert wall_rng.random_calls == 1


def test_redirect_preserves_counter_priority():
    actor = unit(kind="troop", side="attacker", troop_class="qiang")
    guest = unit()
    ordinary = unit("剑兵", "troop", troop_class="jian")
    countered = unit("刀兵", "troop", troop_class="dao")
    # 原选敌走随机分支并选中门客；掩护成功后，重新优先选择被克制的刀兵。
    rng = FixedRng(0.99, 0.0, 0.0)

    assert select_target_with_priority(actor, [guest, ordinary, countered], rng) is countered
    assert rng.random_calls == 3


def test_redirect_preserves_troop_target_weights():
    actor = unit(kind="troop", side="attacker")
    guest = unit()
    light = unit("低权重护院", "troop", battle_modifiers={"target_weight_multiplier": 0.1})
    heavy = unit("高权重护院", "troop", battle_modifiers={"target_weight_multiplier": 0.9})

    assert select_target_with_priority(actor, [guest, light, heavy], FixedRng(0.0, 0.0, 0.9)) is heavy


def test_screening_observed_probability_matches_twenty_guests_and_one_troop_stack():
    actor = unit(kind="troop", side="attacker")
    opponents = [unit(f"门客{i}") for i in range(20)] + [unit("护院", "troop")]
    for version, expected in [("4", 20 / 21), ("5", 20 / 21 * 0.25)]:
        rng = random.Random(20261022)
        guest_targets = sum(
            select_target_with_priority(actor, opponents, rng, battle_engine_version=version).kind == "guest"
            for _ in range(5000)
        )
        assert guest_targets / 5000 == pytest.approx(expected, abs=0.025)


def test_last_guard_can_only_screen_until_defeated_during_split_attacks():
    actor = unit(
        "进攻护院",
        "troop",
        "attacker",
        troop_strength=1000,
        initial_troop_strength=1000,
        hp=10000,
        max_hp=10000,
        initial_hp=10000,
    )
    guest = unit()
    guard = unit(
        "最后一名护院",
        "troop",
        troop_strength=1,
        initial_troop_strength=1,
        hp=10,
        max_hp=10,
        initial_hp=10,
    )

    event = perform_attack(actor, [actor], [guest, guard], FixedRng(0.0))

    assert event is not None
    assert [hit["target"] for hit in [event, *event["additional_attacks"]]] == [guard.name, guest.name]
    assert guard.hp == 0
    assert guest.hp < guest.initial_hp


@pytest.mark.parametrize(
    "version,base_multiplier,skill_multiplier",
    [
        ("legacy", 15, 2),
        ("2", 15, 2),
        ("3", 15, 2),
        ("4", 15, 2),
        ("5", 20, 3),
        ("10", 20, 3),
        ("11", 15, 1.8),
    ],
)
@pytest.mark.parametrize(
    "skills,skill_increment",
    [
        ([], 0),
        ([{"damage_formula": {"base": 2000}}], 2000),
        ([{"damage_formula": {"base": 2000}, "damage_multiplier": 2}], 4500),
    ],
)
def test_versioned_clear_damage_scales_base_and_skill_increment_independently(
    version, base_multiplier, skill_multiplier, skills, skill_increment
):
    result = calculate_attack_damage(
        unit(side="attacker"),
        unit(kind="troop"),
        skills,
        FixedRng(),
        round_priority=0,
        battle_engine_version=version,
    )

    assert result.damage == 500 * base_multiplier + skill_increment * skill_multiplier


@pytest.mark.parametrize(
    "actor_kind,target_kind",
    [
        ("guest", "guest"),
        ("troop", "guest"),
        ("troop", "troop"),
        ("guest", "city_defense"),
    ],
)
def test_other_damage_matchups_keep_the_same_values(actor_kind, target_kind):
    actor, target = unit(kind=actor_kind, side="attacker"), unit(kind=target_kind)
    damage = [
        calculate_attack_damage(actor, target, [], FixedRng(), round_priority=0, battle_engine_version=version).damage
        for version in ["4", "5"]
    ]
    assert damage[0] == damage[1]


@pytest.mark.parametrize("version", ["4", "5"])
def test_debugger_clear_override_still_works_with_versioned_damage(version):
    with patch_battle_params({"slaughter_multiplier": 7}):
        result = calculate_attack_damage(
            unit(side="attacker"),
            unit(kind="troop"),
            [],
            FixedRng(),
            round_priority=0,
            battle_engine_version=version,
        )
    assert result.damage == 3500


def test_new_battles_persist_current_version():
    assert CURRENT_BATTLE_ENGINE_VERSION == "11"
    assert current_replay_metadata(123)["battle_engine_version"] == "11"
