import random
from types import SimpleNamespace

import pytest

from battle.combat_math import SLAUGHTER_MULTIPLIER, effective_attack_value, effective_defense_value, troop_unit_hp
from battle.combatants_pkg.guest_builder import serialize_skills
from battle.simulation.constants import GUEST_SKILL_VS_TROOP_MULTIPLIER
from battle.simulation.damage_calculation import calculate_attack_damage, process_status_effects
from battle.skills import apply_skill_statuses, skill_damage_bonus, trigger_skills
from battle.status_manager import prepare_combatants_for_round
from core.exceptions import GameError


def make_unit(**kwargs):
    defaults = {
        "name": "Tester",
        "attack": 200,
        "defense": 100,
        "hp": 1000,
        "max_hp": 1000,
        "side": "attacker",
        "rarity": "legendary",
        "luck": 500,
        "agility": 120,
        "priority": 0,
        "kind": "guest",
        "troop_strength": 0,
        "initial_troop_strength": 0,
        "unit_attack": 0,
        "unit_defense": 0,
        "unit_hp": 0,
        "status_effects": {},
        "skills": [],
        "has_acted_this_round": False,
        "current_round": 0,
        "last_round_acted": 0,
        "troop_class": "",
        "tech_effects": {},
    }
    defaults.update(kwargs)
    if defaults["initial_troop_strength"] == 0 and defaults["troop_strength"]:
        defaults["initial_troop_strength"] = defaults["troop_strength"]
    return SimpleNamespace(**defaults)


class FixedRng:
    def uniform(self, _a, _b):
        return 1.0

    def random(self):
        return 1.0


class _SkillCollection:
    def __init__(self, skills):
        self._skills = skills

    def all(self):
        return list(self._skills)


def test_trigger_skills_allows_only_one_active():
    """
    测试技能触发规则：
    - 所有技能的触发率由运势决定：0.95 * (luck/300)^0.85，上限95%
    - 主动技能最多触发1个
    - 被动技能可以全部触发

    使用 luck=300 确保触发率达到95%上限，配合固定种子保证测试稳定性。
    """
    attacker = make_unit(
        luck=300,  # 高运势确保95%触发率：0.95 * (300/300)^0.85 = 95%
        skills=[
            {"name": "Active A", "kind": "active"},
            {"name": "Active B", "kind": "active"},
            {"name": "Passive Spur", "kind": "passive"},
        ],
    )
    rng = random.Random(0)

    triggered = trigger_skills(attacker, rng)

    actives = [skill for skill in triggered if skill["kind"] == "active"]
    passives = [skill for skill in triggered if skill["kind"] == "passive"]
    assert len(actives) == 1  # 最多1个主动技能
    assert len(passives) == 1  # 被动技能触发


def test_serialize_skills_preserves_passive_config_for_template_skills():
    passive_skill = SimpleNamespace(
        key="gl_top_nine_yang_guard",
        name="九阳护体",
        base_power=0,
        base_probability=0.75,
        kind="passive",
        status_effect="",
        status_probability=0.0,
        status_duration=1,
        damage_formula={},
        targets=1,
        passive_config={
            "triggers": [
                {
                    "timing": "action_before",
                    "effects": [{"type": "heal_ratio", "value": 0.05, "max_hp_based": True}],
                }
            ]
        },
    )
    guest = SimpleNamespace(
        pk=None,
        template=SimpleNamespace(initial_skills=_SkillCollection([passive_skill])),
    )

    payload = serialize_skills(guest)

    assert payload[0]["kind"] == "passive"
    assert payload[0]["passive_config"]["triggers"][0]["timing"] == "action_before"


def test_skill_damage_bonus_uses_calculator(monkeypatch):
    attacker = make_unit(attack=300, force_attr=200)
    target = make_unit(defense=150)
    skill = {
        "name": "Formula",
        "damage_formula": {
            "base": 10,
            "ally": {"force": 0.1},
            "enemy": {"defense": 0.05},
        },
    }
    bonus = skill_damage_bonus([skill], attacker, target)
    expected = int(10 + 0.1 * attacker.force_attr - 0.05 * target.defense)
    assert bonus == expected


def test_apply_skill_statuses_respects_pending(monkeypatch):
    target = make_unit(status_effects={}, has_acted_this_round=True)
    skills = [
        {
            "name": "Stun",
            "status_effect": "stunned",
            "status_probability": 1.0,
            "status_duration": 2,
        }
    ]
    rng = random.Random(0)

    inflicted = apply_skill_statuses(skills, target, rng)

    assert inflicted == ["眩晕"]
    payload = target.status_effects["stunned"]
    assert payload["pending"] == 2  # deferred because unit already acted
    assert payload["active"] == 0


def test_prepare_combatants_for_round_promotes_pending():
    stunned = {"active": 0, "pending": 2}
    frozen = {"active": 1, "pending": 0}
    ally = make_unit(status_effects={"stunned": stunned, "frozen": frozen}, has_acted_this_round=True)
    enemy = make_unit(status_effects={}, has_acted_this_round=True)

    prepare_combatants_for_round([ally], [enemy], round_no=3, promote_pending=True)

    assert ally.has_acted_this_round is False
    assert enemy.has_acted_this_round is False
    assert ally.current_round == 3
    assert enemy.current_round == 3
    assert ally.status_effects["stunned"]["active"] == 2
    assert ally.status_effects["stunned"]["pending"] == 0


