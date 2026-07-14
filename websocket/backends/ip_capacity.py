"""Redis-backed per-IP WebSocket handshake and connection capacity."""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from enum import Enum

from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from websocket.backends.worker_lease import (
    WORKER_KEY_PREFIX,
    decode_worker_owned_member,
    encode_worker_owned_member,
    is_versioned_worker_owned_member,
    worker_lease_key,
)
from websocket.exceptions import WebSocketConnectionLimitUnavailable


class IPCapacityResult(Enum):
    ACQUIRED = "acquired"
    CONNECTION_LIMITED = "connection_limited"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class IPCapacityDecision:
    result: IPCapacityResult
    active_count: int
    expired_pruned: int
    dead_worker_pruned: int
    malformed_members: int


ACQUIRE_IP_CAPACITY_SCRIPT = """
local connections_key = KEYS[1]
local throttle_key = KEYS[2]
local connection_limit = tonumber(ARGV[1])
local member = ARGV[2]
local slot_ttl = tonumber(ARGV[3])
local rate = tonumber(ARGV[4])
local burst = tonumber(ARGV[5])
local throttle_ttl = tonumber(ARGV[6])
local worker_key_prefix = ARGV[7]
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1]) + tonumber(redis_time[2]) / 1000000
local expired_pruned = redis.call('ZREMRANGEBYSCORE', connections_key, '-inf', now)
local dead_worker_pruned = 0
local malformed_members = 0

for _, existing_member in ipairs(redis.call('ZRANGE', connections_key, 0, -1)) do
  if string.sub(existing_member, 1, 3) == 'v2|' then
    local worker_id, connection_id = string.match(existing_member, '^v2|([0-9a-f]+)|(.+)$')
    if not worker_id or string.len(worker_id) ~= 32 or not connection_id then
      malformed_members = malformed_members + 1
    elseif redis.call('EXISTS', worker_key_prefix .. worker_id) == 0 then
      dead_worker_pruned = dead_worker_pruned + redis.call('ZREM', connections_key, existing_member)
    end
  end
end

local active_count = redis.call('ZCARD', connections_key)
local state = redis.call('HMGET', throttle_key, 'tokens', 'updated_at')
local tokens = tonumber(state[1])
local updated_at = tonumber(state[2])
if not tokens or not updated_at then
  tokens = burst
  updated_at = now
else
  tokens = math.min(burst, tokens + math.max(0, now - updated_at) * rate)
end

if tokens < 0.999999999 then
  redis.call('HSET', throttle_key, 'tokens', tokens, 'updated_at', now)
  redis.call('EXPIRE', throttle_key, throttle_ttl)
  return {-1, active_count, expired_pruned, dead_worker_pruned, malformed_members}
end

redis.call('HSET', throttle_key, 'tokens', math.max(0, tokens - 1), 'updated_at', now)
redis.call('EXPIRE', throttle_key, throttle_ttl)
if active_count >= connection_limit then
  redis.call('EXPIRE', connections_key, slot_ttl * 2)
  return {0, active_count, expired_pruned, dead_worker_pruned, malformed_members}
end
redis.call('ZADD', connections_key, now + slot_ttl, member)
redis.call('EXPIRE', connections_key, slot_ttl * 2)
return {1, active_count + 1, expired_pruned, dead_worker_pruned, malformed_members}
"""

REFRESH_IP_CAPACITY_SCRIPT = """
local connections_key = KEYS[1]
local member = ARGV[1]
local slot_ttl = tonumber(ARGV[2])
if not redis.call('ZSCORE', connections_key, member) then return 0 end
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1]) + tonumber(redis_time[2]) / 1000000
redis.call('ZADD', connections_key, now + slot_ttl, member)
redis.call('EXPIRE', connections_key, slot_ttl * 2)
return 1
"""

RELEASE_IP_CAPACITY_SCRIPT = """
local connections_key = KEYS[1]
local connection_id = ARGV[1]
local removed = redis.call('ZREM', connections_key, connection_id)
if redis.call('ZCARD', connections_key) == 0 then redis.call('DEL', connections_key) end
return removed
"""


def _capacity_keys(client_ip: str) -> tuple[str, str]:
    digest = hashlib.sha256(str(client_ip).encode("utf-8")).hexdigest()[:32]
    key_prefix = f"websocket:ip:{{{digest}}}"
    return f"{key_prefix}:connections", f"{key_prefix}:handshake"


def _as_float(value, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bytes):
        value = value.decode("ascii")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _result_from_status(value) -> IPCapacityResult:
    result = int(value or 0)
    if result > 0:
        return IPCapacityResult.ACQUIRED
    if result < 0:
        return IPCapacityResult.RATE_LIMITED
    return IPCapacityResult.CONNECTION_LIMITED


def _decision_from_redis(value) -> IPCapacityDecision:
    values = list(value or ())
    if len(values) != 5:
        raise ValueError(f"Unexpected WebSocket IP capacity decision: {value!r}")
    return IPCapacityDecision(
        result=_result_from_status(values[0]),
        active_count=int(values[1] or 0),
        expired_pruned=int(values[2] or 0),
        dead_worker_pruned=int(values[3] or 0),
        malformed_members=int(values[4] or 0),
    )


