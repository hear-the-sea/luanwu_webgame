from __future__ import annotations

import copy
import random

import pytest

from battle.combatants_pkg.core import Combatant
from battle.combatants_pkg.tech_effects import build_tech_effects
from battle.combatants_pkg.troop_builder import build_troop_combatants
from battle.simulation.attack_execution import perform_attack
from battle.simulation.damage_calculation import calculate_attack_damage
from battle.troops import load_troop_templates_from_yaml


class FixedRng(random.Random):
    def __init__(self, roll=0.99):
        super().__init__(1)
        self.roll = roll

    def random(self):
        return self.roll

    def uniform(self, _minimum, _maximum):
        return 1.0

    def choice(self, sequence):
        return sequence[0]


@pytest.fixture(autouse=True)
def yaml_troops(monkeypatch):
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", load_troop_templates_from_yaml)


def dao(level, *, count=100, double_strike=0):
    return build_troop_combatants(
        {"dao_sheng": count},
        "attacker",
        tech_levels={"dao_armor_break": level, "dao_double_strike": double_strike},
    )[0]


def target_for(key):
    if key not in {"guest", "city_defense"}:
        return build_troop_combatants({key: 1000}, "defender", tech_levels={})[0]
    return Combatant(
        name="防御目标",
        attack=1,
        defense=200,
        hp=1_000_000_000,
        max_hp=1_000_000_000,
        initial_hp=1_000_000_000,
        side="defender",
        rarity="orange",
        luck=30,
        agility=1,
        priority=0,
        kind=key,
        troop_strength=0,
    )


@pytest.mark.parametrize("level", [-1, 0, 1, 5, 10, 99])
def test_armor_break_has_level_caps_and_both_effects(level):
    effects = build_tech_effects("dao", {"dao_armor_break": level})
    if level <= 0:
        assert effects == {}
    else:
        assert effects["sword_damage_bonus"] == pytest.approx(min(level, 10) * 0.1)
        assert effects["defense_ignore"] == pytest.approx(min(level, 10) * 0.02)


@pytest.mark.parametrize("troop_class", ["qiang", "jian", "quan", "gong", ""])
def test_armor_break_does_not_apply_to_other_classes(troop_class):
    effects = build_tech_effects(troop_class, {"dao_armor_break": 10})
    assert "sword_damage_bonus" not in effects
    assert "defense_ignore" not in effects


@pytest.mark.parametrize("level", [0, 1, 5, 10])
@pytest.mark.parametrize("target_key", ["jian_sheng", "qiang_wang", "bloodthirsty_cavalry", "guest", "city_defense"])
def test_damage_matches_reduced_defense_and_only_swords_get_extra_damage(level, target_key):
    target = target_for(target_key)
    original = copy.deepcopy(target)
    reduced_defense_target = copy.deepcopy(target)
    reduced_defense_target.defense *= 1 - level * 0.02
    reduced_defense_target.unit_defense *= 1 - level * 0.02
    reference = calculate_attack_damage(dao(0), reduced_defense_target, [], FixedRng(), round_priority=0)
    actual = calculate_attack_damage(dao(level), target, [], FixedRng(), round_priority=0)
    sword_multiplier = 1 + level * 0.1 if target_key == "jian_sheng" else 1
    assert actual.damage == pytest.approx(reference.damage * sword_multiplier, abs=1)
    assert target == original


def test_max_armor_break_stacks_with_existing_sword_counter():
    sword = target_for("jian_sheng")
    unclassified = copy.deepcopy(sword)
    unclassified.troop_class = ""
    unclassified.defense *= 0.8
    unclassified.unit_defense *= 0.8
    reference = calculate_attack_damage(dao(0), unclassified, [], FixedRng(), round_priority=0)
    actual = calculate_attack_damage(dao(10), sword, [], FixedRng(), round_priority=0)
    assert actual.damage == pytest.approx(reference.damage * 3, abs=3)


def test_armor_break_and_double_strike_apply_together():
    target = target_for("jian_sheng")
    normal = calculate_attack_damage(dao(10), target, [], FixedRng(0.25), round_priority=0)
    doubled = calculate_attack_damage(dao(10, double_strike=5), target, [], FixedRng(0.25), round_priority=0)
    assert not normal.is_double_strike and doubled.is_double_strike
    assert doubled.damage == pytest.approx(normal.damage * 2, abs=1)


def test_every_troop_subgroup_uses_armor_break():
    target = target_for("guest")
    original = copy.deepcopy(target)
    actor = dao(10, count=2000)
    actual = perform_attack(actor, [actor], [target], FixedRng())
    original.defense *= 0.8
    plain = dao(0, count=2000)
    reference = perform_attack(plain, [plain], [original], FixedRng())
    assert actual is not None and reference is not None
    actual_hits = [actual, *actual["additional_attacks"]]
    reference_hits = [reference, *reference["additional_attacks"]]
    assert len(actual_hits) == len(reference_hits) == 3
    assert [hit["damage"] for hit in actual_hits] == [hit["damage"] for hit in reference_hits]
