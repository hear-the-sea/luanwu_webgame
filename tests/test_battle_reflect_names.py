import random
from pathlib import Path

import pytest
import yaml
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from battle.modifier_lifecycle import clear_modifier_scope
from battle.passives import run_passives_for_timing
from battle.simulation.attack_execution import perform_attack
from battle.simulation.damage_application import apply_damage_results
from tests.test_battle_damage_invariants import _make_guest, _make_troop


def _set_reflect(target, skill):
    target.skills = [skill]
    run_passives_for_timing(
        "round_start",
        actor=target,
        target=None,
        attacker_team=[],
        defender_team=[target],
        round_no=1,
        event_sink=[],
        rng=random.Random(1),
    )


def test_actual_boss_passives_record_active_reflection_source():
    skills = yaml.safe_load(Path("data/arena_coop_special_skills.yaml").read_text())["skills"]
    skills = {skill["key"]: skill for skill in skills}
    target = _make_guest(name="张无忌", side="defender", hp=70000)
    target.max_hp = 200000
    target.template_key = "arena_gl_top_zhang_wuji_boss"
    actor = _make_guest(name="攻击者", side="attacker", hp=20000)
    _set_reflect(target, skills["gl_top_holy_flame_rage"])
    assert target.battle_modifiers["reflect_name"] == "圣火狂势"
    application = apply_damage_results(actor, target, 1000, random.Random(1))
    assert application.reflect_damage == 100
    assert application.reflect_effect == "圣火狂势"

    target.skills = [skills["gl_top_qiankun_shift"]]
    run_passives_for_timing(
        "hit_taken",
        actor=target,
        target=actor,
        attacker_team=[actor],
        defender_team=[target],
        round_no=1,
        event_sink=[],
        rng=random.Random(1),
    )
    event = perform_attack(actor, [actor], [target], random.Random(1))
    assert event["reflect_damage"] > 0
    assert event["reflect_effect"] == "乾坤大挪移"
    clear_modifier_scope(target, "round")
    assert "reflect_name" not in target.battle_modifiers


def test_sword_technology_retains_its_name_and_damage():
    actor = _make_guest(name="攻击者", side="attacker")
    target = _make_troop(name="剑圣", side="defender")
    target.troop_class = "jian"
    target.tech_effects = {"damage_reflect": 0.2}
    application = apply_damage_results(actor, target, 100, random.Random(1))
    assert application.reflect_damage == 20
    assert application.reflect_effect == "护身剑罡"


def test_ranged_hit_names_passive_reflection_when_sword_technology_is_disabled():
    actor = _make_guest(name="攻击者", side="attacker")
    target = _make_troop(name="剑圣", side="defender")
    target.troop_class = "jian"
    target.tech_effects = {"damage_reflect": 0.2}
    target.battle_modifiers = {"reflect_ratio": 0.1, "reflect_cap": 100, "reflect_name": "乾坤大挪移"}

    application = apply_damage_results(actor, target, 100, random.Random(1), ranged_attack=True)

    assert application.reflect_damage == 10
    assert application.reflect_effect == "乾坤大挪移"


@pytest.mark.parametrize("secondary", [False, True])
@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
@pytest.mark.parametrize("source", ["乾坤大挪移", "圣火狂势", "明教号令"])
def test_report_renders_actual_source_on_each_hit(secondary, viewer, source):
    hit = {
        "actor": "攻击者",
        "target": "张无忌",
        "damage": 100,
        "side": "attacker",
        "kind": "guest",
        "reflect_damage": 10,
        "reflect_effect": source,
    }
    event = (
        {
            "actor": "攻击者",
            "target": "杨逍",
            "damage": 100,
            "side": "attacker",
            "kind": "guest",
            "additional_targets": [hit],
        }
        if secondary
        else hit
    )
    html = render_to_string(
        "battle/partials/round_event_list.html",
        {"events": [event], "my_side": viewer, "is_spectator": viewer == "spectator"},
    )
    text = "".join(strip_tags(html).split())
    assert f"张无忌爆发{source}，造成伤害10" in text
    assert "护身剑罡" not in text


def test_old_boss_report_uses_neutral_name_without_guessing_skill():
    hit = {
        "actor": "攻击者",
        "target": "张无忌",
        "damage": 100,
        "kind": "guest",
        "reflect_damage": 10,
        "target_template_key": "arena_gl_top_zhang_wuji_boss",
    }
    text = strip_tags(render_to_string("battle/partials/round_event_list.html", {"events": [hit]}))
    assert "爆发反震" in text
    assert "护身剑罡" not in text
