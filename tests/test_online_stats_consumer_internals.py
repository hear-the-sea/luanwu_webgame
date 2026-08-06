from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from django.core.cache import cache
from django.db import DatabaseError
from django.test import SimpleTestCase
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError

from core.utils import cache_lock as cache_lock_module
from websocket.consumers import OnlineStatsConsumer


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops: list[tuple] = []

    def incr(self, key: str):
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int):
        self._ops.append(("expire", key, ttl))
        return self

    def zadd(self, key: str, mapping: dict):
        self._ops.append(("zadd", key, mapping))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "incr":
                self._redis.incr(op[1])
            elif op[0] == "expire":
                continue
            elif op[0] == "zadd":
                _, key, mapping = op
                self._redis.zadd(key, mapping)
        return []


class _FakeRedis:
    def __init__(self):
        self._counters: dict[str, int] = {}
        self._zsets: dict[str, dict[int, float]] = {}
        self.eval_called: int = 0

    def pipeline(self):
        return _FakePipeline(self)

    def incr(self, key: str):
        self._counters[key] = int(self._counters.get(key, 0)) + 1
        return self._counters[key]

    def get(self, key: str):
        return str(self._counters.get(key)).encode("utf-8") if key in self._counters else None

    def expire(self, key: str, ttl: int):
        return True

    def zadd(self, key: str, mapping: dict[int, float]):
        self._zsets.setdefault(key, {}).update({int(k): float(v) for k, v in mapping.items()})
        return True

    def zremrangebyscore(self, key: str, _min: str, cutoff: float):
        z = self._zsets.get(key, {})
        before = len(z)
        self._zsets[key] = {k: v for k, v in z.items() if float(v) > float(cutoff)}
        return before - len(self._zsets[key])

    def zcard(self, key: str):
        return len(self._zsets.get(key, {}))

    def zunionstore(self, dest: str, keys: list[str], aggregate: str = "SUM") -> int:
        result: dict[int, float] = {}
        for key in keys:
            for user_id, score in self._zsets.get(key, {}).items():
                if user_id not in result:
                    result[user_id] = score
                elif aggregate == "MAX":
                    result[user_id] = max(result[user_id], score)
                elif aggregate == "MIN":
                    result[user_id] = min(result[user_id], score)
                else:
                    result[user_id] += score
        self._zsets[dest] = result
        return len(result)

    def eval(self, _script: str, _numkeys: int, count_key: str, zset_key: str, user_id: str, ttl: str):
        self.eval_called += 1
        # Simple behavior: always remove user.
        self._counters.pop(count_key, None)
        self._zsets.get(zset_key, {}).pop(int(user_id), None)
        return 0


