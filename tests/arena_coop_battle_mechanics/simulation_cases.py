from __future__ import annotations

import random

import pytest

from battle.arena_coop import configure_arena_coop_enemy_guest
from battle.combatants_pkg import assign_agility_based_priorities, build_guest_combatants, build_named_ai_guests
from battle.simulation.battle_flow import simulate_battle
from guests.models import Guest, GuestTemplate, Skill


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
            "passive_config": {
                "triggers": [
                    {
                        "timing": "action_before",
                        "conditions": {"hp_ratio_lte": 0.7},
                        "effects": [
                            {
                                "type": "heal_ratio",
                                "value": 0.05,
                                "max_hp_based": True,
                                "log": True,
                                "log_name": "九阳护体",
                            }
                        ],
                    }
                ]
            },
        },
        "gl_top_qiankun_shift": {
            "name": "乾坤大挪移",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.75,
            "damage_formula": {"base": 0},
            "targets": 1,
            "passive_config": {
                "triggers": [
                    {
                        "timing": "hit_taken",
                        "conditions": {"hp_ratio_lte": 0.4},
                        "effects": [
                            {"type": "set_reflect", "ratio": 0.1, "cap": 8000},
                            {"type": "set_softcap", "threshold": 16000, "overflow_ratio": 0.35},
                        ],
                    }
                ]
            },
        },
        "gl_top_mingjiao_command": {
            "name": "明教号令",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.75,
            "damage_formula": {"base": 0},
            "targets": 1,
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
        "gl_top_holy_flame_rage": {
            "name": "圣火狂势",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.75,
            "damage_formula": {"base": 0},
            "targets": 1,
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
                    },
                    {
                        "timing": "round_start",
                        "conditions": {
                            "self_template_in": ["arena_gl_top_zhang_wuji_boss"],
                            "ally_alive_template_count_gte": {"arena_gl_top_five_flags_elite_front": 1},
                            "ally_alive_template_count_lte": {"arena_gl_top_five_flags_elite_rear": 0},
                        },
                        "effects": [{"type": "modify_incoming_damage", "value": 0.72}],
                    },
                    {
                        "timing": "round_start",
                        "conditions": {
                            "self_template_in": ["arena_gl_top_zhang_wuji_boss"],
                            "ally_alive_template_count_gte": {"arena_gl_top_five_flags_elite_rear": 1},
                            "ally_alive_template_count_lte": {"arena_gl_top_five_flags_elite_front": 0},
                        },
                        "effects": [{"type": "modify_incoming_damage", "value": 0.72}],
                    },
                ]
            },
        },
        "gl_top_guard_morale": {
            "name": "明教战意",
            "rarity": "purple",
            "kind": "passive",
            "base_probability": 0.75,
            "damage_formula": {"base": 0},
            "targets": 1,
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
                            "self_template_in": ["arena_gl_top_wei_yixiao_guard"],
                            "state_present": ["arena_coop_boss_alive", "arena_coop_phase_1"],
                        },
                        "effects": [{"type": "modify_outgoing_damage", "value": 1.242}],
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
                            "self_template_in": ["arena_gl_top_yang_xiao_guard"],
                            "state_present": ["arena_coop_boss_alive", "arena_coop_phase_2_plus"],
                        },
                        "effects": [{"type": "modify_outgoing_damage", "value": 1.265}],
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
                            "state_present": ["arena_coop_boss_alive", "arena_coop_phase_2_plus"],
                        },
                        "effects": [{"type": "modify_outgoing_damage", "value": 1.2075}],
                    },
                ]
            },
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
            "skills": ["gl_top_guard_morale", "gl_top_mingjiao_command", "gl_top_left_envoy_edge"],
        },
        "arena_gl_top_wei_yixiao_guard": {
            "name": "韦一笑",
            "base_attack": 214,
            "base_intellect": 148,
            "base_defense": 182,
            "base_agility": 228,
            "base_luck": 96,
            "base_hp": 3300,
            "skills": ["gl_top_guard_morale", "gl_top_cold_blood_swoop"],
        },
        "arena_gl_top_five_flags_elite_front": {
            "name": "五行旗精锐",
            "base_attack": 198,
            "base_intellect": 132,
            "base_defense": 206,
            "base_agility": 150,
            "base_luck": 70,
            "base_hp": 2950,
            "skills": ["gl_top_guard_morale", "gl_top_five_flags_barrier", "gl_top_banner_fire_volley"],
        },
        "arena_gl_top_five_flags_elite_rear": {
            "name": "五行旗精锐",
            "base_attack": 194,
            "base_intellect": 136,
            "base_defense": 198,
            "base_agility": 156,
            "base_luck": 72,
            "base_hp": 2950,
            "skills": ["gl_top_guard_morale", "gl_top_five_flags_barrier", "gl_top_banner_fire_volley"],
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
    passive_events = [
        event for round_data in result.rounds for event in round_data["events"] if event.get("type") == "passive"
    ]
    assert result.rounds
    assert boss.max_hp == 300000
    assert boss.hp < boss.max_hp
    assert phase_events
    assert passive_events
