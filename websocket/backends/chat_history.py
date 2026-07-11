"""Chat history backend - Redis-backed storage for world chat messages."""

from __future__ import annotations

import json
import logging
from enum import StrEnum

from redis.exceptions import ResponseError

from core.utils.degradation import CHAT_HISTORY_DEGRADED, record_degradation
from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS
from websocket.exceptions import WorldChatInfrastructureError

logger = logging.getLogger(__name__)
LUA_FALLBACK_EXCEPTIONS: tuple[type[Exception], ...] = INFRASTRUCTURE_EXCEPTIONS + (AttributeError,)
MALFORMED_HISTORY_ENTRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    UnicodeDecodeError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
)

# Lua script for batch history trimming (O(1) per removed message vs O(n) round trips)
TRIM_HISTORY_SCRIPT = """
local key = KEYS[1]
local cutoff = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local removed = 0
while removed < limit do
    local tail = redis.call('LINDEX', key, -1)
    if not tail then break end
    local ok, msg = pcall(cjson.decode, tail)
    if not ok then
        redis.call('RPOP', key)
        removed = removed + 1
    elseif msg.ts and tonumber(msg.ts) < cutoff then
        redis.call('RPOP', key)
        removed = removed + 1
    else
        break
    end
end
return removed
"""

APPEND_HISTORY_WITH_DELIVERY_MARKER_SCRIPT = """
local history_key = KEYS[1]
local delivery_marker_key = KEYS[2]
local payload = ARGV[1]
local history_limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local stage = redis.call('GET', delivery_marker_key)

if stage then
    return stage
end

redis.call('LPUSH', history_key, payload)
redis.call('LTRIM', history_key, 0, math.max(0, history_limit - 1))
redis.call('EXPIRE', history_key, ttl)
redis.call('SET', delivery_marker_key, 'history')
return 'history'
"""


class WorldChatDeliveryStage(StrEnum):
    HISTORY = "history"
    BROADCASTED = "broadcasted"


def _now_ts() -> float:
    # Preserve test monkeypatching via `websocket.consumers.time.time`.
    from websocket.consumers import time as consumers_time

    return float(consumers_time.time())


def trim_history_by_time_fallback(
    cutoff_ms: int,
    redis,
    *,
    history_key: str,
    history_limit: int,
) -> None:
    """Python fallback for trimming history when Lua is unavailable."""
    for _ in range(int(history_limit)):
        raw_tail = redis.lindex(history_key, -1)
        if not raw_tail:
            return
        try:
            if isinstance(raw_tail, (bytes, bytearray)):
                raw_tail = raw_tail.decode("utf-8")
            msg = json.loads(raw_tail)
            ts = msg.get("ts") if isinstance(msg, dict) else None
            if isinstance(ts, (int, float)) and int(ts) >= int(cutoff_ms):
                return
        except MALFORMED_HISTORY_ENTRY_EXCEPTIONS as exc:
            logger.debug("Dropping corrupted world chat history tail entry: %s", exc)
        redis.rpop(history_key)


def trim_history_by_time_sync(
    cutoff_ms: int,
    redis,
    *,
    history_key: str,
    history_limit: int,
) -> None:
    """Trim expired messages from history using Lua script for O(1) performance."""
    try:
        redis.eval(TRIM_HISTORY_SCRIPT, 1, history_key, cutoff_ms, history_limit)
    except ResponseError:
        raise
    except LUA_FALLBACK_EXCEPTIONS as exc:
        # Fallback to Python-based trimming when Lua is unavailable (e.g., in tests)
        logger.debug("Lua script unavailable, using Python fallback: %s", exc)
        trim_history_by_time_fallback(cutoff_ms, redis, history_key=history_key, history_limit=history_limit)