def test_effective_defense_value_guest_vs_troop_uses_unit_stat():
    troop = make_unit(kind="troop", defense=360, troop_strength=180, initial_troop_strength=180, unit_defense=30)
    guest = make_unit(kind="guest")
    value = effective_defense_value(troop, guest)
    import math

    expected = max(1, int(30 * max(1.0, math.sqrt(180) / 2.0)))
    assert value == expected


def test_effective_defense_value_troop_vs_troop_scales():
    troop = make_unit(kind="troop", defense=360, troop_strength=180, initial_troop_strength=180, unit_defense=30)
    attacker = make_unit(kind="troop", troop_strength=120, initial_troop_strength=120)
    value = effective_defense_value(troop, attacker)
    import math

    expected = max(1, int(30 * max(1.0, math.sqrt(180) / 2.0)))
    assert value == expected


def test_effective_attack_value_troop_vs_guest_uses_smaller_multiplier():
    troop = make_unit(kind="troop", unit_attack=50, troop_strength=100, initial_troop_strength=100)
    guest = make_unit(kind="guest")
    value = effective_attack_value(troop, guest)
    expected = max(1, int(50 * max(1.0, 100 / 1.5)))
    assert value == expected


def test_effective_attack_value_troop_vs_troop_uses_standard_multiplier():
    troop = make_unit(kind="troop", unit_attack=40, troop_strength=120, initial_troop_strength=120)
    enemy = make_unit(kind="troop")
    value = effective_attack_value(troop, enemy)
    expected = max(1, int(40 * max(1.0, 120 / 1.0)))
    assert value == expected


def test_troop_unit_hp_prefers_explicit_unit_hp():
    troop = make_unit(kind="troop", unit_hp=80)
    assert troop_unit_hp(troop) == 80


def test_troop_unit_hp_falls_back_to_average():
    troop = make_unit(kind="troop", unit_hp=0, max_hp=500, troop_strength=50, initial_troop_strength=50)
    troop.unit_hp = None
    assert troop_unit_hp(troop) == max(1, int(500 / 50))


def test_effective_attack_value_rejects_invalid_current_troop_strength():
    troop = make_unit(kind="troop", unit_attack=40, troop_strength="bad", initial_troop_strength=120)
    enemy = make_unit(kind="troop")

    with pytest.raises(GameError, match="invalid battle current troop strength"):
        effective_attack_value(troop, enemy)


def test_effective_defense_value_rejects_invalid_unit_defense():
    troop = make_unit(kind="troop", defense=360, troop_strength=180, initial_troop_strength=180, unit_defense="bad")
    attacker = make_unit(kind="troop", troop_strength=120, initial_troop_strength=120)

    with pytest.raises(GameError, match="invalid battle unit_defense"):
        effective_defense_value(troop, attacker)


def test_troop_unit_hp_rejects_invalid_max_hp():
    troop = make_unit(kind="troop", unit_hp=None, max_hp="bad", troop_strength=50, initial_troop_strength=50)

    with pytest.raises(GameError, match="invalid battle max_hp"):
        troop_unit_hp(troop)


def test_guest_vs_troop_normal_attack_keeps_slaughter_multiplier():
    actor = make_unit(kind="guest", attack=1000, priority=0)
    target = make_unit(kind="troop", side="defender", unit_defense=10, troop_strength=200, unit_hp=10)
    rng = FixedRng()

    result = calculate_attack_damage(actor, target, skills=[], rng=rng, round_priority=0)

    reduction = target.unit_defense / (target.unit_defense + 50)
    base_damage = max(1, int(actor.attack * (1 - reduction)))
    expected = int(base_damage * 10)
    assert result.damage == expected


def test_guest_vs_troop_skill_damage_uses_reduced_skill_multiplier():
    actor = make_unit(kind="guest", attack=1000, priority=0)
    target = make_unit(kind="troop", side="defender", unit_defense=10, troop_strength=200, unit_hp=10)
    skill = {"name": "Flat Bonus", "damage_formula": {"base": 2000}}
    rng = FixedRng()

    result = calculate_attack_damage(actor, target, skills=[skill], rng=rng, round_priority=0)

    reduction = target.unit_defense / (target.unit_defense + 50)
    base_damage = max(1, int(actor.attack * (1 - reduction)))
    expected = int(base_damage * SLAUGHTER_MULTIPLIER + 2000 * GUEST_SKILL_VS_TROOP_MULTIPLIER)
    assert result.damage == expected


def test_process_status_effects_damage_penalty_requires_damage_argument():
    actor = make_unit()
    target = make_unit(side="defender")
    rng = FixedRng()

    with pytest.raises(AssertionError, match="damage_penalty phase requires 'damage'"):
        process_status_effects(actor, target, [], rng, phase="damage_penalty")


def test_process_status_effects_damage_penalty_applies_penalty_without_rng(monkeypatch):
    actor = make_unit(status_effects={"morale_down": {"active": 2, "pending": 0}})
    target = make_unit(side="defender")
    calls = {"random": 0}

    class _CountingRng(FixedRng):
        def random(self):
            calls["random"] += 1
            return super().random()

    monkeypatch.setattr("battle.utils.status_effects.get_damage_penalty", lambda _actor: 0.25)

    damage = process_status_effects(actor, target, [], _CountingRng(), phase="damage_penalty", damage=100)

    assert damage == 75
    assert calls["random"] == 0
