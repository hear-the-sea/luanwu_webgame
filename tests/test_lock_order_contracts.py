from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from django.db import connection, transaction
from django.db.models import QuerySet

from gameplay.models import Manor
from gameplay.services.arena import coop_core
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions_impl.execution_adapters import load_locked_mission_run
from gameplay.services.raid.combat import battle as raid_battle
from gameplay.services.raid.combat.run_runtime import load_locked_raid_run
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildRaidRun
from guilds.services import guild_dispatch, guild_missions, guild_raids, guild_troops, hero_pool


@pytest.mark.django_db
def test_hero_pool_member_lock_query_does_not_join_manor(django_user_model):
    user = django_user_model.objects.create_user(username="hero_pool_lock_order")
    guild = Guild.objects.create(name="锁序帮会", founder=user)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader")
    member_queries: list[str] = []

    def capture_member_query(execute, sql, params, many, context):
        if "guild_members" in sql.lower() and sql.lstrip().upper().startswith("SELECT"):
            member_queries.append(sql.lower())
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture_member_query), transaction.atomic():
        hero_pool._lock_guild_and_active_member(guild_id=guild.id, member_id=member.id)

    assert member_queries
    assert all("gameplay_manor" not in sql for sql in member_queries)


@pytest.mark.django_db
def test_hero_pool_locked_queries_do_not_select_related_manor(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="hero_pool_all_lock_points")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="完整锁序帮会", founder=user)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader")
    template = GuestTemplate.objects.create(
        key="hero_pool_lock_order_template",
        name="锁序门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )
    guest = Guest.objects.create(manor=manor, template=template, custom_name="锁序门客")
    locked_manor_joins: list[tuple[str, tuple[str, ...]]] = []
    executed_lock_sql: list[str] = []
    original_select_related = QuerySet.select_related

    def capture_locked_select_related(queryset, *fields):
        if queryset.query.select_for_update and any("manor" in field.split("__") for field in fields):
            locked_manor_joins.append((queryset.model._meta.label_lower, fields))
        return original_select_related(queryset, *fields)

    def capture_lock_sql(execute, sql, params, many, context):
        normalized_sql = sql.lower()
        if "for update" in normalized_sql:
            executed_lock_sql.append(normalized_sql)
        return execute(sql, params, many, context)

    monkeypatch.setattr(QuerySet, "select_related", capture_locked_select_related)

    with connection.execute_wrapper(capture_lock_sql):
        entry = hero_pool.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
        hero_pool.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry.id)
        hero_pool.lock_guild_lineup_for_dispatch(guild)
        guild_dispatch.load_dispatch_lineup_rows(guild=guild, pool_entry_ids=[entry.id])

    assert locked_manor_joins == []
    if connection.features.has_select_for_update:
        assert executed_lock_sql
        assert all("gameplay_manor" not in sql for sql in executed_lock_sql)


@pytest.mark.django_db
def test_guild_troop_member_lock_does_not_join_guild_or_manor(django_user_model):
    user = django_user_model.objects.create_user(username="guild_troop_lock_order")
    ensure_manor(user)
    guild = Guild.objects.create(name="护院锁序帮会", founder=user)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader")
    lock_targets: list[str] = []

    def capture_lock_query(execute, sql, params, many, context):
        normalized_sql = sql.lower()
        if sql.lstrip().upper().startswith("SELECT"):
            if 'from "gameplay_manor"' in normalized_sql or "from `gameplay_manor`" in normalized_sql:
                lock_targets.append("manor")
            elif 'from "guilds"' in normalized_sql or "from `guilds`" in normalized_sql:
                lock_targets.append("guild")
            elif 'from "guild_members"' in normalized_sql or "from `guild_members`" in normalized_sql:
                lock_targets.append("member")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture_lock_query), transaction.atomic():
        locked_member = guild_troops._lock_active_member(member)

    assert lock_targets == ["manor", "guild", "member"]

    relation_queries: list[str] = []

    def capture_relation_query(execute, sql, params, many, context):
        relation_queries.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture_relation_query):
        assert locked_member.guild_id == guild.id
        assert locked_member.guild.pk == guild.id
        assert locked_member.user.manor.user_id == user.id

    assert relation_queries == []


