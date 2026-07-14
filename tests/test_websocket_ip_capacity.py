from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from django_redis import get_redis_connection

from websocket.backends.ip_capacity import (
    IPCapacityDecision,
    IPCapacityResult,
    acquire_ip_capacity,
    refresh_ip_capacity_slot,
    release_ip_capacity_slot,
)
from websocket.backends.worker_lease import worker_lease_key
from websocket.exceptions import WebSocketConnectionLimitUnavailable
from websocket.middleware.ip_capacity import WebSocketIPCapacityMiddleware


class _FakeRedis:
    def __init__(self) -> None:
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = {}

    def zremrangebyscore(self, key, _minimum, maximum):
        values = self.sorted_sets.setdefault(key, {})
        expired = [member for member, score in values.items() if score <= float(maximum)]
        for member in expired:
            values.pop(member, None)
        return len(expired)

    def zcard(self, key):
        return len(self.sorted_sets.setdefault(key, {}))

    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zscore(self, key, member):
        return self.sorted_sets.setdefault(key, {}).get(member)

    def zrange(self, key, _start, _end):
        return list(self.sorted_sets.setdefault(key, {}))

    def zrem(self, key, member):
        return int(self.sorted_sets.setdefault(key, {}).pop(member, None) is not None)

    def hget(self, key, field):
        return self.hashes.setdefault(key, {}).get(field)

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update({name: str(value) for name, value in mapping.items()})

    def expire(self, _key, _ttl):
        return True

    def exists(self, key):
        return int(key in self.strings)

    def delete(self, *keys):
        for key in keys:
            self.sorted_sets.pop(key, None)
            self.hashes.pop(key, None)
            self.strings.pop(key, None)


WORKER_ID = "a" * 32


def test_ip_capacity_redis_translates_client_acquisition_failure(monkeypatch):
    middleware = WebSocketIPCapacityMiddleware(AsyncMock())
    monkeypatch.setattr(
        "websocket.middleware.ip_capacity.get_redis_connection",
        Mock(side_effect=ConnectionError("redis down")),
    )

    with pytest.raises(WebSocketConnectionLimitUnavailable):
        middleware._capacity_redis()


def _acquire(
    redis,
    *,
    client_ip="203.0.113.8",
    worker_id=WORKER_ID,
    connection_id="a",
    now_ts=100.0,
    connection_limit=20,
    rate_per_second=10,
    burst=20,
):
    redis.strings.setdefault(worker_lease_key(worker_id), "1")
    return acquire_ip_capacity(
        redis,
        client_ip=client_ip,
        worker_id=worker_id,
        connection_id=connection_id,
        connection_limit=connection_limit,
        rate_per_second=rate_per_second,
        burst=burst,
        ttl_seconds=120,
        now_ts=now_ts,
    )


def test_ip_capacity_rejects_n_plus_one_then_releases_slot():
    redis = _FakeRedis()

    assert _acquire(redis, connection_id="a", connection_limit=2).result is IPCapacityResult.ACQUIRED
    assert _acquire(redis, connection_id="b", connection_limit=2).result is IPCapacityResult.ACQUIRED
    assert _acquire(redis, connection_id="c", connection_limit=2).result is IPCapacityResult.CONNECTION_LIMITED

    release_ip_capacity_slot(redis, client_ip="203.0.113.8", worker_id=WORKER_ID, connection_id="a")

    assert _acquire(redis, connection_id="c", connection_limit=2).result is IPCapacityResult.ACQUIRED


def test_ip_capacity_prunes_expired_slots_and_keeps_ips_separate():
    redis = _FakeRedis()

    assert _acquire(redis, connection_id="old", connection_limit=1, now_ts=100).result is IPCapacityResult.ACQUIRED
    assert _acquire(redis, connection_id="new", connection_limit=1, now_ts=221).result is IPCapacityResult.ACQUIRED
    assert (
        _acquire(redis, client_ip="198.51.100.7", connection_id="other", connection_limit=1, now_ts=100).result
        is IPCapacityResult.ACQUIRED
    )


