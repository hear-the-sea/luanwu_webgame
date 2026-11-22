from __future__ import annotations

import random

import pytest

from battle.combatants_pkg.core import Combatant
from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION
from battle.simulation.attack_execution import perform_attack
from battle.simulation.battle_flow import _resolve_standard_round


def unit(name="甲", side="defender", **overrides):
    values = dict(
        name=name,
        kind="guest",
        side=side,
        attack=100,
        defense=0,
        hp=10000,
        max_hp=10000,
        initial_hp=10000,
        luck=100,
        agility=100,
        priority=0,
        rarity="purple",
        troop_strength=0,
        skills=[],
    )
    values.update(overrides)
    return Combatant(**values)


def burst_skill(**overrides):
    values = dict(
        key="test_burst",
        name="爆发技能",
        kind="active",
        trigger_chance=1.0,
        damage_formula={"base": 100},
        targets=1,
    )
    values.update(overrides)
    return values


def passive(*triggers):
    return dict(key="test_passive", name="测试被动", kind="passive", passive_config={"triggers": list(triggers)})


class DirectedRng(random.Random):
    """仅控制随机输入，不替换选敌、伤害、状态或被动结算。"""

    def __init__(self, targets=(), rolls=()):
        super().__init__(0)
        self.targets = iter(targets)
        self.rolls = iter(rolls)
        self.skill_choices = 0

    def random(self):
        return next(self.rolls, 0.99)

    def choice(self, sequence):
        if isinstance(sequence[0], Combatant):
            name = next(self.targets, sequence[0].name)
            return next(unit for unit in sequence if unit.name == name)
        self.skill_choices += 1
        return sequence[0]

    def shuffle(self, sequence):
        pass

    def uniform(self, _minimum, _maximum):
        return 1.0


def attack(actor, enemies, *, rng=None, version=CURRENT_BATTLE_ENGINE_VERSION):
    return perform_attack(actor, [actor], enemies, rng or DirectedRng(), battle_engine_version=version)


def normal_segment(event):
    (normal,) = event["additional_attacks"]
    assert normal["attack_phase"] == "normal"
    assert normal["skills"] == []
    return normal


def test_normal_reselects_even_when_burst_target_survives():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    first, second = unit(), unit("乙")
    rng = DirectedRng(targets=["甲", "乙"])

    event = attack(actor, [first, second], rng=rng)

    assert event["attack_phase"] == "burst"
    assert event["target"] == "甲"
    assert event["damage"] == 200
    assert first.hp == 9800
    assert normal_segment(event)["target"] == "乙"
    assert second.hp == 8700
    assert rng.skill_choices == 1
    assert actor.has_acted_this_round


def test_reselection_can_choose_same_target_without_duplicating_base_damage():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    enemy = unit()

    event = attack(actor, [enemy])

    assert event["damage"] == 200
    assert normal_segment(event)["damage"] == 1300
    assert enemy.hp == 8500


def test_untriggered_skill_only_performs_one_normal_attack():
    actor = unit("门客", "attacker", skills=[burst_skill(trigger_chance=0)])
    enemy = unit()

    event = attack(actor, [enemy])

    assert event["attack_phase"] == "normal"
    assert event["skills"] == []
    assert event["damage"] == 1300
    assert not event.get("additional_attacks")
    assert enemy.hp == 8700


def test_multitarget_skill_deals_pure_bonus_then_one_normal_attack():
    actor = unit("门客", "attacker", skills=[burst_skill(targets=3)])
    enemies = [unit(), unit("乙"), unit("丙")]

    event = attack(actor, enemies, rng=DirectedRng(targets=["甲", "乙"]))

    burst_hits = [event, *event["additional_targets"]]
    assert [hit["attack_phase"] for hit in burst_hits] == ["burst"] * 3
    assert [hit["damage"] for hit in burst_hits] == [200] * 3
    assert normal_segment(event)["target"] == "乙"
    assert [enemy.hp for enemy in enemies] == [9800, 8500, 9800]


def test_burst_kill_reselects_from_surviving_enemies():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    first, second = unit(hp=100), unit("乙")

    event = attack(actor, [first, second])

    assert event["target_defeated"]
    assert first.hp == 0
    assert normal_segment(event)["target"] == "乙"
    assert second.hp == 8700


def test_burst_wiping_out_opponents_does_not_attack_again():
    actor = unit("门客", "attacker", skills=[burst_skill(targets=2)])
    enemies = [unit(hp=100), unit("乙", hp=100)]

    event = attack(actor, enemies)

    assert all(enemy.hp == 0 for enemy in enemies)
    assert not event.get("additional_attacks")
    assert actor.has_acted_this_round


def test_reflected_burst_killing_actor_cancels_normal_attack():
    actor = unit("门客", "attacker", hp=10, skills=[burst_skill()])
    enemy = unit(troop_class="jian", tech_effects={"damage_reflect": 1.0})

    event = attack(actor, [enemy])

    assert actor.hp == 0
    assert event["actor_defeated"]
    assert event["reflect_applied_damage"] == 10
    assert not event.get("additional_attacks")


def test_missed_burst_still_performs_reselected_normal_attack(monkeypatch):
    # 正式规则已取消闪避；此处只恢复闪避概率以验证未命中分支。
    monkeypatch.setattr("battle.simulation.attack_execution.calculate_dodge_chance", lambda _target: 0.5)
    actor = unit("门客", "attacker", skills=[burst_skill()])
    first, second = unit(), unit("乙")
    rng = DirectedRng(targets=["甲", "乙"], rolls=[0.99, 0.99, 0.0])

    event = attack(actor, [first, second], rng=rng)

    assert event["is_dodge"]
    assert event["damage"] == 0
    assert first.hp == 10000
    assert normal_segment(event)["target"] == "乙"
    assert second.hp == 8700


