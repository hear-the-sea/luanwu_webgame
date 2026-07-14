from __future__ import annotations

import uuid

import pytest
from django_redis import get_redis_connection

from websocket.backends.connection_limiter import (
    ConnectionCapacityDecision,
    acquire_connection_slot,
    refresh_connection_slot,
    release_connection_slot,
)
from websocket.backends.worker_lease import encode_worker_owned_member, worker_lease_key
from websocket.exceptions import WebSocketConnectionLimitUnavailable


class _FakeRedis:
    def __init__(self) -> None:
        self.members: dict[str, dict[str, float]] = {}
        self.strings: dict[str, str] = {}

    def zremrangebyscore(self, key, _minimum, maximum):
        values = self.members.setdefault(key, {})
        expired = [member for member, score in values.items() if score <= float(maximum)]
        for member in expired:
            values.pop(member, None)
        return len(expired)

    def zcard(self, key):
        return len(self.members.setdefault(key, {}))

    def zadd(self, key, mapping):
        self.members.setdefault(key, {}).update(mapping)

    def zrange(self, key, _start, _end):
        return list(self.members.setdefault(key, {}))

    def zrem(self, key, member):
        return int(self.members.setdefault(key, {}).pop(member, None) is not None)

    def delete(self, key):
        self.members.pop(key, None)
        self.strings.pop(key, None)

    def exists(self, key):
        return int(key in self.strings)

    def expire(self, _key, _ttl):
        return True


def test_connection_limiter_rejects_n_plus_one_and_releases_slot():
    redis = _FakeRedis()
    worker_id = "a" * 32
    redis.strings[worker_lease_key(worker_id)] = "1"

    first = acquire_connection_slot(
        redis, user_id=7, worker_id=worker_id, connection_id="a", limit=2, ttl_seconds=60, now_ts=100
    )
    second = acquire_connection_slot(
        redis, user_id=7, worker_id=worker_id, connection_id="b", limit=2, ttl_seconds=60, now_ts=100
    )
    rejected = acquire_connection_slot(
        redis, user_id=7, worker_id=worker_id, connection_id="c", limit=2, ttl_seconds=60, now_ts=100
    )

    assert first == ConnectionCapacityDecision(True, 1, 0, 0, 0)
    assert second == ConnectionCapacityDecision(True, 2, 0, 0, 0)
    assert rejected == ConnectionCapacityDecision(False, 2, 0, 0, 0)

    release_connection_slot(redis, user_id=7, worker_id=worker_id, connection_id="a")

    assert acquire_connection_slot(
        redis, user_id=7, worker_id=worker_id, connection_id="c", limit=2, ttl_seconds=60, now_ts=100
    ).allowed


def test_connection_limiter_prunes_expired_slots_and_release_is_idempotent():
    redis = _FakeRedis()
    worker_id = "a" * 32
    redis.strings[worker_lease_key(worker_id)] = "1"
    assert acquire_connection_slot(
        redis, user_id=8, worker_id=worker_id, connection_id="old", limit=1, ttl_seconds=10, now_ts=100
    ).allowed

    decision = acquire_connection_slot(
        redis, user_id=8, worker_id=worker_id, connection_id="new", limit=1, ttl_seconds=10, now_ts=111
    )
    assert decision.allowed
    assert decision.expired_pruned == 1
    release_connection_slot(redis, user_id=8, worker_id=worker_id, connection_id="missing")
    release_connection_slot(redis, user_id=8, worker_id=worker_id, connection_id="missing")


def test_connection_limiter_prunes_dead_worker_but_preserves_live_worker():
    redis = _FakeRedis()
    dead_worker = "a" * 32
    live_worker = "b" * 32
    redis.strings[worker_lease_key(dead_worker)] = "1"
    redis.strings[worker_lease_key(live_worker)] = "1"
    assert acquire_connection_slot(
        redis, user_id=7, worker_id=dead_worker, connection_id="old", limit=2, ttl_seconds=30, now_ts=100
    ).allowed

    redis.strings.pop(worker_lease_key(dead_worker))
    decision = acquire_connection_slot(
        redis, user_id=7, worker_id=live_worker, connection_id="new", limit=2, ttl_seconds=30, now_ts=101
    )

    assert decision == ConnectionCapacityDecision(True, 1, 0, 1, 0)
    assert list(redis.members["websocket:connections:user:7"]) == [encode_worker_owned_member(live_worker, "new")]


