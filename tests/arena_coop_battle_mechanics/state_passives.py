from __future__ import annotations

import random

import pytest

from battle.arena_coop import (
    adjust_arena_coop_damage,
    configure_arena_coop_enemy_guest,
    sync_arena_coop_combat_state,
    try_trigger_arena_coop_pre_action_heal,
)
from battle.constants import get_battle_config
from battle.simulation.battle_flow import simulate_battle

from .support import apply_round_start_passives, make_guest, make_unit


def test_configure_arena_coop_enemy_guest_applies_fixed_final_hp():
    boss = make_guest("arena_gl_top_zhang_wuji_boss", base_hp=6200)
    yang = make_guest("arena_gl_top_yang_xiao_guard", base_hp=3600)

    assert configure_arena_coop_enemy_guest(boss) is True
    assert configure_arena_coop_enemy_guest(yang) is True

    assert boss.current_hp == 300000
    assert boss.defense_stat > 500
    assert boss.hp_bonus > 0
    assert yang.current_hp == 200000
    assert yang.hp_bonus > 0


def test_sync_arena_coop_combat_state_updates_boss_phase_and_modifiers():
    boss = make_unit("arena_gl_top_zhang_wuji_boss", hp=300000, max_hp=300000, name="张无忌", is_boss=True)
    yang = make_unit("arena_gl_top_yang_xiao_guard", hp=200000, max_hp=200000, name="杨逍")
    wei = make_unit("arena_gl_top_wei_yixiao_guard", hp=200000, max_hp=200000, name="韦一笑")
    front = make_unit("arena_gl_top_five_flags_elite_front", hp=200000, max_hp=200000, name="洪水旗")
    rear = make_unit("arena_gl_top_five_flags_elite_rear", hp=200000, max_hp=200000, name="烈火旗")

    events = sync_arena_coop_combat_state([], [boss, yang, wei, front, rear], round_no=1)

    assert events == []
    assert boss.battle_state["arena_coop_phase"] == 1
    assert boss.battle_modifiers == {}

    boss.hp = 180000
    events = sync_arena_coop_combat_state([], [boss, yang, wei, front, rear], round_no=2)

    assert boss.battle_state["arena_coop_phase"] == 2
    assert events[0]["actor"] == "张无忌"
    assert "二阶段" in events[0]["message"]
    assert boss.battle_modifiers == {}

    boss.hp = 100000
    events = sync_arena_coop_combat_state([], [boss, yang, wei, front, rear], round_no=3)

    assert boss.battle_state["arena_coop_phase"] == 3
    assert "三阶段" in events[0]["message"]
    assert boss.battle_modifiers == {}


