from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from battle.arena_coop import (
    adjust_arena_coop_damage,
    configure_arena_coop_enemy_guest,
    sync_arena_coop_combat_state,
    try_trigger_arena_coop_pre_action_heal,
)
from battle.combatants_pkg import assign_agility_based_priorities, build_guest_combatants, build_named_ai_guests
from battle.constants import get_battle_config
from battle.simulation.battle_flow import simulate_battle
from guests.models import Guest, GuestTemplate, Skill


def _make_guest(template_key: str, *, base_hp: int) -> SimpleNamespace:
    template = SimpleNamespace(key=template_key, base_hp=base_hp)
    return SimpleNamespace(
        template=template,
        level=1,
        force=10,
        intellect=10,
        defense_stat=10,
        agility=10,
        luck=10,
        hp_bonus=0,
        current_hp=1,
        attack_bonus=0,
        defense_bonus=0,
    )


def _make_unit(
    template_key: str,
    *,
    hp: int,
    max_hp: int,
    side: str = "defender",
    name: str | None = None,
    is_boss: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name or template_key,
        template_key=template_key,
        hp=hp,
        max_hp=max_hp,
        side=side,
        kind="guest",
        is_boss=is_boss,
        battle_modifiers={},
        battle_state={},
    )


def test_configure_arena_coop_enemy_guest_applies_fixed_final_hp():
    boss = _make_guest("arena_gl_top_zhang_wuji_boss", base_hp=6200)
    yang = _make_guest("arena_gl_top_yang_xiao_guard", base_hp=3600)

    assert configure_arena_coop_enemy_guest(boss) is True
    assert configure_arena_coop_enemy_guest(yang) is True

    assert boss.current_hp == 300000
    assert boss.defense_stat > 500
    assert boss.hp_bonus > 0
    assert yang.current_hp == 200000
    assert yang.hp_bonus > 0


def test_sync_arena_coop_combat_state_updates_boss_phase_and_modifiers():
    boss = _make_unit("arena_gl_top_zhang_wuji_boss", hp=300000, max_hp=300000, name="张无忌", is_boss=True)
    yang = _make_unit("arena_gl_top_yang_xiao_guard", hp=200000, max_hp=200000, name="杨逍")
    wei = _make_unit("arena_gl_top_wei_yixiao_guard", hp=200000, max_hp=200000, name="韦一笑")
    front = _make_unit("arena_gl_top_five_flags_elite_front", hp=200000, max_hp=200000, name="洪水旗")
    rear = _make_unit("arena_gl_top_five_flags_elite_rear", hp=200000, max_hp=200000, name="烈火旗")

    events = sync_arena_coop_combat_state([], [boss, yang, wei, front, rear], round_no=1)

    assert events == []
    assert boss.battle_state["arena_coop_phase"] == 1
    assert boss.battle_modifiers["incoming_damage_multiplier"] < 0.6
    assert boss.battle_modifiers["outgoing_damage_multiplier"] > 1.2

    boss.hp = 180000
    events = sync_arena_coop_combat_state([], [boss, yang, wei, front, rear], round_no=2)

    assert boss.battle_state["arena_coop_phase"] == 2
    assert events[0]["actor"] == "张无忌"
    assert "二阶段" in events[0]["message"]

    boss.hp = 100000
    events = sync_arena_coop_combat_state([], [boss, yang, wei, front, rear], round_no=3)

    assert boss.battle_state["arena_coop_phase"] == 3
    assert "三阶段" in events[0]["message"]
    assert boss.battle_modifiers["self_heal_ratio_on_action"] >= 0.05


def test_try_trigger_arena_coop_pre_action_heal_restores_boss_hp():
    boss = _make_unit("arena_gl_top_zhang_wuji_boss", hp=120000, max_hp=300000, name="张无忌", is_boss=True)
    boss.battle_modifiers["self_heal_ratio_on_action"] = 0.05

    event = try_trigger_arena_coop_pre_action_heal(boss)

    assert event is not None
    assert event["effect"] == "九阳护体"
    assert event["healed"] == 15000
    assert boss.hp == 135000


def test_adjust_arena_coop_damage_applies_barrier_and_burst_softcap():
    actor = _make_unit("attacker_tpl", hp=10000, max_hp=10000, side="attacker", name="玩家门客")
    boss = _make_unit("arena_gl_top_zhang_wuji_boss", hp=300000, max_hp=300000, name="张无忌", is_boss=True)
    yang = _make_unit("arena_gl_top_yang_xiao_guard", hp=200000, max_hp=200000, name="杨逍")
    wei = _make_unit("arena_gl_top_wei_yixiao_guard", hp=200000, max_hp=200000, name="韦一笑")
    front = _make_unit("arena_gl_top_five_flags_elite_front", hp=200000, max_hp=200000, name="洪水旗")
    rear = _make_unit("arena_gl_top_five_flags_elite_rear", hp=200000, max_hp=200000, name="烈火旗")

    sync_arena_coop_combat_state([actor], [boss, yang, wei, front, rear], round_no=1)

    reduced = adjust_arena_coop_damage(actor, boss, 40000)

    assert reduced < 15000
    assert reduced >= 1