def acquire_ip_capacity(
    redis,
    *,
    client_ip: str,
    worker_id: str,
    connection_id: str,
    connection_limit: int,
    rate_per_second: int,
    burst: int,
    ttl_seconds: int,
    now_ts: float | None = None,
) -> IPCapacityDecision:
    now = float(time.time() if now_ts is None else now_ts)
    limit = max(1, int(connection_limit))
    rate = max(1, int(rate_per_second))
    capacity = max(1, int(burst))
    ttl = max(30, int(ttl_seconds))
    throttle_ttl = max(2, int(math.ceil(capacity / rate) * 2))
    connections_key, throttle_key = _capacity_keys(client_ip)
    member = encode_worker_owned_member(worker_id, connection_id)

    try:
        if hasattr(redis, "eval"):
            result = redis.eval(
                ACQUIRE_IP_CAPACITY_SCRIPT,
                2,
                connections_key,
                throttle_key,
                str(limit),
                member,
                str(ttl),
                str(rate),
                str(capacity),
                str(throttle_ttl),
                WORKER_KEY_PREFIX,
            )
            return _decision_from_redis(result)

        before_prune = int(redis.zcard(connections_key) or 0)
        redis.zremrangebyscore(connections_key, "-inf", now)
        expired_pruned = before_prune - int(redis.zcard(connections_key) or 0)
        dead_worker_pruned = 0
        malformed_members = 0
        for existing_member in redis.zrange(connections_key, 0, -1):
            decoded = decode_worker_owned_member(existing_member)
            if decoded is None:
                if is_versioned_worker_owned_member(existing_member):
                    malformed_members += 1
                continue
            existing_worker_id, _existing_connection_id = decoded
            if not redis.exists(worker_lease_key(existing_worker_id)):
                dead_worker_pruned += int(redis.zrem(connections_key, existing_member) or 0)

        active_count = int(redis.zcard(connections_key) or 0)
        tokens = _as_float(redis.hget(throttle_key, "tokens"), float(capacity))
        updated_at = _as_float(redis.hget(throttle_key, "updated_at"), now)
        tokens = min(float(capacity), tokens + max(0.0, now - updated_at) * rate)
        if tokens < 1.0 - 1e-9:
            redis.hset(throttle_key, mapping={"tokens": tokens, "updated_at": now})
            redis.expire(throttle_key, throttle_ttl)
            return IPCapacityDecision(
                IPCapacityResult.RATE_LIMITED,
                active_count,
                expired_pruned,
                dead_worker_pruned,
                malformed_members,
            )

        redis.hset(throttle_key, mapping={"tokens": max(0.0, tokens - 1.0), "updated_at": now})
        redis.expire(throttle_key, throttle_ttl)
        if active_count >= limit:
            redis.expire(connections_key, ttl * 2)
            return IPCapacityDecision(
                IPCapacityResult.CONNECTION_LIMITED,
                active_count,
                expired_pruned,
                dead_worker_pruned,
                malformed_members,
            )
        redis.zadd(connections_key, {member: now + ttl})
        redis.expire(connections_key, ttl * 2)
        return IPCapacityDecision(
            IPCapacityResult.ACQUIRED,
            active_count + 1,
            expired_pruned,
            dead_worker_pruned,
            malformed_members,
        )
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WebSocketConnectionLimitUnavailable("WebSocket IP capacity unavailable") from exc


def refresh_ip_capacity_slot(
    redis,
    *,
    client_ip: str,
    worker_id: str,
    connection_id: str,
    ttl_seconds: int,
    now_ts: float | None = None,
) -> bool:
    now = float(time.time() if now_ts is None else now_ts)
    ttl = max(30, int(ttl_seconds))
    connections_key, _ = _capacity_keys(client_ip)
    member = encode_worker_owned_member(worker_id, connection_id)
    try:
        if hasattr(redis, "eval"):
            result = redis.eval(
                REFRESH_IP_CAPACITY_SCRIPT,
                1,
                connections_key,
                member,
                str(ttl),
            )
            return bool(int(result or 0))
        if redis.zscore(connections_key, member) is None:
            return False
        redis.zadd(connections_key, {member: now + ttl})
        redis.expire(connections_key, ttl * 2)
        return True
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WebSocketConnectionLimitUnavailable("WebSocket IP capacity unavailable") from exc


def release_ip_capacity_slot(redis, *, client_ip: str, worker_id: str, connection_id: str) -> None:
    connections_key, _ = _capacity_keys(client_ip)
    member = encode_worker_owned_member(worker_id, connection_id)
    try:
        if hasattr(redis, "eval"):
            redis.eval(RELEASE_IP_CAPACITY_SCRIPT, 1, connections_key, member)
            return
        redis.zrem(connections_key, member)
        if int(redis.zcard(connections_key) or 0) == 0:
            redis.delete(connections_key)
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WebSocketConnectionLimitUnavailable("WebSocket IP capacity unavailable") from exc