def test_boss_round_start_passives_apply_phase_modifiers():
    boss = make_unit("arena_gl_top_zhang_wuji_boss", hp=300000, max_hp=300000, name="张无忌", is_boss=True)
    boss.skills = [
        {
            "key": "gl_top_mingjiao_command",
            "name": "明教号令",
            "kind": "passive",
            "passive_config": {
                "triggers": [
                    {
                        "timing": "round_start",
                        "conditions": {"self_template_in": ["arena_gl_top_zhang_wuji_boss"], "hp_ratio_gte": 0.700001},
                        "effects": [
                            {"type": "modify_outgoing_damage", "value": 1.32},
                            {"type": "set_softcap", "threshold": 12000, "overflow_ratio": 0.35},
                        ],
                    },
                    {
                        "timing": "round_start",
                        "conditions": {
                            "self_template_in": ["arena_gl_top_zhang_wuji_boss"],
                            "hp_ratio_lte": 0.7,
                            "hp_ratio_gte": 0.400001,
                        },
                        "effects": [
                            {"type": "modify_outgoing_damage", "value": 1.584},
                            {"type": "set_softcap", "threshold": 14000, "overflow_ratio": 0.35},
                            {"type": "set_reflect", "ratio": 0.06, "cap": 5000},
                        ],
                    },
                ]
            },
        },
        {
            "key": "gl_top_holy_flame_rage",
            "name": "圣火狂势",
            "kind": "passive",
            "passive_config": {
                "triggers": [
                    {
                        "timing": "round_start",
                        "conditions": {"self_template_in": ["arena_gl_top_zhang_wuji_boss"], "hp_ratio_lte": 0.4},
                        "effects": [
                            {"type": "modify_outgoing_damage", "value": 1.848},
                            {"type": "set_softcap", "threshold": 16000, "overflow_ratio": 0.35},
                            {"type": "set_reflect", "ratio": 0.1, "cap": 8000},
                        ],
                    }
                ]
            },
        },
        {
            "key": "gl_top_five_flags_barrier",
            "name": "五行旗护阵",
            "kind": "passive",
            "passive_config": {
                "triggers": [
                    {
                        "timing": "round_start",
                        "conditions": {
                            "self_template_in": ["arena_gl_top_zhang_wuji_boss"],
                            "ally_alive_template_count_gte": {
                                "arena_gl_top_five_flags_elite_front": 1,
                                "arena_gl_top_five_flags_elite_rear": 1,
                            },
                        },
                        "effects": [{"type": "modify_incoming_damage", "value": 0.5}],
                    }
                ]
            },
        },
    ]
    yang = make_unit("arena_gl_top_yang_xiao_guard", hp=200000, max_hp=200000, name="杨逍")
    wei = make_unit("arena_gl_top_wei_yixiao_guard", hp=200000, max_hp=200000, name="韦一笑")
    front = make_unit("arena_gl_top_five_flags_elite_front", hp=200000, max_hp=200000, name="洪水旗")
    rear = make_unit("arena_gl_top_five_flags_elite_rear", hp=200000, max_hp=200000, name="烈火旗")
    defenders = [boss, yang, wei, front, rear]

    sync_arena_coop_combat_state([], defenders, round_no=1)
    apply_round_start_passives(defenders)
    assert boss.battle_modifiers["incoming_damage_multiplier"] == pytest.approx(0.5)
    assert boss.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.32)
    assert boss.battle_modifiers["burst_softcap_threshold"] == pytest.approx(12000)
    assert "reflect_ratio" not in boss.battle_modifiers

    boss.hp = 180000
    sync_arena_coop_combat_state([], defenders, round_no=2)
    apply_round_start_passives(defenders)
    assert boss.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.584)
    assert boss.battle_modifiers["burst_softcap_threshold"] == pytest.approx(14000)
    assert boss.battle_modifiers["reflect_ratio"] == pytest.approx(0.06)
    assert boss.battle_modifiers["reflect_cap"] == pytest.approx(5000)

    boss.hp = 100000
    sync_arena_coop_combat_state([], defenders, round_no=3)
    apply_round_start_passives(defenders)
    assert boss.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.848)
    assert boss.battle_modifiers["burst_softcap_threshold"] == pytest.approx(16000)
    assert boss.battle_modifiers["reflect_ratio"] == pytest.approx(0.1)
    assert boss.battle_modifiers["reflect_cap"] == pytest.approx(8000)


def test_try_trigger_arena_coop_pre_action_heal_restores_boss_hp():
    boss = make_unit("arena_gl_top_zhang_wuji_boss", hp=120000, max_hp=300000, name="张无忌", is_boss=True)
    boss.battle_modifiers["self_heal_ratio_on_action"] = 0.05

    event = try_trigger_arena_coop_pre_action_heal(boss)

    assert event is not None
    assert event["effect"] == "九阳护体"
    assert event["healed"] == 15000
    assert boss.hp == 135000


def test_try_trigger_arena_coop_pre_action_heal_skips_when_action_before_passive_exists():
    boss = make_unit("arena_gl_top_zhang_wuji_boss", hp=120000, max_hp=300000, name="张无忌", is_boss=True)
    boss.battle_modifiers["self_heal_ratio_on_action"] = 0.05
    boss.skills = [
        {
            "key": "gl_top_nine_yang_guard",
            "name": "九阳护体",
            "kind": "passive",
            "passive_config": {
                "triggers": [
                    {
                        "timing": "action_before",
                        "effects": [{"type": "heal_ratio", "value": 0.05, "max_hp_based": True, "log": True}],
                    }
                ]
            },
        }
    ]

    event = try_trigger_arena_coop_pre_action_heal(boss)

    assert event is None
    assert boss.hp == 120000