def test_sync_arena_coop_combat_state_clears_guard_buffs_after_boss_death():
    boss = _make_unit("arena_gl_top_zhang_wuji_boss", hp=300000, max_hp=300000, name="张无忌", is_boss=True)
    yang = _make_unit("arena_gl_top_yang_xiao_guard", hp=200000, max_hp=200000, name="杨逍")

    sync_arena_coop_combat_state([], [boss, yang], round_no=1)
    assert yang.battle_modifiers["outgoing_damage_multiplier"] > 1.0

    boss.hp = 0
    sync_arena_coop_combat_state([], [boss, yang], round_no=2)

    assert yang.battle_modifiers == {}


def test_arena_coop_battle_config_uses_extended_round_budget():
    config = get_battle_config("arena_coop")

    assert config["max_rounds"] == 24


def test_simulate_battle_counts_priority_rounds_toward_total_round_limit(monkeypatch):
    attacker = _make_unit("attacker_tpl", hp=1000, max_hp=1000, side="attacker", name="甲")
    defender = _make_unit("defender_tpl", hp=1000, max_hp=1000, side="defender", name="乙")

    monkeypatch.setattr(
        "battle.simulation.battle_flow.resolve_priority_phases",
        lambda attacker_units, defender_units, rng: (
            [{"round": 1, "events": [], "priority": -2}, {"round": 2, "events": [], "priority": -1}],
            3,
        ),
    )
    monkeypatch.setattr(
        "battle.simulation.battle_flow._resolve_standard_round",
        lambda attacker_units, defender_units, rng, round_no: {"round": round_no, "events": []},
    )
    monkeypatch.setattr("battle.simulation.battle_flow.alive", lambda units: list(units))

    result = simulate_battle(
        [attacker],
        [defender],
        random.Random(1),
        seed=1,
        travel_seconds=0,
        config={"max_rounds": 24, "loot_pool": {}},
        max_rounds=24,
    )

    assert len(result.rounds) == 24
    assert result.rounds[-1]["round"] == 24


