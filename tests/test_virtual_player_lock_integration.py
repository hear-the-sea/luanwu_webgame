from __future__ import annotations

import logging
import os
import threading
import time
import uuid

import pytest
from django.core.cache import cache
from django_redis import get_redis_connection

from core.utils import cache_lock
from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock, renew_best_effort_lock

pytestmark = [pytest.mark.integration]


def _redis_lock_details(lock_key: str):
    try:
        redis = get_redis_connection("default")
    except NotImplementedError:
        pytest.skip("virtual player lock integration requires django-redis")
    redis_key = cache.make_key(lock_key) if hasattr(cache, "make_key") else lock_key
    return redis, redis_key


def _fail_non_atomic_release(*_args, **_kwargs):
    raise AssertionError("Redis integration lock release must use the atomic Lua path")


def test_virtual_player_lock_lua_release_deletes_serialized_owner_and_allows_reacquire(monkeypatch):
    if os.environ.get("DJANGO_TEST_USE_ENV_SERVICES", "0") != "1":
        pytest.skip("virtual player lock integration requires DJANGO_TEST_USE_ENV_SERVICES=1")

    lock_key = f"integration:virtual-player-roll:{uuid.uuid4().hex}"
    logger = logging.getLogger(__name__)
    redis, redis_key = _redis_lock_details(lock_key)
    monkeypatch.setattr(cache_lock, "_release_cache_lock_non_atomic_if_owner", _fail_non_atomic_release)

    acquired, from_cache, worker_a_token = acquire_best_effort_lock(
        lock_key,
        timeout_seconds=30,
        logger=logger,
        log_context="virtual player integration lock",
        allow_local_fallback=False,
    )
    assert acquired is True
    assert from_cache is True
    assert worker_a_token

    try:
        release_best_effort_lock(
            lock_key,
            from_cache=from_cache,
            lock_token=worker_a_token,
            logger=logger,
            log_context="virtual player integration lock",
        )

        assert redis.get(redis_key) is None
        reacquired, reacquired_from_cache, worker_b_token = acquire_best_effort_lock(
            lock_key,
            timeout_seconds=30,
            logger=logger,
            log_context="virtual player integration lock",
            allow_local_fallback=False,
        )
        assert reacquired is True
        assert reacquired_from_cache is True
        assert worker_b_token
        assert worker_b_token != worker_a_token
    finally:
        cache.delete(lock_key)


def test_virtual_player_lock_lua_release_preserves_reacquired_owner_token(monkeypatch):
    if os.environ.get("DJANGO_TEST_USE_ENV_SERVICES", "0") != "1":
        pytest.skip("virtual player lock integration requires DJANGO_TEST_USE_ENV_SERVICES=1")

    lock_key = f"integration:virtual-player-roll:{uuid.uuid4().hex}"
    replacement_token = f"worker-b:{uuid.uuid4().hex}"
    logger = logging.getLogger(__name__)
    redis, redis_key = _redis_lock_details(lock_key)
    monkeypatch.setattr(cache_lock, "_release_cache_lock_non_atomic_if_owner", _fail_non_atomic_release)

    acquired, from_cache, worker_a_token = acquire_best_effort_lock(
        lock_key,
        timeout_seconds=30,
        logger=logger,
        log_context="virtual player integration lock",
        allow_local_fallback=False,
    )
    assert acquired is True
    assert from_cache is True
    assert worker_a_token

    try:
        cache.set(lock_key, replacement_token, timeout=30)
        replacement_encoded = redis.get(redis_key)
        assert replacement_encoded is not None

        release_best_effort_lock(
            lock_key,
            from_cache=from_cache,
            lock_token=worker_a_token,
            logger=logger,
            log_context="virtual player integration lock",
        )

        assert redis.get(redis_key) == replacement_encoded
        assert cache.get(lock_key) == replacement_token
    finally:
        cache.delete(lock_key)


