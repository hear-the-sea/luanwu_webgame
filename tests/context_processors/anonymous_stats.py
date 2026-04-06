from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django_redis.exceptions import ConnectionInterrupted

from gameplay.context_processors import notifications

pytestmark = pytest.mark.django_db


def test_notifications_global_stats_cache_timeouts_stay_responsive():
    from gameplay.selectors import stats

    assert stats.TOTAL_USERS_CACHE_TIMEOUT <= 30
    assert stats.ONLINE_USERS_CACHE_TIMEOUT <= 5


def test_notifications_anonymous_tolerates_explicit_cache_and_redis_infrastructure_failures(monkeypatch):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    monkeypatch.setattr(
        "gameplay.selectors.stats.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache read failed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats.cache.set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache write failed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats.get_redis_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("redis down")),
    )

    context = notifications(request)
    assert context["message_unread_count"] == 0
    assert context["online_user_count"] >= 0
    assert context["total_user_count"] >= 0


def test_notifications_anonymous_runtime_marker_cache_error_bubbles_up(monkeypatch):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    monkeypatch.setattr(
        "gameplay.selectors.stats.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache read failed")),
    )

    with pytest.raises(RuntimeError, match="cache read failed"):
        notifications(request)


def test_notifications_anonymous_runtime_marker_cache_set_error_bubbles_up(monkeypatch):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    def fake_cache_get(key, default=None):
        if key in {"stats:total_users_count", "stats:online_users_count"}:
            return None
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_cache_get)
    monkeypatch.setattr(
        "gameplay.selectors.stats.cache.set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache write failed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats.User.objects.filter",
        lambda **_kwargs: type("FakeQuerySet", (), {"count": lambda self: 7})(),
    )
    monkeypatch.setattr("gameplay.selectors.stats._load_online_user_count_from_redis", lambda: 3)

    with pytest.raises(RuntimeError, match="cache write failed"):
        notifications(request)


def test_notifications_anonymous_handles_corrupted_cached_online_value(monkeypatch):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    def fake_cache_get(key, default=None):
        if key == "stats:total_users_count":
            return 7
        if key == "stats:online_users_count":
            return "invalid-int"
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_cache_get)

    context = notifications(request)
    assert context["total_user_count"] == 7
    assert context["online_user_count"] == 0


def test_notifications_ajax_requests_skip_global_stats_queries(monkeypatch):
    request = RequestFactory().get("/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    request.user = AnonymousUser()

    monkeypatch.setattr(
        "gameplay.selectors.stats.load_total_user_count",
        lambda: (_ for _ in ()).throw(AssertionError("global stats should be skipped for ajax")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats.load_online_user_count",
        lambda: (_ for _ in ()).throw(AssertionError("online stats should be skipped for ajax")),
    )

    context = notifications(request)

    assert context["total_user_count"] == 0
    assert context["online_user_count"] == 0


def test_notifications_total_user_count_uses_local_fallback_when_cache_reads_fail(monkeypatch):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    gameplay_selectors_stats = __import__("gameplay.selectors.stats", fromlist=["load_total_user_count"])
    gameplay_selectors_stats._LOCAL_STATS_CACHE.clear()

    count_calls = {"count": 0}

    monkeypatch.setattr(
        "gameplay.selectors.stats.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache read failed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats.cache.set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache write failed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats.User.objects.filter",
        lambda **_kwargs: type(
            "FakeQuerySet",
            (),
            {"count": lambda self: count_calls.__setitem__("count", count_calls["count"] + 1) or 11},
        )(),
    )
    monkeypatch.setattr("gameplay.selectors.stats.load_online_user_count", lambda: 0)

    first = notifications(request)
    second = notifications(request)

    assert first["total_user_count"] == 11
    assert second["total_user_count"] == 11
    assert count_calls["count"] == 1


def test_notifications_online_user_count_uses_local_fallback_when_cache_and_redis_fail(monkeypatch):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    gameplay_selectors_stats = __import__("gameplay.selectors.stats", fromlist=["load_online_user_count"])
    gameplay_selectors_stats._LOCAL_STATS_CACHE.clear()

    count_calls = {"count": 0}

    monkeypatch.setattr(
        "gameplay.selectors.stats.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache read failed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats.cache.set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache write failed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats.get_redis_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("redis down")),
    )
    monkeypatch.setattr("gameplay.selectors.stats.load_total_user_count", lambda: 0)
    monkeypatch.setattr(
        "gameplay.selectors.stats._load_online_user_count_from_db",
        lambda: count_calls.__setitem__("count", count_calls["count"] + 1) or 3,
    )

    first = notifications(request)
    second = notifications(request)

    assert first["online_user_count"] == 3
    assert second["online_user_count"] == 3
    assert count_calls["count"] == 1


def test_notifications_online_user_count_runtime_marker_redis_error_bubbles_up(monkeypatch):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    monkeypatch.setattr("gameplay.selectors.stats.load_total_user_count", lambda: 0)
    monkeypatch.setattr("gameplay.selectors.stats.cache.get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "gameplay.selectors.stats.get_redis_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("redis down")),
    )

    with pytest.raises(RuntimeError, match="redis down"):
        notifications(request)