@pytest.mark.django_db
def test_full_arena_coop_simulation_runs_phase_mechanics_end_to_end():
    skill_payloads = {
        "gl_top_nine_yang_guard": {
            "name": "九阳护体",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.8,
            "damage_formula": {"base": 0},
            "targets": 1,
        },
        "gl_top_qiankun_shift": {
            "name": "乾坤大挪移",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.75,
            "damage_formula": {"base": 0},
            "targets": 1,
        },
        "gl_top_mingjiao_command": {
            "name": "明教号令",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.75,
            "damage_formula": {"base": 0},
            "targets": 1,
        },
        "gl_top_holy_flame_rage": {
            "name": "圣火狂势",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.75,
            "damage_formula": {"base": 0},
            "targets": 1,
        },
        "gl_top_cold_blood_swoop": {
            "name": "蝠影掠命",
            "rarity": "purple",
            "kind": "active",
            "base_probability": 0.8,
            "damage_formula": {"base": 1680, "ally": {"force": 0.6, "agility": 1.2}, "enemy": {"defense": 0.25}},
            "targets": 1,
        },
        "gl_top_five_flags_barrier": {
            "name": "五行旗护阵",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.75,
            "damage_formula": {"base": 0},
            "targets": 1,
        },
        "gl_top_qiankun_holy_flame": {
            "name": "乾坤圣火印",
            "rarity": "purple",
            "kind": "active",
            "base_probability": 0.8,
            "damage_formula": {
                "base": 3600,
                "ally": {"force": 0.65, "intellect": 0.4},
                "enemy": {"defense": 0.28},
            },
            "targets": 2,
        },
        "gl_top_left_envoy_edge": {
            "name": "光明左使",
            "rarity": "purple",
            "kind": "active",
            "base_probability": 0.78,
            "damage_formula": {
                "base": 2400,
                "ally": {"force": 0.58, "intellect": 0.35},
                "enemy": {"defense": 0.22},
            },
            "targets": 2,
        },
        "gl_top_banner_fire_volley": {
            "name": "旗火齐发",
            "rarity": "purple",
            "kind": "active",
            "base_probability": 0.72,
            "damage_formula": {
                "base": 1800,
                "ally": {"force": 0.4, "intellect": 0.18},
                "enemy": {"defense": 0.16},
            },
            "targets": 2,
        },
    }
    for key, payload in skill_payloads.items():
        Skill.objects.create(key=key, **payload)

    template_payloads = {
        "arena_gl_top_zhang_wuji_boss": {
            "name": "张无忌",
            "base_attack": 268,
            "base_intellect": 226,
            "base_defense": 240,
            "base_agility": 188,
            "base_luck": 118,
            "base_hp": 6200,
            "skills": [
                "gl_top_nine_yang_guard",
                "gl_top_qiankun_shift",
                "gl_top_mingjiao_command",
                "gl_top_holy_flame_rage",
                "gl_top_qiankun_holy_flame",
            ],
        },
        "arena_gl_top_yang_xiao_guard": {
            "name": "杨逍",
            "base_attack": 224,
            "base_intellect": 186,
            "base_defense": 192,
            "base_agility": 176,
            "base_luck": 92,
            "base_hp": 3600,
            "skills": ["gl_top_mingjiao_command", "gl_top_left_envoy_edge"],
        },
        "arena_gl_top_wei_yixiao_guard": {
            "name": "韦一笑",
            "base_attack": 214,
            "base_intellect": 148,
            "base_defense": 182,
            "base_agility": 228,
            "base_luck": 96,
            "base_hp": 3300,
            "skills": ["gl_top_cold_blood_swoop"],
        },
        "arena_gl_top_five_flags_elite_front": {
            "name": "五行旗精锐",
            "base_attack": 198,
            "base_intellect": 132,
            "base_defense": 206,
            "base_agility": 150,
            "base_luck": 70,
            "base_hp": 2950,
            "skills": ["gl_top_five_flags_barrier", "gl_top_banner_fire_volley"],
        },
        "arena_gl_top_five_flags_elite_rear": {
            "name": "五行旗精锐",
            "base_attack": 194,
            "base_intellect": 136,
            "base_defense": 198,
            "base_agility": 156,
            "base_luck": 72,
            "base_hp": 2950,
            "skills": ["gl_top_five_flags_barrier", "gl_top_banner_fire_volley"],
        },
    }
    for key, payload in template_payloads.items():
        skills = payload.pop("skills")
        template = GuestTemplate.objects.create(
            key=key,
            name=payload["name"],
            archetype="military",
            rarity="purple",
            default_gender="unknown",
            default_morality=50,
            recruitable=False,
            base_attack=payload["base_attack"],
            base_intellect=payload["base_intellect"],
            base_defense=payload["base_defense"],
            base_agility=payload["base_agility"],
            base_luck=payload["base_luck"],
            base_hp=payload["base_hp"],
        )
        template.initial_skills.set(list(Skill.objects.filter(key__in=skills)))

    attacker_template = GuestTemplate.objects.create(
        key="arena_coop_reviewer_attacker_tpl",
        name="实测门客",
        archetype="military",
        rarity="orange",
        default_gender="unknown",
        default_morality=50,
        recruitable=False,
        base_attack=600,
        base_intellect=400,
        base_defense=420,
        base_agility=280,
        base_luck=140,
        base_hp=12000,
    )
    attackers = []
    for idx in range(15):
        guest = Guest(
            template=attacker_template,
            level=100,
            force=1550,
            intellect=900,
            defense_stat=950,
            agility=420 - (idx % 3) * 20,
            luck=150,
            hp_bonus=25000,
            current_hp=1,
        )
        guest.current_hp = guest.max_hp
        attackers.append(guest)

    defender_guests = build_named_ai_guests(
        [
            {"key": "arena_gl_top_zhang_wuji_boss", "label": "张无忌"},
            {"key": "arena_gl_top_yang_xiao_guard", "label": "杨逍"},
            {"key": "arena_gl_top_wei_yixiao_guard", "label": "韦一笑"},
            {"key": "arena_gl_top_five_flags_elite_front", "label": "五行旗前阵"},
            {"key": "arena_gl_top_five_flags_elite_rear", "label": "五行旗后阵"},
        ],
        level=90,
    )
    for idx, guest in enumerate(defender_guests):
        configure_arena_coop_enemy_guest(guest)
        setattr(guest, "_is_boss", idx == 0)

    attacker_units = build_guest_combatants(attackers, side="attacker", limit=len(attackers))
    defender_units = build_guest_combatants(defender_guests, side="defender", limit=len(defender_guests))
    assign_agility_based_priorities(attacker_units, defender_units)

    result = simulate_battle(
        attacker_units,
        defender_units,
        random.Random(7),
        seed=7,
        travel_seconds=0,
        config={"max_rounds": 24, "loot_pool": {}},
        max_rounds=24,
    )

    boss = next(unit for unit in defender_units if unit.template_key == "arena_gl_top_zhang_wuji_boss")
    phase_events = [
        event for round_data in result.rounds for event in round_data["events"] if event.get("status") == "phase_shift"
    ]
    assert result.rounds
    assert boss.max_hp == 300000
    assert boss.hp < boss.max_hp
    assert phase_events
