from __future__ import annotations

import asyncio
import logging
import time

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError
from django_redis import get_redis_connection

from core.utils.cache_lock import acquire_best_effort_lock
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.online_presence_backend import (
    ONLINE_USERS_CACHE_KEY,
    ONLINE_USERS_TTL_SECONDS,
    ONLINE_USERS_ZSET_KEY,
    ONLINE_WS_USERS_ZSET_KEY,
    count_online_users,
    touch_ws_presence,
)
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

from .session_guard import SingleSessionWebSocketMixin, WebSocketSessionValidationResult

User = get_user_model()

logger = logging.getLogger(__name__)


class OnlineStatsConsumer(SingleSessionWebSocketMixin, AsyncJsonWebsocketConsumer):
    """WebSocket consumer for real-time online user statistics."""

    STATS_GROUP = "online_stats"
    ONLINE_USERS_KEY = ONLINE_USERS_ZSET_KEY
    ONLINE_WS_USERS_KEY = ONLINE_WS_USERS_ZSET_KEY
    ONLINE_USERS_TTL = ONLINE_USERS_TTL_SECONDS  # 30 minutes window for auto-cleanup
    ONLINE_USERS_HEARTBEAT_INTERVAL = 60  # refresh every 1 minute
    ONLINE_USER_CONN_COUNT_KEY_PREFIX = "online_user_conn_count:"

    ONLINE_COUNT_CACHE_KEY = ONLINE_USERS_CACHE_KEY
    TOTAL_COUNT_CACHE_KEY = "stats:total_users_count"
    ONLINE_COUNT_CACHE_TTL = 5
    TOTAL_COUNT_CACHE_TTL = 30

    BROADCAST_DEBOUNCE_SECONDS = 1
    BROADCAST_DEBOUNCE_CACHE_KEY = "stats:online:broadcast:debounce"

    user_id: int | None = None
    is_real_user: bool = False
    heartbeat_task: asyncio.Task | None = None

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            logger.warning(
                "WebSocket authentication failed for OnlineStatsConsumer",
                extra={
                    "path": self.scope.get("path"),
                    "client": self.scope.get("client"),
                },
            )
            await self.close()
            return
        validation_result = await self._ensure_valid_session()
        if validation_result is WebSocketSessionValidationResult.UNAVAILABLE:
            await self.close(code=self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE)
            return
        if validation_result is WebSocketSessionValidationResult.INVALID:
            await self.close()
            return
        if validation_result is not WebSocketSessionValidationResult.VALID:
            raise RuntimeError(f"Unexpected websocket session validation result: {validation_result!r}")

        self.user_id = user.id
        self.is_real_user = not (user.is_staff or user.is_superuser)

        await self.channel_layer.group_add(self.STATS_GROUP, self.channel_name)
        await self.accept()

        if self.is_real_user:
            await self.add_online_connection(self.user_id)
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        stats = await self.get_stats()
        await self.send_json(stats)
        await self._broadcast_stats_best_effort(stats)

    async def disconnect(self, close_code):
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass
            finally:
                self.heartbeat_task = None

        if self.is_real_user:
            await self.remove_online_connection(self.user_id)

        await self.channel_layer.group_discard(self.STATS_GROUP, self.channel_name)

        stats = await self.get_stats()
        await self._broadcast_stats_best_effort(stats)

    async def _broadcast_stats_best_effort(self, stats: dict) -> None:
        if int(self.BROADCAST_DEBOUNCE_SECONDS) > 0:
            debounce_seconds = max(1, int(self.BROADCAST_DEBOUNCE_SECONDS))
            acquired, _from_cache, _lock_token = acquire_best_effort_lock(
                self.BROADCAST_DEBOUNCE_CACHE_KEY,
                timeout_seconds=debounce_seconds,
                logger=logger,
                log_context="online stats broadcast debounce",
            )
            if not acquired:
                return

        await self.channel_layer.group_send(
            self.STATS_GROUP,
            {
                "type": "stats_update",
                "stats": stats,
            },
        )

    async def stats_update(self, event):
        await self.send_json(event["stats"])

    def _get_redis(self):
        return get_redis_connection("default")

    def _safe_cache_get(self, key: str):
        try:
            return cache.get(key)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning("Online stats cache.get failed: key=%s error=%s", key, exc, exc_info=True)
            return None

    def _safe_cache_set(self, key: str, value: int, timeout: int) -> None:
        try:
            cache.set(key, value, timeout=timeout)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning("Online stats cache.set failed: key=%s error=%s", key, exc, exc_info=True)

    def _safe_cache_delete(self, key: str) -> None:
        try:
            cache.delete(key)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning("Online stats cache.delete failed: key=%s error=%s", key, exc, exc_info=True)

    def _touch_online_user_sync(self, user_id: int, now_ts: float) -> None:
        redis = self._get_redis()
        count_key = f"{self.ONLINE_USER_CONN_COUNT_KEY_PREFIX}{int(user_id)}"
        touch_ws_presence(redis, user_id=int(user_id), now_ts=float(now_ts), ttl_seconds=self.ONLINE_USERS_TTL)
        redis.expire(count_key, self.ONLINE_USERS_TTL * 2)
        self._safe_cache_delete(self.ONLINE_COUNT_CACHE_KEY)

    def _add_online_connection_sync(self, user_id: int, now_ts: float) -> None:
        redis = self._get_redis()
        count_key = f"{self.ONLINE_USER_CONN_COUNT_KEY_PREFIX}{int(user_id)}"
        pipe = redis.pipeline()
        pipe.incr(count_key)
        pipe.expire(count_key, self.ONLINE_USERS_TTL * 2)
        pipe.zadd(self.ONLINE_WS_USERS_KEY, {int(user_id): float(now_ts)})
        pipe.expire(self.ONLINE_WS_USERS_KEY, self.ONLINE_USERS_TTL * 2)
        pipe.execute()

        self._safe_cache_delete(self.ONLINE_COUNT_CACHE_KEY)

    def _remove_online_connection_sync(self, user_id: int) -> int:
        redis = self._get_redis()
        count_key = f"{self.ONLINE_USER_CONN_COUNT_KEY_PREFIX}{int(user_id)}"

        script = """
        local count_key = KEYS[1]
        local ws_zset_key = KEYS[2]
        local user_id = ARGV[1]
        local ttl = tonumber(ARGV[2])

        local current = redis.call('GET', count_key)
        if not current then
          redis.call('ZREM', ws_zset_key, user_id)
          return 0
        end
        current = tonumber(current)
        if current <= 1 then
          redis.call('DEL', count_key)
          redis.call('ZREM', ws_zset_key, user_id)
          return 0
        end

        local new_count = redis.call('DECR', count_key)
        redis.call('EXPIRE', count_key, ttl)
        return new_count
        """

        try:
            if not hasattr(self, "_online_stats_remove_script_sha"):
                self._online_stats_remove_script_sha = redis.script_load(script)
            remaining_raw = redis.evalsha(
                self._online_stats_remove_script_sha,
                2,
                count_key,
                self.ONLINE_WS_USERS_KEY,
                str(int(user_id)),
                str(int(self.ONLINE_USERS_TTL * 2)),
            )
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            remaining_raw = redis.eval(
                script,
                2,
                count_key,
                self.ONLINE_WS_USERS_KEY,
                str(int(user_id)),
                str(int(self.ONLINE_USERS_TTL * 2)),
            )

        remaining = int(remaining_raw or 0)
        self._safe_cache_delete(self.ONLINE_COUNT_CACHE_KEY)
        return remaining

    def _cleanup_expired_users_sync(self, now_ts: float) -> int:
        redis = self._get_redis()
        try:
            count_online_users(redis, now_ts=float(now_ts), ttl_seconds=self.ONLINE_USERS_TTL)
            return 0
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning("Online stats Redis cleanup failed; skipping: %s", exc)
            return 0

    def _get_online_count_sync(self) -> int:
        cached_count = self._safe_cache_get(self.ONLINE_COUNT_CACHE_KEY)
        if cached_count is not None:
            return int(cached_count)

        now_ts = time.time()
        redis = self._get_redis()
        count = int(count_online_users(redis, now_ts=now_ts, ttl_seconds=self.ONLINE_USERS_TTL) or 0)
        self._safe_cache_set(self.ONLINE_COUNT_CACHE_KEY, count, timeout=self.ONLINE_COUNT_CACHE_TTL)
        return count

    async def touch_online_user(self, user_id: int):
        await sync_to_async(self._touch_online_user_sync, thread_sensitive=True)(user_id, time.time())

    async def add_online_connection(self, user_id: int):
        await sync_to_async(self._add_online_connection_sync, thread_sensitive=True)(user_id, time.time())

    async def remove_online_connection(self, user_id: int):
        await sync_to_async(self._remove_online_connection_sync, thread_sensitive=True)(user_id)

    async def _heartbeat_loop(self) -> None:
        interval = max(30, min(self.ONLINE_USERS_HEARTBEAT_INTERVAL, self.ONLINE_USERS_TTL // 2))
        while True:
            try:
                await asyncio.sleep(interval)
                if not self.is_real_user or not self.user_id:
                    return
                await self.touch_online_user(self.user_id)
                stats = await self.get_stats()
                await self._broadcast_stats_best_effort(stats)
            except asyncio.CancelledError:
                return
            except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
                logger.debug("Online stats heartbeat Redis error; will retry: %s", exc)
                continue

    async def get_stats(self):
        try:
            online_count = await sync_to_async(self._get_online_count_sync, thread_sensitive=True)()
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning("Online stats Redis read failed; reporting 0 online users: %s", exc)
            online_count = 0

        try:
            total_count = await self.get_total_users()
        except DatabaseError as exc:
            logger.warning("Online stats DB read failed; reporting 0 total users: %s", exc)
            total_count = 0

        return {
            "online_count": online_count,
            "total_count": total_count,
        }

    @database_sync_to_async
    def get_total_users(self):
        cached_total = self._safe_cache_get(self.TOTAL_COUNT_CACHE_KEY)
        if cached_total is not None:
            return int(cached_total)

        try:
            total = User.objects.filter(is_staff=False, is_superuser=False).count()
        except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning("Failed to COUNT total users; returning 0: %s", exc)
            return 0

        self._safe_cache_set(self.TOTAL_COUNT_CACHE_KEY, total, timeout=self.TOTAL_COUNT_CACHE_TTL)
        return total
