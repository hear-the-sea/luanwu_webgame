from __future__ import annotations

import logging
import threading

import pytest
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError

import core.utils.cache_lock as cache_lock


def test_build_action_lock_key_uses_namespace_action_owner_and_scope():
    assert cache_lock.build_action_lock_key("map:view_lock", "start_raid", 12, "88") == "map:view_lock:start_raid:12:88"


def test_cache_lock_falls_back_to_local_lock_when_cache_unavailable(monkeypatch):
    class _BrokenCache:
        def add(self, *_args, **_kwargs):
            raise ConnectionInterrupted("cache down")

        def delete(self, *_args, **_kwargs):
            raise ConnectionInterrupted("cache down")

    cache_lock._LOCAL_LOCKS.clear()
    monkeypatch.setattr(cache_lock, "cache", _BrokenCache())

    acquired_1, from_cache_1, token_1 = cache_lock.acquire_best_effort_lock(
        "lock:test:1",
        timeout_seconds=5,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    acquired_2, from_cache_2, token_2 = cache_lock.acquire_best_effort_lock(
        "lock:test:1",
        timeout_seconds=5,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )

    assert acquired_1 is True
    assert from_cache_1 is False
    assert bool(token_1)
    assert acquired_2 is False
    assert from_cache_2 is False
    assert token_2 is None

    cache_lock.release_best_effort_lock(
        "lock:test:1",
        from_cache=False,
        lock_token=token_1,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    acquired_3, from_cache_3, token_3 = cache_lock.acquire_best_effort_lock(
        "lock:test:1",
        timeout_seconds=5,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    assert acquired_3 is True
    assert from_cache_3 is False
    assert bool(token_3)

    cache_lock._LOCAL_LOCKS.clear()


def test_action_lock_wraps_local_fallback_key_and_releases_it(monkeypatch):
    class _BrokenCache:
        def add(self, *_args, **_kwargs):
            raise ConnectionInterrupted("cache down")

        def delete(self, *_args, **_kwargs):
            raise ConnectionInterrupted("cache down")

    logger = logging.getLogger(__name__)
    cache_lock._LOCAL_LOCKS.clear()
    monkeypatch.setattr(cache_lock, "cache", _BrokenCache())

    acquired, lock_key, lock_token = cache_lock.acquire_action_lock(
        "recruit:view_lock",
        "draw",
        7,
        "pool-a",
        timeout_seconds=5,
        logger=logger,
        log_context="test action lock",
    )

    assert acquired is True
    assert lock_key == "local:recruit:view_lock:draw:7:pool-a"
    assert bool(lock_token)

    cache_lock.release_action_lock(
        lock_key,
        lock_token=lock_token,
        logger=logger,
        log_context="test action lock",
    )

    reacquired, second_key, second_token = cache_lock.acquire_action_lock(
        "recruit:view_lock",
        "draw",
        7,
        "pool-a",
        timeout_seconds=5,
        logger=logger,
        log_context="test action lock",
    )

    assert reacquired is True
    assert second_key == lock_key
    assert bool(second_token)
    cache_lock._LOCAL_LOCKS.clear()


def test_action_lock_uses_cache_key_when_cache_is_available(monkeypatch):
    class _FakeCache:
        def __init__(self):
            self._keys: dict[str, str] = {}

        def add(self, key, value, *_args, **_kwargs):
            if key in self._keys:
                return False
            self._keys[key] = value
            return True

        def get(self, key, default=None):
            return self._keys.get(key, default)

        def make_key(self, key):
            return key

        def delete(self, key):
            self._keys.pop(key, None)
            return True

    logger = logging.getLogger(__name__)
    monkeypatch.setattr(cache_lock, "cache", _FakeCache())

    acquired, lock_key, lock_token = cache_lock.acquire_action_lock(
        "jail:view_lock",
        "release_api",
        3,
        "45",
        timeout_seconds=5,
        logger=logger,
        log_context="test action lock",
        allow_local_fallback=False,
    )

    assert acquired is True
    assert lock_key == "jail:view_lock:release_api:3:45"
    assert bool(lock_token)


def test_cache_lock_can_fail_closed_when_local_fallback_disabled(monkeypatch):
    class _BrokenCache:
        def add(self, *_args, **_kwargs):
            raise ConnectionInterrupted("cache down")

    cache_lock._LOCAL_LOCKS.clear()
    monkeypatch.setattr(cache_lock, "cache", _BrokenCache())

    acquired, from_cache, token = cache_lock.acquire_best_effort_lock(
        "lock:test:fail-closed",
        timeout_seconds=5,
        logger=logging.getLogger(__name__),
        log_context="test lock",
        allow_local_fallback=False,
    )

    assert acquired is False
    assert from_cache is False
    assert token is None
    assert cache_lock._LOCAL_LOCKS == {}


def test_cache_lock_programming_error_bubbles_up(monkeypatch):
    class _BrokenCache:
        def add(self, *_args, **_kwargs):
            raise AssertionError("broken cache contract")

    cache_lock._LOCAL_LOCKS.clear()
    monkeypatch.setattr(cache_lock, "cache", _BrokenCache())

    try:
        try:
            cache_lock.acquire_best_effort_lock(
                "lock:test:programming",
                timeout_seconds=5,
                logger=logging.getLogger(__name__),
                log_context="test lock",
            )
        except AssertionError as exc:
            assert "broken cache contract" in str(exc)
        else:
            raise AssertionError("expected acquire_best_effort_lock to bubble programming error")
    finally:
        cache_lock._LOCAL_LOCKS.clear()


def test_cache_lock_runtime_marker_error_bubbles_up(monkeypatch):
    class _BrokenCache:
        def add(self, *_args, **_kwargs):
            raise RuntimeError("cache down")

    cache_lock._LOCAL_LOCKS.clear()
    monkeypatch.setattr(cache_lock, "cache", _BrokenCache())

    try:
        try:
            cache_lock.acquire_best_effort_lock(
                "lock:test:runtime-marker",
                timeout_seconds=5,
                logger=logging.getLogger(__name__),
                log_context="test lock",
            )
        except RuntimeError as exc:
            assert "cache down" in str(exc)
        else:
            raise AssertionError("expected acquire_best_effort_lock to bubble runtime marker error")
    finally:
        cache_lock._LOCAL_LOCKS.clear()


def test_cache_lock_uses_cache_when_available(monkeypatch):
    class _FakeCache:
        def __init__(self):
            self._keys: dict[str, str] = {}
            self.deleted: list[str] = []

        def add(self, key, value, *_args, **_kwargs):
            if key in self._keys:
                return False
            self._keys[key] = value
            return True

        def get(self, key, default=None):
            return self._keys.get(key, default)

        def make_key(self, key):
            return key

        def delete(self, key):
            self.deleted.append(key)
            self._keys.pop(key, None)
            return True

    fake_cache = _FakeCache()
    monkeypatch.setattr(cache_lock, "cache", fake_cache)

    acquired_1, from_cache_1, token_1 = cache_lock.acquire_best_effort_lock(
        "lock:test:2",
        timeout_seconds=5,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    acquired_2, from_cache_2, token_2 = cache_lock.acquire_best_effort_lock(
        "lock:test:2",
        timeout_seconds=5,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )

    assert acquired_1 is True
    assert from_cache_1 is True
    assert bool(token_1)
    assert acquired_2 is False
    assert from_cache_2 is True
    assert token_2 is None

    cache_lock.release_best_effort_lock(
        "lock:test:2",
        from_cache=True,
        lock_token=token_1,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    assert fake_cache.deleted == ["lock:test:2"]


def test_cache_lock_release_skips_on_token_mismatch(monkeypatch):
    class _FakeCache:
        def __init__(self):
            self._keys: dict[str, str] = {}
            self.deleted: list[str] = []

        def add(self, key, value, *_args, **_kwargs):
            if key in self._keys:
                return False
            self._keys[key] = value
            return True

        def get(self, key, default=None):
            return self._keys.get(key, default)

        def make_key(self, key):
            return key

        def delete(self, key):
            self.deleted.append(key)
            self._keys.pop(key, None)
            return True

    fake_cache = _FakeCache()
    monkeypatch.setattr(cache_lock, "cache", fake_cache)

    acquired, from_cache, token = cache_lock.acquire_best_effort_lock(
        "lock:test:mismatch",
        timeout_seconds=5,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    assert acquired is True
    assert from_cache is True
    assert bool(token)

    cache_lock.release_best_effort_lock(
        "lock:test:mismatch",
        from_cache=True,
        lock_token="wrong-token",
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    assert fake_cache.deleted == []

    acquired_again, from_cache_again, token_again = cache_lock.acquire_best_effort_lock(
        "lock:test:mismatch",
        timeout_seconds=5,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    assert acquired_again is False
    assert from_cache_again is True
    assert token_again is None

    cache_lock.release_best_effort_lock(
        "lock:test:mismatch",
        from_cache=True,
        lock_token=token,
        logger=logging.getLogger(__name__),
        log_context="test lock",
    )
    assert fake_cache.deleted == ["lock:test:mismatch"]


def test_atomic_cache_lock_release_encodes_token_with_cache_client(monkeypatch):
    encoded_token = b"pickle:owner-token"
    eval_calls: list[tuple[str, int, str, bytes]] = []

    class _FakeClient:
        def encode(self, value):
            assert value == "owner-token"
            return encoded_token

    class _FakeCache:
        client = _FakeClient()

        def make_key(self, key):
            return f"encoded:{key}"

    class _FakeRedis:
        def eval(self, script, key_count, redis_key, lock_token):
            eval_calls.append((script, key_count, redis_key, lock_token))
            return 1

    monkeypatch.setattr(cache_lock, "cache", _FakeCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _FakeRedis())

    released = cache_lock._release_cache_lock_atomic_if_owner(
        "lock:test:serialized-owner",
        lock_token="owner-token",
        logger=logging.getLogger(__name__),
        log_context="test serialized owner release",
    )

    assert released is cache_lock._AtomicCacheLockReleaseResult.RELEASED
    assert len(eval_calls) == 1
    _script, key_count, redis_key, lock_token = eval_calls[0]
    assert key_count == 1
    assert redis_key == "encoded:lock:test:serialized-owner"
    assert lock_token == encoded_token


def test_atomic_cache_lock_release_encodes_token_for_owner_mismatch(monkeypatch):
    encoded_token = b"pickle:owner-token"
    eval_calls: list[tuple[str, int, str, bytes]] = []

    class _FakeClient:
        def encode(self, value):
            assert value == "owner-token"
            return encoded_token

    class _FakeCache:
        client = _FakeClient()

        def make_key(self, key):
            return f"encoded:{key}"

    class _FakeRedis:
        def eval(self, script, key_count, redis_key, lock_token):
            eval_calls.append((script, key_count, redis_key, lock_token))
            return 0

    monkeypatch.setattr(cache_lock, "cache", _FakeCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _FakeRedis())

    released = cache_lock._release_cache_lock_atomic_if_owner(
        "lock:test:serialized-non-owner",
        lock_token="owner-token",
        logger=logging.getLogger(__name__),
        log_context="test serialized non-owner release",
    )

    assert released is cache_lock._AtomicCacheLockReleaseResult.NOT_OWNER
    assert len(eval_calls) == 1
    _script, key_count, redis_key, lock_token = eval_calls[0]
    assert key_count == 1
    assert redis_key == "encoded:lock:test:serialized-non-owner"
    assert lock_token == encoded_token


def test_cache_lock_renew_uses_encoded_owner_token_and_normalized_ttl(monkeypatch):
    encoded_token = b"pickle:owner-token"
    eval_calls: list[tuple[str, int, str, bytes, int]] = []

    class _FakeClient:
        def encode(self, value):
            assert value == "owner-token"
            return encoded_token

    class _FakeCache:
        client = _FakeClient()

        def make_key(self, key):
            return f"encoded:{key}"

    class _FakeRedis:
        def eval(self, script, key_count, redis_key, lock_token, timeout_seconds):
            eval_calls.append((script, key_count, redis_key, lock_token, timeout_seconds))
            return 1

    monkeypatch.setattr(cache_lock, "cache", _FakeCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _FakeRedis())

    renewed = cache_lock.renew_best_effort_lock(
        "lock:test:renew-owner",
        from_cache=True,
        lock_token="owner-token",
        timeout_seconds=0,
        logger=logging.getLogger(__name__),
        log_context="test owner lock renew",
    )

    assert renewed is True
    assert len(eval_calls) == 1
    script, key_count, redis_key, lock_token, timeout_seconds = eval_calls[0]
    assert "EXPIRE" in script
    assert key_count == 1
    assert redis_key == "encoded:lock:test:renew-owner"
    assert lock_token == encoded_token
    assert timeout_seconds == 1


def test_cache_lock_renew_owner_mismatch_does_not_extend_replacement_owner(monkeypatch):
    replacement_token = b"pickle:replacement-owner"
    redis_state = {"token": replacement_token, "ttl": 27}

    class _FakeClient:
        def encode(self, value):
            return f"pickle:{value}".encode()

    class _FakeCache:
        client = _FakeClient()

        def make_key(self, key):
            return key

    class _FakeRedis:
        def eval(self, _script, _key_count, _redis_key, lock_token, timeout_seconds):
            if redis_state["token"] != lock_token:
                return 0
            redis_state["ttl"] = timeout_seconds
            return 1

    monkeypatch.setattr(cache_lock, "cache", _FakeCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _FakeRedis())

    renewed = cache_lock.renew_best_effort_lock(
        "lock:test:renew-mismatch",
        from_cache=True,
        lock_token="expired-owner",
        timeout_seconds=90,
        logger=logging.getLogger(__name__),
        log_context="test replacement owner lock renew",
    )

    assert renewed is False
    assert redis_state == {"token": replacement_token, "ttl": 27}


def test_cache_lock_renew_fails_closed_when_redis_connection_fails(monkeypatch):
    class _NoFallbackCache:
        def get(self, *_args, **_kwargs):
            raise AssertionError("Redis connection failure must not use cache.get fallback")

        def touch(self, *_args, **_kwargs):
            raise AssertionError("Redis connection failure must not use cache.touch fallback")

    def _raise_connection_error(_alias):
        raise ConnectionInterrupted("Redis connection unavailable")

    monkeypatch.setattr(cache_lock, "cache", _NoFallbackCache())
    monkeypatch.setattr("django_redis.get_redis_connection", _raise_connection_error)

    assert (
        cache_lock.renew_best_effort_lock(
            "lock:test:renew-connection-failure",
            from_cache=True,
            lock_token="owner-token",
            timeout_seconds=30,
            logger=logging.getLogger(__name__),
            log_context="test connection failure renew",
        )
        is False
    )


def test_cache_lock_renew_fails_closed_when_redis_eval_fails(monkeypatch):
    class _FakeClient:
        def encode(self, value):
            return value.encode()

    class _NoFallbackCache:
        client = _FakeClient()

        def make_key(self, key):
            return key

        def get(self, *_args, **_kwargs):
            raise AssertionError("Redis eval failure must not use cache.get fallback")

        def touch(self, *_args, **_kwargs):
            raise AssertionError("Redis eval failure must not use cache.touch fallback")

    class _BrokenRedis:
        def eval(self, *_args):
            raise RedisError("Lua renew unavailable")

    monkeypatch.setattr(cache_lock, "cache", _NoFallbackCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _BrokenRedis())

    assert (
        cache_lock.renew_best_effort_lock(
            "lock:test:renew-eval-failure",
            from_cache=True,
            lock_token="owner-token",
            timeout_seconds=30,
            logger=logging.getLogger(__name__),
            log_context="test eval failure renew",
        )
        is False
    )


def test_cache_lock_renew_fails_closed_without_redis_cache_encoder(monkeypatch):
    class _NoEncoderCache:
        def make_key(self, key):
            return key

        def get(self, *_args, **_kwargs):
            raise AssertionError("Missing Redis encoder must not use cache.get fallback")

        def touch(self, *_args, **_kwargs):
            raise AssertionError("Missing Redis encoder must not use cache.touch fallback")

    class _FakeRedis:
        def eval(self, *_args):
            raise AssertionError("atomic renew must not guess the cache serializer")

    monkeypatch.setattr(cache_lock, "cache", _NoEncoderCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _FakeRedis())

    assert (
        cache_lock.renew_best_effort_lock(
            "lock:test:renew-unknown-serializer",
            from_cache=True,
            lock_token="owner-token",
            timeout_seconds=30,
            logger=logging.getLogger(__name__),
            log_context="test unknown serializer renew",
        )
        is False
    )


def test_cache_lock_renew_bubbles_up_programming_error(monkeypatch):
    class _BrokenClient:
        def encode(self, _value):
            raise TypeError("broken cache encoder contract")

    class _FakeCache:
        client = _BrokenClient()

        def make_key(self, key):
            return key

    class _FakeRedis:
        def eval(self, *_args):
            raise AssertionError("Redis eval must not run after encoder failure")

    monkeypatch.setattr(cache_lock, "cache", _FakeCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _FakeRedis())

    with pytest.raises(TypeError, match="broken cache encoder contract"):
        cache_lock.renew_best_effort_lock(
            "lock:test:renew-programming-error",
            from_cache=True,
            lock_token="owner-token",
            timeout_seconds=30,
            logger=logging.getLogger(__name__),
            log_context="test programming error renew",
        )


def test_cache_lock_renew_non_redis_fallback_is_owner_only(monkeypatch):
    class _FakeCache:
        def __init__(self):
            self.values = {"lock:test:renew-non-redis": "owner-token"}
            self.touched: list[tuple[str, int]] = []

        def get(self, key, default=None):
            return self.values.get(key, default)

        def touch(self, key, timeout):
            self.touched.append((key, timeout))
            return key in self.values

    def _raise_unsupported_backend(_alias):
        raise NotImplementedError("This backend does not support raw Redis")

    fake_cache = _FakeCache()
    monkeypatch.setattr(cache_lock, "cache", fake_cache)
    monkeypatch.setattr("django_redis.get_redis_connection", _raise_unsupported_backend)

    assert (
        cache_lock.renew_best_effort_lock(
            "lock:test:renew-non-redis",
            from_cache=True,
            lock_token="other-owner",
            timeout_seconds=60,
            logger=logging.getLogger(__name__),
            log_context="test non-Redis mismatch renew",
        )
        is False
    )
    assert fake_cache.touched == []

    assert (
        cache_lock.renew_best_effort_lock(
            "lock:test:renew-non-redis",
            from_cache=True,
            lock_token="owner-token",
            timeout_seconds=60,
            logger=logging.getLogger(__name__),
            log_context="test non-Redis owner renew",
        )
        is True
    )
    assert fake_cache.touched == [("lock:test:renew-non-redis", 60)]


def test_non_redis_renew_does_not_touch_owner_reacquired_during_compare(monkeypatch):
    get_started = threading.Event()
    replacement_added = threading.Event()
    renew_results: list[bool] = []
    acquire_results: list[tuple[bool, bool, str | None]] = []

    class _FakeCache:
        def __init__(self):
            self.values = {"lock:test:renew-race": "owner-a"}
            self.touched_tokens: list[str] = []

        def get(self, key, default=None):
            captured = self.values.get(key, default)
            get_started.set()
            replacement_added.wait(timeout=0.3)
            return captured

        def add(self, key, value, timeout=None):
            del timeout
            self.values[key] = value
            replacement_added.set()
            return True

        def touch(self, key, timeout):
            del timeout
            self.touched_tokens.append(self.values[key])
            return True

    def _raise_unsupported_backend(_alias):
        raise NotImplementedError("This backend does not support raw Redis")

    fake_cache = _FakeCache()
    monkeypatch.setattr(cache_lock, "cache", fake_cache)
    monkeypatch.setattr("django_redis.get_redis_connection", _raise_unsupported_backend)
    logger = logging.getLogger(__name__)

    renew_thread = threading.Thread(
        target=lambda: renew_results.append(
            cache_lock.renew_best_effort_lock(
                "lock:test:renew-race",
                from_cache=True,
                lock_token="owner-a",
                timeout_seconds=60,
                logger=logger,
                log_context="test non-Redis renew race",
            )
        )
    )
    renew_thread.start()
    assert get_started.wait(timeout=1)

    acquire_thread = threading.Thread(
        target=lambda: acquire_results.append(
            cache_lock.acquire_best_effort_lock(
                "lock:test:renew-race",
                timeout_seconds=10,
                logger=logger,
                log_context="test non-Redis replacement acquire",
            )
        )
    )
    acquire_thread.start()
    renew_thread.join(timeout=2)
    acquire_thread.join(timeout=2)

    assert not renew_thread.is_alive()
    assert not acquire_thread.is_alive()
    assert renew_results == [True]
    assert acquire_results and acquire_results[0][0] is True
    assert fake_cache.touched_tokens == ["owner-a"]
    assert fake_cache.values["lock:test:renew-race"] != "owner-a"


def test_local_cache_lock_renew_is_owner_only(monkeypatch):
    class _BrokenCache:
        def add(self, *_args, **_kwargs):
            raise ConnectionInterrupted("cache down")

    cache_lock._LOCAL_LOCKS.clear()
    monkeypatch.setattr(cache_lock, "cache", _BrokenCache())
    logger = logging.getLogger(__name__)
    acquired, from_cache, lock_token = cache_lock.acquire_best_effort_lock(
        "lock:test:renew-local",
        timeout_seconds=5,
        logger=logger,
        log_context="test local acquire",
        allow_local_fallback=True,
    )
    assert acquired is True
    assert from_cache is False
    assert lock_token
    original_expiry = cache_lock._LOCAL_LOCKS["lock:test:renew-local"][1]

    assert (
        cache_lock.renew_best_effort_lock(
            "lock:test:renew-local",
            from_cache=False,
            lock_token="other-owner",
            timeout_seconds=60,
            logger=logger,
            log_context="test local mismatch renew",
        )
        is False
    )
    assert cache_lock._LOCAL_LOCKS["lock:test:renew-local"] == (lock_token, original_expiry)

    assert (
        cache_lock.renew_best_effort_lock(
            "lock:test:renew-local",
            from_cache=False,
            lock_token=lock_token,
            timeout_seconds=60,
            logger=logger,
            log_context="test local owner renew",
        )
        is True
    )
    assert cache_lock._LOCAL_LOCKS["lock:test:renew-local"][0] == lock_token
    assert cache_lock._LOCAL_LOCKS["lock:test:renew-local"][1] > original_expiry
    cache_lock._LOCAL_LOCKS.clear()


def test_atomic_cache_lock_release_bubbles_up_encoder_programming_error(monkeypatch):
    class _BrokenClient:
        def encode(self, _value):
            raise AssertionError("broken cache encoder contract")

    class _FakeCache:
        client = _BrokenClient()

        def make_key(self, key):
            return key

    class _FakeRedis:
        def eval(self, *_args):
            raise AssertionError("Redis eval must not run after encoder failure")

    monkeypatch.setattr(cache_lock, "cache", _FakeCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _FakeRedis())

    try:
        cache_lock._release_cache_lock_atomic_if_owner(
            "lock:test:encoder-programming-error",
            lock_token="owner-token",
            logger=logging.getLogger(__name__),
            log_context="test encoder programming error",
        )
    except AssertionError as exc:
        assert "broken cache encoder contract" in str(exc)
    else:
        raise AssertionError("expected cache encoder programming error to bubble")


def test_cache_lock_release_does_not_fallback_when_redis_connection_fails(monkeypatch):
    class _NoFallbackCache:
        def get(self, *_args, **_kwargs):
            raise AssertionError("Redis infrastructure failure must not use cache.get fallback")

        def delete(self, *_args, **_kwargs):
            raise AssertionError("Redis infrastructure failure must not use cache.delete fallback")

    def _raise_connection_error(_alias):
        raise ConnectionInterrupted("Redis connection unavailable")

    monkeypatch.setattr(cache_lock, "cache", _NoFallbackCache())
    monkeypatch.setattr("django_redis.get_redis_connection", _raise_connection_error)

    released = cache_lock.release_cache_key_if_owner(
        "lock:test:connection-failure",
        lock_token="owner-token",
        logger=logging.getLogger(__name__),
        log_context="test connection failure release",
    )

    assert released is False


def test_cache_lock_release_does_not_fallback_when_redis_eval_fails(monkeypatch):
    class _FakeClient:
        def encode(self, value):
            return f"encoded:{value}".encode()

    class _NoFallbackCache:
        client = _FakeClient()

        def make_key(self, key):
            return key

        def get(self, *_args, **_kwargs):
            raise AssertionError("Redis eval failure must not use cache.get fallback")

        def delete(self, *_args, **_kwargs):
            raise AssertionError("Redis eval failure must not use cache.delete fallback")

    class _BrokenRedis:
        def eval(self, *_args):
            raise RedisError("Lua release unavailable")

    monkeypatch.setattr(cache_lock, "cache", _NoFallbackCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _BrokenRedis())

    released = cache_lock.release_cache_key_if_owner(
        "lock:test:eval-failure",
        lock_token="owner-token",
        logger=logging.getLogger(__name__),
        log_context="test eval failure release",
    )

    assert released is False


def test_cache_lock_release_does_not_fallback_without_redis_cache_encoder(monkeypatch):
    class _FakeCache:
        def make_key(self, key):
            return key

        def get(self, *_args, **_kwargs):
            raise AssertionError("Missing Redis encoder must not use cache.get fallback")

        def delete(self, *_args, **_kwargs):
            raise AssertionError("Missing Redis encoder must not use cache.delete fallback")

    class _FakeRedis:
        def eval(self, *_args):
            raise AssertionError("atomic release must not guess the cache serializer")

    monkeypatch.setattr(cache_lock, "cache", _FakeCache())
    monkeypatch.setattr("django_redis.get_redis_connection", lambda _alias: _FakeRedis())

    released = cache_lock.release_cache_key_if_owner(
        "lock:test:unknown-serializer",
        lock_token="owner-token",
        logger=logging.getLogger(__name__),
        log_context="test unknown serializer release",
    )

    assert released is False


def test_cache_lock_release_falls_back_for_unsupported_cache_backend(monkeypatch):
    class _FakeCache:
        def __init__(self):
            self.values = {"lock:test:unsupported-backend": "owner-token"}
            self.deleted: list[str] = []

        def get(self, key, default=None):
            return self.values.get(key, default)

        def delete(self, key):
            self.deleted.append(key)
            self.values.pop(key, None)
            return True

    def _raise_unsupported_backend(_alias):
        raise NotImplementedError("This backend does not support raw Redis")

    fake_cache = _FakeCache()
    monkeypatch.setattr(cache_lock, "cache", fake_cache)
    monkeypatch.setattr("django_redis.get_redis_connection", _raise_unsupported_backend)

    released = cache_lock.release_cache_key_if_owner(
        "lock:test:unsupported-backend",
        lock_token="owner-token",
        logger=logging.getLogger(__name__),
        log_context="test unsupported backend release",
    )

    assert released is True
    assert fake_cache.deleted == ["lock:test:unsupported-backend"]


def test_release_cache_key_if_owner_prefers_atomic_path(monkeypatch):
    logger = logging.getLogger(__name__)
    called = {"atomic": 0, "fallback": 0}

    def _atomic(*_args, **_kwargs):
        called["atomic"] += 1
        return cache_lock._AtomicCacheLockReleaseResult.RELEASED

    def _fallback(*_args, **_kwargs):
        called["fallback"] += 1
        return True

    monkeypatch.setattr(cache_lock, "_release_cache_lock_atomic_if_owner", _atomic)
    monkeypatch.setattr(cache_lock, "_release_cache_lock_non_atomic_if_owner", _fallback)

    released = cache_lock.release_cache_key_if_owner(
        "lock:test:atomic",
        lock_token="token",
        logger=logger,
        log_context="test release",
    )

    assert released is True
    assert called["atomic"] == 1
    assert called["fallback"] == 0


def test_release_cache_key_if_owner_falls_back_when_backend_is_unsupported(monkeypatch):
    logger = logging.getLogger(__name__)
    called = {"atomic": 0, "fallback": 0}

    def _atomic(*_args, **_kwargs):
        called["atomic"] += 1
        return cache_lock._AtomicCacheLockReleaseResult.UNSUPPORTED_BACKEND

    def _fallback(*_args, **_kwargs):
        called["fallback"] += 1
        return True

    monkeypatch.setattr(cache_lock, "_release_cache_lock_atomic_if_owner", _atomic)
    monkeypatch.setattr(cache_lock, "_release_cache_lock_non_atomic_if_owner", _fallback)

    released = cache_lock.release_cache_key_if_owner(
        "lock:test:fallback",
        lock_token="token",
        logger=logger,
        log_context="test release",
    )

    assert released is True
    assert called["atomic"] == 1
    assert called["fallback"] == 1


def test_release_cache_key_if_owner_bubbles_up_programming_error_on_get(monkeypatch):
    logger = logging.getLogger(__name__)

    class _BrokenCache:
        def get(self, *_args, **_kwargs):
            raise AssertionError("broken cache get contract")

    monkeypatch.setattr(cache_lock, "cache", _BrokenCache())
    monkeypatch.setattr(
        cache_lock,
        "_release_cache_lock_atomic_if_owner",
        lambda *_args, **_kwargs: cache_lock._AtomicCacheLockReleaseResult.UNSUPPORTED_BACKEND,
    )

    try:
        cache_lock.release_cache_key_if_owner(
            "lock:test:get-programming",
            lock_token="token",
            logger=logger,
            log_context="test release",
        )
    except AssertionError as exc:
        assert "broken cache get contract" in str(exc)
    else:
        raise AssertionError("expected release_cache_key_if_owner to bubble cache.get programming error")


def test_release_cache_key_if_owner_bubbles_up_programming_error_on_delete(monkeypatch):
    logger = logging.getLogger(__name__)

    class _BrokenCache:
        def get(self, *_args, **_kwargs):
            return "token"

        def delete(self, *_args, **_kwargs):
            raise AssertionError("broken cache delete contract")

    monkeypatch.setattr(cache_lock, "cache", _BrokenCache())
    monkeypatch.setattr(
        cache_lock,
        "_release_cache_lock_atomic_if_owner",
        lambda *_args, **_kwargs: cache_lock._AtomicCacheLockReleaseResult.UNSUPPORTED_BACKEND,
    )

    try:
        cache_lock.release_cache_key_if_owner(
            "lock:test:delete-programming",
            lock_token="token",
            logger=logger,
            log_context="test release",
        )
    except AssertionError as exc:
        assert "broken cache delete contract" in str(exc)
    else:
        raise AssertionError("expected release_cache_key_if_owner to bubble cache.delete programming error")
