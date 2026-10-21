"""恶来专属技能的持久化、破甲与死战阶段回归验证。"""

import json

import pytest

from battle.combatants_pkg.guest_builder import _serialize_skill
from battle.simulation.target_selection import select_attack_targets
from battle.skills import apply_skill_statuses, trigger_skills
from battle.utils.status_effects import (
    capture_active_status_effects,
    consume_active_status_effects,
    get_defense_multiplier,
)
from core.utils.yaml_validators import validate_guest_skills
from guests.models import Skill
from tests.test_zhanlong_boss_skills import CATALOG, FixedRng, damage, passives, unit


def skill(suffix):
    payload = next(entry for entry in CATALOG["skills"] if entry["key"] == "surname_li_elai_" + suffix)
    payload = json.loads(json.dumps(payload))
    fields = {field.name for field in Skill._meta.fields}
    return _serialize_skill(Skill(**{key: value for key, value in payload.items() if key in fields}))


def boss(**overrides):
    return unit(skills=[skill("crushing_blow"), skill("last_stand")], **overrides)


def test_elai_catalog_validates():
    result = validate_guest_skills(CATALOG)
    assert result.is_valid, result


@pytest.mark.parametrize("luck", [1, 100, 300, 999])
def test_crushing_blow_has_fixed_probability_after_serialization(luck):
    actor = boss(luck=luck)
    assert actor.skills[0]["trigger_chance"] == 0.35
    assert any(s["kind"] == "active" for s in trigger_skills(actor, FixedRng(0.349)))
    assert not any(s["kind"] == "active" for s in trigger_skills(actor, FixedRng(0.351)))


def test_crushing_blow_breaks_armor_for_two_actions_without_stacking():
    actor, target = boss(), unit(side="defender")
    initial = damage(actor, target)
    for _ in range(2):
        assert apply_skill_statuses([skill("crushing_blow")], target, FixedRng()) == ["破甲"]
    assert get_defense_multiplier(target) == 0.75
    assert target.defense == 800
    assert damage(actor, target) > initial
    consume_active_status_effects(target, capture_active_status_effects(target))
    assert damage(actor, target) > initial
    consume_active_status_effects(target, capture_active_status_effects(target))
    assert damage(actor, target) == initial


@pytest.mark.parametrize("hp,enraged", [(30001, False), (30000, False), (29999, True), (1, True), (0, False)])
@pytest.mark.parametrize("timing", ["hit_taken", "action_before"])
def test_last_stand_only_triggers_alive_and_strictly_below_thirty_percent(hp, enraged, timing):
    actor = boss(hp=hp)
    events = passives(timing, actor, [actor], [])
    assert actor.skills[0]["targets"] == (3 if enraged else 2)
    assert actor.luck == 60
    assert len(events) == int(enraged)


def test_last_stand_persists_without_retriggering_or_mutating_other_units():
    actor, other = boss(hp=29999), boss()
    original_skills = actor.skills
    other.skills = original_skills
    events = []
    for hp in [29999, 90000, 1]:
        actor.hp = hp
        for timing in ["hit_taken", "action_before"]:
            passives(timing, actor, [actor], [], events)
    assert len(events) == 1
    assert actor.skills[0]["targets"] == 3
    assert other.skills[0]["targets"] == original_skills[0]["targets"] == 2


@pytest.mark.parametrize("enraged,remaining,expected", [(False, 4, 2), (True, 4, 3), (True, 2, 2), (True, 1, 1)])
def test_crushing_blow_selects_distinct_living_targets(enraged, remaining, expected):
    actor = boss(hp=1 if enraged else 100000)
    passives("action_before", actor, [actor], [])
    enemies = [unit(side="defender", name=f"敌人{i}") for i in range(remaining)]
    enemies.append(unit(side="defender", hp=0))
    selection = select_attack_targets(actor, [actor], enemies, FixedRng(0.1), trigger_skills)
    assert len(selection.engaged_targets) == expected
    assert len({id(target) for target in selection.engaged_targets}) == expected
    assert all(target.hp > 0 for target in selection.engaged_targets)
