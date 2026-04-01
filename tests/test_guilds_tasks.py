from __future__ import annotations

from datetime import timedelta

import pytest
from django.db.utils import DatabaseError
from django.utils import timezone
from kombu.exceptions import OperationalError

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.services import hero_pool as hero_pool_service


def _dispatch_immediately(task, *, args=None, kwargs=None, countdown=None, logger=None, log_message="", **_kwargs):
    del kwargs, countdown, logger, log_message, _kwargs
    task.run(*(args or []))
    return True


def _create_user_with_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    return user, manor


def _create_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"模板{key}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )


def _create_guest(*, manor, template: GuestTemplate, name: str, level: int = 20) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        level=level,
        force=120,
        intellect=85,
        defense_stat=100,
        agility=90,
        luck=60,
    )


def _create_active_guild_run(django_user_model, *, username: str, key_suffix: str, return_at):
    from battle.models import TroopTemplate
    from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildTroopStorage

    user, manor = _create_user_with_manor(django_user_model, username)
    guild = Guild.objects.create(name=f"任务帮会{key_suffix}", founder=user, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key=f"guild_task_{key_suffix}",
        name="任务测试",
        description="",
        difficulty="junior",
        task_type="dispatch",
        base_duration_seconds=60,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
    )
    troop_template = TroopTemplate.objects.create(key=f"guild_task_archer_{key_suffix}", name="任务弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = _create_guest(manor=manor, template=_create_template(f"guild_task_tpl_{key_suffix}"), name="队长")
    entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry.id)

    run = GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=member,
        status="active",
        selected_guest_count=1,
        ruby_reward=template.ruby_reward,
        guest_ids=[guest.id],
        guest_snapshots=[],
        troop_loadout={troop_template.key: 20},
        battle_at=return_at - timedelta(seconds=30),
        return_at=return_at,
    )
    return run


@pytest.mark.django_db
def test_complete_guild_mission_task_reschedules_when_not_due(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_mission_task

    now = timezone.now()
    run = _create_active_guild_run(
        django_user_model,
        username="guild_task_future",
        key_suffix="future",
        return_at=now + timedelta(seconds=10),
    )

    called: dict[str, object] = {}
    finalized: list[int] = []

    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.safe_apply_async_with_dedup",
        lambda *_args, args=None, countdown=None, **_kwargs: called.update({"args": args, "countdown": countdown})
        or True,
    )
    monkeypatch.setattr("guilds.tasks.finalize_guild_mission_run", lambda *_args, **_kwargs: finalized.append(run.id))
    monkeypatch.setattr(
        complete_guild_mission_task,
        "retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    assert complete_guild_mission_task.run(run.id) == "rescheduled"
    assert called["args"] == [run.id]
    assert called["countdown"] == 10
    assert not finalized


@pytest.mark.django_db
def test_complete_guild_mission_task_finalizes_due_run(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_mission_task

    now = timezone.now()
    run = _create_active_guild_run(
        django_user_model,
        username="guild_task_due",
        key_suffix="due",
        return_at=now - timedelta(seconds=1),
    )

    finalized: list[tuple[int, object]] = []
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.finalize_guild_mission_run",
        lambda locked_run, now=None: finalized.append((locked_run.id, now)),
    )

    assert complete_guild_mission_task.run(run.id) == "completed"
    assert finalized == [(run.id, now)]


@pytest.mark.django_db
def test_complete_guild_mission_task_retries_when_reschedule_dispatch_fails(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_mission_task

    now = timezone.now()
    run = _create_active_guild_run(
        django_user_model,
        username="guild_task_retry",
        key_suffix="retry",
        return_at=now + timedelta(seconds=5),
    )

    retried: dict[str, object] = {}

    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr("guilds.tasks.safe_apply_async_with_dedup", lambda *_args, **_kwargs: False)

    def _retry(*, exc=None, **_kwargs):
        retried["exc"] = exc
        raise RuntimeError("retried")

    monkeypatch.setattr(complete_guild_mission_task, "retry", _retry)

    with pytest.raises(RuntimeError, match="retried"):
        complete_guild_mission_task.run(run.id)

    assert "guild mission reschedule dispatch failed" in str(retried["exc"])


@pytest.mark.django_db
def test_scan_due_guild_missions_finalizes_overdue_runs(monkeypatch, django_user_model):
    from guilds.models import GuildMissionRun
    from guilds.tasks import scan_due_guild_missions

    now = timezone.now()
    due_run = _create_active_guild_run(
        django_user_model,
        username="guild_task_scan_due",
        key_suffix="scan_due",
        return_at=now - timedelta(seconds=5),
    )
    future_run = _create_active_guild_run(
        django_user_model,
        username="guild_task_scan_future",
        key_suffix="scan_future",
        return_at=now + timedelta(seconds=30),
    )

    finalized: list[int] = []
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)

    def _fake_finalize(run, *, now=None):
        GuildMissionRun.objects.filter(pk=run.pk).update(status=GuildMissionRun.Status.COMPLETED, completed_at=now)
        finalized.append(run.id)
        return True

    monkeypatch.setattr("guilds.tasks.finalize_guild_mission_run", _fake_finalize)

    assert scan_due_guild_missions() == 1
    assert finalized == [due_run.id]
    assert GuildMissionRun.objects.get(pk=future_run.pk).status == GuildMissionRun.Status.ACTIVE


@pytest.mark.django_db
def test_scan_due_guild_missions_programming_error_bubbles_up(monkeypatch, django_user_model):
    from guilds.tasks import scan_due_guild_missions

    now = timezone.now()
    _create_active_guild_run(
        django_user_model,
        username="guild_task_scan_bug",
        key_suffix="scan_bug",
        return_at=now - timedelta(seconds=5),
    )

    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.finalize_guild_mission_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken guild mission scan contract")),
    )

    with pytest.raises(AssertionError, match="broken guild mission scan contract"):
        scan_due_guild_missions()


