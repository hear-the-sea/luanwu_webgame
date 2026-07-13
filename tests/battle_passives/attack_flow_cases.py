from types import SimpleNamespace

from .support import _resolve_standard_round, make_unit, perform_attack, random


def test_resolve_standard_round_expands_attack_timing_passives_into_round_events(monkeypatch):
    actor = make_unit(
        name="甲",
        side="attacker",
        hp=1000,
        max_hp=1000,
        has_acted_this_round=False,
        current_round=0,
        last_round_acted=0,
        agility=100,
        priority=0,
        troop_strength=0,
        initial_troop_strength=0,
        unit_attack=0,
        unit_defense=0,
        unit_hp=0,
        status_effects={},
        tech_effects={},
        troop_class="",
        kind="guest",
    )
    enemy = make_unit(
        name="张无忌",
        side="defender",
        hp=1000,
        max_hp=1000,
        has_acted_this_round=False,
        current_round=0,
        last_round_acted=0,
        agility=10,
        priority=0,
        troop_strength=0,
        initial_troop_strength=0,
        unit_attack=0,
        unit_defense=0,
        unit_hp=0,
        status_effects={},
        tech_effects={},
        troop_class="",
        kind="guest",
    )

    monkeypatch.setattr("battle.arena_coop.sync_arena_coop_combat_state", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("battle.arena_coop.try_trigger_arena_coop_pre_action_heal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("battle.status_manager.try_trigger_battle_heal_on_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("battle.utils.status_effects.handle_pre_action_status", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("battle.simulation.battle_flow.determine_turn_order", lambda *_args, **_kwargs: [actor])
    monkeypatch.setattr(
        "battle.simulation.battle_flow.perform_attack",
        lambda *_args, **_kwargs: {
            "actor": actor.name,
            "target": enemy.name,
            "damage": 100,
            "is_crit": False,
            "is_dodge": False,
            "side": actor.side,
            "skills": [],
            "agility": actor.agility,
            "kind": actor.kind,
            "priority": actor.priority,
            "status_inflicted": [],
            "index": 0,
            "kills": 0,
            "target_defeated": False,
            "passive_events_before": [
                {"type": "passive", "side": "attacker", "unit": "甲", "effect": "先手蓄劲", "message": "蓄势待发"}
            ],
            "passive_events_after": [
                {"type": "passive", "side": "defender", "unit": "张无忌", "effect": "乾坤留痕", "message": "卸力反震"}
            ],
        },
    )

    round_data = _resolve_standard_round([actor], [enemy], random.Random(1), round_no=1)

    assert round_data["events"][0]["type"] == "passive"
    assert round_data["events"][0]["effect"] == "先手蓄劲"
    assert round_data["events"][1]["actor"] == "甲"
    assert round_data["events"][2]["type"] == "passive"
    assert round_data["events"][2]["effect"] == "乾坤留痕"
    assert [event["order"] for event in round_data["events"]] == [1, 2, 3]


def test_perform_attack_runs_hit_taken_passives(monkeypatch):
    actor = make_unit(
        name="甲",
        side="attacker",
        agility=100,
        priority=0,
        troop_strength=0,
        initial_troop_strength=0,
        unit_attack=0,
        unit_defense=0,
        unit_hp=0,
        status_effects={},
        tech_effects={},
        troop_class="",
        kind="guest",
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=False,
        template_key="attacker_tpl",
        current_round=1,
    )
    target = make_unit(
        name="张无忌",
        side="defender",
        agility=20,
        priority=0,
        troop_strength=0,
        initial_troop_strength=0,
        unit_attack=0,
        unit_defense=0,
        unit_hp=0,
        status_effects={},
        tech_effects={},
        troop_class="",
        kind="guest",
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=True,
        template_key="arena_gl_top_zhang_wuji_boss",
        skills=[
            {
                "key": "gl_top_qiankun_shift",
                "name": "乾坤大挪移",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {"timing": "hit_taken", "effects": [{"type": "set_reflect", "ratio": 0.1, "cap": 8000}]}
                    ]
                },
            }
        ],
        battle_modifiers={},
        battle_state={},
        current_round=1,
    )

    monkeypatch.setattr(
        "battle.simulation.attack_execution.select_attack_targets",
        lambda *_args, **_kwargs: SimpleNamespace(engaged_targets=[target], skills=[]),
    )
    monkeypatch.setattr("battle.simulation.attack_execution.calculate_dodge_chance", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        "battle.simulation.attack_execution.calculate_attack_damage",
        lambda *_args, **_kwargs: SimpleNamespace(damage=10, is_crit=False, is_double_strike=False),
    )
    monkeypatch.setattr(
        "battle.simulation.attack_execution.apply_damage_results",
        lambda *_args, **_kwargs: SimpleNamespace(
            display_damage=10,
            kills=0,
            target_defeated=False,
            reflect_damage=0,
            reflect_kills=0,
            reflect_defeated=False,
            counter_damage=0,
            counter_kills=0,
            counter_defeated=False,
            actor_defeated=False,
        ),
    )
    monkeypatch.setattr("battle.simulation.attack_execution.process_status_effects", lambda *_args, **_kwargs: [])

    perform_attack(actor, [actor], [target], random.Random(1))

    assert target.battle_modifiers["reflect_ratio"] == 0.1
    assert target.battle_modifiers["reflect_cap"] == 8000


def test_perform_attack_exposes_attack_timing_passive_events(monkeypatch):
    actor = make_unit(
        name="甲",
        side="attacker",
        agility=100,
        priority=0,
        troop_strength=0,
        initial_troop_strength=0,
        unit_attack=0,
        unit_defense=0,
        unit_hp=0,
        status_effects={},
        tech_effects={},
        troop_class="",
        kind="guest",
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=False,
        template_key="attacker_tpl",
        current_round=1,
        skills=[
            {
                "key": "attack_before_log",
                "name": "先手蓄劲",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "attack_before",
                            "effects": [{"type": "emit_log", "log_name": "先手蓄劲", "message": "蓄势待发"}],
                        }
                    ]
                },
            }
        ],
    )
    target = make_unit(
        name="张无忌",
        side="defender",
        agility=20,
        priority=0,
        troop_strength=0,
        initial_troop_strength=0,
        unit_attack=0,
        unit_defense=0,
        unit_hp=0,
        status_effects={},
        tech_effects={},
        troop_class="",
        kind="guest",
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=True,
        template_key="arena_gl_top_zhang_wuji_boss",
        skills=[
            {
                "key": "hit_taken_log",
                "name": "乾坤留痕",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "hit_taken",
                            "effects": [{"type": "emit_log", "log_name": "乾坤留痕", "message": "卸力反震"}],
                        }
                    ]
                },
            }
        ],
        battle_modifiers={},
        battle_state={},
        current_round=1,
    )

    monkeypatch.setattr(
        "battle.simulation.attack_execution.select_attack_targets",
        lambda *_args, **_kwargs: SimpleNamespace(engaged_targets=[target], skills=[]),
    )
    monkeypatch.setattr("battle.simulation.attack_execution.calculate_dodge_chance", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        "battle.simulation.attack_execution.calculate_attack_damage",
        lambda *_args, **_kwargs: SimpleNamespace(damage=10, is_crit=False, is_double_strike=False),
    )
    monkeypatch.setattr(
        "battle.simulation.attack_execution.apply_damage_results",
        lambda *_args, **_kwargs: SimpleNamespace(
            display_damage=10,
            kills=0,
            target_defeated=False,
            reflect_damage=0,
            reflect_kills=0,
            reflect_defeated=False,
            counter_damage=0,
            counter_kills=0,
            counter_defeated=False,
            actor_defeated=False,
        ),
    )
    monkeypatch.setattr("battle.simulation.attack_execution.process_status_effects", lambda *_args, **_kwargs: [])

    entry = perform_attack(actor, [actor], [target], random.Random(1))

    assert entry is not None
    assert entry["passive_events_before"][0]["effect"] == "先手蓄劲"
    assert entry["passive_events_after"][0]["effect"] == "乾坤留痕"