class OnlineStatsConsumerInternalTests(SimpleTestCase):
    def _build_consumer(self) -> OnlineStatsConsumer:
        consumer = OnlineStatsConsumer()
        consumer.channel_name = "test-channel"
        consumer.channel_layer = AsyncMock()
        consumer.send_json = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        return consumer

    def test_online_stats_refresh_thresholds_stay_responsive(self):
        consumer = self._build_consumer()

        assert consumer.ONLINE_USERS_HEARTBEAT_INTERVAL <= 60
        assert consumer.ONLINE_COUNT_CACHE_TTL <= 5
        assert consumer.TOTAL_COUNT_CACHE_TTL <= 30

    def test_broadcast_debounce_skips_when_gate_is_closed(self):
        consumer = self._build_consumer()
        consumer.BROADCAST_DEBOUNCE_SECONDS = 1
        consumer.channel_layer.group_send = AsyncMock()

        # Simulate debounce gate closed.
        original_add = cache.add
        cache.add = lambda *_a, **_k: False
        try:
            asyncio.run(consumer._broadcast_stats_best_effort({"online_count": 1}))
        finally:
            cache.add = original_add

        consumer.channel_layer.group_send.assert_not_awaited()

    def test_broadcast_debounce_falls_back_if_cache_errors(self):
        consumer = self._build_consumer()
        consumer.BROADCAST_DEBOUNCE_SECONDS = 1
        consumer.channel_layer.group_send = AsyncMock()

        original_add = cache.add
        cache.add = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
        try:
            asyncio.run(consumer._broadcast_stats_best_effort({"online_count": 1}))
        finally:
            cache.add = original_add

        consumer.channel_layer.group_send.assert_awaited_once()

    def test_broadcast_debounce_runtime_marker_cache_error_bubbles_up(self):
        consumer = self._build_consumer()
        consumer.BROADCAST_DEBOUNCE_SECONDS = 1
        consumer.channel_layer.group_send = AsyncMock()

        original_add = cache.add
        cache.add = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache down"))
        try:
            with pytest.raises(RuntimeError, match="cache down"):
                asyncio.run(consumer._broadcast_stats_best_effort({"online_count": 1}))
        finally:
            cache.add = original_add

        consumer.channel_layer.group_send.assert_not_awaited()

    def test_broadcast_debounce_local_fallback_gates_when_cache_errors(self):
        consumer = self._build_consumer()
        consumer.BROADCAST_DEBOUNCE_SECONDS = 1
        consumer.channel_layer.group_send = AsyncMock()

        cache_lock_module._LOCAL_LOCKS.clear()
        original_add = cache.add
        cache.add = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
        try:
            asyncio.run(consumer._broadcast_stats_best_effort({"online_count": 1}))
            asyncio.run(consumer._broadcast_stats_best_effort({"online_count": 2}))
        finally:
            cache.add = original_add
            cache_lock_module._LOCAL_LOCKS.clear()

        consumer.channel_layer.group_send.assert_awaited_once()

    def test_get_online_count_sync_caches_and_cleans_up(self):
        consumer = self._build_consumer()
        fake = _FakeRedis()
        consumer._get_redis = lambda: fake

        now_ts = time.time()
        fake.zadd(consumer.ONLINE_WS_USERS_KEY, {1: now_ts})
        fake.zadd(consumer.ONLINE_WS_USERS_KEY, {2: now_ts - consumer.ONLINE_USERS_TTL - 10})

        cache.delete(consumer.ONLINE_COUNT_CACHE_KEY)
        count = consumer._get_online_count_sync()
        assert count == 1

        # Cache hit path
        assert consumer._get_online_count_sync() == 1

    def test_get_online_count_sync_tolerates_cache_backend_failure(self):
        consumer = self._build_consumer()
        fake = _FakeRedis()
        consumer._get_redis = lambda: fake

        now_ts = time.time()
        fake.zadd(consumer.ONLINE_WS_USERS_KEY, {1: now_ts})

        original_get = cache.get
        original_set = cache.set
        cache.get = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
        cache.set = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
        try:
            assert consumer._get_online_count_sync() == 1
        finally:
            cache.get = original_get
            cache.set = original_set

    def test_get_online_count_sync_cache_set_runtime_marker_bubbles_up(self):
        consumer = self._build_consumer()
        fake = _FakeRedis()
        consumer._get_redis = lambda: fake

        now_ts = time.time()
        fake.zadd(consumer.ONLINE_WS_USERS_KEY, {1: now_ts})

        original_get = cache.get
        original_set = cache.set
        cache.get = lambda *_a, **_k: None
        cache.set = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache set failed"))
        try:
            with pytest.raises(RuntimeError, match="cache set failed"):
                consumer._get_online_count_sync()
        finally:
            cache.get = original_get
            cache.set = original_set

    def test_touch_online_user_sync_refreshes_redis_lease_without_invalidating_count_cache(self):
        consumer = self._build_consumer()
        fake = _FakeRedis()
        consumer._get_redis = lambda: fake

        consumer._touch_online_user_sync(user_id=1, now_ts=time.time())

        assert 1 in fake._zsets[consumer.ONLINE_WS_USERS_KEY]

    def test_heartbeat_loop_refreshes_and_broadcasts_stats(self):
        consumer = self._build_consumer()
        consumer.is_real_user = True
        consumer.user_id = 9
        consumer.touch_online_user = AsyncMock()
        consumer._refresh_and_broadcast_stats_best_effort = AsyncMock()

        sleep_calls = {"count": 0}
        original_sleep = asyncio.sleep

        async def _fake_sleep(_interval: float):
            sleep_calls["count"] += 1
            if sleep_calls["count"] > 1:
                raise asyncio.CancelledError()
            return None

        asyncio.sleep = _fake_sleep
        try:
            asyncio.run(consumer._heartbeat_loop())
        finally:
            asyncio.sleep = original_sleep

        consumer.touch_online_user.assert_awaited_once_with(9)
        consumer._refresh_and_broadcast_stats_best_effort.assert_awaited_once_with()

    def test_heartbeat_broadcast_lock_skips_duplicate_stats_refresh(self):
        consumer = self._build_consumer()
        consumer.get_stats = AsyncMock(return_value={"online_count": 4, "total_count": 12})
        consumer._send_stats_update = AsyncMock()
        consumer._acquire_broadcast_debounce = lambda: False

        asyncio.run(consumer._refresh_and_broadcast_stats_best_effort())

        consumer.get_stats.assert_not_awaited()
        consumer._send_stats_update.assert_not_awaited()

    def test_heartbeat_broadcast_refreshes_stats_after_lock_acquisition(self):
        consumer = self._build_consumer()
        stats = {"online_count": 4, "total_count": 12}
        consumer.get_stats = AsyncMock(return_value=stats)
        consumer._send_stats_update = AsyncMock()
        consumer._acquire_broadcast_debounce = lambda: True

        asyncio.run(consumer._refresh_and_broadcast_stats_best_effort())

        consumer.get_stats.assert_awaited_once_with()
        consumer._send_stats_update.assert_awaited_once_with(stats)

    def test_cleanup_expired_users_sync_handles_redis_error(self):
        consumer = self._build_consumer()

        class _BrokenRedis(_FakeRedis):
            def zremrangebyscore(self, key: str, _min: str, cutoff: float):
                raise RedisError("down")

        consumer._get_redis = lambda: _BrokenRedis()
        assert consumer._cleanup_expired_users_sync(time.time()) == 0

    def test_get_stats_handles_total_users_database_error(self):
        consumer = self._build_consumer()
        consumer._get_online_count_sync = lambda: 2
        consumer.get_total_users = AsyncMock(side_effect=DatabaseError("boom"))

        stats = asyncio.run(consumer.get_stats())
        assert stats["online_count"] == 2
        assert stats["total_count"] == 0


