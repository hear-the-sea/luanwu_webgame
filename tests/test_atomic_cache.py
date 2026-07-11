import threading

import pytest
from django.core.cache import cache as django_cache

from core.utils import atomic_cache
from core.utils.atomic_cache import increment_counter, merge_int_id_set, normalize_int_ids


@pytest.fixture(autouse=True)
def _owner_aware_atomic_cache_release(monkeypatch):
    def _release_if_owner(key, *, lock_token, **_kwargs):
        atomic_cache.cache.delete(key)
        return True

    monkeypatch.setattr(
        atomic_cache,
        "release_cache_key_if_owner",
        _release_if_owner,
        raising=False,
    )


def test_normalize_int_ids_deduplicates_preserving_order():
    assert normalize_int_ids([1, "2", " 2 ", "", "3", 1, 4]) == [1, 2, 3, 4]


def test_merge_int_id_set_merges_existing_values():
    django_cache.delete("ids:test")
    django_cache.set("ids:test", [1, 2], timeout=60)

    merged = merge_int_id_set("ids:test", [2, 3, 4], ttl=60)

    assert set(merged) == {1, 2, 3, 4}
    assert len(merged) == 4
    assert set(django_cache.get("ids:test")) == {1, 2, 3, 4}
    assert len(django_cache.get("ids:test")) == 4


def test_merge_int_id_set_waits_for_lock_before_merging(monkeypatch):
    errors: list[BaseException] = []
    first_set_started = threading.Event()
    allow_first_set = threading.Event()
    state_lock = threading.Lock()
    values: dict[str, list[int]] = {}
    lock_held = False

    class FakeCache:
        def add(self, key, value, timeout=None):
            nonlocal lock_held
            if key != "ids:contended:lock":
                raise AssertionError(f"unexpected add key: {key}")
            with state_lock:
                if lock_held:
                    return False
                lock_held = True
                return True

        def get(self, key, default=None):
            with state_lock:
                value = values.get(key, default)
                if value is default:
                    return default
                return list(value)

        def set(self, key, value, timeout=None):
            if key != "ids:contended":
                raise AssertionError(f"unexpected set key: {key}")
            if value == [1]:
                first_set_started.set()
                allow_first_set.wait(timeout=5)
            with state_lock:
                values[key] = list(value)

        def delete(self, key):
            nonlocal lock_held
            if key != "ids:contended:lock":
                raise AssertionError(f"unexpected delete key: {key}")
            with state_lock:
                lock_held = False

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())
    monkeypatch.setattr("core.utils.atomic_cache._MERGE_LOCK_POLL_INTERVAL_SECONDS", 0.001)

    def _merge(ids):
        try:
            merge_int_id_set("ids:contended", ids, ttl=60)
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=_merge, args=([1],))
    second = threading.Thread(target=_merge, args=([2],))

    first.start()
    assert first_set_started.wait(timeout=5)
    second.start()
    allow_first_set.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert errors == []
    assert values["ids:contended"] == [1, 2]


def test_merge_int_id_set_best_effort_when_cache_is_unavailable(monkeypatch):
    class FakeCache:
        def add(self, key, value, timeout=None):
            raise ConnectionError("cache down")

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    merged = merge_int_id_set("ids:down", ["1", "2", "2", "", 3], timeout=None)
    merged_again = merge_int_id_set("ids:down", [3, "4"], timeout=None)

    assert merged == [1, 2, 3]
    assert merged_again == [1, 2, 3, 4]


def test_merge_int_id_set_best_effort_when_cache_get_fails_after_lock(monkeypatch):
    deleted_keys: list[str] = []

    class FakeCache:
        def add(self, key, value, timeout=None):
            return True

        def get(self, key, default=None):
            raise ConnectionError("cache get failed")

        def delete(self, key):
            deleted_keys.append(key)

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    merged = merge_int_id_set("ids:get-fail", [1, 2], ttl=60)
    merged_again = merge_int_id_set("ids:get-fail", [2, 3], ttl=60)

    assert merged == [1, 2]
    assert merged_again == [1, 2, 3]
    assert deleted_keys == ["ids:get-fail:lock", "ids:get-fail:lock"]