def test_virtual_player_lock_owner_renew_extends_real_redis_ttl():
    if os.environ.get("DJANGO_TEST_USE_ENV_SERVICES", "0") != "1":
        pytest.skip("virtual player lock integration requires DJANGO_TEST_USE_ENV_SERVICES=1")

    lock_key = f"integration:virtual-player-renew:{uuid.uuid4().hex}"
    logger = logging.getLogger(__name__)
    redis, redis_key = _redis_lock_details(lock_key)
    acquired, from_cache, worker_a_token = acquire_best_effort_lock(
        lock_key,
        timeout_seconds=2,
        logger=logger,
        log_context="virtual player integration renew",
        allow_local_fallback=False,
    )
    assert acquired is True
    assert from_cache is True
    assert worker_a_token

    try:
        time.sleep(0.4)
        ttl_before_renew = redis.pttl(redis_key)
        assert 0 < ttl_before_renew < 2_000

        assert (
            renew_best_effort_lock(
                lock_key,
                from_cache=from_cache,
                lock_token=worker_a_token,
                timeout_seconds=5,
                logger=logger,
                log_context="virtual player integration renew",
            )
            is True
        )

        ttl_after_renew = redis.pttl(redis_key)
        assert ttl_after_renew > ttl_before_renew
        assert 4_000 < ttl_after_renew <= 5_000
    finally:
        cache.delete(lock_key)


def test_virtual_player_lock_old_owner_renew_preserves_replacement_owner_ttl():
    if os.environ.get("DJANGO_TEST_USE_ENV_SERVICES", "0") != "1":
        pytest.skip("virtual player lock integration requires DJANGO_TEST_USE_ENV_SERVICES=1")

    lock_key = f"integration:virtual-player-renew-mismatch:{uuid.uuid4().hex}"
    replacement_token = f"worker-b:{uuid.uuid4().hex}"
    logger = logging.getLogger(__name__)
    redis, redis_key = _redis_lock_details(lock_key)
    acquired, from_cache, worker_a_token = acquire_best_effort_lock(
        lock_key,
        timeout_seconds=10,
        logger=logger,
        log_context="virtual player integration renew mismatch",
        allow_local_fallback=False,
    )
    assert acquired is True
    assert from_cache is True
    assert worker_a_token

    try:
        cache.set(lock_key, replacement_token, timeout=10)
        replacement_encoded = redis.get(redis_key)
        ttl_before_renew = redis.pttl(redis_key)
        assert replacement_encoded is not None

        assert (
            renew_best_effort_lock(
                lock_key,
                from_cache=from_cache,
                lock_token=worker_a_token,
                timeout_seconds=60,
                logger=logger,
                log_context="virtual player integration renew mismatch",
            )
            is False
        )

        ttl_after_renew = redis.pttl(redis_key)
        assert redis.get(redis_key) == replacement_encoded
        assert 0 <= ttl_before_renew - ttl_after_renew < 2_000
        assert ttl_after_renew < 20_000
    finally:
        cache.delete(lock_key)


def test_virtual_player_slow_roll_heartbeat_keeps_competing_worker_out(monkeypatch):
    if os.environ.get("DJANGO_TEST_USE_ENV_SERVICES", "0") != "1":
        pytest.skip("virtual player lock integration requires DJANGO_TEST_USE_ENV_SERVICES=1")

    from gameplay.services import virtual_players

    lock_key = f"integration:virtual-player-slow-roll:{uuid.uuid4().hex}"
    logger = logging.getLogger(__name__)
    roll_started = threading.Event()
    allow_roll_to_finish = threading.Event()
    results: list[int] = []
    errors: list[BaseException] = []

    def _slow_roll(*, limit=None, now=None, ownership_guard=None):
        del limit, now
        assert ownership_guard is not None
        roll_started.set()
        assert allow_roll_to_finish.wait(timeout=10)
        ownership_guard()
        return 7

    def _run_roll():
        try:
            results.append(virtual_players.roll_virtual_player_population(limit=7))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(virtual_players, "ROLL_LOCK_KEY", lock_key)
    monkeypatch.setattr(virtual_players, "ROLL_LOCK_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(virtual_players, "_roll_virtual_player_population_unlocked", _slow_roll)

    roll_thread = threading.Thread(target=_run_roll, daemon=True)
    roll_thread.start()
    try:
        assert roll_started.wait(timeout=5)
        time.sleep(2.5)

        competitor_acquired, _competitor_from_cache, competitor_token = acquire_best_effort_lock(
            lock_key,
            timeout_seconds=2,
            logger=logger,
            log_context="virtual player competing integration roll",
            allow_local_fallback=False,
        )

        assert competitor_acquired is False
        assert competitor_token is None
    finally:
        allow_roll_to_finish.set()
        roll_thread.join(timeout=10)
        cache.delete(lock_key)

    assert not roll_thread.is_alive()
    assert errors == []
    assert results == [7]
