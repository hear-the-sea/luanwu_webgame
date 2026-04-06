from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from django_redis.exceptions import ConnectionInterrupted

from core.middleware.online_presence import OnlinePresenceMiddleware
from gameplay.context_processors import notifications
from tests.context_processors.support import FakeRedis

pytestmark = pytest.mark.django_db


def test_online_presence_http_touch_debounce_stays_responsive():
    from gameplay.services import online_presence_backend

    assert online_presence_backend.ONLINE_USER_TOUCH_CACHE_TIMEOUT <= 15


def test_notifications_authenticated_http_touch_refreshes_online_count(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_touch_user", password="pass")
    request = RequestFactory().get("/")
    request.user = user

    fake_redis = FakeRedis()
    monkeypatch.setattr("gameplay.selectors.stats.get_redis_connection", lambda *_args, **_kwargs: fake_redis)
    monkeypatch.setattr(
        "gameplay.services.online_presence.get_redis_connection_if_supported",
        lambda *_args, **_kwargs: fake_redis,
    )
    monkeypatch.setattr("gameplay.context_processors._populate_authenticated_context", lambda *_args, **_kwargs: None)

    cache.set("stats:online_users_count", 0, timeout=60)
    OnlinePresenceMiddleware(lambda _request: None)(request)

    context = notifications(request)

    assert context["online_user_count"] == 1


def test_online_presence_middleware_tolerates_connection_interrupted(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_touch_fail_user", password="pass")
    request = RequestFactory().get("/")
    request.user = user

    monkeypatch.setattr("gameplay.services.online_presence.cache.add", lambda *_args, **_kwargs: True)
    deleted_keys: list[str] = []
    monkeypatch.setattr("gameplay.services.online_presence.cache.delete", lambda key: deleted_keys.append(key))

    class BrokenRedis:
        def zadd(self, *_args, **_kwargs):
            raise ConnectionInterrupted("redis down")

    monkeypatch.setattr("gameplay.services.online_presence.get_redis_connection_if_supported", lambda: BrokenRedis())

    OnlinePresenceMiddleware(lambda _request: None)(request)

    assert any(key.startswith("stats:online_users:touch:") for key in deleted_keys)


def test_online_presence_middleware_runtime_marker_redis_error_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_touch_runtime_user", password="pass")
    request = RequestFactory().get("/")
    request.user = user

    monkeypatch.setattr("gameplay.services.online_presence.cache.add", lambda *_args, **_kwargs: True)
    deleted_keys: list[str] = []
    monkeypatch.setattr("gameplay.services.online_presence.cache.delete", lambda key: deleted_keys.append(key))

    class BrokenRedis:
        def zadd(self, *_args, **_kwargs):
            raise RuntimeError("redis down")

    monkeypatch.setattr("gameplay.services.online_presence.get_redis_connection_if_supported", lambda: BrokenRedis())

    with pytest.raises(RuntimeError, match="redis down"):
        OnlinePresenceMiddleware(lambda _request: None)(request)

    assert any(key.startswith("stats:online_users:touch:") for key in deleted_keys)


def test_online_presence_middleware_cache_delete_programming_error_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_touch_delete_programming_user", password="pass")
    request = RequestFactory().get("/")
    request.user = user

    monkeypatch.setattr("gameplay.services.online_presence.cache.add", lambda *_args, **_kwargs: True)

    class BrokenRedis:
        def zadd(self, *_args, **_kwargs):
            raise ConnectionInterrupted("redis down")

    monkeypatch.setattr("gameplay.services.online_presence.get_redis_connection_if_supported", lambda: BrokenRedis())
    monkeypatch.setattr(
        "gameplay.services.online_presence.cache.delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken presence cache delete contract")),
    )

    with pytest.raises(AssertionError, match="broken presence cache delete contract"):
        OnlinePresenceMiddleware(lambda _request: None)(request)