def test_merge_int_id_set_best_effort_when_cache_set_fails_after_lock(monkeypatch):
    deleted_keys: list[str] = []

    class FakeCache:
        def add(self, key, value, timeout=None):
            return True

        def get(self, key, default=None):
            return [5]

        def set(self, key, value, timeout=None):
            raise ConnectionError("cache set failed")

        def delete(self, key):
            deleted_keys.append(key)

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    merged = merge_int_id_set("ids:set-fail", [6], ttl=60)
    merged_again = merge_int_id_set("ids:set-fail", [7], ttl=60)

    assert merged == [5, 6]
    assert merged_again == [5, 6, 7]
    assert deleted_keys == ["ids:set-fail:lock", "ids:set-fail:lock"]


def test_merge_int_id_set_flushes_local_fallback_after_cache_recovers(monkeypatch):
    deleted_keys: list[str] = []
    healthy = False
    values = {"ids:recover": [8]}

    class FakeCache:
        def add(self, key, value, timeout=None):
            if key.endswith(":lock"):
                if not healthy:
                    raise ConnectionError("cache down")
                return True
            raise AssertionError(f"unexpected add key: {key}")

        def get(self, key, default=None):
            if not healthy:
                raise ConnectionError("cache down")
            return list(values.get(key, default))

        def set(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache down")
            values[key] = list(value)

        def delete(self, key):
            deleted_keys.append(key)

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    assert merge_int_id_set("ids:recover", [1, 2], ttl=60) == [1, 2]
    assert merge_int_id_set("ids:recover", [2, 3], ttl=60) == [1, 2, 3]

    healthy = True

    recovered = merge_int_id_set("ids:recover", [4], ttl=60)
    steady_state = merge_int_id_set("ids:recover", [5], ttl=60)

    assert recovered == [8, 1, 2, 3, 4]
    assert values["ids:recover"] == [8, 1, 2, 3, 4, 5]
    assert steady_state == [8, 1, 2, 3, 4, 5]
    assert deleted_keys == ["ids:recover:lock", "ids:recover:lock"]


def test_merge_int_id_set_old_worker_does_not_release_replacement_owner(monkeypatch):
    lock_key = "ids:replacement-owner:lock"
    replacement_token = "replacement-owner-token"
    state: dict[str, object] = {}
    acquired_tokens: list[str] = []
    release_calls: list[tuple[str, str]] = []

    class FakeCache:
        def add(self, key, value, timeout=None):
            assert key == lock_key
            acquired_tokens.append(value)
            state[key] = value
            return True

        def get(self, key, default=None):
            return state.get(key, default)

        def set(self, key, value, timeout=None):
            assert key == "ids:replacement-owner"
            state[key] = replacement_token
            state[lock_key] = replacement_token

        def delete(self, key):
            raise AssertionError(f"raw cache.delete must not release merge lock: {key}")

    def _release_if_owner(key, *, lock_token, **_kwargs):
        release_calls.append((key, lock_token))
        if state.get(key) != lock_token:
            return False
        state.pop(key, None)
        return True

    monkeypatch.setattr(atomic_cache, "cache", FakeCache())
    monkeypatch.setattr(atomic_cache, "release_cache_key_if_owner", _release_if_owner)

    assert merge_int_id_set("ids:replacement-owner", [1], ttl=60) == [1]

    assert len(acquired_tokens) == 1
    assert acquired_tokens[0] != replacement_token
    assert release_calls == [(lock_key, acquired_tokens[0])]
    assert state[lock_key] == replacement_token


def test_increment_counter_preserves_concurrent_first_writes(monkeypatch):
    errors: list[BaseException] = []
    start = threading.Event()
    lock = threading.Lock()
    values: dict[str, int] = {}

    class FakeCache:
        def incr(self, key, delta=1):
            raise ValueError("missing")

        def add(self, key, value, timeout=None):
            with lock:
                if key in values:
                    return False
                values[key] = value
                return True

        def get(self, key, default=None):
            with lock:
                return values.get(key, default)

        def set(self, key, value, timeout=None):
            with lock:
                values[key] = value

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    def _run_increment():
        try:
            start.wait(timeout=5)
            increment_counter("counter:test", ttl=60)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_run_increment) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert values["counter:test"] == 2


def test_increment_counter_best_effort_when_cache_is_unavailable(monkeypatch):
    class FakeCache:
        def incr(self, key, delta=1):
            raise ConnectionError("cache down")

        def add(self, key, value, timeout=None):
            raise ConnectionError("cache down")

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    assert increment_counter("counter:down", ttl=60) == 1
    assert increment_counter("counter:down", ttl=60) == 2


