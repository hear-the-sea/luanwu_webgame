"""演武赵云三目标主动、逐目标残血增伤与模板同步回归。"""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

from battle.combatants_pkg.guest_builder import _serialize_skill
from battle.simulation.attack_execution import perform_attack
from battle.simulation.damage_application import apply_damage_results
from battle.simulation.target_selection import select_attack_targets
from battle.skills import trigger_skills
from core.utils.yaml_validators import validate_guest_skills
from gameplay.models import MissionTemplate
from guests.models import GuestTemplate, Skill
from tests.test_zhanlong_boss_skills import FixedRng, damage, passives, skill, unit

rules = import_module("gameplay.migrations.0234_zhaoyun_dragon_assault")
base = import_module("gameplay.migrations.0220_zhanlong_quest_chain")


def zhaoyun(**kwargs):
    return unit(luck=150, skills=[skill("dragon_assault")], **kwargs)


@pytest.mark.parametrize("hp,boosted", [(35001, False), (35000, False), (34999, True), (1, True)])
def test_each_target_gets_execute_bonus_only_strictly_below_thirty_five_percent(hp, boosted):
    actor = zhaoyun()
    target = unit(side="defender", hp=hp)
    current = actor.skills[0]
    plain = {k: v for k, v in current.items() if k not in {"execute_hp_ratio_lt", "execute_multiplier"}}
    normal_hit = damage(actor, target, [plain])
    current_hit = damage(actor, target, [current])
    if boosted:
        assert current_hit > normal_hit * 1.45
    else:
        assert current_hit == normal_hit
    assert damage(actor, target) == damage(actor, unit(side="defender"))


@pytest.mark.parametrize("living", [1, 2, 3, 5])
def test_assault_selects_up_to_three_distinct_living_targets(living):
    actor = zhaoyun()
    enemies = [unit(side="defender", name=f"敌将{i}") for i in range(living)] + [unit(side="defender", hp=0)]
    selection = select_attack_targets(actor, [actor], enemies, FixedRng(0.4), trigger_skills)
    assert len(selection.engaged_targets) == len({id(target) for target in selection.engaged_targets}) == min(3, living)
    assert all(target.hp > 0 for target in selection.engaged_targets)


def test_assault_uses_luck_and_both_force_and_agility():
    actor = zhaoyun()
    assert trigger_skills(actor, FixedRng(0.4))
    actor.luck = 50
    assert not trigger_skills(actor, FixedRng(0.4))
    target = unit(side="defender")
    current = actor.skills[0]
    original = damage(actor, target, [current])
    actor.force_attr += 200
    assert damage(actor, target, [current]) > original
    actor.force_attr -= 200
    actor.agility += 200
    assert damage(actor, target, [current]) > original


def test_one_attack_checks_three_target_health_ratios_independently(monkeypatch):
    actor = zhaoyun()
    targets = [unit(side="defender", name=f"敌将{i}", hp=hp) for i, hp in enumerate([34999, 35000, 90000])]
    expected = {target.name: damage(actor, target, actor.skills) for target in targets}
    monkeypatch.setattr("battle.simulation.attack_execution._trigger_attack_skills", lambda *_args: actor.skills)
    result = perform_attack(actor, [actor], targets, FixedRng(), round_priority=0)
    hits = [result, *result.get("additional_targets", [])]
    assert len(hits) == 3
    assert {hit["target"]: hit["damage"] for hit in hits} == expected
    assert expected[targets[0].name] > expected[targets[1].name]
    assert expected[targets[1].name] == expected[targets[2].name]


def test_zhaoyun_no_longer_receives_old_damage_reduction_or_counter():
    actor = unit()
    target = zhaoyun(side="defender")
    before = damage(actor, target)
    passives("battle_start", target, [actor], [target])
    assert not target.battle_modifiers
    assert damage(actor, target) == before
    result = apply_damage_results(actor, target, 100, FixedRng(0.0))
    assert result.counter_damage == 0


def test_legacy_inclusive_execute_still_applies_at_exact_threshold():
    actor = unit()
    target = unit(side="defender", hp=35000)
    current = skill("hundred_pace_shot")
    plain = {k: v for k, v in current.items() if k not in {"execute_hp_ratio", "execute_multiplier"}}
    assert damage(actor, target, [current]) > damage(actor, target, [plain]) * 1.4


@pytest.mark.parametrize(
    "active",
    [
        {"execute_hp_ratio_lt": True, "execute_multiplier": 1.5},
        {"execute_hp_ratio_lt": 1.1, "execute_multiplier": 1.5},
        {"execute_hp_ratio_lt": 0.35},
        {"execute_multiplier": 1.5},
        {"execute_hp_ratio": 0.35, "execute_hp_ratio_lt": 0.35, "execute_multiplier": 1.5},
    ],
)
def test_assault_rejects_invalid_or_ambiguous_execute_options(active):
    config = deepcopy(rules.SKILL_DEFAULTS["zhanlong_dragon_assault"])
    config.update(key="invalid_dragon_assault", passive_config={"active": active})
    assert not validate_guest_skills({"skills": [config]}).is_valid


@pytest.mark.django_db
def test_migration_replaces_only_zhaoyun_skill_and_preserves_other_templates():
    skill_defaults = {**base.FALLBACK_SKILL_DEFAULTS, **base.SKILL_DEFAULTS}
    for key in base.ENEMY_DEFAULTS:
        template, _ = GuestTemplate.objects.update_or_create(key=key, defaults=base.ENEMY_DEFAULTS[key])
        for skill_key in base.ENEMY_SKILLS[key]:
            Skill.objects.update_or_create(key=skill_key, defaults=skill_defaults[skill_key])
        template.initial_skills.set(Skill.objects.filter(key__in=base.ENEMY_SKILLS[key]))
    MissionTemplate.objects.update_or_create(
        key="zhanlong_wuhu_shizhen",
        defaults={**base.MISSION_DEFAULTS["zhanlong_wuhu_shizhen"], "mission_card_daily_limit": 5},
    )
    before = {row["key"]: row for row in GuestTemplate.objects.filter(key__in=base.ENEMY_DEFAULTS).values()}
    for _ in range(2):
        rules.sync_zhaoyun_dragon_assault(apps, SimpleNamespace(connection=connection))
    for key, original in before.items():
        template = GuestTemplate.objects.get(key=key)
        assert GuestTemplate.objects.filter(pk=template.pk).values().get() == {
            **original,
            **rules.GUEST_UPDATES.get(key, {}),
        }
        expected = rules.GUEST_SKILLS.get(key, base.ENEMY_SKILLS[key])
        assert set(template.initial_skills.values_list("key", flat=True)) == set(expected)
    stored = Skill.objects.get(key="zhanlong_dragon_assault")
    assert _serialize_skill(stored)["execute_hp_ratio_lt"] == 0.35
    data = Path(__file__).resolve().parents[1] / "data"
    rows = yaml.safe_load((data / "guest_skills.yaml").read_text())["skills"]
    current = next(row for row in rows if row["key"] == stored.key)
    assert {k: v for k, v in current.items() if k != "key"} == rules.SKILL_DEFAULTS[stored.key]