@pytest.mark.django_db
def test_get_total_users_uses_cache(django_user_model):
    consumer = OnlineStatsConsumer()

    django_user_model.objects.create_user(username="total_users_1", password="pass")
    django_user_model.objects.create_user(username="staff", password="pass", is_staff=True)

    cache.delete(consumer.TOTAL_COUNT_CACHE_KEY)
    # Call the underlying sync implementation to avoid SQLite/thread
    # locking issues (the async wrapper executes in a separate thread).
    first = consumer.get_total_users.__wrapped__(consumer)
    assert first == 1

    django_user_model.objects.filter(username="total_users_1").delete()
    second = consumer.get_total_users.__wrapped__(consumer)
    assert second == 1


@pytest.mark.django_db
def test_get_total_users_tolerates_cache_backend_failure(django_user_model):
    consumer = OnlineStatsConsumer()

    django_user_model.objects.create_user(username="total_users_cache_failure", password="pass")

    original_get = cache.get
    original_set = cache.set
    cache.get = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
    cache.set = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
    try:
        total = consumer.get_total_users.__wrapped__(consumer)
    finally:
        cache.get = original_get
        cache.set = original_set

    assert total == 1


@pytest.mark.django_db
def test_get_total_users_runtime_marker_cache_error_bubbles_up(django_user_model):
    consumer = OnlineStatsConsumer()

    django_user_model.objects.create_user(username="total_users_runtime_marker", password="pass")

    original_get = cache.get
    cache.get = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache down"))
    try:
        with pytest.raises(RuntimeError, match="cache down"):
            consumer.get_total_users.__wrapped__(consumer)
    finally:
        cache.get = original_get