def test_zero_damage_control_applies_status_without_counter_or_hit_taken():
    skill = burst_skill(damage_formula={"base": 0}, status_effect="stunned", status_probability=1.0, status_duration=1)
    actor = unit("门客", "attacker", skills=[skill])
    counter = unit(
        hp=5000,
        tech_effects={"counter_attack_chance": 1.0, "counter_attack_damage": 1.0},
        skills=[passive({"timing": "hit_taken", "effects": [{"type": "heal_ratio", "value": 0.1}]})],
    )
    other = unit("乙")

    event = attack(actor, [counter, other], rng=DirectedRng(targets=["甲", "乙"]))

    assert event["damage"] == 0
    assert event["status_inflicted"]
    assert event["counter_damage"] == 0
    assert counter.hp == 5000
    assert actor.hp == 10000
    assert normal_segment(event)["target"] == "乙"


def test_attack_before_and_after_passives_trigger_once_across_both_segments():
    healing = passive(
        {"timing": "attack_before", "effects": [{"type": "heal_ratio", "value": 0.1, "max_hp_based": True}]},
        {"timing": "attack_after", "effects": [{"type": "heal_ratio", "value": 0.1, "max_hp_based": True}]},
    )
    actor = unit("门客", "attacker", hp=100, max_hp=1000, skills=[burst_skill(targets=2), healing])

    event = attack(actor, [unit(), unit("乙")])

    assert event["attack_phase"] == "burst"
    assert normal_segment(event)
    assert actor.hp == 300


def test_permanent_and_pending_true_damage_only_apply_in_normal_segment():
    actor = unit(
        "门客",
        "attacker",
        skills=[burst_skill()],
        battle_modifiers={"true_damage_ratio_sources": {"test": {"value": 0.01}}},
        battle_state={"_pending_extra_target_true_damage": [{"value": 0.02, "log_name": "真伤"}]},
    )
    enemy = unit()

    event = attack(actor, [enemy])

    assert event["damage"] == 200
    assert normal_segment(event)["damage"] == 1600
    assert enemy.hp == 8200
    assert "_pending_extra_target_true_damage" not in actor.battle_state


def test_version_five_keeps_single_combined_attack():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    enemy = unit()

    event = attack(actor, [enemy], version="5")

    assert "attack_phase" not in event
    assert not event.get("additional_attacks")
    assert event["damage"] == 1700
    assert enemy.hp == 8300


def test_each_actual_hit_triggers_defender_hit_taken_passive():
    actor = unit("门客", "attacker", skills=[burst_skill()])
    enemy = unit(
        hp=5000,
        skills=[
            passive({"timing": "hit_taken", "effects": [{"type": "heal_ratio", "value": 0.1, "max_hp_based": True}]})
        ],
    )

    event = attack(actor, [enemy])

    assert event["target_hp_after"] == 4800
    assert normal_segment(event)["target_hp_before"] == 5800
    assert enemy.hp == 5500


def test_skill_specific_lowest_hp_targeting_is_not_inherited_by_normal_attack():
    actor = unit("门客", "attacker", skills=[burst_skill(target_priority="lowest_hp_ratio")])
    first, second = unit(hp=5000), unit("乙")

    event = attack(actor, [first, second], rng=DirectedRng(targets=["乙", "乙"]))

    assert event["target"] == "甲"
    assert normal_segment(event)["target"] == "乙"


def test_pending_true_damage_selects_extra_target_after_normal_reselection():
    actor = unit(
        "门客",
        "attacker",
        skills=[burst_skill()],
        battle_state={"_pending_extra_target_true_damage": [{"value": 0.02, "log_name": "真伤"}]},
    )
    first, second = unit(), unit("乙")

    event = attack(actor, [first, second], rng=DirectedRng(targets=["甲", "乙", "甲"]))

    normal = normal_segment(event)
    assert event["damage"] == 200
    assert normal["damage"] == 1300
    assert normal["additional_targets"][0]["target"] == "甲"
    assert normal["additional_targets"][0]["damage"] == 200
    assert [first.hp, second.hp] == [9600, 8700]


def test_flow_runs_action_before_and_end_only_once_for_burst_and_normal():
    healing = passive(
        {"timing": "action_before", "effects": [{"type": "heal_ratio", "value": 0.1, "max_hp_based": True}]},
        {"timing": "action_end", "effects": [{"type": "heal_ratio", "value": 0.1, "max_hp_based": True}]},
    )
    actor = unit("门客", "attacker", hp=100, max_hp=1000, skills=[burst_skill(), healing])
    enemy = unit(attack=0, agility=0)

    result = _resolve_standard_round([actor], [enemy], DirectedRng(), 1, battle_engine_version="6")

    actor_event = next(event for event in result["events"] if event.get("actor") == actor.name)
    assert normal_segment(actor_event)
    assert actor.hp == 299


@pytest.mark.parametrize("damage_multiplier,expected_burst", [(2.0, 3000), (3.0, 5800)])
def test_skill_multiplier_increment_includes_extra_base_only_in_burst(damage_multiplier, expected_burst):
    actor = unit("门客", "attacker", skills=[burst_skill(damage_multiplier=damage_multiplier)])
    enemy = unit(hp=100000, max_hp=100000)

    event = attack(actor, [enemy])

    assert event["damage"] == expected_burst
    assert normal_segment(event)["damage"] == 1300
