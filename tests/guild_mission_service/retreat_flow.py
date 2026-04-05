from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from core.exceptions import GuildValidationError
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildTroopStorage
from tests.guild_mission_service.support import create_guest, create_template, create_user_with_manor, hero_pool_service


@pytest.mark.django_db(transaction=True)
def test_retreat_guild_mission_returns_all_troops_without_ruby_reward(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_retreat_leader")
    guild = Guild.objects.create(name="帮会任务撤回帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_retreat_task",
        name="撤回测试任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
        sort_weight=2,
    )
    troop_template = TroopTemplate.objects.create(key="guild_retreat_archer", name="撤回弓手")
    storage = GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = create_guest(manor=leader_manor, template=create_template("guild_retreat_tpl"), name="撤回门客")
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

    guild_mission_service.request_retreat(run=run, operator=leader)
    run.refresh_from_db()
    storage.refresh_from_db()

    assert run.status == "retreated"
    assert run.completed_at is not None
    assert storage.count == 50
    assert not guild.warehouse_items.filter(item_key="red_ruby").exists()


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_ignores_overdue_active_run_after_finalizing(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_launch_overdue_leader")
    guild = Guild.objects.create(name="帮会任务发起收口帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_overdue_task",
        name="发起收口任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
    )
    guest = create_guest(
        manor=leader_manor,
        template=create_template("guild_launch_overdue_tpl"),
        name="继续出征门客",
    )
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    overdue_run = guild_mission_service.launch_guild_mission(
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
        seed=12,
    )
    GuildMissionRun.objects.filter(pk=overdue_run.pk).update(return_at=timezone.now() - timedelta(seconds=1))
    monkeypatch.setattr(guild_mission_service, "execute_battle", lambda *args, **kwargs: report)

    new_run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )

    overdue_run.refresh_from_db()
    assert overdue_run.status == GuildMissionRun.Status.COMPLETED
    assert new_run.status == GuildMissionRun.Status.ACTIVE
    assert GuildMissionRun.objects.filter(guild=guild, status=GuildMissionRun.Status.ACTIVE).count() == 1


@pytest.mark.django_db(transaction=True)
def test_request_retreat_finalizes_overdue_active_run_instead_of_refunding_all_troops(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_retreat_overdue_leader")
    guild = Guild.objects.create(name="帮会任务撤回收口帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_retreat_overdue_task",
        name="撤回收口任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
    )
    troop_template = TroopTemplate.objects.create(key="guild_retreat_overdue_archer", name="撤回收口弓手")
    storage = GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = create_guest(
        manor=leader_manor,
        template=create_template("guild_retreat_overdue_tpl"),
        name="收口撤回门客",
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
        troop_loadout={troop_template.key: 20},
    )
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
        seed=13,
    )
    GuildMissionRun.objects.filter(pk=run.pk).update(return_at=timezone.now() - timedelta(seconds=1))
    monkeypatch.setattr(guild_mission_service, "execute_battle", lambda *args, **kwargs: report)

    with pytest.raises(GuildValidationError, match="当前任务不可撤回"):
        guild_mission_service.request_retreat(run=run, operator=leader)

    run.refresh_from_db()
    storage.refresh_from_db()

    assert run.status == GuildMissionRun.Status.COMPLETED
    assert storage.count == 42
