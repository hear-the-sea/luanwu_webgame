from __future__ import annotations

import pytest
from django.db.utils import DatabaseError
from kombu.exceptions import OperationalError

from tests.guilds_tasks.support import dispatch_immediately


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
    monkeypatch.setattr("common.utils.celery.safe_apply_async", dispatch_immediately)

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
    monkeypatch.setattr("common.utils.celery.safe_apply_async", dispatch_immediately)

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


@pytest.mark.django_db
def test_process_single_guild_production_runs_mysticism_once_per_day(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.tasks import process_single_guild_production

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "guilds.tasks.produce_soul_containers",
        lambda guild, level: calls.append((guild.id, int(level))),
    )

    founder = django_user_model.objects.create_user(username="g_mysticism_founder", password="pass")
    guild = Guild.objects.create(name="G-mysticism", founder=founder, is_active=True)
    tech = GuildTechnology.objects.create(
        guild=guild,
        tech_key="mysticism",
        category="production",
        level=3,
        max_level=3,
    )

    first_result = process_single_guild_production.run(guild.id)
    second_result = process_single_guild_production.run(guild.id)

    assert first_result == f"processed guild {guild.id}: soul_container"
    assert second_result == f"processed guild {guild.id}: "
    assert calls == [(guild.id, 3)]
    tech.refresh_from_db()
    assert tech.last_production_at is not None


def test_process_single_guild_production_missing_guild_id_bubbles_up():
    from guilds.tasks import process_single_guild_production

    with pytest.raises(AssertionError, match="guild_id is required when failed_ids is empty"):
        process_single_guild_production.run()


def test_persist_failed_guild_ids_cache_infrastructure_error_is_best_effort(monkeypatch):
    from guilds.tasks import _persist_failed_guild_ids

    monkeypatch.setattr(
        "guilds.tasks.merge_int_id_set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("cache unavailable")),
    )

    _persist_failed_guild_ids([1, 2])


def test_persist_failed_guild_ids_cache_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import _persist_failed_guild_ids

    monkeypatch.setattr(
        "guilds.tasks.merge_int_id_set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken failed-id cache contract")),
    )

    with pytest.raises(AssertionError, match="broken failed-id cache contract"):
        _persist_failed_guild_ids([1, 2])


def test_persist_failed_guild_ids_uses_atomic_merge(monkeypatch):
    from guilds.tasks import FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY, _persist_failed_guild_ids

    calls = []

    monkeypatch.setattr(
        "guilds.tasks.merge_int_id_set",
        lambda key, ids, *, ttl: calls.append((key, ids, ttl)) or [1, 2],
    )

    _persist_failed_guild_ids([1, 2])

    assert calls == [(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY, [1, 2], None)]


def test_failed_guild_ids_remain_visible_after_cache_recovers_without_new_write(monkeypatch):
    from guilds.tasks import _persist_failed_guild_ids, get_failed_guild_ids

    healthy = False
    values = {}

    monkeypatch.setattr("core.utils.atomic_cache._LOCAL_ID_SET_FALLBACK", {})

    class FakeCache:
        def add(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache unavailable")
            if key.endswith(":lock"):
                return True
            values.setdefault(key, value)
            return True

        def get(self, key, default=None):
            if not healthy:
                raise ConnectionError("cache unavailable")
            return values.get(key, default)

        def set(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache unavailable")
            values[key] = value

        def delete(self, key):
            values.pop(key, None)

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())
    monkeypatch.setattr("guilds.tasks.cache", FakeCache())

    _persist_failed_guild_ids([7, 8])

    healthy = True

    assert get_failed_guild_ids() == [7, 8]


def test_get_failed_guild_ids_cache_infrastructure_error_returns_empty(monkeypatch):
    from guilds.tasks import get_failed_guild_ids

    monkeypatch.setattr("core.utils.atomic_cache._LOCAL_ID_SET_FALLBACK", {})
    monkeypatch.setattr(
        "guilds.tasks.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("cache unavailable")),
    )

    assert get_failed_guild_ids() == []


def test_get_failed_guild_ids_cache_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import get_failed_guild_ids

    monkeypatch.setattr("core.utils.atomic_cache._LOCAL_ID_SET_FALLBACK", {})
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


def test_clear_failed_guild_ids_removes_local_fallback_without_revival(monkeypatch):
    from guilds.tasks import _clear_failed_guild_ids, _persist_failed_guild_ids, get_failed_guild_ids

    healthy = False
    values = {}

    monkeypatch.setattr("core.utils.atomic_cache._LOCAL_ID_SET_FALLBACK", {})

    class FakeCache:
        def add(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache unavailable")
            if key.endswith(":lock"):
                return True
            values.setdefault(key, value)
            return True

        def get(self, key, default=None):
            if not healthy:
                raise ConnectionError("cache unavailable")
            return values.get(key, default)

        def set(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache unavailable")
            values[key] = value

        def delete(self, key):
            values.pop(key, None)

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())
    monkeypatch.setattr("guilds.tasks.cache", FakeCache())

    _persist_failed_guild_ids([7, 8])
    healthy = True

    _clear_failed_guild_ids()

    assert get_failed_guild_ids() == []


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