def test_perform_attack_only_exposes_active_skills_in_attack_log(monkeypatch):
    actor = make_unit(
        name="甲",
        side="attacker",
        agility=100,
        priority=0,
        troop_strength=0,
        initial_troop_strength=0,
        unit_attack=0,
        unit_defense=0,
        unit_hp=0,
        status_effects={},
        tech_effects={},
        troop_class="",
        kind="guest",
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=False,
        template_key="attacker_tpl",
        current_round=1,
    )
    target = make_unit(
        name="张无忌",
        side="defender",
        agility=20,
        priority=0,
        troop_strength=0,
        initial_troop_strength=0,
        unit_attack=0,
        unit_defense=0,
        unit_hp=0,
        status_effects={},
        tech_effects={},
        troop_class="",
        kind="guest",
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=True,
        template_key="arena_gl_top_zhang_wuji_boss",
        battle_modifiers={},
        battle_state={},
        current_round=1,
    )

    monkeypatch.setattr(
        "battle.simulation.attack_execution._trigger_attack_skills",
        lambda *_args, **_kwargs: [
            {"name": "乾坤圣火印", "kind": "active", "targets": 1},
            {"name": "九阳护体", "kind": "passive", "targets": 1},
        ],
    )
    monkeypatch.setattr("battle.simulation.attack_execution.calculate_dodge_chance", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        "battle.simulation.attack_execution.calculate_attack_damage",
        lambda *_args, **_kwargs: SimpleNamespace(damage=10, is_crit=False, is_double_strike=False),
    )
    monkeypatch.setattr(
        "battle.simulation.attack_execution.apply_damage_results",
        lambda *_args, **_kwargs: SimpleNamespace(
            display_damage=10,
            kills=0,
            target_defeated=False,
            reflect_damage=0,
            reflect_kills=0,
            reflect_defeated=False,
            counter_damage=0,
            counter_kills=0,
            counter_defeated=False,
            actor_defeated=False,
        ),
    )
    monkeypatch.setattr("battle.simulation.attack_execution.process_status_effects", lambda *_args, **_kwargs: [])

    entry = perform_attack(actor, [actor], [target], random.Random(1))

    assert entry is not None
    assert entry["skills"] == ["乾坤圣火印"]


