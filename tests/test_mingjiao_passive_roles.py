from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from battle.arena_coop import adjust_arena_coop_damage
from battle.passives import run_passives_for_timing
from battle.simulation.damage_application import apply_damage_results
from battle.status_manager import prepare_combatants_for_round
from tests.test_battle_damage_invariants import _make_guest
from tests.test_qiankun_round_start import FixedRng


@pytest.fixture
def boss_skills():
    skills = yaml.safe_load(Path("data/arena_coop_special_skills.yaml").read_text())["skills"]
    keys = {"gl_top_mingjiao_command", "gl_top_holy_flame_rage", "gl_top_qiankun_shift"}
    return [deepcopy(skill) for skill in skills if skill["key"] in keys]


def make_team(skills, hp, guards):
    boss = _make_guest(name="张无忌", side="defender", hp=100000)
    boss.template_key = "arena_gl_top_zhang_wuji_boss"
    boss.hp = hp
    boss.skills = skills
    team = [boss]
    for index in range(4):
        guard = _make_guest(name=f"护卫{index}", side="defender")
        guard.template_key = f"arena_gl_top_guard_{index}"
        if index >= guards:
            guard.hp = 0
        team.append(guard)
    return boss, team


def begin_round(boss, team, attacker, round_no, roll):
    prepare_combatants_for_round([attacker], team, round_no)
    events = []
    run_passives_for_timing(
        "round_start",
        actor=boss,
        target=None,
        attacker_team=[attacker],
        defender_team=team,
        round_no=round_no,
        event_sink=events,
        rng=FixedRng(roll),
    )
    return events


@pytest.mark.parametrize("guards", range(5))
@pytest.mark.parametrize(
    "hp,rage",
    [(100000, 1), (75001, 1), (75000, 1.25), (45001, 1.25), (45000, 1.5), (20000, 1.5)],
)
@pytest.mark.parametrize("roll,active", [(0.1, True), (0.99, False)])
def test_actual_boss_skills_preserve_phase_damage_and_separate_defense(boss_skills, guards, hp, rage, roll, active):
    boss, team = make_team(boss_skills, hp, guards)
    attacker = _make_guest(name="攻击者", side="attacker", hp=200000)
    events = begin_round(boss, team, attacker, 1, roll)
    assert adjust_arena_coop_damage(boss, attacker, 10000) == pytest.approx(10000 * (1 + guards * 0.08) * rage)
    assert sum(e["effect"] == "明教号令" for e in events) == bool(guards)
    assert sum(e["effect"] == "圣火狂势" for e in events) == (rage > 1)
    damage = adjust_arena_coop_damage(attacker, boss, 32000)
    assert damage == (21600 if active else 32000)
    application = apply_damage_results(attacker, boss, 1000, FixedRng(0.1))
    assert application.reflect_damage == (100 if active else 0)
    if active:
        assert application.reflect_effect == "乾坤大挪移"
    assert sum(e["effect"] == "乾坤大挪移" for e in events) == active


def test_guard_deaths_and_healing_recalculate_without_leaking_old_buffs(boss_skills):
    boss, team = make_team(boss_skills, 40000, 4)
    attacker = _make_guest(name="攻击者", side="attacker", hp=200000)
    begin_round(boss, team, attacker, 1, 0.1)
    assert adjust_arena_coop_damage(boss, attacker, 10000) == pytest.approx(19800)
    for guard in team[1:]:
        guard.hp = 0
    boss.hp = 70000
    begin_round(boss, team, attacker, 2, 0.99)
    assert adjust_arena_coop_damage(boss, attacker, 10000) == pytest.approx(12500)
    assert adjust_arena_coop_damage(attacker, boss, 32000) == 32000
    assert "reflect_name" not in boss.battle_modifiers
    boss.hp = 100000
    assert begin_round(boss, team, attacker, 3, 0.99) == []
    assert adjust_arena_coop_damage(boss, attacker, 10000) == 10000