def test_connection_limiter_keeps_legacy_and_malformed_members_until_score_expiry():
    redis = _FakeRedis()
    worker_id = "b" * 32
    redis.strings[worker_lease_key(worker_id)] = "1"
    redis.members["websocket:connections:user:8"] = {"legacy": 120.0, "v2|broken": 120.0}

    decision = acquire_connection_slot(
        redis, user_id=8, worker_id=worker_id, connection_id="new", limit=2, ttl_seconds=30, now_ts=100
    )

    assert decision == ConnectionCapacityDecision(False, 2, 0, 0, 1)


def test_connection_limiter_counts_malformed_bytes_members():
    redis = _FakeRedis()
    worker_id = "b" * 32
    redis.strings[worker_lease_key(worker_id)] = "1"
    redis.members["websocket:connections:user:8"] = {b"v2|broken": 120.0}

    decision = acquire_connection_slot(
        redis, user_id=8, worker_id=worker_id, connection_id="new", limit=1, ttl_seconds=30, now_ts=100
    )

    assert decision.malformed_members == 1


def test_connection_limiter_translates_redis_failures():
    class _BrokenRedis(_FakeRedis):
        def zremrangebyscore(self, key, minimum, maximum):
            raise ConnectionError("redis down")

    with pytest.raises(WebSocketConnectionLimitUnavailable):
        acquire_connection_slot(
            _BrokenRedis(),
            user_id=9,
            worker_id="a" * 32,
            connection_id="broken",
            limit=1,
            ttl_seconds=10,
            now_ts=100,
        )


def test_refresh_uses_the_redis_sorted_set_api_only():
    class _OpaqueRedis:
        def __init__(self) -> None:
            member = encode_worker_owned_member("a" * 32, "opaque")
            self._scores = {"websocket:connections:user:10": {member: 110.0}}

        def zscore(self, key, member):
            return self._scores.get(key, {}).get(member)

        def zadd(self, key, mapping):
            self._scores.setdefault(key, {}).update(mapping)

        def expire(self, _key, _ttl):
            return True

    redis = _OpaqueRedis()

    assert refresh_connection_slot(
        redis,
        user_id=10,
        worker_id="a" * 32,
        connection_id="opaque",
        ttl_seconds=10,
        now_ts=105,
    )
    assert not refresh_connection_slot(
        redis,
        user_id=10,
        worker_id="a" * 32,
        connection_id="missing",
        ttl_seconds=10,
        now_ts=105,
    )


@pytest.mark.integration
def test_connection_limiter_lua_scripts_against_real_redis():
    redis = get_redis_connection("default")
    user_id = uuid.uuid4().int % 2_000_000_000
    first_connection = f"first-{uuid.uuid4()}"
    second_connection = f"second-{uuid.uuid4()}"
    first_worker = uuid.uuid4().hex
    second_worker = uuid.uuid4().hex

    try:
        redis.set(worker_lease_key(first_worker), "1", ex=30)
        redis.set(worker_lease_key(second_worker), "1", ex=30)
        assert acquire_connection_slot(
            redis,
            user_id=user_id,
            worker_id=first_worker,
            connection_id=first_connection,
            limit=1,
            ttl_seconds=30,
        )
        assert not acquire_connection_slot(
            redis,
            user_id=user_id,
            worker_id=second_worker,
            connection_id=second_connection,
            limit=1,
            ttl_seconds=30,
        )
        assert refresh_connection_slot(
            redis,
            user_id=user_id,
            worker_id=first_worker,
            connection_id=first_connection,
            ttl_seconds=30,
        )

        redis.delete(worker_lease_key(first_worker))

        decision = acquire_connection_slot(
            redis,
            user_id=user_id,
            worker_id=second_worker,
            connection_id=second_connection,
            limit=1,
            ttl_seconds=30,
        )
        assert decision.allowed
        assert decision.dead_worker_pruned == 1
    finally:
        release_connection_slot(redis, user_id=user_id, worker_id=first_worker, connection_id=first_connection)
        release_connection_slot(redis, user_id=user_id, worker_id=second_worker, connection_id=second_connection)
        redis.delete(worker_lease_key(first_worker), worker_lease_key(second_worker))