@pytest.mark.django_db
def test_guild_mission_report_owner_preflight_recreates_missing_manor(django_user_model):
    user = django_user_model.objects.create_user(username="mission_report_owner_preflight")
    guild = Guild.objects.create(name="任务战报归属帮", founder=user)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="report_owner_preflight",
        name="战报归属预检",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        recommended_guest_count=1,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
    )
    run = GuildMissionRun.objects.create(guild=guild, template=template, started_by=member)
    Manor.objects.filter(user=user).delete()

    owner_user_id = guild_missions._ensure_report_owner_for_mission_run(run.id)

    assert owner_user_id == user.id
    assert Manor.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_guild_raid_report_owner_preflight_recreates_missing_manor(django_user_model):
    attacker = django_user_model.objects.create_user(username="raid_report_owner_preflight")
    defender = django_user_model.objects.create_user(username="raid_report_owner_defender")
    attacker_guild = Guild.objects.create(name="进攻归属帮", founder=attacker)
    defender_guild = Guild.objects.create(name="防守归属帮", founder=defender)
    member = GuildMember.objects.create(guild=attacker_guild, user=attacker, position="leader")
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=member,
    )
    Manor.objects.filter(user=attacker).delete()

    owner_user_id = guild_raids._ensure_report_owner_for_raid_run(run.id)

    assert owner_user_id == attacker.id
    assert Manor.objects.filter(user=attacker).exists()


def test_locked_guild_report_owner_identity_prefers_starter_then_founder():
    started = SimpleNamespace(started_by_id=1, started_by=SimpleNamespace(user_id=7))
    mission_fallback = SimpleNamespace(started_by=None, guild=SimpleNamespace(founder_id=8))
    raid_fallback = SimpleNamespace(started_by=None, attacker_guild=SimpleNamespace(founder_id=9))

    assert guild_missions._locked_mission_report_owner_user_id(started) == 7
    assert guild_missions._locked_mission_report_owner_user_id(mission_fallback) == 8
    assert guild_raids._locked_raid_report_owner_user_id(started) == 7
    assert guild_raids._locked_raid_report_owner_user_id(raid_fallback) == 9


@pytest.mark.django_db
def test_guild_mission_settlement_rejects_stale_report_owner_discovery(django_user_model, monkeypatch):
    owner = django_user_model.objects.create_user(username="mission_report_owner_current")
    stale_owner = django_user_model.objects.create_user(username="mission_report_owner_stale")
    guild = Guild.objects.create(name="任务归属复核帮", founder=owner)
    member = GuildMember.objects.create(guild=guild, user=owner, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="report_owner_revalidate",
        name="战报归属复核",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        recommended_guest_count=1,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
    )
    run = GuildMissionRun.objects.create(guild=guild, template=template, started_by=member)
    monkeypatch.setattr(
        guild_missions,
        "_ensure_report_owner_for_mission_run",
        lambda _run_id: stale_owner.id,
    )

    assert guild_missions.finalize_guild_mission_run(run) is False
    run.refresh_from_db()
    assert run.status == GuildMissionRun.Status.ACTIVE
    assert run.battle_report_id is None


@pytest.mark.django_db
def test_guild_raid_settlement_rejects_stale_report_owner_discovery(django_user_model, monkeypatch):
    owner = django_user_model.objects.create_user(username="raid_report_owner_current")
    stale_owner = django_user_model.objects.create_user(username="raid_report_owner_stale")
    defender = django_user_model.objects.create_user(username="raid_report_owner_target")
    attacker_guild = Guild.objects.create(name="进攻归属复核帮", founder=owner)
    defender_guild = Guild.objects.create(name="防守归属复核帮", founder=defender)
    member = GuildMember.objects.create(guild=attacker_guild, user=owner, position="leader")
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=member,
    )
    monkeypatch.setattr(
        guild_raids,
        "_ensure_report_owner_for_raid_run",
        lambda _run_id: stale_owner.id,
    )

    assert guild_raids.process_guild_raid_battle(run) is False
    run.refresh_from_db()
    assert run.status == GuildRaidRun.Status.MARCHING
    assert run.battle_report_id is None