def test_adjust_arena_coop_damage_applies_barrier_and_burst_softcap():
    actor = make_unit("attacker_tpl", hp=10000, max_hp=10000, side="attacker", name="玩家门客")
    boss = make_unit("arena_gl_top_zhang_wuji_boss", hp=300000, max_hp=300000, name="张无忌", is_boss=True)
    yang = make_unit("arena_gl_top_yang_xiao_guard", hp=200000, max_hp=200000, name="杨逍")
    wei = make_unit("arena_gl_top_wei_yixiao_guard", hp=200000, max_hp=200000, name="韦一笑")
    front = make_unit("arena_gl_top_five_flags_elite_front", hp=200000, max_hp=200000, name="洪水旗")
    rear = make_unit("arena_gl_top_five_flags_elite_rear", hp=200000, max_hp=200000, name="烈火旗")

    boss.skills = [
        {
            "key": "gl_top_mingjiao_command",
            "name": "明教号令",
            "kind": "passive",
            "passive_config": {
                "triggers": [
                    {
                        "timing": "round_start",
                        "conditions": {"self_template_in": ["arena_gl_top_zhang_wuji_boss"], "hp_ratio_gte": 0.700001},
                        "effects": [
                            {"type": "modify_outgoing_damage", "value": 1.32},
                            {"type": "set_softcap", "threshold": 12000, "overflow_ratio": 0.35},
                        ],
                    }
                ]
            },
        },
        {
            "key": "gl_top_five_flags_barrier",
            "name": "五行旗护阵",
            "kind": "passive",
            "passive_config": {
                "triggers": [
                    {
                        "timing": "round_start",
                        "conditions": {
                            "self_template_in": ["arena_gl_top_zhang_wuji_boss"],
                            "ally_alive_template_count_gte": {
                                "arena_gl_top_five_flags_elite_front": 1,
                                "arena_gl_top_five_flags_elite_rear": 1,
                            },
                        },
                        "effects": [{"type": "modify_incoming_damage", "value": 0.5}],
                    }
                ]
            },
        },
    ]
    defenders = [boss, yang, wei, front, rear]

    sync_arena_coop_combat_state([actor], defenders, round_no=1)
    apply_round_start_passives(defenders)

    reduced = adjust_arena_coop_damage(actor, boss, 40000)

    assert reduced < 15000
    assert reduced >= 1


