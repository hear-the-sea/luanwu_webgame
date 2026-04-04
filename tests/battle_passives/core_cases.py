from .support import (
    _resolve_standard_round,
    adjust_arena_coop_damage,
    apply_effect,
    conditions_match,
    make_unit,
    prepare_combatants_for_round,
    random,
    run_passives_for_timing,
    simulate_battle,
)


def test_passive_conditions_match_hp_ratio_and_boss_flag():
    actor = make_unit(hp=80, max_hp=200, is_boss=True, template_key="arena_gl_top_zhang_wuji_boss")
    context = {"actor": actor, "target": None, "attacker_team": [actor], "defender_team": []}

    assert conditions_match(
        {
            "hp_ratio_lte": 0.5,
            "self_is_boss": True,
            "self_template_in": ["arena_gl_top_zhang_wuji_boss"],
        },
        context,
    )


def test_apply_heal_ratio_effect_uses_max_hp_and_emits_event():
    actor = make_unit(name="张无忌", side="defender", hp=120000, max_hp=300000)
    events = []

    apply_effect(
        {"type": "heal_ratio", "value": 0.05, "max_hp_based": True, "log": True, "log_name": "九阳护体"},
        {"actor": actor, "target": None, "event_sink": events},
    )

    assert actor.hp == 135000
    assert events[0]["type"] == "passive"
    assert events[0]["effect"] == "九阳护体"
    assert events[0]["healed"] == 15000


def test_apply_set_reflect_and_softcap_effects_write_modifiers():
    actor = make_unit(name="张无忌", battle_modifiers={})

    apply_effect({"type": "set_reflect", "ratio": 0.1, "cap": 8000}, {"actor": actor, "event_sink": []})
    apply_effect(
        {"type": "set_softcap", "threshold": 16000, "overflow_ratio": 0.35},
        {"actor": actor, "event_sink": []},
    )

    assert actor.battle_modifiers["reflect_ratio"] == 0.1
    assert actor.battle_modifiers["reflect_cap"] == 8000
    assert actor.battle_modifiers["burst_softcap_sources"]["__direct__"]["threshold"] == 16000
    assert actor.battle_modifiers["burst_softcap_sources"]["__direct__"]["overflow_ratio"] == 0.35
    assert "burst_softcap_threshold" not in actor.battle_modifiers
    assert "burst_softcap_overflow_ratio" not in actor.battle_modifiers


def test_apply_effect_can_buff_all_allies():
    actor = make_unit(name="军师", side="defender", template_key="guild_wulinzhai_junshi", battle_modifiers={})
    ally = make_unit(name="喽啰", side="defender", template_key="guild_wulinzhai_minion", battle_modifiers={})
    enemy = make_unit(name="来敌", side="attacker", template_key="hero_guest", battle_modifiers={})

    apply_effect(
        {"type": "modify_outgoing_damage", "value": 1.12, "target_scope": "allies"},
        {
            "actor": actor,
            "target": None,
            "attacker_team": [enemy],
            "defender_team": [actor, ally],
            "event_sink": [],
        },
    )

    assert actor.battle_modifiers["outgoing_damage_multiplier"] == 1.12
    assert ally.battle_modifiers["outgoing_damage_multiplier"] == 1.12
    assert enemy.battle_modifiers == {}


def test_apply_effect_can_buff_filtered_ally_templates_only():
    actor = make_unit(name="监军", side="defender", template_key="guild_blackwind_gate_overseer", battle_modifiers={})
    frontline = make_unit(name="铁卫", side="defender", template_key="guild_blackwind_iron_guard", battle_modifiers={})
    rear = make_unit(name="弓队长", side="defender", template_key="guild_blackwind_bow_captain", battle_modifiers={})

    apply_effect(
        {
            "type": "set_softcap",
            "threshold": 9800,
            "overflow_ratio": 0.4,
            "target_scope": "allies",
            "target_template_in": ["guild_blackwind_gate_overseer", "guild_blackwind_iron_guard"],
        },
        {
            "actor": actor,
            "target": None,
            "attacker_team": [],
            "defender_team": [actor, frontline, rear],
            "event_sink": [],
        },
    )

    assert actor.battle_modifiers["burst_softcap_sources"]["__direct__"]["threshold"] == 9800
    assert frontline.battle_modifiers["burst_softcap_sources"]["__direct__"]["threshold"] == 9800
    assert rear.battle_modifiers == {}