def test_guild_mission_finalization_locks_report_manor_before_guild_root():
    source = inspect.getsource(guild_missions._finalize_guild_mission_run_atomic)

    assert source.index("_lock_report_owner_manor_for_mission_run") < source.index("_lock_guild_root_for_mission_run")


def test_guild_raid_battle_locks_report_manor_before_guild_roots():
    source = inspect.getsource(guild_raids._process_guild_raid_battle_atomic)

    assert source.index("_lock_report_owner_manor_for_raid_run") < source.index("_lock_guild_roots_for_raid_run")


def test_load_locked_mission_run_locks_manor_before_run(monkeypatch):
    calls: list[str] = []
    run = object()

    class _IdentityQuery:
        def values_list(self, *_args, **_kwargs):
            return self

        def first(self):
            return 7

    class _LockedRunQuery:
        def select_related(self, *_args):
            return self

        def prefetch_related(self, *_args):
            return self

        def filter(self, **_kwargs):
            return self

        def first(self):
            return run

    class _RunManager:
        def filter(self, **_kwargs):
            return _IdentityQuery()

        def select_for_update(self):
            calls.append("run")
            return _LockedRunQuery()

    class _MissionRunModel:
        objects = _RunManager()

    class _LockedManorQuery:
        def get(self, **_kwargs):
            calls.append("manor")
            return object()

    monkeypatch.setattr(Manor.objects, "select_for_update", lambda: _LockedManorQuery())

    result = load_locked_mission_run(mission_run_model=_MissionRunModel, run_pk=3)

    assert result is run
    assert calls == ["manor", "run"]


def test_load_locked_raid_run_locks_manors_before_run(monkeypatch):
    calls: list[str] = []
    run = object()

    class _IdentityQuery:
        def values_list(self, *_args, **_kwargs):
            return self

        def first(self):
            return (9, 4)

    class _LockedRunQuery:
        def select_related(self, *_args):
            return self

        def prefetch_related(self, *_args):
            return self

        def filter(self, **_kwargs):
            return self

        def first(self):
            return run

    class _RunManager:
        def filter(self, **_kwargs):
            return _IdentityQuery()

        def select_for_update(self):
            calls.append("run")
            return _LockedRunQuery()

    class _RaidRunModel:
        objects = _RunManager()

    class _LockedManorQuery:
        def filter(self, **kwargs):
            assert kwargs == {"pk__in": [4, 9]}
            return self

        def order_by(self, field):
            assert field == "pk"
            calls.append("manors")
            return []

    monkeypatch.setattr(Manor.objects, "select_for_update", lambda: _LockedManorQuery())

    result = load_locked_raid_run(raid_run_model=_RaidRunModel, run_pk=3)

    assert result is run
    assert calls == ["manors", "run"]


def test_guild_mission_root_lock_discovers_then_locks_guild(monkeypatch):
    calls: list[str] = []
    locked_guild = object()

    class _IdentityQuery:
        def values_list(self, *_args, **_kwargs):
            return self

        def first(self):
            calls.append("identity")
            return 8

    class _RunManager:
        def filter(self, **_kwargs):
            return _IdentityQuery()

    class _RunModel:
        objects = _RunManager()

    class _LockedGuildQuery:
        def get(self, **kwargs):
            assert kwargs == {"pk": 8}
            calls.append("guild")
            return locked_guild

    class _GuildManager:
        def select_for_update(self):
            return _LockedGuildQuery()

    class _GuildModel:
        objects = _GuildManager()

    monkeypatch.setattr(guild_missions, "GuildMissionRun", _RunModel)
    monkeypatch.setattr(guild_missions, "Guild", _GuildModel)

    result = guild_missions._lock_guild_root_for_mission_run(3)

    assert result is locked_guild
    assert calls == ["identity", "guild"]