def test_ip_capacity_token_bucket_depletes_and_refills():
    redis = _FakeRedis()

    for index in range(20):
        assert _acquire(redis, connection_id=f"burst-{index}", connection_limit=100).result is IPCapacityResult.ACQUIRED
    assert _acquire(redis, connection_id="limited", connection_limit=100).result is IPCapacityResult.RATE_LIMITED
    assert (
        _acquire(redis, connection_id="refilled", connection_limit=100, now_ts=100.1).result
        is IPCapacityResult.ACQUIRED
    )


def test_ip_capacity_full_connections_still_consume_handshake_tokens():
    redis = _FakeRedis()
    options = {
        "connection_limit": 1,
        "rate_per_second": 1,
        "burst": 2,
        "now_ts": 100,
    }

    assert _acquire(redis, connection_id="active", **options).result is IPCapacityResult.ACQUIRED
    assert _acquire(redis, connection_id="capacity-full", **options).result is IPCapacityResult.CONNECTION_LIMITED
    assert _acquire(redis, connection_id="rate-limited", **options).result is IPCapacityResult.RATE_LIMITED


def test_ip_capacity_refresh_extends_only_an_existing_slot():
    redis = _FakeRedis()
    assert _acquire(redis, connection_id="active").result is IPCapacityResult.ACQUIRED

    assert refresh_ip_capacity_slot(
        redis,
        client_ip="203.0.113.8",
        worker_id=WORKER_ID,
        connection_id="active",
        ttl_seconds=120,
        now_ts=150,
    )
    assert not refresh_ip_capacity_slot(
        redis,
        client_ip="203.0.113.8",
        worker_id=WORKER_ID,
        connection_id="missing",
        ttl_seconds=120,
        now_ts=150,
    )


def test_ip_capacity_translates_redis_failures():
    class _BrokenRedis(_FakeRedis):
        def zremrangebyscore(self, key, minimum, maximum):
            raise ConnectionError("redis down")

    with pytest.raises(WebSocketConnectionLimitUnavailable):
        _acquire(_BrokenRedis())


def test_ip_capacity_prunes_only_the_dead_workers_slots():
    redis = _FakeRedis()
    dead_worker = "a" * 32
    live_worker = "b" * 32
    assert _acquire(redis, worker_id=dead_worker, connection_id="old").result is IPCapacityResult.ACQUIRED
    redis.strings[worker_lease_key(live_worker)] = "1"
    redis.strings.pop(worker_lease_key(dead_worker))

    decision = _acquire(redis, worker_id=live_worker, connection_id="new")

    assert decision == IPCapacityDecision(IPCapacityResult.ACQUIRED, 1, 0, 1, 0)


def test_ip_capacity_counts_malformed_bytes_members():
    redis = _FakeRedis()
    connections_key = next(iter(redis.sorted_sets), None)
    assert connections_key is None
    from websocket.backends.ip_capacity import _capacity_keys

    connections_key, _ = _capacity_keys("203.0.113.8")
    redis.sorted_sets[connections_key] = {b"v2|broken": 120.0}

    decision = _acquire(redis, connection_limit=1)

    assert decision.malformed_members == 1


