from __future__ import annotations

import importlib

import pytest
from django_redis.exceptions import ConnectionInterrupted

from gameplay.services.utils.cache import cached, get_or_set

cache_utils = importlib.import_module("gameplay.services.utils.cache")


def test_get_or_set_tolerates_cache_get_failure(monkeypatch):
    calls = {"compute": 0}

    monkeypatch.setattr(
        cache_utils.cache,
        "get",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )
    monkeypatch.setattr(cache_utils.cache, "set", lambda *_a, **_k: None)

    def _compute():
        calls["compute"] += 1
        return {"ok": True}

    result = get_or_set("cache:test:get_failure", _compute)

    assert result == {"ok": True}
    assert calls["compute"] == 1


def test_get_or_set_tolerates_cache_set_failure(monkeypatch):
    calls = {"compute": 0}

    monkeypatch.setattr(cache_utils.cache, "get", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cache_utils.cache,
        "set",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )

    def _compute():
        calls["compute"] += 1
        return 7

    result = get_or_set("cache:test:set_failure", _compute)

    assert result == 7
    assert calls["compute"] == 1


def test_cached_decorator_tolerates_cache_backend_failure(monkeypatch):
    calls = {"compute": 0}

    monkeypatch.setattr(
        cache_utils.cache,
        "get",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )
    monkeypatch.setattr(
        cache_utils.cache,
        "set",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )

    @cached(lambda value: f"cache:test:{value}")
    def _compute(value: int) -> int:
        calls["compute"] += 1
        return value + 1

    assert _compute(3) == 4
    assert _compute(3) == 4
    assert calls["compute"] == 2


def test_invalidate_recruitment_hall_cache_tolerates_delete_many_failure(monkeypatch):
    monkeypatch.setattr(
        cache_utils.cache,
        "delete_many",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )

    cache_utils.invalidate_recruitment_hall_cache(1)


def test_invalidate_recruitment_hall_cache_runtime_marker_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        cache_utils.cache,
        "delete_many",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache delete failed")),
    )

    with pytest.raises(RuntimeError, match="cache delete failed"):
        cache_utils.invalidate_recruitment_hall_cache(1)


def test_invalidate_market_stats_cache_deletes_expected_key(monkeypatch):
    deleted: list[str] = []
    monkeypatch.setattr(cache_utils.cache, "delete", deleted.append)

    cache_utils.invalidate_market_stats_cache()

    assert deleted == [cache_utils.CacheKeys.market_stats()]


def test_get_or_set_runtime_marker_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        cache_utils.cache,
        "get",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        get_or_set("cache:test:get_runtime_marker", lambda: {"ok": True})


def test_cached_decorator_cache_set_runtime_marker_bubbles_up(monkeypatch):
    monkeypatch.setattr(cache_utils.cache, "get", lambda _key, default=None: default)
    monkeypatch.setattr(
        cache_utils.cache,
        "set",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache set failed")),
    )

    @cached(lambda value: f"cache:test:runtime:{value}")
    def _compute(value: int) -> int:
        return value + 1

    with pytest.raises(RuntimeError, match="cache set failed"):
        _compute(3)
