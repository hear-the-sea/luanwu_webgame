"""Redis-backed concurrent WebSocket connection accounting."""

from __future__ import annotations

import time
from dataclasses import dataclass

from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from websocket.backends.worker_lease import (
    WORKER_KEY_PREFIX,
    decode_worker_owned_member,
    encode_worker_owned_member,
    is_versioned_worker_owned_member,
    worker_lease_key,
)
from websocket.exceptions import WebSocketConnectionLimitUnavailable

ACQUIRE_CONNECTION_SLOT_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local member = ARGV[2]
local ttl = tonumber(ARGV[3])
local key_ttl = tonumber(ARGV[4])
local worker_key_prefix = ARGV[5]
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1]) + tonumber(redis_time[2]) / 1000000
local expired_pruned = redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local dead_worker_pruned = 0
local malformed_members = 0

for _, existing_member in ipairs(redis.call('ZRANGE', key, 0, -1)) do
  if string.sub(existing_member, 1, 3) == 'v2|' then
    local worker_id, connection_id = string.match(existing_member, '^v2|([0-9a-f]+)|(.+)$')
    if not worker_id or string.len(worker_id) ~= 32 or not connection_id then
      malformed_members = malformed_members + 1
    elseif redis.call('EXISTS', worker_key_prefix .. worker_id) == 0 then
      dead_worker_pruned = dead_worker_pruned + redis.call('ZREM', key, existing_member)
    end
  end
end

local active_count = redis.call('ZCARD', key)
if active_count >= limit then
  redis.call('EXPIRE', key, key_ttl)
  return {0, active_count, expired_pruned, dead_worker_pruned, malformed_members}
end
redis.call('ZADD', key, now + ttl, member)
redis.call('EXPIRE', key, key_ttl)
return {1, active_count + 1, expired_pruned, dead_worker_pruned, malformed_members}
"""

REFRESH_CONNECTION_SLOT_SCRIPT = """
local key = KEYS[1]
local member = ARGV[1]
local ttl = tonumber(ARGV[2])
local key_ttl = tonumber(ARGV[3])
if not redis.call('ZSCORE', key, member) then return 0 end
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1]) + tonumber(redis_time[2]) / 1000000
redis.call('ZADD', key, now + ttl, member)
redis.call('EXPIRE', key, key_ttl)
return 1
"""

RELEASE_CONNECTION_SLOT_SCRIPT = """
local key = KEYS[1]
local member = ARGV[1]
local removed = redis.call('ZREM', key, member)
if redis.call('ZCARD', key) == 0 then redis.call('DEL', key) end
return removed
"""


@dataclass(frozen=True)
class ConnectionCapacityDecision:
    allowed: bool
    active_count: int
    expired_pruned: int
    dead_worker_pruned: int
    malformed_members: int

    def __bool__(self) -> bool:
        return self.allowed


def _connection_key(user_id: int) -> str:
    return f"websocket:connections:user:{int(user_id)}"


def _decision_from_redis(result) -> ConnectionCapacityDecision:
    values = list(result or ())
    if len(values) != 5:
        raise ValueError(f"Unexpected WebSocket capacity decision: {result!r}")
    return ConnectionCapacityDecision(
        allowed=bool(int(values[0] or 0)),
        active_count=int(values[1] or 0),
        expired_pruned=int(values[2] or 0),
        dead_worker_pruned=int(values[3] or 0),
        malformed_members=int(values[4] or 0),
    )


def acquire_connection_slot(
    redis,
    *,
    user_id: int,
    worker_id: str,
    connection_id: str,
    limit: int,
    ttl_seconds: int,
    now_ts: float | None = None,
) -> ConnectionCapacityDecision:
    now = float(time.time() if now_ts is None else now_ts)
    ttl = max(2, int(ttl_seconds))
    key = _connection_key(user_id)
    member = encode_worker_owned_member(worker_id, connection_id)
    try:
        if hasattr(redis, "eval"):
            result = redis.eval(
                ACQUIRE_CONNECTION_SLOT_SCRIPT,
                1,
                key,
                str(max(1, int(limit))),
                member,
                str(ttl),
                str(ttl * 2),
                WORKER_KEY_PREFIX,
            )
            return _decision_from_redis(result)

        before_prune = int(redis.zcard(key) or 0)
        redis.zremrangebyscore(key, "-inf", now)
        expired_pruned = before_prune - int(redis.zcard(key) or 0)
        dead_worker_pruned = 0
        malformed_members = 0
        for existing_member in redis.zrange(key, 0, -1):
            decoded = decode_worker_owned_member(existing_member)
            if decoded is None:
                if is_versioned_worker_owned_member(existing_member):
                    malformed_members += 1
                continue
            existing_worker_id, _existing_connection_id = decoded
            if not redis.exists(worker_lease_key(existing_worker_id)):
                dead_worker_pruned += int(redis.zrem(key, existing_member) or 0)

        active_count = int(redis.zcard(key) or 0)
        if active_count >= max(1, int(limit)):
            redis.expire(key, ttl * 2)
            return ConnectionCapacityDecision(
                False,
                active_count,
                expired_pruned,
                dead_worker_pruned,
                malformed_members,
            )
        redis.zadd(key, {member: now + ttl})
        redis.expire(key, ttl * 2)
        return ConnectionCapacityDecision(
            True,
            active_count + 1,
            expired_pruned,
            dead_worker_pruned,
            malformed_members,
        )
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WebSocketConnectionLimitUnavailable("WebSocket connection limiter unavailable") from exc


def refresh_connection_slot(
    redis,
    *,
    user_id: int,
    worker_id: str,
    connection_id: str,
    ttl_seconds: int,
    now_ts: float | None = None,
) -> bool:
    now = float(time.time() if now_ts is None else now_ts)
    ttl = max(2, int(ttl_seconds))
    key = _connection_key(user_id)
    member = encode_worker_owned_member(worker_id, connection_id)
    try:
        if hasattr(redis, "eval"):
            result = redis.eval(
                REFRESH_CONNECTION_SLOT_SCRIPT,
                1,
                key,
                member,
                str(ttl),
                str(ttl * 2),
            )
            return bool(int(result or 0))
        if redis.zscore(key, member) is None:
            return False
        redis.zadd(key, {member: now + ttl})
        redis.expire(key, ttl * 2)
        return True
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WebSocketConnectionLimitUnavailable("WebSocket connection limiter unavailable") from exc


def release_connection_slot(redis, *, user_id: int, worker_id: str, connection_id: str) -> None:
    key = _connection_key(user_id)
    member = encode_worker_owned_member(worker_id, connection_id)
    try:
        if hasattr(redis, "eval"):
            redis.eval(RELEASE_CONNECTION_SLOT_SCRIPT, 1, key, member)
            return
        redis.zrem(key, member)
        if int(redis.zcard(key) or 0) == 0:
            redis.delete(key)
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WebSocketConnectionLimitUnavailable("WebSocket connection limiter unavailable") from exc
