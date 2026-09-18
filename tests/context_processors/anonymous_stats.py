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
    assert context["sidebar_action_points_label"] == "0/1000"
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


def test_notifications_partial_navigation_requests_keep_loading_global_stats(monkeypatch):
    request = RequestFactory().get(
        "/",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_X_PARTIAL_NAVIGATION="1",
        HTTP_ACCEPT="text/html",
    )
    request.user = AnonymousUser()

    monkeypatch.setattr("gameplay.selectors.stats.load_total_user_count", lambda: 23)
    monkeypatch.setattr("gameplay.selectors.stats.load_online_user_count", lambda: 7)

    context = notifications(request)

    assert context["total_user_count"] == 23
    assert context["online_user_count"] == 7


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


def test_notifications_cache_failure_circuit_skips_repeated_backend_calls(monkeypatch):
    from gameplay.selectors import stats

    stats._LOCAL_STATS_CACHE.clear()
    first_request = RequestFactory().get("/")
    first_request.user = AnonymousUser()
    second_request = RequestFactory().get("/")
    second_request.user = AnonymousUser()
    cache_get_calls = 0
    db_count_calls = 0

    def broken_cache_get(*_args, **_kwargs):
        nonlocal cache_get_calls
        cache_get_calls += 1
        raise ConnectionInterrupted("cache read failed")

    def fake_count():
        nonlocal db_count_calls
        db_count_calls += 1
        return 11

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", broken_cache_get)
    monkeypatch.setattr(
        "gameplay.selectors.stats.User.objects.filter",
        lambda **_kwargs: type("FakeQuerySet", (), {"count": lambda self: fake_count()})(),
    )
    monkeypatch.setattr(
        "gameplay.selectors.stats._load_online_user_count_from_redis",
        lambda: (_ for _ in ()).throw(AssertionError("Redis must be skipped after a cache failure")),
    )

    first = notifications(first_request)
    second = notifications(second_request)

    assert first["total_user_count"] == 11
    assert first["online_user_count"] == 11
    assert second == first
    assert cache_get_calls == 1
    assert db_count_calls == 2


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