def test_guild_raid_root_lock_uses_sorted_guild_pair(monkeypatch):
    calls: list[tuple[int, int]] = []

    class _IdentityQuery:
        def values_list(self, *_args, **_kwargs):
            return self

        def first(self):
            return (9, 4)

    class _RunManager:
        def filter(self, **_kwargs):
            return _IdentityQuery()

    class _RunModel:
        objects = _RunManager()

    monkeypatch.setattr(guild_raids, "GuildRaidRun", _RunModel)
    monkeypatch.setattr(
        guild_raids,
        "_lock_guild_pair",
        lambda *, attacker_guild_id, defender_guild_id, **_kwargs: (
            calls.append((attacker_guild_id, defender_guild_id)) or object(),
            object(),
        ),
    )

    result = guild_raids._lock_guild_roots_for_raid_run(3)

    assert result is not None
    assert calls == [(9, 4)]


def test_cancel_coop_context_locks_manor_event_then_entry(monkeypatch):
    calls: list[str] = []
    locked_event = object()
    locked_entry = object()
    locked_manor = object()

    class _IdentityQuery:
        def order_by(self, *_args):
            return self

        def values_list(self, *_args):
            return self

        def first(self):
            return (5, 7)

    class _LockedEntryQuery:
        def filter(self, **_kwargs):
            return self

        def first(self):
            return locked_entry

    class _EntryManager:
        def filter(self, **_kwargs):
            return _IdentityQuery()

        def select_for_update(self):
            calls.append("entry")
            return _LockedEntryQuery()

    class _EntryStatus:
        REGISTERED = "registered"

    class _EntryModel:
        objects = _EntryManager()
        Status = _EntryStatus

    class _LockedEventQuery:
        def get(self, **_kwargs):
            calls.append("event")
            return locked_event

    class _EventManager:
        def select_for_update(self):
            return _LockedEventQuery()

    class _EventStatus:
        RECRUITING = "recruiting"
        PREPARING = "preparing"
        RUNNING = "running"

    class _EventModel:
        objects = _EventManager()
        Status = _EventStatus

    class _LockedManorQuery:
        def get(self, **_kwargs):
            calls.append("manor")
            return locked_manor

    monkeypatch.setattr(coop_core, "ArenaCoopEntry", _EntryModel)
    monkeypatch.setattr(coop_core, "ArenaCoopEvent", _EventModel)
    monkeypatch.setattr(Manor.objects, "select_for_update", lambda: _LockedManorQuery())

    result = coop_core._lock_coop_cancellation_context(3)

    assert result == (locked_manor, locked_entry, locked_event)
    assert calls == ["manor", "event", "entry"]


def test_prepare_raid_battle_locks_manors_before_run(monkeypatch):
    calls: list[str] = []
    run = object()

    class _IdentityQuery:
        def values_list(self, *_args, **_kwargs):
            return self

        def first(self):
            return (12, 5)

    class _LockedRunQuery:
        def select_related(self, *_args):
            return self

        def prefetch_related(self, *_args):
            return self

        def filter(self, **_kwargs):
            return self

        def first(self):
            return run

    class _RunManager:
        def filter(self, **_kwargs):
            return _IdentityQuery()

        def select_for_update(self):
            calls.append("run")
            return _LockedRunQuery()

    class _RaidRunModel:
        objects = _RunManager()

    class _LockedManorQuery:
        def filter(self, **kwargs):
            assert kwargs == {"pk__in": [5, 12]}
            return self

        def order_by(self, field):
            assert field == "pk"
            calls.append("manors")
            return []

    monkeypatch.setattr(raid_battle, "RaidRun", _RaidRunModel)
    monkeypatch.setattr(Manor.objects, "select_for_update", lambda: _LockedManorQuery())

    result = raid_battle._load_locked_raid_run(3)

    assert result is run
    assert calls == ["manors", "run"]
