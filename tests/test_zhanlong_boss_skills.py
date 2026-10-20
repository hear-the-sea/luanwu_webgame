"""战龙任务技能从持久化载荷到真实战斗结算的回归验证。"""

import json
from pathlib import Path

import pytest
import yaml

from battle.combatants_pkg.core import Combatant
from battle.combatants_pkg.guest_builder import _serialize_skill
from battle.modifier_lifecycle import clear_round_and_action_modifiers
from battle.passives import run_passives_for_timing
from battle.simulation.damage_application import apply_damage_results
from battle.simulation.damage_calculation import calculate_attack_damage
from battle.simulation.target_selection import select_attack_targets
from battle.skills import apply_skill_statuses, trigger_skills
from battle.utils.status_effects import capture_active_status_effects, consume_active_status_effects
from core.utils.yaml_validators import validate_guest_skills
from guests.models import Skill

CATALOG = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/guest_skills.yaml").read_text())
SKILLS = {entry["key"]: entry for entry in CATALOG["skills"] if entry["key"].startswith("zhanlong_")}


class FixedRng:
    def __init__(self, value=0.99):
        self.value = value

    def random(self):
        return self.value

    def uniform(self, _a, _b):
        return 1.0

    def choice(self, values):
        return values[0]

    def shuffle(self, values):
        pass


def skill(key):
    # 实际 Skill 模型上的 JSON 往返，验证导入器持久化的字段经序列化后仍然生效。
    payload = SKILLS["zhanlong_" + key]
    stored = json.loads(json.dumps(payload))
    model_fields = {field.name for field in Skill._meta.fields}
    return _serialize_skill(Skill(**{key: value for key, value in stored.items() if key in model_fields}))


def unit(**overrides):
    payload = dict(
        name="门客",
        attack=1800,
        force_attr=900,
        defense=800,
        hp=100000,
        max_hp=100000,
        side="attacker",
        rarity="orange",
        luck=60,
        agility=150,
        priority=0,
        kind="guest",
        troop_strength=0,
    )
    payload.update(overrides)
    if payload["kind"] == "troop":
        payload.update(troop_strength=100, initial_troop_strength=100, unit_attack=10, unit_defense=5, unit_hp=1000)
    return Combatant(**payload)


def damage(actor, target, skills=()):
    return calculate_attack_damage(actor, target, list(skills), FixedRng(), round_priority=0).damage


def passives(timing, actor, allies, enemies, events=None, round_no=1):
    event_sink = [] if events is None else events
    run_passives_for_timing(
        timing,
        actor=actor,
        target=None,
        attacker_team=allies,
        defender_team=enemies,
        round_no=round_no,
        event_sink=event_sink,
        rng=FixedRng(),
    )
    return event_sink


def test_zhanlong_catalog_validates_and_has_all_nine_skills():
    assert len(SKILLS) == 9
    result = validate_guest_skills(CATALOG)
    assert result.is_valid, result


@pytest.mark.parametrize("luck", [1, 60, 300, 999])
def test_tiger_roar_keeps_fixed_probability_through_skill_json_and_serializer(luck):
    actor = unit(luck=luck, skills=[skill("tiger_dragon_roar")])
    assert actor.skills[0]["trigger_chance"] == 0.25
    assert trigger_skills(actor, FixedRng(0.249))
    assert not trigger_skills(actor, FixedRng(0.251))


def test_tiger_roar_is_one_large_hit_and_still_respects_defense_and_damage_reduction():
    actor = unit()
    target = unit(side="defender")
    roar = skill("tiger_dragon_roar")
    hit = damage(actor, target, [roar])
    assert roar["targets"] == 1
    assert 4.0 <= hit / damage(actor, target) <= 6.0
    armored = unit(side="defender", defense=2400)
    assert damage(actor, armored, [roar]) < hit
    armored.skills = [skill("dragon_guard")]
    before_guard = damage(actor, armored, [roar])
    passives("battle_start", armored, [actor], [armored])
    assert damage(actor, armored, [roar]) == pytest.approx(before_guard * 0.8, abs=1)
    assert damage(actor, armored, [roar]) < armored.hp


def test_hammer_inflicts_real_weakened_status():
    target = unit()
    assert apply_skill_statuses([skill("iron_hammer")], target, FixedRng(0.1)) == ["士气低落"]
    weakened_damage = damage(target, unit(side="defender"))
    target.status_effects.clear()
    assert weakened_damage == pytest.approx(damage(target, unit(side="defender")) * 0.7, abs=1)


