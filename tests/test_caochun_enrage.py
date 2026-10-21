"""曹纯狂暴阈值、战斗隔离、技能目标和满科技铁骑回归。"""

from copy import deepcopy
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

from battle.combatants_pkg.troop_builder import build_troop_combatants
from battle.modifier_lifecycle import clear_round_and_action_modifiers
from battle.simulation.attack_execution import perform_attack
from battle.simulation.target_selection import select_attack_targets
from battle.skills import trigger_skills
from battle.troops import load_troop_templates_from_yaml
from core.game_data.technology import resolve_enemy_tech_levels
from core.utils.yaml_validators import validate_guest_skills
from gameplay.models import MissionTemplate
from guests.models import Skill
from tests.test_zhanlong_boss_skills import FixedRng, passives, skill, unit

rules = import_module("gameplay.migrations.0232_caochun_enrage_and_cavalry")
base = import_module("gameplay.migrations.0220_zhanlong_quest_chain")
progression_rules = import_module("gameplay.migrations.0240_zhanlong_progressive_difficulty")
DATA = Path(__file__).resolve().parents[1] / "data"


def commander(**kwargs):
    return unit(luck=120, skills=[skill("cavalry_assault"), skill("cavalry_command")], **kwargs)


@pytest.mark.parametrize("hp,enraged", [(30001, False), (30000, False), (29999, True), (1, True), (0, False)])
@pytest.mark.parametrize("timing", ["hit_taken", "action_before"])
def test_enrage_only_below_thirty_percent_and_never_after_death(hp, enraged, timing):
    actor = commander(hp=hp)
    before = asdict(actor)
    events = passives(timing, actor, [actor], [])
    after = asdict(actor)
    assert actor.luck == (220 if enraged else 120)
    assert actor.skills[0]["targets"] == (3 if enraged else 2)
    for key in set(before) - {"luck", "skills", "battle_state"}:
        assert after[key] == before[key]
    assert len(events) == int(enraged)


def test_enrage_persists_after_healing_and_round_cleanup_and_never_stacks():
    actor = commander(hp=29999)
    events = []
    original_skills = actor.skills
    other_actor = commander()
    other_actor.skills = original_skills
    for hp in [29999, 1, 90000, 20000]:
        actor.hp = hp
        for timing in ["hit_taken", "action_before"]:
            passives(timing, actor, [actor], [], events)
        clear_round_and_action_modifiers(actor)
    assert actor.luck == 220
    assert actor.skills[0]["targets"] == 3
    assert len(events) == 1
    assert other_actor.luck == 120
    assert original_skills[0]["targets"] == other_actor.skills[0]["targets"] == 2


def test_luck_bonus_changes_real_skill_trigger_and_selects_three_distinct_targets():
    actor = commander(hp=29999)
    assert not any(s["kind"] == "active" for s in trigger_skills(actor, FixedRng(0.5)))
    passives("action_before", actor, [actor], [])
    assert any(s["kind"] == "active" for s in trigger_skills(actor, FixedRng(0.5)))
    enemies = [unit(side="defender", name=f"敌将{i}") for i in range(4)]
    selection = select_attack_targets(actor, [actor], enemies, FixedRng(0.5), trigger_skills)
    assert len(selection.engaged_targets) == len({id(t) for t in selection.engaged_targets}) == 3


def test_actual_hit_crossing_threshold_triggers_enrage_immediately(monkeypatch):
    attacker = unit()
    target = commander(side="defender", hp=30000)
    monkeypatch.setattr(
        "battle.simulation.attack_execution.calculate_attack_damage",
        lambda *_args, **_kwargs: SimpleNamespace(damage=1, is_crit=False, is_double_strike=False),
    )
    result = perform_attack(attacker, [attacker], [target], FixedRng(), round_priority=0)
    assert target.hp == 29999
    assert target.luck == 220
    assert target.skills[0]["targets"] == 3
    assert any(event["effect"] == "精骑狂暴" for event in result["passive_events_after"])


