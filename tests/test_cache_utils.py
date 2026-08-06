from __future__ import annotations

import importlib
import threading

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


def test_get_or_set_rechecks_cache_after_acquiring_stampede_lock(monkeypatch):
    values = iter([None, {"ok": True}])
    calls = {"compute": 0}
    released = []

    monkeypatch.setattr(cache_utils.cache, "get", lambda *_args, **_kwargs: next(values))
    monkeypatch.setattr(
        cache_utils,
        "acquire_best_effort_lock",
        lambda *_args, **_kwargs: (True, True, "lock-token"),
    )
    monkeypatch.setattr(
        cache_utils,
        "release_best_effort_lock",
        lambda key, **kwargs: released.append((key, kwargs)),
    )

    def compute():
        calls["compute"] += 1
        return {"unexpected": True}

    assert cache_utils.get_or_set("cache:test:stampede-recheck", compute) == {"ok": True}
    assert calls["compute"] == 0
    assert released and released[0][1]["lock_token"] == "lock-token"


def test_get_or_set_prevents_concurrent_cache_miss_stampede():
    key = "cache:test:stampede-concurrency"
    cache_utils.cache.delete(key)
    started = threading.Event()
    release_compute = threading.Event()
    calls = {"compute": 0}
    results = []
    errors = []

    def compute():
        calls["compute"] += 1
        started.set()
        assert release_compute.wait(timeout=2)
        return {"value": 42}

    def worker():
        try:
            results.append(cache_utils.get_or_set(key, compute, lock_wait_timeout=1.0))
        except Exception as exc:  # pragma: no cover - assertion below reports the failure
            errors.append(exc)

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker)
    first.start()
    assert started.wait(timeout=2)
    second.start()
    release_compute.set()
    first.join(timeout=2)
    second.join(timeout=2)
    cache_utils.cache.delete(key)

    assert not errors
    assert not first.is_alive() and not second.is_alive()
    assert calls["compute"] == 1
    assert results == [{"value": 42}, {"value": 42}]


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
