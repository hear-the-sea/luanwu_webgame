"""桥头张飞强化独立于演武张飞，低血量转为双目标。"""

from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

from battle.combatants_pkg.guest_builder import _serialize_skill
from battle.modifier_lifecycle import clear_round_and_action_modifiers
from battle.simulation.target_selection import select_attack_targets
from battle.skills import trigger_skills
from gameplay.models import MissionTemplate
from guests.models import Guest, GuestTemplate, Skill
from tests.test_zhanlong_boss_skills import FixedRng, damage, passives, skill, unit

rules = import_module("gameplay.migrations.0233_bridge_zhangfei_fury")
merged_rules = import_module("gameplay.migrations.0235_bridge_roar_without_dust_passive")
base = import_module("gameplay.migrations.0220_zhanlong_quest_chain")
progression_rules = import_module("gameplay.migrations.0240_zhanlong_progressive_difficulty")
DATA = Path(__file__).resolve().parents[1] / "data"


def bridge(**kwargs):
    return unit(agility=400, skills=[skill("bridge_tiger_dragon_roar")], **kwargs)


def test_bridge_roar_doubles_multiplier_and_preserves_fixed_trigger_and_ordinary_roar():
    actor = bridge()
    enemy = unit(side="defender")
    ordinary = skill("tiger_dragon_roar")
    current = actor.skills[0]
    assert current["damage_multiplier"] == ordinary["damage_multiplier"] * 2 == 4.6
    # 引擎分别结算普攻与技能伤害，最终伤害还取决于双方属性和减伤。
    assert 2.0 <= damage(actor, enemy, [current]) / damage(actor, enemy, [ordinary]) <= 2.3
    assert trigger_skills(actor, FixedRng(0.249))[-1]["kind"] == "active"
    assert not any(s["kind"] == "active" for s in trigger_skills(actor, FixedRng(0.251)))
    assert ordinary["damage_multiplier"] == 2.3
    assert ordinary["targets"] == 1


@pytest.mark.parametrize("hp,count", [(20001, 1), (20000, 1), (19999, 2), (1, 2), (0, 1)])
@pytest.mark.parametrize("timing", ["hit_taken", "action_before"])
def test_bridge_roar_switches_only_below_twenty_percent_without_changing_stats(hp, count, timing):
    actor = bridge(hp=hp)
    before = asdict(actor)
    events = passives(timing, actor, [actor], [])
    after = asdict(actor)
    assert actor.skills[0]["targets"] == count
    for key in set(before) - {"skills", "battle_state"}:
        assert after[key] == before[key]
    assert len(events) == int(count == 2)


def test_low_health_bridge_hits_two_without_changing_other_actors_or_repeating_logs():
    actor = bridge(hp=19999)
    other = bridge()
    shared_skills = actor.skills
    other.skills = shared_skills
    events = []
    for hp in [19999, 1, 90000, 10000]:
        actor.hp = hp
        passives("hit_taken", actor, [actor], [], events)
        passives("action_before", actor, [actor], [], events)
        clear_round_and_action_modifiers(actor)
    assert len(events) == 1
    enemies = [unit(side="defender", name=f"敌将{i}") for i in range(3)]
    selection = select_attack_targets(actor, [actor], enemies, FixedRng(0.249), trigger_skills)
    assert len(selection.engaged_targets) == len({id(t) for t in selection.engaged_targets}) == 2
    assert other.skills[0]["targets"] == shared_skills[0]["targets"] == 1
    ordinary = unit(hp=1, skills=[skill("tiger_dragon_roar")])
    passives("action_before", ordinary, [ordinary], [])
    ordinary_selection = select_attack_targets(ordinary, [ordinary], enemies, FixedRng(0.249), trigger_skills)
    assert len(ordinary_selection.engaged_targets) == 1


def test_current_yaml_preserves_bridge_dedicated_skill_and_ordinary_agility():
    rows = yaml.safe_load((DATA / "guests/zhanlong.yaml").read_text())["heroes"]["orange"]
    guests = {row["key"]: row for row in rows}
    bridge_guest = guests["task_zhanlong_zhangfei_bridge"]
    ordinary = guests["task_zhanlong_zhangfei"]
    assert bridge_guest["custom_stats"]["agility"] == progression_rules.GUEST_UPDATES[bridge_guest["key"]].get(
        "base_agility", rules.GUEST_UPDATES[bridge_guest["key"]]["base_agility"]
    )
    assert bridge_guest["skills"] == merged_rules.GUEST_SKILLS[bridge_guest["key"]]
    assert ordinary["custom_stats"]["agility"] == 220
    assert ordinary["skills"] == ["zhanlong_tiger_dragon_roar"]
    skills = yaml.safe_load((DATA / "guest_skills.yaml").read_text())["skills"]
    current = next(row for row in skills if row["key"] == "zhanlong_bridge_tiger_dragon_roar")
    assert {k: v for k, v in current.items() if k != "key"} == merged_rules.SKILL_DEFAULTS[current["key"]]
    dust = next(row for row in skills if row["key"] == "zhanlong_dust_ruse")
    assert all(dust[field] == value for field, value in rules.SKILL_UPDATES[dust["key"]].items())


@pytest.mark.django_db
def test_migration_preserves_ordinary_zhangfei_and_player_instances(manor_with_user):
    manor, _client = manor_with_user
    for key in ["zhanlong_tiger_dragon_roar", "zhanlong_dust_ruse"]:
        Skill.objects.update_or_create(key=key, defaults=base.SKILL_DEFAULTS[key])
    for key in ["task_zhanlong_zhangfei_bridge", "task_zhanlong_zhangfei"]:
        template, _ = GuestTemplate.objects.update_or_create(key=key, defaults=base.ENEMY_DEFAULTS[key])
        template.initial_skills.set(Skill.objects.filter(key__in=base.ENEMY_SKILLS[key]))
    for key in rules.MISSION_UPDATES:
        MissionTemplate.objects.update_or_create(
            key=key, defaults={**base.MISSION_DEFAULTS[key], "mission_card_daily_limit": 5}
        )
    guest = Guest.objects.create(
        manor=manor, template=template, force=100, intellect=80, defense_stat=90, agility=35, luck=11
    )
    guest_before = Guest.objects.filter(pk=guest.pk).values().get()
    ordinary_before = GuestTemplate.objects.filter(key="task_zhanlong_zhangfei").values().get()
    skill_before = Skill.objects.filter(key="zhanlong_tiger_dragon_roar").values().get()
    for _ in range(2):
        rules.sync_bridge_zhangfei_fury(apps, SimpleNamespace(connection=connection))
    assert Guest.objects.filter(pk=guest.pk).values().get() == guest_before
    assert GuestTemplate.objects.filter(key="task_zhanlong_zhangfei").values().get() == ordinary_before
    assert Skill.objects.filter(key="zhanlong_tiger_dragon_roar").values().get() == skill_before
    bridge_template = GuestTemplate.objects.get(key="task_zhanlong_zhangfei_bridge")
    assert bridge_template.base_agility == 400
    assert set(bridge_template.initial_skills.values_list("key", flat=True)) == set(
        rules.GUEST_SKILLS[bridge_template.key]
    )
    stored = Skill.objects.get(key="zhanlong_bridge_tiger_dragon_roar")
    assert _serialize_skill(stored)["damage_multiplier"] == 4.6