def test_apply_effect_deduplicates_same_skill_source_across_multiple_holders():
    general = make_unit(name="夜哨甲", side="defender", template_key="guild_wulinzhai_night_watch", battle_modifiers={})
    watcher = make_unit(name="夜哨乙", side="defender", template_key="guild_wulinzhai_junshi", battle_modifiers={})
    ally = make_unit(name="喽啰", side="defender", template_key="guild_wulinzhai_minion", battle_modifiers={})

    first_context = {
        "actor": general,
        "skill_key": "guild_wulinzhai_night_signal",
        "target": None,
        "attacker_team": [],
        "defender_team": [general, watcher, ally],
        "event_sink": [],
    }
    second_context = {
        "actor": watcher,
        "skill_key": "guild_wulinzhai_night_signal",
        "target": None,
        "attacker_team": [],
        "defender_team": [general, watcher, ally],
        "event_sink": [],
    }

    apply_effect({"type": "modify_outgoing_damage", "value": 1.12, "target_scope": "allies"}, first_context)
    apply_effect({"type": "modify_outgoing_damage", "value": 1.12, "target_scope": "allies"}, second_context)

    assert ally.battle_modifiers["outgoing_damage_multiplier"] == 1.12


def test_apply_effect_combines_distinct_skill_sources_for_damage_multiplier():
    actor = make_unit(name="守将", side="defender", template_key="guild_blackwind_gate_general", battle_modifiers={})
    ally = make_unit(name="铁卫", side="defender", template_key="guild_blackwind_iron_guard", battle_modifiers={})
    enemy = make_unit(name="来敌", side="attacker", template_key="hero_guest", battle_modifiers={})

    apply_effect(
        {"type": "modify_outgoing_damage", "value": 1.18, "target_scope": "allies"},
        {
            "actor": actor,
            "skill_key": "guild_blackwind_battle_standard",
            "target": None,
            "attacker_team": [enemy],
            "defender_team": [actor, ally],
            "event_sink": [],
        },
    )
    apply_effect(
        {
            "type": "modify_outgoing_damage",
            "value": 1.1,
            "target_scope": "allies",
            "target_template_in": ["guild_blackwind_iron_guard"],
        },
        {
            "actor": make_unit(
                name="监军",
                side="defender",
                template_key="guild_blackwind_gate_overseer",
                battle_modifiers={},
            ),
            "skill_key": "guild_blackwind_overseer_order",
            "target": None,
            "attacker_team": [enemy],
            "defender_team": [actor, ally],
            "event_sink": [],
        },
    )

    assert ally.battle_modifiers["outgoing_damage_multiplier"] == 1.298
    assert adjust_arena_coop_damage(ally, enemy, 1000) == 1298


def test_run_passives_for_timing_deduplicates_same_group_aura_and_combines_distinct_ones():
    general = make_unit(
        name="守将",
        side="defender",
        template_key="guild_blackwind_gate_general",
        battle_modifiers={},
        skills=[
            {
                "key": "guild_blackwind_battle_standard",
                "name": "黑风战旗",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "round_start",
                            "effects": [{"type": "modify_outgoing_damage", "value": 1.18, "target_scope": "allies"}],
                        }
                    ]
                },
            }
        ],
    )
    overseer = make_unit(
        name="监军甲",
        side="defender",
        template_key="guild_blackwind_gate_overseer",
        battle_modifiers={},
        skills=[
            {
                "key": "guild_blackwind_overseer_order",
                "name": "督战严令",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "round_start",
                            "effects": [
                                {
                                    "type": "modify_outgoing_damage",
                                    "value": 1.1,
                                    "target_scope": "allies",
                                    "target_template_in": ["guild_blackwind_iron_guard"],
                                }
                            ],
                        }
                    ]
                },
            }
        ],
    )
    second_overseer = make_unit(
        name="监军乙",
        side="defender",
        template_key="guild_blackwind_patrol_captain",
        battle_modifiers={},
        skills=[
            {
                "key": "guild_blackwind_overseer_order",
                "name": "督战严令",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "round_start",
                            "effects": [
                                {
                                    "type": "modify_outgoing_damage",
                                    "value": 1.1,
                                    "target_scope": "allies",
                                    "target_template_in": ["guild_blackwind_iron_guard"],
                                }
                            ],
                        }
                    ]
                },
            }
        ],
    )
    frontline = make_unit(name="铁卫", side="defender", template_key="guild_blackwind_iron_guard", battle_modifiers={})

    defenders = [general, overseer, second_overseer, frontline]
    for actor in defenders:
        run_passives_for_timing(
            "round_start",
            actor=actor,
            target=None,
            attacker_team=[],
            defender_team=defenders,
            round_no=1,
            event_sink=[],
            rng=random.Random(1),
        )

    assert frontline.battle_modifiers["outgoing_damage_multiplier"] == 1.298
    assert (
        adjust_arena_coop_damage(frontline, make_unit(name="来敌", side="attacker", template_key="hero_guest"), 1000)
        == 1298
    )