@pytest.mark.asyncio
async def test_ip_capacity_middleware_runs_inner_app_and_releases(monkeypatch):
    inner = AsyncMock()
    middleware = WebSocketIPCapacityMiddleware(inner)
    middleware._worker_lease_manager = AsyncMock()
    middleware._worker_lease_manager.ensure_started.return_value = WORKER_ID
    monkeypatch.setattr(
        middleware,
        "_acquire_capacity",
        AsyncMock(return_value=IPCapacityDecision(IPCapacityResult.ACQUIRED, 1, 0, 0, 0)),
    )
    monkeypatch.setattr(middleware, "_release_capacity", AsyncMock())
    monkeypatch.setattr(middleware, "_run_slot_heartbeat", AsyncMock())
    send = AsyncMock()
    scope = {"type": "websocket", "client": ("203.0.113.8", 53100), "headers": []}

    await middleware(scope, AsyncMock(), send)

    inner.assert_awaited_once()
    middleware._acquire_capacity.assert_awaited_once()
    assert middleware._acquire_capacity.await_args.args[2] == WORKER_ID
    assert middleware._run_slot_heartbeat.call_args.args[2] == WORKER_ID
    assert middleware._release_capacity.await_args.args[2] == WORKER_ID
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ip_capacity_middleware_logs_dead_worker_cleanup_without_full_ip(monkeypatch):
    inner = AsyncMock()
    middleware = WebSocketIPCapacityMiddleware(inner)
    middleware._worker_lease_manager = AsyncMock()
    middleware._worker_lease_manager.ensure_started.return_value = WORKER_ID
    monkeypatch.setattr(
        middleware,
        "_acquire_capacity",
        AsyncMock(return_value=IPCapacityDecision(IPCapacityResult.ACQUIRED, 1, 0, 2, 0)),
    )
    monkeypatch.setattr(middleware, "_release_capacity", AsyncMock())
    monkeypatch.setattr(middleware, "_run_slot_heartbeat", AsyncMock())
    info = Mock()
    monkeypatch.setattr("websocket.middleware.ip_capacity.logger.info", info)

    await middleware(
        {
            "type": "websocket",
            "path": "/ws/online-stats/",
            "client": ("203.0.113.8", 53100),
            "headers": [],
        },
        AsyncMock(),
        AsyncMock(),
    )

    assert info.call_args.args[0] == "WebSocket IP dead worker slots pruned"
    assert info.call_args.kwargs["extra"] == {
        "client_ip_id": info.call_args.kwargs["extra"]["client_ip_id"],
        "path": "/ws/online-stats/",
        "active_slots": 1,
        "expired_pruned": 0,
        "dead_worker_pruned": 2,
        "malformed_members": 0,
        "worker_id": "aaaaaaaa",
    }
    assert "203.0.113.8" not in repr(info.call_args)


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_result", [False, WebSocketConnectionLimitUnavailable("redis down")])
async def test_ip_capacity_heartbeat_closes_when_slot_cannot_be_refreshed(monkeypatch, refresh_result):
    middleware = WebSocketIPCapacityMiddleware(AsyncMock())
    if isinstance(refresh_result, Exception):
        refresh = AsyncMock(side_effect=refresh_result)
    else:
        refresh = AsyncMock(return_value=refresh_result)
    monkeypatch.setattr(middleware, "_refresh_capacity", refresh)
    monkeypatch.setattr("websocket.middleware.ip_capacity.asyncio.sleep", AsyncMock())
    send = AsyncMock()

    await middleware._run_slot_heartbeat("203.0.113.8", "connection", WORKER_ID, send)

    send.assert_awaited_once_with({"type": "websocket.close", "code": 1013})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [IPCapacityResult.CONNECTION_LIMITED, IPCapacityResult.RATE_LIMITED],
)
async def test_ip_capacity_middleware_rejects_excess_connections(monkeypatch, result):
    inner = AsyncMock()
    middleware = WebSocketIPCapacityMiddleware(inner)
    middleware._worker_lease_manager = AsyncMock()
    middleware._worker_lease_manager.ensure_started.return_value = WORKER_ID
    monkeypatch.setattr(
        middleware,
        "_acquire_capacity",
        AsyncMock(return_value=IPCapacityDecision(result, 20, 1, 2, 3)),
    )
    send = AsyncMock()
    info = Mock()
    monkeypatch.setattr("websocket.middleware.ip_capacity.logger.info", info)

    await middleware(
        {
            "type": "websocket",
            "path": "/ws/online-stats/",
            "client": ("203.0.113.8", 53100),
            "headers": [],
        },
        AsyncMock(),
        send,
    )

    send.assert_awaited_once_with({"type": "websocket.close", "code": 4429})
    inner.assert_not_awaited()
    assert info.call_args.kwargs["extra"]["path"] == "/ws/online-stats/"
    assert "client_ip" not in info.call_args.kwargs["extra"]
    assert "203.0.113.8" not in repr(info.call_args)


@pytest.mark.asyncio
async def test_ip_capacity_middleware_fails_closed_when_redis_is_unavailable(monkeypatch):
    inner = AsyncMock()
    middleware = WebSocketIPCapacityMiddleware(inner)
    middleware._worker_lease_manager = AsyncMock()
    middleware._worker_lease_manager.ensure_started.return_value = WORKER_ID
    monkeypatch.setattr(
        middleware,
        "_acquire_capacity",
        AsyncMock(side_effect=WebSocketConnectionLimitUnavailable("redis down")),
    )
    send = AsyncMock()

    await middleware(
        {"type": "websocket", "client": ("203.0.113.8", 53100), "headers": []},
        AsyncMock(),
        send,
    )

    send.assert_awaited_once_with({"type": "websocket.close", "code": 1013})
    inner.assert_not_awaited()