@pytest.mark.parametrize("remaining", [1, 2])
def test_enraged_skill_respects_remaining_living_targets(remaining):
    actor = commander(hp=1)
    passives("action_before", actor, [actor], [])
    enemies = [unit(side="defender") for _ in range(remaining)] + [unit(side="defender", hp=0)]
    selection = select_attack_targets(actor, [actor], enemies, FixedRng(0.5), trigger_skills)
    assert len(selection.engaged_targets) == remaining
    assert all(target.hp > 0 for target in selection.engaged_targets)


@pytest.mark.parametrize("mission_key", ["zhanlong_changban_tuwei", "zhanlong_changban_duanqiao"])
def test_cavalry_stages_have_correct_counts_and_all_five_maxed_technologies(monkeypatch, mission_key):
    missions = yaml.safe_load((DATA / "mission_templates.yaml").read_text())["missions"]
    mission = next(m for m in missions if m["key"] == mission_key)
    count = progression_rules.MISSION_UPDATES[mission_key]["enemy_troops"]["bloodthirsty_cavalry"]
    assert mission["enemy_troops"] == {"bloodthirsty_cavalry": count}
    assert (mission["daily_limit"], mission["mission_card_daily_limit"]) == (1, 5)
    levels = resolve_enemy_tech_levels(mission["enemy_technology"])
    assert {key: levels[key] for key in mission["enemy_technology"]["levels"]} == {
        "wolf_force": 10,
        "wolf_defense": 10,
        "wolf_hp": 10,
        "wolf_agility": 10,
        "wolf_berserk": 5,
    }
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", load_troop_templates_from_yaml)
    cavalry = build_troop_combatants(mission["enemy_troops"], "defender", tech_levels=levels)[0]
    assert cavalry.troop_strength == count
    assert (cavalry.unit_attack, cavalry.unit_defense, cavalry.unit_hp, cavalry.agility) == (34, 20, 144, 42)
    assert cavalry.skills[0]["trigger_chance"] == 0.5


@pytest.mark.parametrize("overrides", [{"luck_bonus": True}, {"luck_bonus": -1}, {"targets": 0}, {"skill_key": ""}])
def test_invalid_enrage_configuration_is_rejected(overrides):
    config = deepcopy(rules.SKILL_UPDATES["zhanlong_cavalry_command"])
    config.update(key="invalid_enrage", name="狂暴", kind="passive", rarity="purple")
    config["passive_config"]["triggers"][1]["effects"][0].update(overrides)
    assert not validate_guest_skills({"skills": [config]}).is_valid


@pytest.mark.django_db
def test_migration_updates_only_mission_and_two_skills_and_is_repeatable():
    for key, updates in rules.MISSION_UPDATES.items():
        MissionTemplate.objects.update_or_create(
            key=key, defaults={**base.MISSION_DEFAULTS[key], "mission_card_daily_limit": 5}
        )
    for key in rules.SKILL_UPDATES:
        Skill.objects.update_or_create(key=key, defaults=base.SKILL_DEFAULTS[key])
    mission_before = MissionTemplate.objects.get(key="zhanlong_changban_tuwei")
    before = MissionTemplate.objects.filter(pk=mission_before.pk).values().get()
    unrelated = Skill.objects.create(key="unrelated_enrage_skill", name="保留技能", description="保留")
    for _ in range(2):
        rules.sync_caochun_enrage_and_cavalry(apps, SimpleNamespace(connection=connection))
    after = MissionTemplate.objects.filter(pk=mission_before.pk).values().get()
    assert after == {**before, **rules.MISSION_UPDATES[mission_before.key]}
    for key, updates in rules.SKILL_UPDATES.items():
        stored = Skill.objects.get(key=key)
        assert all(getattr(stored, field) == value for field, value in updates.items())
    unrelated.refresh_from_db()
    assert unrelated.description == "保留"