def test_run_passives_for_timing_executes_matching_trigger_only_once():
    actor = make_unit(
        name="张无忌",
        side="defender",
        hp=120000,
        max_hp=300000,
        battle_modifiers={},
        battle_state={},
        skills=[
            {
                "key": "gl_top_nine_yang_guard",
                "name": "九阳护体",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "action_before",
                            "conditions": {"hp_ratio_lte": 0.5, "state_absent": "heal_once"},
                            "effects": [
                                {"type": "set_state", "key": "heal_once", "value": True},
                                {
                                    "type": "heal_ratio",
                                    "value": 0.05,
                                    "max_hp_based": True,
                                    "log": True,
                                    "log_name": "九阳护体",
                                },
                            ],
                        }
                    ]
                },
            }
        ],
    )
    events = []

    run_passives_for_timing(
        "action_before",
        actor=actor,
        target=None,
        attacker_team=[],
        defender_team=[actor],
        round_no=1,
        event_sink=events,
        rng=random.Random(1),
    )
    run_passives_for_timing(
        "action_before",
        actor=actor,
        target=None,
        attacker_team=[],
        defender_team=[actor],
        round_no=1,
        event_sink=events,
        rng=random.Random(1),
    )

    assert actor.hp == 135000
    assert len(events) == 1


def test_run_passives_for_hit_taken_updates_target_reflect_state():
    attacker = make_unit(name="甲", side="attacker")
    target = make_unit(
        name="张无忌",
        side="defender",
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
    )

    run_passives_for_timing(
        "hit_taken",
        actor=target,
        target=attacker,
        attacker_team=[attacker],
        defender_team=[target],
        round_no=1,
        event_sink=[],
        rng=random.Random(1),
    )

    assert target.battle_modifiers["reflect_ratio"] == 0.1


def test_simulate_battle_runs_battle_start_passive_once(monkeypatch):
    actor = make_unit(
        name="张无忌",
        side="attacker",
        hp=120000,
        max_hp=300000,
        battle_modifiers={},
        battle_state={},
        skills=[
            {
                "key": "opening_guard",
                "name": "开场回春",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "battle_start",
                            "conditions": {"state_absent": "battle_start_once"},
                            "effects": [
                                {"type": "set_state", "key": "battle_start_once", "value": True},
                                {
                                    "type": "heal_ratio",
                                    "value": 0.05,
                                    "max_hp_based": True,
                                    "log": True,
                                    "log_name": "开场回春",
                                },
                            ],
                        }
                    ]
                },
            }
        ],
    )
    enemy = make_unit(name="敌人", side="defender")

    monkeypatch.setattr("battle.simulation.battle_flow.resolve_priority_phases", lambda *_args, **_kwargs: ([], 1))
    monkeypatch.setattr(
        "battle.simulation.battle_flow._resolve_standard_round",
        lambda *_args, **_kwargs: {"round": 1, "events": []},
    )
    monkeypatch.setattr("battle.simulation.battle_flow.roll_loot", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "battle.simulation.battle_flow.summarize_losses",
        lambda *_args, **_kwargs: {"attacker": {}, "defender": {}},
    )

    result = simulate_battle(
        [actor],
        [enemy],
        random.Random(1),
        seed=1,
        travel_seconds=0,
        config={"max_rounds": 1, "loot_pool": {}},
        max_rounds=1,
    )

    assert result.rounds[0]["events"][0]["type"] == "passive"
    assert result.rounds[0]["events"][0]["effect"] == "开场回春"
    assert actor.hp == 135000


def test_resolve_standard_round_logs_action_before_passive_event(monkeypatch):
    actor = make_unit(
        name="张无忌",
        side="attacker",
        hp=120000,
        max_hp=300000,
        battle_modifiers={},
        battle_state={},
        skills=[
            {
                "key": "gl_top_nine_yang_guard",
                "name": "九阳护体",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "action_before",
                            "conditions": {"hp_ratio_lte": 0.5},
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
            }
        ],
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
        name="敌人",
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
            "damage": 0,
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
        },
    )

    round_data = _resolve_standard_round([actor], [enemy], random.Random(1), round_no=1)

    passive_events = [event for event in round_data["events"] if event.get("type") == "passive"]
    assert passive_events
    assert passive_events[0]["order"] == 1
    assert passive_events[0]["effect"] == "九阳护体"
    assert actor.hp == 135000


def test_prepare_combatants_for_round_clears_temporary_passive_modifiers():
    actor = make_unit(
        name="张无忌",
        hp=100,
        max_hp=100,
        battle_modifiers={},
        battle_state={},
        status_effects={},
        skills=[
            {
                "key": "opening_pressure",
                "name": "开局压制",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "round_start",
                            "conditions": {"hp_ratio_gte": 0.5},
                            "effects": [{"type": "modify_outgoing_damage", "value": 1.5}],
                        }
                    ]
                },
            }
        ],
    )

    prepare_combatants_for_round([actor], [], 1)
    run_passives_for_timing(
        "round_start",
        actor=actor,
        target=None,
        attacker_team=[actor],
        defender_team=[],
        round_no=1,
        event_sink=[],
        rng=random.Random(1),
    )
    assert actor.battle_modifiers["outgoing_damage_multiplier"] == 1.5

    actor.hp = 10
    prepare_combatants_for_round([actor], [], 2)
    run_passives_for_timing(
        "round_start",
        actor=actor,
        target=None,
        attacker_team=[actor],
        defender_team=[],
        round_no=2,
        event_sink=[],
        rng=random.Random(1),
    )

    assert actor.battle_modifiers == {}