def test_perform_attack_keeps_independent_state_snapshots_for_multiple_targets(monkeypatch):
    actor = make_unit(
        name="甲",
        side="attacker",
        hp=1000,
        max_hp=1000,
        agility=100,
        priority=0,
        current_round=1,
        kind="guest",
        troop_class="",
        troop_strength=0,
        initial_troop_strength=0,
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=False,
    )
    first_target = make_unit(
        name="乙",
        side="defender",
        hp=700,
        max_hp=1000,
        agility=20,
        priority=0,
        current_round=1,
        kind="guest",
        troop_strength=0,
        initial_troop_strength=0,
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=False,
    )
    second_target = make_unit(
        name="丙",
        side="defender",
        hp=300,
        max_hp=1000,
        agility=10,
        priority=0,
        current_round=1,
        kind="guest",
        troop_strength=0,
        initial_troop_strength=0,
        guest_id=None,
        owner_entry_id=None,
        combatant_slot=None,
        is_boss=False,
    )
    monkeypatch.setattr(
        "battle.simulation.attack_execution.select_attack_targets",
        lambda *_args, **_kwargs: SimpleNamespace(engaged_targets=[first_target, second_target], skills=[]),
    )
    monkeypatch.setattr("battle.simulation.attack_execution.calculate_dodge_chance", lambda *_args: 0.0)
    monkeypatch.setattr(
        "battle.simulation.attack_execution.calculate_attack_damage",
        lambda *_args, **_kwargs: SimpleNamespace(damage=10, is_crit=False, is_double_strike=False),
    )
    monkeypatch.setattr(
        "battle.simulation.attack_execution.apply_damage_results",
        lambda *_args, **_kwargs: SimpleNamespace(
            display_damage=10,
            kills=0,
            target_defeated=False,
            reflect_damage=0,
            reflect_kills=0,
            reflect_defeated=False,
            counter_damage=0,
            counter_kills=0,
            counter_defeated=False,
            actor_defeated=False,
        ),
    )
    monkeypatch.setattr("battle.simulation.attack_execution.process_status_effects", lambda *_args, **_kwargs: [])

    entry = perform_attack(actor, [actor], [first_target, second_target], random.Random(1))

    assert entry is not None
    additional = entry["additional_targets"][0]
    assert entry["target_state"]["current"] == 700
    assert additional["target_state"]["current"] == 300
    assert entry["target_state"] is not additional["target_state"]