def test_increment_counter_best_effort_when_cache_get_fails_after_second_value_error(monkeypatch):
    class FakeCache:
        def incr(self, key, delta=1):
            raise ValueError("missing")

        def add(self, key, value, timeout=None):
            return False

        def get(self, key, default=None):
            raise ConnectionError("cache get failed")

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    assert increment_counter("counter:get-fail", ttl=60) == 1
    assert increment_counter("counter:get-fail", ttl=60) == 2


def test_increment_counter_flushes_local_fallback_after_cache_recovers(monkeypatch):
    healthy = False
    values = {"counter:recover": 10}

    class FakeCache:
        def incr(self, key, delta=1):
            if not healthy:
                raise ConnectionError("cache down")
            if key not in values:
                raise ValueError("missing")
            values[key] += delta
            return values[key]

        def add(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache down")
            if key in values:
                return False
            values[key] = value
            return True

        def get(self, key, default=None):
            if not healthy:
                raise ConnectionError("cache down")
            return values.get(key, default)

        def set(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache down")
            values[key] = value

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    assert increment_counter("counter:recover", ttl=60) == 1
    assert increment_counter("counter:recover", ttl=60) == 2

    healthy = True

    recovered = increment_counter("counter:recover", ttl=60)
    steady_state = increment_counter("counter:recover", ttl=60)

    assert recovered == 13
    assert steady_state == 14
    assert values["counter:recover"] == 14


def test_increment_counter_restores_claimed_pending_when_recovery_flush_fails(monkeypatch):
    values = {"counter:recover-retry": 10}
    state = "down"

    class FakeCache:
        def incr(self, key, delta=1):
            nonlocal state
            if state == "down":
                raise ConnectionError("cache down")
            if state == "fail_once":
                state = "healthy"
                raise ConnectionError("cache down during recovery")
            values[key] += delta
            return values[key]

        def add(self, key, value, timeout=None):
            if state != "healthy":
                raise ConnectionError("cache down")
            if key in values:
                return False
            values[key] = value
            return True

        def get(self, key, default=None):
            if state != "healthy":
                raise ConnectionError("cache down")
            return values.get(key, default)

        def set(self, key, value, timeout=None):
            if state != "healthy":
                raise ConnectionError("cache down")
            values[key] = value

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    assert increment_counter("counter:recover-retry", ttl=60) == 1
    assert increment_counter("counter:recover-retry", ttl=60) == 2

    state = "fail_once"
    assert increment_counter("counter:recover-retry", ttl=60) == 3

    recovered = increment_counter("counter:recover-retry", ttl=60)
    steady_state = increment_counter("counter:recover-retry", ttl=60)

    assert recovered == 14
    assert steady_state == 15
    assert values["counter:recover-retry"] == 15


def test_increment_counter_concurrent_recovery_flushes_pending_only_once(monkeypatch):
    healthy = False
    values = {"counter:recover-concurrent": 10}
    start = threading.Event()
    first_incr_started = threading.Event()
    allow_first_incr = threading.Event()
    state_lock = threading.Lock()
    errors: list[BaseException] = []
    results: list[int] = []

    class FakeCache:
        def incr(self, key, delta=1):
            if not healthy:
                raise ConnectionError("cache down")
            if key != "counter:recover-concurrent":
                raise AssertionError(f"unexpected incr key: {key}")
            if not first_incr_started.is_set():
                first_incr_started.set()
                allow_first_incr.wait(timeout=5)
            with state_lock:
                values[key] += delta
                return values[key]

        def add(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache down")
            raise AssertionError("add should not be used when key exists")

        def get(self, key, default=None):
            if not healthy:
                raise ConnectionError("cache down")
            with state_lock:
                return values.get(key, default)

        def set(self, key, value, timeout=None):
            if not healthy:
                raise ConnectionError("cache down")
            with state_lock:
                values[key] = value

    monkeypatch.setattr("core.utils.atomic_cache.cache", FakeCache())

    assert increment_counter("counter:recover-concurrent", ttl=60) == 1
    assert increment_counter("counter:recover-concurrent", ttl=60) == 2

    healthy = True

    def _run_increment():
        try:
            start.wait(timeout=5)
            results.append(increment_counter("counter:recover-concurrent", ttl=60))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_run_increment) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.set()
    assert first_incr_started.wait(timeout=5)
    allow_first_incr.set()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) in ([11, 14], [13, 14])
    assert values["counter:recover-concurrent"] == 14