def test_azure_slash_reduces_defense_for_two_action_opportunities_without_mutating_stats():
    actor, target = unit(), unit(side="defender")
    initial = damage(actor, target)
    assert apply_skill_statuses([skill("azure_dragon_slash")], target, FixedRng()) == ["破甲"]
    assert damage(actor, target) > initial
    assert target.defense == 800
    consume_active_status_effects(target, capture_active_status_effects(target))
    assert damage(actor, target) > initial
    consume_active_status_effects(target, capture_active_status_effects(target))
    assert damage(actor, target) == initial


def test_cavalry_command_only_buffs_living_allied_troops_until_next_round_after_death():
    commander = unit(skills=[skill("cavalry_command")])
    ally = unit(kind="troop")
    dead_ally = unit(kind="troop", hp=0)
    enemy = unit(kind="troop", side="defender")
    passives("round_start", commander, [commander, ally, dead_ally], [enemy])
    assert ally.battle_modifiers["outgoing_damage_multiplier"] == 1.12
    assert not commander.battle_modifiers
    assert not dead_ally.battle_modifiers
    assert not enemy.battle_modifiers
    before_buff = damage(ally, enemy)
    commander.hp = 0
    clear_round_and_action_modifiers(ally)
    assert not ally.battle_modifiers
    assert damage(ally, enemy) < before_buff


def test_dust_ruse_emits_once_and_changes_no_troop_or_damage_attributes():
    actor = unit(skills=[skill("dust_ruse")])
    target = unit(side="defender")
    initial = damage(actor, target)
    events = []
    for round_no in [1, 2, 3]:
        passives("round_start", actor, [actor], [target], events, round_no)
        clear_round_and_action_modifiers(actor)
    assert len(events) == 1
    assert "百万" in events[0]["message"]
    assert actor.troop_strength == 0
    assert actor.hp == actor.max_hp
    assert damage(actor, target) == initial


def test_dragon_guard_reduces_damage_and_counter_only_rolls_while_alive():
    actor = unit()
    defender = unit(side="defender", skills=[skill("dragon_guard")])
    baseline = damage(actor, defender)
    passives("battle_start", defender, [actor], [defender])
    assert damage(actor, defender) == pytest.approx(baseline * 0.8, abs=1)
    result = apply_damage_results(actor, defender, 100, FixedRng(0.1))
    assert result.counter_damage > 0
    result = apply_damage_results(actor, defender, 100, FixedRng(0.9))
    assert result.counter_damage == 0
    result = apply_damage_results(actor, defender, defender.hp, FixedRng(0.1))
    assert result.counter_damage == 0


@pytest.mark.parametrize("key,count", [("cavalry_assault", 2), ("xiliang_charge", 3)])
def test_multi_target_generals_keep_distinct_target_counts(key, count):
    actor = unit()
    enemies = [unit(side="defender", name=f"敌将{i}") for i in range(4)]
    selected = select_attack_targets(actor, [actor], enemies, FixedRng(), lambda *_: [skill(key)])
    assert len(selected.engaged_targets) == count
    current = skill(key)
    baseline = damage(actor, enemies[0], [current])
    actor.agility += 200
    assert damage(actor, enemies[0], [current]) > baseline


def test_hundred_pace_prioritizes_ratio_and_has_real_execute_bonus_only_when_skill_fires():
    actor = unit(skills=[skill("hundred_pace_shot")])
    low_absolute = unit(side="defender", hp=500, max_hp=1000)
    low_ratio = unit(side="defender", hp=2000, max_hp=10000)
    current = actor.skills[0]
    selected = select_attack_targets(actor, [actor], [low_absolute, low_ratio], FixedRng(), lambda *_: [current])
    assert selected.engaged_targets == [low_ratio]
    hit = damage(actor, low_ratio, [current])
    low_ratio.hp = 5000
    assert hit > damage(actor, low_ratio, [current]) * 1.4
    normal = select_attack_targets(actor, [actor], [low_absolute, low_ratio], FixedRng(), lambda *_: [])
    assert normal.engaged_targets == [low_absolute]


@pytest.mark.parametrize(
    "options",
    [
        {"trigger_chance": True},
        {"trigger_chance": 1.1},
        {"damage_multiplier": float("inf")},
        {"target_priority": "invented"},
        {"execute_hp_ratio": 0.3},
        {"name": "overwritten"},
    ],
)
def test_active_option_schema_rejects_invalid_parameters(options):
    config = dict(SKILLS["zhanlong_tiger_dragon_roar"], passive_config={"active": options})
    assert not validate_guest_skills({"skills": [config]}).is_valid