@pytest.mark.django_db
def test_guild_tech_daily_production_runs_and_updates_last_production_at(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.tasks import guild_tech_daily_production

    calls: list[tuple[str, int]] = []

    monkeypatch.setattr(
        "guilds.tasks.produce_equipment",
        lambda guild, level: calls.append(("equipment", int(level))),
    )
    monkeypatch.setattr(
        "guilds.tasks.produce_experience_items",
        lambda guild, level: calls.append(("exp", int(level))),
    )
    monkeypatch.setattr(
        "guilds.tasks.produce_resource_packs",
        lambda guild, level: calls.append(("packs", int(level))),
    )
    monkeypatch.setattr(
        guild_tech_daily_production,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr("common.utils.celery.safe_apply_async", _dispatch_immediately)

    founder = django_user_model.objects.create_user(username="g_founder", password="pass")
    guild = Guild.objects.create(name="G1", founder=founder, is_active=True)

    for key in ("equipment_forge", "experience_refine", "resource_supply"):
        GuildTechnology.objects.create(guild=guild, tech_key=key, level=2)

    result = guild_tech_daily_production.run()
    assert result == "dispatched 1 guild tasks"
    assert sorted(calls) == [("equipment", 2), ("exp", 2), ("packs", 2)]

    updated = {t.tech_key: t.last_production_at for t in GuildTechnology.objects.filter(guild=guild)}
    assert all(updated.values())


@pytest.mark.django_db
def test_guild_tech_daily_production_handles_inner_errors(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.tasks import guild_tech_daily_production

    calls: list[str] = []

    def _boom(_guild, _level):
        raise DatabaseError("boom")

    monkeypatch.setattr("guilds.tasks.produce_equipment", _boom)
    monkeypatch.setattr(
        "guilds.tasks.produce_experience_items",
        lambda guild, level: calls.append("exp"),
    )
    monkeypatch.setattr(
        "guilds.tasks.produce_resource_packs",
        lambda guild, level: calls.append("packs"),
    )
    monkeypatch.setattr(
        guild_tech_daily_production,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr("common.utils.celery.safe_apply_async", _dispatch_immediately)

    founder = django_user_model.objects.create_user(username="g_founder2", password="pass")
    guild = Guild.objects.create(name="G2", founder=founder, is_active=True)
    GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=2)
    GuildTechnology.objects.create(guild=guild, tech_key="experience_refine", level=2)
    GuildTechnology.objects.create(guild=guild, tech_key="resource_supply", level=2)

    result = guild_tech_daily_production.run()
    assert result == "dispatched 1 guild tasks"
    assert sorted(calls) == ["exp", "packs"]


@pytest.mark.django_db
def test_process_single_guild_production_is_idempotent_per_day(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.tasks import process_single_guild_production

    calls: list[int] = []

    monkeypatch.setattr("guilds.tasks.produce_equipment", lambda guild, level: calls.append(level))

    founder = django_user_model.objects.create_user(username="g_founder_daily_once", password="pass")
    guild = Guild.objects.create(name="G-once", founder=founder, is_active=True)
    tech = GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=2)

    first = process_single_guild_production.run(guild.id)
    second = process_single_guild_production.run(guild.id)

    tech.refresh_from_db()
    assert first == f"processed guild {guild.id}: equipment"
    assert second == f"processed guild {guild.id}: "
    assert calls == [2]
    assert tech.last_production_at is not None


@pytest.mark.django_db
def test_process_single_guild_production_does_not_mark_timestamp_on_failure(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.tasks import process_single_guild_production

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "guilds.tasks.produce_equipment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("boom")),
    )
    monkeypatch.setattr(
        "common.utils.celery.safe_apply_async",
        lambda *_args, **kwargs: captured.setdefault("kwargs", kwargs) or True,
    )

    founder = django_user_model.objects.create_user(username="g_founder_daily_fail", password="pass")
    guild = Guild.objects.create(name="G-fail", founder=founder, is_active=True)
    tech = GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=2)

    result = process_single_guild_production.run(guild.id)

    tech.refresh_from_db()
    assert result == f"processed guild {guild.id}: ; failed_guild_ids={[guild.id]}"
    assert tech.last_production_at is None
    assert captured["kwargs"]["args"] == [None, [guild.id], 1]


@pytest.mark.django_db
def test_process_single_guild_production_persists_failed_ids_when_retry_dispatch_fails(monkeypatch, django_user_model):
    from django.core.cache import cache

    from guilds.models import Guild, GuildTechnology
    from guilds.tasks import (
        FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY,
        get_failed_guild_ids,
        process_single_guild_production,
    )

    cache.delete(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY)

    monkeypatch.setattr(
        "guilds.tasks.produce_equipment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("boom")),
    )
    monkeypatch.setattr("common.utils.celery.safe_apply_async", lambda *_args, **_kwargs: False)

    founder = django_user_model.objects.create_user(username="g_founder_dispatch_persist", password="pass")
    guild = Guild.objects.create(name="G-dispatch-persist", founder=founder, is_active=True)
    GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=2)

    result = process_single_guild_production.run(guild.id)

    assert result == f"processed guild {guild.id}: ; failed_guild_ids={[guild.id]}"
    assert cache.get(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY) == [guild.id]
    assert get_failed_guild_ids() == [guild.id]

    cache.delete(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY)


@pytest.mark.django_db
def test_process_single_guild_production_programming_error_bubbles_up(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.tasks import process_single_guild_production

    founder = django_user_model.objects.create_user(username="g_founder_programming_error", password="pass")
    guild = Guild.objects.create(name="G-programming-error", founder=founder, is_active=True)
    tech = GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=2)

    monkeypatch.setattr(
        "guilds.tasks.produce_equipment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken guild production contract")),
    )

    with pytest.raises(AssertionError, match="broken guild production contract"):
        process_single_guild_production.run(guild.id)

    tech.refresh_from_db()
    assert tech.last_production_at is None


