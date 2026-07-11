"""Rate limiter backend for Redis-backed sliding-window world chat throttling."""

from __future__ import annotations

import logging
import math
import uuid

from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from websocket.exceptions import WorldChatInfrastructureError

logger = logging.getLogger(__name__)


SLIDING_WINDOW_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_messages = tonumber(ARGV[3])
local member = ARGV[4]
local ttl_seconds = tonumber(ARGV[5])
local cutoff_ms = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff_ms)
local current_count = redis.call('ZCARD', key)

if current_count >= max_messages then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_after_ms = window_ms
  if oldest[2] then
    retry_after_ms = tonumber(oldest[2]) + window_ms - now_ms
    if retry_after_ms < 1 then
      retry_after_ms = 1
    end
  end
  redis.call('EXPIRE', key, ttl_seconds)
  return {0, retry_after_ms}
end

redis.call('ZADD', key, now_ms, member)
redis.call('EXPIRE', key, ttl_seconds)
return {1, 0}
"""


def _now_ts() -> float:
    # Preserve test monkeypatching via `websocket.consumers.time.time`.
    from websocket.consumers import time as consumers_time

    return float(consumers_time.time())


def _direct_rate_limit_sync(
    redis,
    *,
    key: str,
    now_ms: int,
    window_ms: int,
    rate_limit_max_messages: int,
    ttl_seconds: int,
    member: str,
) -> tuple[bool, int | None]:
    redis.zremrangebyscore(key, "-inf", now_ms - window_ms)
    current_count = int(redis.zcard(key) or 0)

    if current_count >= rate_limit_max_messages:
        oldest_entries = redis.zrange(key, 0, 0, withscores=True)
        retry_after_ms = window_ms
        if oldest_entries:
            _oldest_member, oldest_score = oldest_entries[0]
            retry_after_ms = max(1, int(float(oldest_score) + window_ms - now_ms))
        redis.expire(key, ttl_seconds)
        return False, int(math.ceil(retry_after_ms / 1000.0))

    redis.zadd(key, {member: float(now_ms)})
    redis.expire(key, ttl_seconds)
    return True, None


def _script_rate_limit_sync(
    redis,
    *,
    key: str,
    now_ms: int,
    window_ms: int,
    rate_limit_max_messages: int,
    ttl_seconds: int,
    member: str,
) -> tuple[bool, int | None]:
    if not hasattr(redis, "eval"):
        return _direct_rate_limit_sync(
            redis,
            key=key,
            now_ms=now_ms,
            window_ms=window_ms,
            rate_limit_max_messages=rate_limit_max_messages,
            ttl_seconds=ttl_seconds,
            member=member,
        )

    result = redis.eval(
        SLIDING_WINDOW_RATE_LIMIT_SCRIPT,
        1,
        key,
        str(int(now_ms)),
        str(int(window_ms)),
        str(int(rate_limit_max_messages)),
        member,
        str(int(ttl_seconds)),
    )
    allowed_raw, retry_after_ms_raw = result
    allowed = bool(int(allowed_raw))
    if allowed:
        return True, None
    retry_after_ms = max(1, int(retry_after_ms_raw or window_ms))
    return False, int(math.ceil(retry_after_ms / 1000.0))


def rate_limit_sync(
    user_id: int | None,
    redis,
    *,
    rate_limit_window_seconds: int,
    rate_limit_max_messages: int,
) -> tuple[bool, int | None]:
    """Check whether a user is within the sliding-window rate limit.

    Returns (allowed, retry_after_seconds).
    """
    if not user_id:
        return False, 3

    now_ms = int(_now_ts() * 1000)
    window_ms = int(rate_limit_window_seconds) * 1000
    key = f"chat:world:rate:{int(user_id)}"
    ttl_seconds = max(int(rate_limit_window_seconds) * 2, int(rate_limit_window_seconds) + 2, 2)
    member = f"{now_ms}:{uuid.uuid4().hex}"

    try:
        return _script_rate_limit_sync(
            redis,
            key=key,
            now_ms=now_ms,
            window_ms=window_ms,
            rate_limit_max_messages=rate_limit_max_messages,
            ttl_seconds=ttl_seconds,
            member=member,
        )
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("World chat rate limit Redis error; rejecting send: %s", exc)
        raise WorldChatInfrastructureError("world chat rate limit backend unavailable") from exc