def get_history_sync(
    redis,
    *,
    history_key: str,
    history_on_connect: int,
    history_limit: int,
    history_message_ttl_seconds: int,
    user_id: int | None,
) -> tuple[list[dict], bool]:
    """Fetch recent history from Redis.

    Returns a tuple of (messages, history_degraded).
    """
    cutoff_ms = int((_now_ts() - float(history_message_ttl_seconds)) * 1000)
    try:
        raw_items = redis.lrange(history_key, 0, max(0, history_on_connect - 1))
    except INFRASTRUCTURE_EXCEPTIONS:
        record_degradation(
            CHAT_HISTORY_DEGRADED,
            component="world_chat",
            detail="history Redis read failed",
            user_id=user_id,
        )
        return [], True

    messages: list[dict] = []
    for raw in reversed(raw_items or []):
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                continue
            ts = msg.get("ts")
            if isinstance(ts, (int, float)) and int(ts) < cutoff_ms:
                continue
            messages.append(msg)
        except MALFORMED_HISTORY_ENTRY_EXCEPTIONS as exc:
            logger.debug("Skipping malformed world chat history entry: %s", exc)
            continue

    try:
        trim_history_by_time_sync(cutoff_ms, redis, history_key=history_key, history_limit=history_limit)
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.debug("World chat history trim skipped due to Redis error: %s", exc)
    return messages, False


def _coerce_delivery_stage(raw_stage) -> WorldChatDeliveryStage:
    if isinstance(raw_stage, (bytes, bytearray)):
        raw_stage = raw_stage.decode("utf-8")
    try:
        return WorldChatDeliveryStage(str(raw_stage))
    except ValueError as exc:
        raise ResponseError(f"unexpected world chat delivery stage: {raw_stage!r}") from exc


def _append_history_fallback(
    payload: str,
    redis,
    *,
    history_key: str,
    delivery_marker_key: str,
    history_limit: int,
    history_message_ttl_seconds: int,
) -> WorldChatDeliveryStage:
    existing_stage = redis.get(delivery_marker_key)
    if existing_stage is not None:
        return _coerce_delivery_stage(existing_stage)
    pipe = redis.pipeline()
    pipe.lpush(history_key, payload)
    pipe.ltrim(history_key, 0, max(0, history_limit - 1))
    pipe.expire(history_key, int(history_message_ttl_seconds) + 60)
    pipe.execute()
    if not redis.set(delivery_marker_key, WorldChatDeliveryStage.HISTORY.value):
        raise WorldChatInfrastructureError("world chat delivery marker update failed")
    return WorldChatDeliveryStage.HISTORY


def append_history_sync(
    message: dict,
    redis,
    *,
    history_key: str,
    delivery_marker_key: str,
    history_limit: int,
    history_message_ttl_seconds: int,
) -> WorldChatDeliveryStage:
    """Append history once and return its durable delivery-marker stage."""
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    try:
        eval_func = getattr(redis, "eval", None)
        if eval_func is not None:
            stage = _coerce_delivery_stage(
                eval_func(
                    APPEND_HISTORY_WITH_DELIVERY_MARKER_SCRIPT,
                    2,
                    history_key,
                    delivery_marker_key,
                    payload,
                    history_limit,
                    int(history_message_ttl_seconds) + 60,
                )
            )
        else:
            stage = _append_history_fallback(
                payload,
                redis,
                history_key=history_key,
                delivery_marker_key=delivery_marker_key,
                history_limit=history_limit,
                history_message_ttl_seconds=history_message_ttl_seconds,
            )
    except ResponseError:
        raise
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("World chat history append failed; rejecting send: %s", exc)
        raise WorldChatInfrastructureError("world chat history backend unavailable") from exc
    return stage


def mark_delivery_broadcasted_sync(redis, *, delivery_marker_key: str) -> None:
    try:
        if not redis.set(
            delivery_marker_key,
            WorldChatDeliveryStage.BROADCASTED.value,
            xx=True,
            keepttl=True,
        ):
            raise WorldChatInfrastructureError("world chat delivery marker update failed")
    except ResponseError:
        raise
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WorldChatInfrastructureError("world chat delivery marker unavailable") from exc


def expire_delivery_marker_sync(
    redis,
    *,
    delivery_marker_key: str,
    ttl_seconds: int,
) -> None:
    try:
        if not redis.expire(delivery_marker_key, int(ttl_seconds)):
            raise WorldChatInfrastructureError("world chat delivery marker expiry failed")
    except ResponseError:
        raise
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WorldChatInfrastructureError("world chat delivery marker unavailable") from exc
