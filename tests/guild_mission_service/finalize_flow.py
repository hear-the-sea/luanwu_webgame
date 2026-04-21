from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.utils import timezone

from battle import execution as battle_execution
from battle.combatants_pkg import cache as guest_template_cache
from battle.models import BattleReport, TroopTemplate
from gameplay.models import Message
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildTroopStorage
from tests.guild_mission_service.support import create_guest, create_template, create_user_with_manor, hero_pool_service


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_returns_survivors_and_awards_ruby(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_finalize_leader")
    guild = Guild.objects.create(name="帮会任务结算帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_finalize_task",
        name="剿匪",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=5,
        recommended_guest_count=1,
        allow_troops=True,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        is_active=True,
    )
    troop_template = TroopTemplate.objects.create(key="guild_finalize_archer", name="结算弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = create_guest(manor=leader_manor, template=create_template("guild_finalize_tpl"), name="先锋")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={troop_template.key: 20},
    )
    run.attacker_troop_tech_snapshot = {"archer_attack": 6, "archer_hp": 3}
    run.save(update_fields=["attacker_troop_tech_snapshot"])

    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={troop_template.key: 20},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {"casualties": [{"key": troop_template.key, "lost": 8}]}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now() - timedelta(seconds=5),
        completed_at=timezone.now(),
        seed=1,
    )
    captured: dict[str, Any] = {}

    def _fake_execute_battle(*args, **kwargs):
        captured["options"] = args[3]
        return report

    monkeypatch.setattr("guilds.services.guild_missions.execute_battle", _fake_execute_battle)
    monkeypatch.setattr(
        "guilds.services.guild_missions.build_guild_troop_tech_levels",
        lambda _guild: {"archer_attack": 6, "archer_hp": 3},
        raising=False,
    )

    now = timezone.now()
    guild_mission_service.finalize_guild_mission_run(run, now=now)
    run.refresh_from_db()

    storage = GuildTroopStorage.objects.get(guild=guild, troop_template=troop_template)
    options = captured.get("options")
    assert options is not None
    assert options.attacker_tech_levels == {"archer_attack": 6, "archer_hp": 3}
    assert run.status == "completed"
    assert run.completed_at == now
    assert run.battle_report_id == report.id
    assert storage.count == 42
    assert guild.warehouse_items.get(item_key="red_ruby").quantity == 5


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_sends_report_message_to_all_active_members(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_report_leader")
    admin, admin_manor = create_user_with_manor(django_user_model, "guild_mission_report_admin")
    member, member_manor = create_user_with_manor(django_user_model, "guild_mission_report_member")
    guild = Guild.objects.create(name="帮会任务战报群发帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    GuildMember.objects.create(guild=guild, user=admin, position="admin", is_active=True)
    GuildMember.objects.create(guild=guild, user=member, position="member", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_report_delivery_task",
        name="群发战报任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=0,
        recommended_guest_count=1,
        allow_troops=False,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        is_active=True,
    )
    guest = create_guest(manor=leader_manor, template=create_template("guild_report_delivery_tpl"), name="先锋")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )

    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {"casualties": []}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now() - timedelta(seconds=5),
        completed_at=timezone.now(),
        seed=21,
    )
    monkeypatch.setattr("guilds.services.guild_missions.execute_battle", lambda *args, **kwargs: report)

    assert guild_mission_service.finalize_guild_mission_run(run)

    messages = list(
        Message.objects.filter(
            kind=Message.Kind.BATTLE,
            title=f"{template.name} 战报",
            battle_report=report,
        ).order_by("manor_id")
    )

    assert [message.manor_id for message in messages] == sorted([leader_manor.id, admin_manor.id, member_manor.id])


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_defers_report_messages_until_after_commit(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_after_commit_leader")
    guild = Guild.objects.create(name="帮会任务延后战报帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_after_commit_report_task",
        name="延后战报任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=0,
        recommended_guest_count=1,
        allow_troops=False,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        is_active=True,
    )
    guest = create_guest(
        manor=leader_manor,
        template=create_template("guild_mission_after_commit_tpl"),
        name="延后战报门客",
    )
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )

    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {"casualties": []}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now() - timedelta(seconds=5),
        completed_at=timezone.now(),
        seed=99,
    )

    callbacks: list[object] = []
    reported_run_ids: list[int] = []

    monkeypatch.setattr(guild_mission_service.transaction, "on_commit", lambda callback: callbacks.append(callback))
    monkeypatch.setattr("guilds.services.guild_missions.execute_battle", lambda *args, **kwargs: report)
    monkeypatch.setattr(
        "guilds.services.guild_missions._send_guild_mission_report_messages",
        lambda current_run, _report: reported_run_ids.append(current_run.id),
    )

    assert guild_mission_service.finalize_guild_mission_run(run) is True
    assert reported_run_ids == []
    assert len(callbacks) == 1

    callbacks[0]()

    assert reported_run_ids == [run.id]


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_forwards_expanded_battle_limits(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_expand_limits_leader")
    guild = Guild.objects.create(name="公会任务扩展人数帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    enemy_templates = [create_template(f"guild_expand_enemy_template_{i}") for i in range(7)]
    enemy_guests = [{"template_key": tpl.key, "level": 25} for tpl in enemy_templates]
    assert all("template_key" in entry for entry in enemy_guests)
    template = GuildMissionTemplate.objects.create(
        key="guild_expand_limits_task",
        name="人数上限测试",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=0,
        recommended_guest_count=6,
        allow_troops=False,
        enemy_guests=enemy_guests,
        enemy_troops={"archer": 120},
        enemy_technology={"guest_level": 60, "guest_bonus": 0.15, "guest_skills": ["stratagem_burst"]},
        is_active=True,
    )

    guest_template = create_template("guild_expand_limits_guest")
    guests = [create_guest(manor=leader_manor, template=guest_template, name=f"门客{i}") for i in range(6)]
    guest_snapshots = build_guest_battle_snapshots(guests, include_identity=True)
    run = GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=leader_member,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=len(guests),
        ruby_reward=0,
        guest_ids=[guest.id for guest in guests],
        guest_snapshots=guest_snapshots,
        troop_loadout={},
    )

    guest_template_cache.clear_guest_template_cache()

    captured: dict[str, Any] = {}

    original_build_named = battle_execution.build_named_ai_guests

    def _wrapped_build_named_ai_guests(guest_keys, level):
        captured["raw_guest_keys"] = [dict(entry) if isinstance(entry, dict) else entry for entry in guest_keys]
        captured["enemy_level"] = level
        return original_build_named(guest_keys, level=level)

    monkeypatch.setattr(
        "battle.execution.build_named_ai_guests",
        _wrapped_build_named_ai_guests,
    )

    original_execute = battle_execution.execute_battle

    def _wrapped_execute_battle(manor, guests_arg, active_guests_arg, options):
        captured["len_guests"] = len(guests_arg)
        captured["len_active_guests"] = len(active_guests_arg)
        captured["options"] = options
        return original_execute(manor, guests_arg, active_guests_arg, options)

    monkeypatch.setattr("guilds.services.guild_missions.execute_battle", _wrapped_execute_battle)

    def _fake_execute_simulation(attacker_units, defender_units, options, config, rng, final_seed):
        captured["attacker_unit_count"] = len(attacker_units)
        captured["defender_unit_count"] = len(defender_units)
        simulation = SimpleNamespace(
            losses={"attacker": {"casualties": []}, "defender": {}},
            drops={},
            winner="attacker",
            rounds=[],
            starts_at=timezone.now(),
            completed_at=timezone.now(),
            seed=final_seed,
        )
        return simulation, options.opponent_name or config.get("name", "乱军试炼")

    monkeypatch.setattr("battle.execution._execute_simulation", _fake_execute_simulation)

    from guilds.services import guild_missions as guild_mission_service

    assert guild_mission_service.finalize_guild_mission_run(run)

    options = captured.get("options")
    assert options is not None
    assert captured.get("len_guests") == 6
    assert captured.get("len_active_guests") == 6
    assert captured["enemy_level"] == 60
    assert options.limit == 6
    assert options.defender_limit == 7
    assert captured["attacker_unit_count"] == 6
    assert captured["defender_unit_count"] == 7
    assert options.defender_setup["troop_loadout"] == {"archer": 120}
    assert options.defender_setup["technology"]["guest_level"] == 60
    assert len(captured["raw_guest_keys"]) == 7
    assert all(isinstance(entry, dict) for entry in captured["raw_guest_keys"])
    assert all("template_key" not in entry for entry in captured["raw_guest_keys"])
    assert [entry["key"] for entry in captured["raw_guest_keys"]] == [tpl.key for tpl in enemy_templates]


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_attacker_troops_use_guild_tech_levels_not_manor_tech(django_user_model, monkeypatch):
    from battle.troops import invalidate_troop_templates_cache
    from gameplay.models import PlayerTechnology

    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_attacker_guild_tech")
    guild = Guild.objects.create(name="帮会任务科技归属帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_mission_attacker_guild_tech_task",
        name="科技归属任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=0,
        recommended_guest_count=1,
        allow_troops=True,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        is_active=True,
    )
    TroopTemplate.objects.update_or_create(
        key="archer",
        defaults={
            "name": "弓手",
            "base_attack": 10,
            "base_defense": 4,
            "base_hp": 20,
            "speed_bonus": 5,
            "priority": 1,
            "default_count": 0,
        },
    )
    invalidate_troop_templates_cache()
    archer_template = TroopTemplate.objects.get(key="archer")
    GuildTroopStorage.objects.create(guild=guild, troop_template=archer_template, count=10)
    PlayerTechnology.objects.create(manor=leader_manor, tech_key="gong_attack", level=10)
    PlayerTechnology.objects.create(manor=leader_manor, tech_key="gong_hp", level=10)

    guest = create_guest(
        manor=leader_manor, template=create_template("guild_mission_attacker_guild_tech_tpl"), name="先锋"
    )
    guest_snapshots = build_guest_battle_snapshots([guest], include_identity=True)
    run = GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=leader_member,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=1,
        ruby_reward=0,
        guest_ids=[guest.id],
        guest_snapshots=guest_snapshots,
        troop_loadout={"archer": 4},
        attacker_troop_tech_snapshot={"gong_attack": 2, "gong_hp": 1},
    )

    captured: dict[str, Any] = {}

    def _fake_execute_simulation(attacker_units, defender_units, options, config, rng, final_seed):
        troop = next(unit for unit in attacker_units if getattr(unit, "template_key", "") == "archer")
        captured["unit_attack"] = troop.unit_attack
        captured["unit_hp"] = troop.unit_hp
        captured["tech_effects"] = dict(troop.tech_effects)
        simulation = SimpleNamespace(
            losses={"attacker": {"casualties": []}, "defender": {"casualties": []}},
            drops={},
            winner="attacker",
            rounds=[],
            starts_at=timezone.now(),
            completed_at=timezone.now(),
            seed=final_seed,
        )
        return simulation, options.opponent_name or config.get("name", "帮会任务")

    monkeypatch.setattr(
        "guilds.services.guild_missions.build_guild_troop_tech_levels",
        lambda _guild: {"gong_attack": 2, "gong_hp": 1},
        raising=False,
    )
    monkeypatch.setattr("battle.execution._execute_simulation", _fake_execute_simulation)

    from guilds.services import guild_missions as guild_mission_service

    assert guild_mission_service.finalize_guild_mission_run(run) is True
    assert captured["unit_attack"] == 12
    assert captured["unit_hp"] == 22
    assert captured["tech_effects"] == {}


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_falls_back_to_current_guild_tech_for_legacy_empty_snapshot(
    django_user_model,
    monkeypatch,
):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_legacy_snapshot_leader")
    guild = Guild.objects.create(name="帮会任务兼容帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_mission_legacy_snapshot_task",
        name="兼容任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=0,
        recommended_guest_count=1,
        allow_troops=True,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        is_active=True,
    )
    troop_template = TroopTemplate.objects.create(key="guild_mission_legacy_archer", name="兼容弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=20)
    guest = create_guest(manor=leader_manor, template=create_template("guild_mission_legacy_snapshot_tpl"), name="先锋")
    run = GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=leader_member,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=1,
        ruby_reward=0,
        guest_ids=[guest.id],
        guest_snapshots=build_guest_battle_snapshots([guest], include_identity=True),
        troop_loadout={troop_template.key: 6},
        attacker_troop_tech_snapshot={},
    )
    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={troop_template.key: 6},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={},
        drops={},
        winner="defender",
        starts_at=timezone.now(),
        completed_at=timezone.now(),
        seed=7,
    )

    captured: dict[str, Any] = {}

    def _fake_execute_battle(*args, **kwargs):
        captured["options"] = args[3]
        return report

    monkeypatch.setattr("guilds.services.guild_missions.execute_battle", _fake_execute_battle)
    monkeypatch.setattr(
        "guilds.services.guild_missions.build_guild_troop_tech_levels",
        lambda _guild: {"archer_attack": 4, "archer_hp": 2},
        raising=False,
    )

    from guilds.services import guild_missions as guild_mission_service

    assert guild_mission_service.finalize_guild_mission_run(run, now=timezone.now()) is True
    assert captured["options"].attacker_tech_levels == {"archer_attack": 4, "archer_hp": 2}


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_uses_launch_time_troop_tech_snapshot_after_guild_upgrade(
    django_user_model, monkeypatch
):
    from battle.troops import invalidate_troop_templates_cache
    from gameplay.models import PlayerTechnology
    from guilds.models import GuildTechnology

    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_snapshot_timing_leader")
    guild = Guild.objects.create(name="帮会任务时序帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_mission_snapshot_timing_task",
        name="时序任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=0,
        recommended_guest_count=1,
        allow_troops=True,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        is_active=True,
    )
    troop_tactics = GuildTechnology.objects.create(
        guild=guild,
        tech_key="troop_tactics",
        category="combat",
        level=2,
        max_level=10,
    )
    TroopTemplate.objects.update_or_create(
        key="archer",
        defaults={
            "name": "弓手",
            "base_attack": 10,
            "base_defense": 4,
            "base_hp": 20,
            "speed_bonus": 5,
            "priority": 1,
            "default_count": 0,
        },
    )
    invalidate_troop_templates_cache()
    archer_template = TroopTemplate.objects.get(key="archer")
    GuildTroopStorage.objects.create(guild=guild, troop_template=archer_template, count=10)
    PlayerTechnology.objects.create(manor=leader_manor, tech_key="gong_attack", level=10)
    PlayerTechnology.objects.create(manor=leader_manor, tech_key="gong_hp", level=10)
    guest = create_guest(manor=leader_manor, template=create_template("guild_mission_snapshot_timing_tpl"), name="先锋")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={"archer": 4},
    )
    troop_tactics.level = 10
    troop_tactics.save(update_fields=["level"])

    captured: dict[str, Any] = {}

    def _fake_execute_simulation(attacker_units, defender_units, options, config, rng, final_seed):
        troop = next(unit for unit in attacker_units if getattr(unit, "template_key", "") == "archer")
        captured["unit_attack"] = troop.unit_attack
        captured["unit_hp"] = troop.unit_hp
        simulation = SimpleNamespace(
            losses={"attacker": {"casualties": []}, "defender": {"casualties": []}},
            drops={},
            winner="attacker",
            rounds=[],
            starts_at=timezone.now(),
            completed_at=timezone.now(),
            seed=final_seed,
        )
        return simulation, options.opponent_name or config.get("name", "帮会任务")

    monkeypatch.setattr("battle.execution._execute_simulation", _fake_execute_simulation)

    assert guild_mission_service.finalize_guild_mission_run(run) is True
    assert captured["unit_attack"] == 12
    assert captured["unit_hp"] == 24