def test_sync_arena_coop_combat_state_exposes_phase_state_for_guard_passives():
    guard_morale = {
        "key": "gl_top_guard_morale",
        "name": "明教战意",
        "kind": "passive",
        "passive_config": {
            "triggers": [
                {
                    "timing": "round_start",
                    "conditions": {
                        "self_template_in": ["arena_gl_top_yang_xiao_guard"],
                        "state_present": ["arena_coop_boss_alive", "arena_coop_phase_1"],
                    },
                    "effects": [{"type": "modify_outgoing_damage", "value": 1.188}],
                },
                {
                    "timing": "round_start",
                    "conditions": {
                        "self_template_in": ["arena_gl_top_yang_xiao_guard"],
                        "state_present": ["arena_coop_boss_alive", "arena_coop_phase_2_plus"],
                    },
                    "effects": [{"type": "modify_outgoing_damage", "value": 1.265}],
                },
                {
                    "timing": "round_start",
                    "conditions": {
                        "self_template_in": ["arena_gl_top_wei_yixiao_guard"],
                        "state_present": ["arena_coop_boss_alive", "arena_coop_phase_1"],
                    },
                    "effects": [{"type": "modify_outgoing_damage", "value": 1.242}],
                },
                {
                    "timing": "round_start",
                    "conditions": {
                        "self_template_in": ["arena_gl_top_wei_yixiao_guard"],
                        "state_present": ["arena_coop_boss_alive", "arena_coop_phase_2_plus"],
                    },
                    "effects": [{"type": "modify_outgoing_damage", "value": 1.3225}],
                },
                {
                    "timing": "round_start",
                    "conditions": {
                        "self_template_in": [
                            "arena_gl_top_five_flags_elite_front",
                            "arena_gl_top_five_flags_elite_rear",
                        ],
                        "state_present": ["arena_coop_boss_alive", "arena_coop_phase_1"],
                    },
                    "effects": [{"type": "modify_outgoing_damage", "value": 1.134}],
                },
                {
                    "timing": "round_start",
                    "conditions": {
                        "self_template_in": [
                            "arena_gl_top_five_flags_elite_front",
                            "arena_gl_top_five_flags_elite_rear",
                        ],
                        "state_present": ["arena_coop_boss_alive", "arena_coop_phase_2_plus"],
                    },
                    "effects": [{"type": "modify_outgoing_damage", "value": 1.2075}],
                },
            ]
        },
    }
    boss = make_unit("arena_gl_top_zhang_wuji_boss", hp=300000, max_hp=300000, name="张无忌", is_boss=True)
    yang = make_unit("arena_gl_top_yang_xiao_guard", hp=200000, max_hp=200000, name="杨逍")
    wei = make_unit("arena_gl_top_wei_yixiao_guard", hp=200000, max_hp=200000, name="韦一笑")
    front = make_unit("arena_gl_top_five_flags_elite_front", hp=200000, max_hp=200000, name="洪水旗")
    rear = make_unit("arena_gl_top_five_flags_elite_rear", hp=200000, max_hp=200000, name="烈火旗")
    for guard in [yang, wei, front, rear]:
        guard.skills = [guard_morale]

    defenders = [boss, yang, wei, front, rear]
    sync_arena_coop_combat_state([], defenders, round_no=1)

    assert yang.battle_modifiers == {}
    assert wei.battle_modifiers == {}
    assert front.battle_modifiers == {}
    assert yang.battle_state["arena_coop_phase_1"] is True
    assert "arena_coop_phase_2_plus" not in yang.battle_state
    assert yang.battle_state["arena_coop_boss_alive"] is True

    apply_round_start_passives(defenders)

    assert yang.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.188)
    assert wei.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.242)
    assert front.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.134)

    boss.hp = 180000
    sync_arena_coop_combat_state([], defenders, round_no=2)

    assert yang.battle_modifiers == {}
    assert yang.battle_state["arena_coop_phase_2_plus"] is True
    assert "arena_coop_phase_1" not in yang.battle_state

    apply_round_start_passives(defenders)

    assert yang.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.265)
    assert wei.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.3225)
    assert rear.battle_modifiers["outgoing_damage_multiplier"] == pytest.approx(1.2075)


def test_sync_arena_coop_combat_state_clears_guard_buffs_after_boss_death():
    boss = make_unit("arena_gl_top_zhang_wuji_boss", hp=300000, max_hp=300000, name="张无忌", is_boss=True)
    yang = make_unit("arena_gl_top_yang_xiao_guard", hp=200000, max_hp=200000, name="杨逍")
    yang.skills = [
        {
            "key": "gl_top_guard_morale",
            "name": "明教战意",
            "kind": "passive",
            "passive_config": {
                "triggers": [
                    {
                        "timing": "round_start",
                        "conditions": {
                            "self_template_in": ["arena_gl_top_yang_xiao_guard"],
                            "state_present": ["arena_coop_boss_alive", "arena_coop_phase_1"],
                        },
                        "effects": [{"type": "modify_outgoing_damage", "value": 1.188}],
                    }
                ]
            },
        }
    ]

    defenders = [boss, yang]
    sync_arena_coop_combat_state([], defenders, round_no=1)
    apply_round_start_passives(defenders)
    assert yang.battle_modifiers["outgoing_damage_multiplier"] > 1.0

    boss.hp = 0
    sync_arena_coop_combat_state([], defenders, round_no=2)

    assert yang.battle_modifiers == {}


def test_arena_coop_battle_config_uses_extended_round_budget():
    config = get_battle_config("arena_coop")

    assert config["max_rounds"] == 24


def test_simulate_battle_counts_priority_rounds_toward_total_round_limit(monkeypatch):
    attacker = make_unit("attacker_tpl", hp=1000, max_hp=1000, side="attacker", name="甲")
    defender = make_unit("defender_tpl", hp=1000, max_hp=1000, side="defender", name="乙")

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