def test_process_single_guild_production_missing_guild_id_bubbles_up():
    from guilds.tasks import process_single_guild_production

    with pytest.raises(AssertionError, match="guild_id is required when failed_ids is empty"):
        process_single_guild_production.run()


def test_persist_failed_guild_ids_cache_infrastructure_error_is_best_effort(monkeypatch):
    from guilds.tasks import _persist_failed_guild_ids

    monkeypatch.setattr(
        "guilds.tasks.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("cache unavailable")),
    )

    _persist_failed_guild_ids([1, 2])


def test_persist_failed_guild_ids_cache_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import _persist_failed_guild_ids

    monkeypatch.setattr(
        "guilds.tasks.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken failed-id cache contract")),
    )

    with pytest.raises(AssertionError, match="broken failed-id cache contract"):
        _persist_failed_guild_ids([1, 2])


def test_get_failed_guild_ids_cache_infrastructure_error_returns_empty(monkeypatch):
    from guilds.tasks import get_failed_guild_ids

    monkeypatch.setattr(
        "guilds.tasks.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("cache unavailable")),
    )

    assert get_failed_guild_ids() == []


def test_get_failed_guild_ids_cache_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import get_failed_guild_ids

    monkeypatch.setattr(
        "guilds.tasks.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken failed-id cache read contract")),
    )

    with pytest.raises(AssertionError, match="broken failed-id cache read contract"):
        get_failed_guild_ids()