@pytest.mark.asyncio
async def test_ip_capacity_middleware_fails_closed_when_worker_lease_is_unavailable(monkeypatch):
    inner = AsyncMock()
    middleware = WebSocketIPCapacityMiddleware(inner)
    middleware._worker_lease_manager = AsyncMock()
    middleware._worker_lease_manager.ensure_started.side_effect = ConnectionError("redis down")
    acquire = AsyncMock()
    monkeypatch.setattr(middleware, "_acquire_capacity", acquire)
    send = AsyncMock()

    await middleware(
        {"type": "websocket", "client": ("203.0.113.8", 53100), "headers": []},
        AsyncMock(),
        send,
    )

    send.assert_awaited_once_with({"type": "websocket.close", "code": 1013})
    acquire.assert_not_awaited()
    inner.assert_not_awaited()


@pytest.mark.integration
def test_ip_capacity_lua_scripts_against_real_redis():
    redis = get_redis_connection("default")
    client_ip = f"2001:db8::{uuid.uuid4().int:x}"
    throttle_ip = f"2001:db8::{uuid.uuid4().int:x}"
    first_connection = f"first-{uuid.uuid4()}"
    second_connection = f"second-{uuid.uuid4()}"
    throttle_connections = [f"throttle-{uuid.uuid4()}" for _ in range(3)]
    first_worker = uuid.uuid4().hex
    second_worker = uuid.uuid4().hex
    throttle_worker = uuid.uuid4().hex

    try:
        redis.set(worker_lease_key(first_worker), "1", ex=30)
        redis.set(worker_lease_key(second_worker), "1", ex=30)
        first = acquire_ip_capacity(
            redis,
            client_ip=client_ip,
            worker_id=first_worker,
            connection_id=first_connection,
            connection_limit=1,
            rate_per_second=100,
            burst=100,
            ttl_seconds=30,
        )
        second = acquire_ip_capacity(
            redis,
            client_ip=client_ip,
            worker_id=second_worker,
            connection_id=second_connection,
            connection_limit=1,
            rate_per_second=100,
            burst=100,
            ttl_seconds=30,
        )

        assert first.result is IPCapacityResult.ACQUIRED
        assert second.result is IPCapacityResult.CONNECTION_LIMITED
        assert refresh_ip_capacity_slot(
            redis,
            client_ip=client_ip,
            worker_id=first_worker,
            connection_id=first_connection,
            ttl_seconds=30,
        )

        redis.delete(worker_lease_key(first_worker))

        decision = acquire_ip_capacity(
            redis,
            client_ip=client_ip,
            worker_id=second_worker,
            connection_id=second_connection,
            connection_limit=1,
            rate_per_second=100,
            burst=100,
            ttl_seconds=30,
        )
        assert decision.result is IPCapacityResult.ACQUIRED
        assert decision.dead_worker_pruned == 1

        redis.set(worker_lease_key(throttle_worker), "1", ex=30)
        throttle_results = [
            acquire_ip_capacity(
                redis,
                client_ip=throttle_ip,
                worker_id=throttle_worker,
                connection_id=connection_id,
                connection_limit=1,
                rate_per_second=1,
                burst=2,
                ttl_seconds=30,
            ).result
            for connection_id in throttle_connections
        ]
        assert throttle_results == [
            IPCapacityResult.ACQUIRED,
            IPCapacityResult.CONNECTION_LIMITED,
            IPCapacityResult.RATE_LIMITED,
        ]
    finally:
        release_ip_capacity_slot(redis, client_ip=client_ip, worker_id=first_worker, connection_id=first_connection)
        release_ip_capacity_slot(redis, client_ip=client_ip, worker_id=second_worker, connection_id=second_connection)
        for connection_id in throttle_connections:
            release_ip_capacity_slot(
                redis,
                client_ip=throttle_ip,
                worker_id=throttle_worker,
                connection_id=connection_id,
            )
        redis.delete(
            worker_lease_key(first_worker),
            worker_lease_key(second_worker),
            worker_lease_key(throttle_worker),
        )
