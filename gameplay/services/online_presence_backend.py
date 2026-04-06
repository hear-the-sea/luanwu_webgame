from __future__ import annotations

import logging
from typing import Any

from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

ONLINE_USERS_ZSET_KEY = "online_users_zset"
ONLINE_HTTP_USERS_ZSET_KEY = "online_users_http_zset"
ONLINE_WS_USERS_ZSET_KEY = "online_users_ws_zset"
ONLINE_USERS_TTL_SECONDS = 1800
ONLINE_USERS_CACHE_KEY = "stats:online_users_count"
ONLINE_USER_TOUCH_CACHE_KEY_PREFIX = "stats:online_users:touch:"
ONLINE_USER_TOUCH_CACHE_TIMEOUT = 15


def get_redis_connection_if_supported() -> Any | None:
    try:
        return get_redis_connection("default")
    except NotImplementedError:
        return None


def touch_http_presence(
    redis: Any, *, user_id: int, now_ts: float, ttl_seconds: int = ONLINE_USERS_TTL_SECONDS
) -> None:
    redis.zadd(ONLINE_HTTP_USERS_ZSET_KEY, {int(user_id): float(now_ts)})
    redis.expire(ONLINE_HTTP_USERS_ZSET_KEY, int(ttl_seconds) * 2)


def touch_ws_presence(redis: Any, *, user_id: int, now_ts: float, ttl_seconds: int = ONLINE_USERS_TTL_SECONDS) -> None:
    redis.zadd(ONLINE_WS_USERS_ZSET_KEY, {int(user_id): float(now_ts)})
    redis.expire(ONLINE_WS_USERS_ZSET_KEY, int(ttl_seconds) * 2)


def cleanup_online_presence_sources(redis: Any, *, now_ts: float, ttl_seconds: int = ONLINE_USERS_TTL_SECONDS) -> None:
    cutoff = float(now_ts) - float(ttl_seconds)
    redis.zremrangebyscore(ONLINE_HTTP_USERS_ZSET_KEY, "-inf", cutoff)
    redis.zremrangebyscore(ONLINE_WS_USERS_ZSET_KEY, "-inf", cutoff)
    redis.zremrangebyscore(ONLINE_USERS_ZSET_KEY, "-inf", cutoff)


def count_online_users(redis: Any, *, now_ts: float, ttl_seconds: int = ONLINE_USERS_TTL_SECONDS) -> int:
    cleanup_online_presence_sources(redis, now_ts=now_ts, ttl_seconds=ttl_seconds)
    redis.zunionstore(ONLINE_USERS_ZSET_KEY, [ONLINE_HTTP_USERS_ZSET_KEY, ONLINE_WS_USERS_ZSET_KEY], aggregate="MAX")
    redis.expire(ONLINE_USERS_ZSET_KEY, int(ttl_seconds) * 2)
    return int(redis.zcard(ONLINE_USERS_ZSET_KEY) or 0)