def test_clear_failed_guild_ids_cache_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import _clear_failed_guild_ids

    monkeypatch.setattr(
        "guilds.tasks.cache.delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken failed-id cache delete contract")),
    )

    with pytest.raises(AssertionError, match="broken failed-id cache delete contract"):
        _clear_failed_guild_ids()


@pytest.mark.django_db
def test_guild_tech_daily_production_retries_when_dispatch_fails(monkeypatch, django_user_model):
    from guilds.models import Guild
    from guilds.tasks import guild_tech_daily_production

    founder = django_user_model.objects.create_user(username="g_founder_dispatch_fail", password="pass")
    Guild.objects.create(name="G-dispatch", founder=founder, is_active=True)

    monkeypatch.setattr(
        "common.utils.celery.safe_apply_async",
        lambda *_a, **_k: (_ for _ in ()).throw(OperationalError("dispatch failed")),
    )

    called = {"retry": 0}

    def _retry(exc):
        called["retry"] += 1
        raise RuntimeError("retried")

    monkeypatch.setattr(guild_tech_daily_production, "retry", _retry)

    with pytest.raises(RuntimeError, match="retried"):
        guild_tech_daily_production.run()

    assert called["retry"] == 1


@pytest.mark.django_db
def test_guild_tech_daily_production_programming_error_bubbles_up(monkeypatch, django_user_model):
    from guilds.models import Guild
    from guilds.tasks import guild_tech_daily_production

    founder = django_user_model.objects.create_user(username="g_founder_dispatch_programming", password="pass")
    Guild.objects.create(name="G-dispatch-programming", founder=founder, is_active=True)

    monkeypatch.setattr(
        "common.utils.celery.safe_apply_async",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("broken guild dispatch contract")),
    )
    monkeypatch.setattr(
        guild_tech_daily_production,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(AssertionError, match="broken guild dispatch contract"):
        guild_tech_daily_production.run()


@pytest.mark.django_db
def test_reset_guild_weekly_stats_retries_on_error(monkeypatch):
    from guilds.tasks import reset_guild_weekly_stats

    monkeypatch.setattr("guilds.tasks.reset_weekly_contributions", lambda: (_ for _ in ()).throw(DatabaseError("x")))

    called = {"retry": 0}

    def _retry(exc):
        called["retry"] += 1
        raise RuntimeError("retried")

    monkeypatch.setattr(reset_guild_weekly_stats, "retry", _retry)

    with pytest.raises(RuntimeError, match="retried"):
        reset_guild_weekly_stats.run()

    assert called["retry"] == 1


@pytest.mark.django_db
def test_reset_guild_weekly_stats_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import reset_guild_weekly_stats

    monkeypatch.setattr(
        "guilds.tasks.reset_weekly_contributions",
        lambda: (_ for _ in ()).throw(AssertionError("broken weekly reset contract")),
    )
    monkeypatch.setattr(
        reset_guild_weekly_stats,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(AssertionError, match="broken weekly reset contract"):
        reset_guild_weekly_stats.run()


@pytest.mark.django_db
def test_cleanup_invalid_guild_hero_pool_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import cleanup_invalid_guild_hero_pool

    monkeypatch.setattr(
        "guilds.tasks.cleanup_invalid_hero_pool_entries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken hero pool cleanup contract")),
    )
    monkeypatch.setattr(
        cleanup_invalid_guild_hero_pool,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(AssertionError, match="broken hero pool cleanup contract"):
        cleanup_invalid_guild_hero_pool.run()


@pytest.mark.django_db
def test_cleanup_old_guild_logs_deletes_only_old_rows(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildDonationLog, GuildExchangeLog, GuildMember, GuildResourceLog
    from guilds.tasks import cleanup_old_guild_logs

    monkeypatch.setattr(
        cleanup_old_guild_logs,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    founder = django_user_model.objects.create_user(username="g_founder3", password="pass")
    member_user = django_user_model.objects.create_user(username="g_member", password="pass")
    guild = Guild.objects.create(name="G3", founder=founder, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=member_user)

    donation = GuildDonationLog.objects.create(
        guild=guild,
        member=member,
        resource_type="silver",
        amount=1,
        contribution_gained=1,
    )
    exchange = GuildExchangeLog.objects.create(
        guild=guild,
        member=member,
        item_key="x",
        quantity=1,
        contribution_cost=1,
    )
    resource = GuildResourceLog.objects.create(guild=guild, action="donation", silver_change=1)

    old_ts = timezone.now() - timedelta(days=31)
    GuildDonationLog.objects.filter(pk=donation.pk).update(donated_at=old_ts)
    GuildExchangeLog.objects.filter(pk=exchange.pk).update(exchanged_at=old_ts)
    GuildResourceLog.objects.filter(pk=resource.pk).update(created_at=old_ts)

    # New rows should be kept.
    GuildDonationLog.objects.create(
        guild=guild,
        member=member,
        resource_type="silver",
        amount=2,
        contribution_gained=2,
    )

    result = cleanup_old_guild_logs.run()
    assert "cleaned up" in result
    assert GuildDonationLog.objects.count() == 1
    assert GuildExchangeLog.objects.count() == 0
    assert GuildResourceLog.objects.count() == 0


@pytest.mark.django_db
def test_cleanup_invalid_guild_hero_pool_task(monkeypatch, django_user_model):
    from gameplay.services.manor.core import ensure_manor
    from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
    from guilds.models import Guild, GuildBattleLineupEntry, GuildHeroPoolEntry, GuildMember
    from guilds.tasks import cleanup_invalid_guild_hero_pool

    monkeypatch.setattr(
        cleanup_invalid_guild_hero_pool,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    leader = django_user_model.objects.create_user(username="ghp_cleanup_leader", password="pass")
    manor = ensure_manor(leader)
    guild = Guild.objects.create(name="任务清理帮", founder=leader, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = GuestTemplate.objects.create(
        key="ghp_cleanup_tpl",
        name="清理模板",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        custom_name="清理门客",
        level=10,
        force=100,
        intellect=80,
        defense_stat=90,
        agility=70,
        luck=50,
    )

    entry = GuildHeroPoolEntry.objects.create(
        guild=guild,
        owner_member=member,
        source_guest=guest,
        slot_index=1,
        last_submitted_at=timezone.now() - timedelta(days=8),
    )
    GuildBattleLineupEntry.objects.create(guild=guild, pool_entry=entry, slot_index=1, selected_by=leader)

    # 制造无效数据：成员已不在本帮
    member.is_active = False
    member.save(update_fields=["is_active"])

    result = cleanup_invalid_guild_hero_pool.run()

    assert "cleaned" in result
    assert GuildHeroPoolEntry.objects.filter(pk=entry.pk).count() == 0
    assert GuildBattleLineupEntry.objects.filter(guild=guild).count() == 0
