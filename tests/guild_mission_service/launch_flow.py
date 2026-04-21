from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from core.exceptions import GuildValidationError
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildTechnology, GuildTroopStorage
from tests.guild_mission_service.support import create_guest, create_template, create_user_with_manor, hero_pool_service


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_snapshots_guests_and_deducts_troops(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_launch_leader")
    guild = Guild.objects.create(name="帮会任务发起帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_task",
        name="巡山",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=2,
        allow_troops=True,
        is_active=True,
        sort_weight=1,
    )
    GuildTechnology.objects.create(guild=guild, tech_key="guild_dispatch_capacity", level=2, max_level=20)
    troop_template = TroopTemplate.objects.create(key="guild_launch_archer", name="任务弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest_a = create_guest(manor=leader_manor, template=create_template("guild_launch_tpl_a"), name="甲")
    guest_b = create_guest(manor=leader_manor, template=create_template("guild_launch_tpl_b"), name="乙")
    entry_a = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_a.id, slot_index=1).entry
    entry_b = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_b.id, slot_index=2).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_a.id)
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_b.id)

    scheduled_run_ids: list[int] = []
    monkeypatch.setattr(
        "guilds.services.guild_missions.schedule_guild_mission_completion",
        lambda run: scheduled_run_ids.append(run.id),
    )

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry_a.id, entry_b.id],
        troop_loadout={troop_template.key: 20},
    )

    assert run.status == "active"
    assert run.started_by_id == leader_member.id
    assert run.selected_guest_count == 2
    assert run.guest_ids == [guest_a.id, guest_b.id]
    assert len(run.guest_snapshots) == 2
    assert GuildTroopStorage.objects.get(guild=guild, troop_template=troop_template).count == 30
    assert run.return_at is not None
    assert scheduled_run_ids == [run.id]


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_snapshots_attacker_troop_tech_levels(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_launch_tech_snapshot_leader")
    guild = Guild.objects.create(name="帮会任务科技快照帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_tech_snapshot_task",
        name="科技快照任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=0,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=1,
    )
    guest = create_guest(manor=leader_manor, template=create_template("guild_launch_tech_snapshot_tpl"), name="甲")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)
    monkeypatch.setattr(
        "guilds.services.guild_missions.build_guild_troop_tech_levels",
        lambda _guild: {"gong_attack": 2, "gong_hp": 1},
        raising=False,
    )

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )

    assert run.attacker_troop_tech_snapshot == {"gong_attack": 2, "gong_hp": 1}


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_clamps_snapshot_hp_when_guest_current_hp_exceeds_max(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_launch_hp_clamp_leader")
    guild = Guild.objects.create(name="帮会任务血量钳制帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_hp_clamp_task",
        name="血量钳制任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=2,
    )
    guest = create_guest(
        manor=leader_manor,
        template=create_template("guild_launch_hp_clamp_tpl"),
        name="钳制门客",
    )
    guest.current_hp = guest.max_hp + 5000
    guest.save(update_fields=["current_hp"])

    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr(
        "guilds.services.guild_missions.schedule_guild_mission_completion",
        lambda _run: None,
    )

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )

    assert len(run.guest_snapshots) == 1
    assert run.guest_snapshots[0]["current_hp"] == run.guest_snapshots[0]["max_hp"]


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_defers_completion_dispatch_until_commit(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_launch_on_commit_leader")
    guild = Guild.objects.create(name="帮会任务提交后调度帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_on_commit_task",
        name="提交后调度",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=3,
    )
    guest = create_guest(manor=leader_manor, template=create_template("guild_launch_on_commit_tpl"), name="甲")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    dispatch_calls: list[list[int]] = []
    monkeypatch.setattr(
        "guilds.services.guild_missions.safe_apply_async",
        lambda _task, *, args=None, **_kwargs: dispatch_calls.append(list(args or [])) or True,
    )

    from guilds.services import guild_missions as guild_mission_service

    with transaction.atomic():
        with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
            run = guild_mission_service.launch_guild_mission(
                guild=guild,
                operator=leader,
                template_key=template.key,
                pool_entry_ids=[entry.id],
                troop_loadout={},
            )
            assert dispatch_calls == []
    assert len(callbacks) == 1

    assert dispatch_calls == [[run.id]]


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_applies_global_time_multiplier_to_schedule(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_launch_time_scale_leader")
    guild = Guild.objects.create(name="帮会任务时间倍率帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_time_scale_task",
        name="时间倍率任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=4,
    )
    guest = create_guest(manor=leader_manor, template=create_template("guild_launch_time_scale_tpl"), name="甲")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    fixed_now = timezone.now()
    monkeypatch.setattr(guild_mission_service.timezone, "now", lambda: fixed_now)

    with override_settings(GAME_TIME_MULTIPLIER=5):
        run = guild_mission_service.launch_guild_mission(
            guild=guild,
            operator=leader,
            template_key=template.key,
            pool_entry_ids=[entry.id],
            troop_loadout={},
        )

    assert run.battle_at == fixed_now + timedelta(seconds=120)
    assert run.return_at == fixed_now + timedelta(seconds=120)


@pytest.mark.django_db
def test_schedule_guild_mission_completion_recomputes_countdown_after_commit(monkeypatch):
    from guilds.services import guild_missions as guild_mission_service

    now = timezone.now()
    run = SimpleNamespace(id=321, return_at=now + timedelta(seconds=30))
    callbacks: list[object] = []
    countdowns: list[int] = []

    monkeypatch.setattr(guild_mission_service.transaction, "on_commit", lambda callback: callbacks.append(callback))
    monkeypatch.setattr(guild_mission_service.timezone, "now", lambda: now)
    monkeypatch.setattr(
        guild_mission_service,
        "safe_apply_async",
        lambda _task, *, countdown=None, **_kwargs: countdowns.append(int(countdown or 0)) or True,
    )

    guild_mission_service.schedule_guild_mission_completion(run)

    monkeypatch.setattr(guild_mission_service.timezone, "now", lambda: now + timedelta(seconds=7))
    callbacks[0]()

    assert countdowns == [23]


@pytest.mark.django_db
def test_schedule_guild_mission_completion_finalizes_sync_when_due_dispatch_fails(monkeypatch):
    from guilds.services import guild_missions as guild_mission_service

    now = timezone.now()
    run = SimpleNamespace(id=322, return_at=now)
    callbacks: list[object] = []
    finalized: list[tuple[int, object]] = []

    monkeypatch.setattr(guild_mission_service.transaction, "on_commit", lambda callback: callbacks.append(callback))
    monkeypatch.setattr(guild_mission_service.timezone, "now", lambda: now)
    monkeypatch.setattr(guild_mission_service, "safe_apply_async", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        guild_mission_service,
        "finalize_guild_mission_run",
        lambda scheduled_run, now=None: finalized.append((scheduled_run.id, now)) or True,
    )

    guild_mission_service.schedule_guild_mission_completion(run)
    callbacks[0]()

    assert finalized == [(322, now)]


@pytest.mark.django_db(transaction=True)
def test_get_guild_mission_page_context_does_not_finalize_overdue_active_run(django_user_model, monkeypatch):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_context_finalize_leader")
    guild = Guild.objects.create(name="帮会任务读路径收口帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_context_finalize_task",
        name="读路径收口任务",
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
        template=create_template("guild_context_finalize_tpl"),
        name="收口门客",
    )
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_mission_queries as guild_mission_query_service
    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )
    GuildMissionRun.objects.filter(pk=run.pk).update(return_at=timezone.now() - timedelta(seconds=1))
    monkeypatch.setattr(
        guild_mission_service,
        "refresh_due_guild_mission_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("refresh_due_guild_mission_runs should not run during page context reads")
        ),
    )

    context = guild_mission_query_service.get_guild_mission_page_context(leader_member)

    assert context["active_run"] is None
    run.refresh_from_db()
    assert run.status == GuildMissionRun.Status.ACTIVE
    assert run.completed_at is None


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_keeps_overdue_finalization_when_new_launch_validation_fails(
    django_user_model, monkeypatch
):
    leader, leader_manor = create_user_with_manor(django_user_model, "guild_mission_launch_overdue_rollback_leader")
    guild = Guild.objects.create(name="帮会任务事务收口帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_overdue_rollback_task",
        name="事务收口任务",
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
        template=create_template("guild_launch_overdue_rollback_tpl"),
        name="事务收口门客",
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
        seed=112,
    )
    GuildMissionRun.objects.filter(pk=overdue_run.pk).update(return_at=timezone.now() - timedelta(seconds=1))
    monkeypatch.setattr(guild_mission_service, "execute_battle", lambda *args, **kwargs: report)

    with pytest.raises(GuildValidationError, match="请选择至少一名上阵门客"):
        guild_mission_service.launch_guild_mission(
            guild=guild,
            operator=leader,
            template_key=template.key,
            pool_entry_ids=[],
            troop_loadout={},
        )

    overdue_run.refresh_from_db()
    assert overdue_run.status == GuildMissionRun.Status.COMPLETED
    assert overdue_run.completed_at is not None
    assert GuildMissionRun.objects.filter(guild=guild, status=GuildMissionRun.Status.ACTIVE).count() == 0
