from __future__ import annotations

import json

import pytest
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError, ResponseError

from websocket.backends.chat_history import append_history_sync, get_history_sync
from websocket.backends.rate_limiter import rate_limit_sync
from websocket.consumers import WorldChatConsumer
from websocket.consumers.world_chat import WorldChatInfrastructureError

HISTORY_KEY = "chat:world:history"
HISTORY_LIMIT = 200
HISTORY_TTL_SECONDS = 24 * 60 * 60


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops: list[tuple] = []

    def lpush(self, key: str, value: str):
        self._ops.append(("lpush", key, value))
        return self

    def ltrim(self, key: str, start: int, end: int):
        self._ops.append(("ltrim", key, start, end))
        return self

    def expire(self, key: str, ttl: int):
        self._ops.append(("expire", key, ttl))
        return self

    def set(self, key: str, value: str):
        self._ops.append(("set", key, value))
        return self

    def execute(self):
        for op in self._ops:
            name = op[0]
            if name == "lpush":
                _, key, value = op
                self._redis.lpush(key, value)
            elif name == "ltrim":
                _, key, start, end = op
                self._redis.ltrim(key, start, end)
            elif name == "expire":
                _, key, ttl = op
                self._redis.expire(key, ttl)
            elif name == "set":
                _, key, value = op
                self._redis.set(key, value)
        return []


class _FakeRedis:
    def __init__(self):
        self._lists: dict[str, list[str]] = {}
        self._strings: dict[str, str] = {}
        self._expirations: dict[str, int] = {}
        self._counters: dict[str, int] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    def get(self, key: str):
        return self._strings.get(key)

    def set(
        self,
        key: str,
        value: str,
        *,
        xx: bool = False,
        keepttl: bool = False,
    ):
        if xx and key not in self._strings:
            return False
        self._strings[key] = value
        if not keepttl:
            self._expirations.pop(key, None)
        return True

    def lrange(self, key: str, start: int, end: int):
        items = list(self._lists.get(key, []))
        if not items:
            return []

        if end < 0:
            end = len(items) + end
        end = min(end, len(items) - 1)
        if start < 0:
            start = 0
        if start > end:
            return []
        return items[start : end + 1]

    def lindex(self, key: str, index: int):
        items = self._lists.get(key, [])
        if not items:
            return None
        return items[index]

    def rpop(self, key: str):
        items = self._lists.get(key, [])
        if not items:
            return None
        return items.pop()

    def lpush(self, key: str, value: str):
        self._lists.setdefault(key, []).insert(0, value)
        return len(self._lists[key])

    def ltrim(self, key: str, start: int, end: int):
        items = self._lists.get(key, [])
        if not items:
            return True
        if end < 0:
            end = len(items) + end
        end = min(end, len(items) - 1)
        self._lists[key] = items[start : end + 1]
        return True

    def expire(self, key: str, ttl: int):
        self._expirations[key] = ttl
        return True

    def pipeline(self):
        return _FakePipeline(self)

    def incr(self, key: str):
        self._counters[key] = int(self._counters.get(key, 0)) + 1
        return self._counters[key]

    def zadd(self, key: str, mapping: dict[object, float]):
        zset = self._zsets.setdefault(key, {})
        for member, score in mapping.items():
            zset[str(member)] = float(score)
        return len(mapping)

    def zcard(self, key: str):
        return len(self._zsets.get(key, {}))

    def zremrangebyscore(self, key: str, min_score, max_score):
        zset = self._zsets.get(key, {})
        lower = float("-inf") if min_score == "-inf" else float(min_score)
        upper = float(max_score)
        removed = [member for member, score in zset.items() if lower <= score <= upper]
        for member in removed:
            zset.pop(member, None)
        return len(removed)

    def zrange(self, key: str, start: int, end: int, withscores: bool = False):
        zset = self._zsets.get(key, {})
        items = sorted(zset.items(), key=lambda item: (item[1], item[0]))
        if not items:
            return []
        if end < 0:
            end = len(items) + end
        end = min(end, len(items) - 1)
        if start < 0:
            start = 0
        if start > end:
            return []
        sliced = items[start : end + 1]
        if withscores:
            return [(member, score) for member, score in sliced]
        return [member for member, _score in sliced]


def _build_consumer(fake_redis: _FakeRedis) -> WorldChatConsumer:
    consumer = WorldChatConsumer()
    object.__setattr__(consumer, "_get_redis", lambda: fake_redis)
    consumer.user_id = 1
    consumer.display_name = "u"
    return consumer


def test_world_chat_get_history_sync_returns_empty_on_redis_error(monkeypatch):
    class _BrokenRedis(_FakeRedis):
        def lrange(self, key: str, start: int, end: int):  # noqa: D401
            raise RedisError("down")

    messages, degraded = get_history_sync(
        _BrokenRedis(),
        history_key=HISTORY_KEY,
        history_on_connect=60,
        history_limit=HISTORY_LIMIT,
        history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        user_id=1,
    )

    assert messages == []
    assert degraded is True


def test_world_chat_get_history_sync_skips_old_and_malformed_entries(monkeypatch):
    fake = _FakeRedis()

    # Fix time so cutoff_ms is deterministic.
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 2000.0)
    cutoff_ms = int((2000.0 - 900) * 1000)

    recent = {"type": "message", "ts": cutoff_ms + 1000, "text": "new"}
    old = {"type": "message", "ts": cutoff_ms - 1000, "text": "old"}

    fake._lists[HISTORY_KEY] = [
        json.dumps(recent).encode("utf-8"),
        b"{bad json",
        json.dumps(old),
    ]

    messages, degraded = get_history_sync(
        fake,
        history_key=HISTORY_KEY,
        history_on_connect=10,
        history_limit=10,
        history_message_ttl_seconds=900,
        user_id=1,
    )

    assert messages == [recent]
    assert degraded is False
    # The internal trim should drop malformed/old tail entries.
    assert len(fake._lists[HISTORY_KEY]) == 1


def test_world_chat_get_history_sync_programming_error_bubbles_up(monkeypatch):
    fake = _FakeRedis()
    fake._lists[HISTORY_KEY] = [json.dumps({"type": "message", "ts": 1, "text": "ok"})]

    monkeypatch.setattr(
        "websocket.backends.chat_history.json.loads",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("history parse bug")),
    )

    with pytest.raises(RuntimeError, match="history parse bug"):
        get_history_sync(
            fake,
            history_key=HISTORY_KEY,
            history_on_connect=60,
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
            user_id=1,
        )


def test_world_chat_append_history_sync_pushes_and_trims(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 2000.0)

    cutoff_ms = int((2000.0 - 900) * 1000)
    msg1 = {"type": "message", "ts": cutoff_ms + 1, "text": "a"}
    msg2 = {"type": "message", "ts": cutoff_ms + 2, "text": "b"}
    msg3 = {"type": "message", "ts": cutoff_ms + 3, "text": "c"}

    for index, message in enumerate((msg1, msg2, msg3)):
        append_history_sync(
            message,
            fake,
            history_key=HISTORY_KEY,
            delivery_marker_key=f"chat:world:delivery:trim-{index}",
            history_limit=2,
            history_message_ttl_seconds=900,
        )

    assert len(fake._lists[HISTORY_KEY]) == 2
    # Newest first (LPUSH)
    assert json.loads(fake._lists[HISTORY_KEY][0])["text"] == "c"


def test_world_chat_delivery_marker_survives_history_eviction(monkeypatch):
    from websocket.backends.chat_history import WorldChatDeliveryStage

    fake = _FakeRedis()
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 2000.0)
    original = {
        "type": "message",
        "operation_id": "original-operation",
        "sender": {"id": 1},
        "ts": 1_999_000,
        "text": "original",
    }
    original_marker = "chat:world:delivery:original-message"

    assert (
        append_history_sync(
            original,
            fake,
            history_key=HISTORY_KEY,
            delivery_marker_key=original_marker,
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )
        is WorldChatDeliveryStage.HISTORY
    )

    for index in range(HISTORY_LIMIT + 1):
        assert (
            append_history_sync(
                {
                    "type": "message",
                    "operation_id": f"later-operation-{index}",
                    "sender": {"id": 2},
                    "ts": 1_999_001 + index,
                    "text": f"later-{index}",
                },
                fake,
                history_key=HISTORY_KEY,
                delivery_marker_key=f"chat:world:delivery:later-message-{index}",
                history_limit=HISTORY_LIMIT,
                history_message_ttl_seconds=HISTORY_TTL_SECONDS,
            )
            is WorldChatDeliveryStage.HISTORY
        )

    assert all(json.loads(raw)["text"] != "original" for raw in fake._lists[HISTORY_KEY])
    history_before_replay = list(fake._lists[HISTORY_KEY])

    assert (
        append_history_sync(
            original,
            fake,
            history_key=HISTORY_KEY,
            delivery_marker_key=original_marker,
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )
        is WorldChatDeliveryStage.HISTORY
    )
    assert fake._lists[HISTORY_KEY] == history_before_replay
    assert fake.get(original_marker) == "history"
    assert original_marker not in fake._expirations


def test_world_chat_broadcasted_marker_skips_history_write(monkeypatch):
    from websocket.backends.chat_history import WorldChatDeliveryStage

    fake = _FakeRedis()
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 2000.0)
    marker_key = "chat:world:delivery:already-broadcasted"
    fake.set(marker_key, "broadcasted")
    fake._lists[HISTORY_KEY] = [json.dumps({"type": "message", "ts": 1_999_000})]
    history_before = list(fake._lists[HISTORY_KEY])

    stage = append_history_sync(
        {"type": "message", "ts": 1_999_100, "text": "must not append"},
        fake,
        history_key=HISTORY_KEY,
        delivery_marker_key=marker_key,
        history_limit=HISTORY_LIMIT,
        history_message_ttl_seconds=HISTORY_TTL_SECONDS,
    )

    assert stage is WorldChatDeliveryStage.BROADCASTED
    assert fake._lists[HISTORY_KEY] == history_before


def test_world_chat_delivery_marker_ttl_is_explicitly_applied_after_broadcast(monkeypatch):
    from websocket.backends.chat_history import (
        WorldChatDeliveryStage,
        expire_delivery_marker_sync,
        mark_delivery_broadcasted_sync,
    )

    fake = _FakeRedis()
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 2000.0)
    marker_key = "chat:world:delivery:marker-lifecycle"

    assert (
        append_history_sync(
            {"type": "message", "ts": 1_999_000, "text": "lifecycle"},
            fake,
            history_key=HISTORY_KEY,
            delivery_marker_key=marker_key,
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )
        is WorldChatDeliveryStage.HISTORY
    )
    assert marker_key not in fake._expirations

    mark_delivery_broadcasted_sync(fake, delivery_marker_key=marker_key)
    assert fake.get(marker_key) == "broadcasted"
    assert marker_key not in fake._expirations

    expire_delivery_marker_sync(
        fake,
        delivery_marker_key=marker_key,
        ttl_seconds=HISTORY_TTL_SECONDS + 60,
    )
    assert fake._expirations[marker_key] == HISTORY_TTL_SECONDS + 60

    mark_delivery_broadcasted_sync(fake, delivery_marker_key=marker_key)
    assert fake._expirations[marker_key] == HISTORY_TTL_SECONDS + 60


def test_world_chat_mark_broadcasted_requires_existing_delivery_marker():
    from websocket.backends.chat_history import mark_delivery_broadcasted_sync

    fake = _FakeRedis()

    with pytest.raises(WorldChatInfrastructureError, match="marker update failed"):
        mark_delivery_broadcasted_sync(
            fake,
            delivery_marker_key="delivery:missing",
        )


def test_world_chat_append_does_not_fallback_for_internal_eval_attribute_error():
    internal_error = AttributeError("eval implementation bug")

    class _BrokenEvalRedis(_FakeRedis):
        def eval(self, *args, **kwargs):
            raise internal_error

        def pipeline(self):
            raise AssertionError("internal eval errors must not use the fallback")

    with pytest.raises(AttributeError) as exc_info:
        append_history_sync(
            {"type": "message", "ts": 1, "text": "hello"},
            _BrokenEvalRedis(),
            history_key=HISTORY_KEY,
            delivery_marker_key="delivery:eval-attribute-error",
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )

    assert exc_info.value is internal_error


def test_world_chat_append_history_sync_is_idempotent_by_delivery_marker(monkeypatch):
    from websocket.backends.chat_history import WorldChatDeliveryStage, append_history_sync

    fake = _FakeRedis()
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 2000.0)
    first = {
        "type": "message",
        "operation_id": "op-1",
        "sender": {"id": 1},
        "ts": 1_999_000,
        "text": "first",
    }
    conflicting_replay = {
        "type": "message",
        "operation_id": "op-1",
        "sender": {"id": 1},
        "ts": 1_999_001,
        "text": "must not replace first",
    }
    other_sender = {
        "type": "message",
        "operation_id": "op-1",
        "sender": {"id": 2},
        "ts": 1_999_002,
        "text": "other sender",
    }
    second = {
        "type": "message",
        "operation_id": "op-2",
        "sender": {"id": 1},
        "ts": 1_999_003,
        "text": "second",
    }

    assert (
        append_history_sync(
            first,
            fake,
            history_key="history",
            delivery_marker_key="delivery:first",
            history_limit=200,
            history_message_ttl_seconds=900,
        )
        is WorldChatDeliveryStage.HISTORY
    )
    assert (
        append_history_sync(
            conflicting_replay,
            fake,
            history_key="history",
            delivery_marker_key="delivery:first",
            history_limit=200,
            history_message_ttl_seconds=900,
        )
        is WorldChatDeliveryStage.HISTORY
    )
    assert (
        append_history_sync(
            other_sender,
            fake,
            history_key="history",
            delivery_marker_key="delivery:other-sender",
            history_limit=200,
            history_message_ttl_seconds=900,
        )
        is WorldChatDeliveryStage.HISTORY
    )
    assert (
        append_history_sync(
            second,
            fake,
            history_key="history",
            delivery_marker_key="delivery:second",
            history_limit=200,
            history_message_ttl_seconds=900,
        )
        is WorldChatDeliveryStage.HISTORY
    )

    stored = [json.loads(raw) for raw in fake._lists["history"]]
    assert [(entry["sender"]["id"], entry["operation_id"]) for entry in stored] == [
        (1, "op-2"),
        (2, "op-1"),
        (1, "op-1"),
    ]
    assert stored[2]["text"] == "first"


def test_world_chat_append_history_sync_uses_one_atomic_lua_delivery_gate(monkeypatch):
    from websocket.backends.chat_history import (
        APPEND_HISTORY_WITH_DELIVERY_MARKER_SCRIPT,
        WorldChatDeliveryStage,
        append_history_sync,
    )

    class _LuaRedis:
        def __init__(self):
            self.calls = []

        def eval(self, *args):
            self.calls.append(args)
            return b"history"

    fake = _LuaRedis()
    message = {
        "type": "message",
        "operation_id": "op-lua",
        "sender": {"id": 7},
        "ts": 1_999_000,
        "text": "atomic",
    }

    assert (
        append_history_sync(
            message,
            fake,
            history_key="history",
            delivery_marker_key="delivery:lua",
            history_limit=200,
            history_message_ttl_seconds=900,
        )
        is WorldChatDeliveryStage.HISTORY
    )
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == APPEND_HISTORY_WITH_DELIVERY_MARKER_SCRIPT
    assert fake.calls[0][1:4] == (2, "history", "delivery:lua")


def test_world_chat_append_history_sync_operation_gate_raises_stable_infrastructure_error():
    from websocket.backends.chat_history import append_history_sync

    class _BrokenRedis(_FakeRedis):
        def eval(self, *args, **kwargs):
            raise RedisError("gate down")

    with pytest.raises(WorldChatInfrastructureError, match="history backend unavailable"):
        append_history_sync(
            {"type": "message", "operation_id": "op-broken", "ts": 1, "text": "hello"},
            _BrokenRedis(),
            history_key="history",
            delivery_marker_key="delivery:broken",
            history_limit=200,
            history_message_ttl_seconds=900,
        )


def test_world_chat_append_history_sync_response_error_bubbles_unchanged():
    from websocket.backends.chat_history import append_history_sync

    response_error = ResponseError("bad append lua")

    class _BrokenRedis(_FakeRedis):
        def eval(self, *args, **kwargs):
            raise response_error

    with pytest.raises(ResponseError) as exc_info:
        append_history_sync(
            {
                "type": "message",
                "operation_id": "op-response",
                "sender": {"id": 1},
                "ts": 1,
                "text": "hello",
            },
            _BrokenRedis(),
            history_key="history",
            delivery_marker_key="delivery:response",
            history_limit=200,
            history_message_ttl_seconds=900,
        )

    assert exc_info.value is response_error


def test_world_chat_trim_history_response_error_bubbles_unchanged():
    from websocket.backends.chat_history import trim_history_by_time_sync

    response_error = ResponseError("bad trim lua")

    class _BrokenRedis(_FakeRedis):
        def eval(self, *args, **kwargs):
            raise response_error

    with pytest.raises(ResponseError) as exc_info:
        trim_history_by_time_sync(1000, _BrokenRedis(), history_key="history", history_limit=200)

    assert exc_info.value is response_error


def test_world_chat_append_history_sync_raises_infrastructure_error_on_cache_failure(monkeypatch):
    class _BrokenRedis(_FakeRedis):
        def pipeline(self):
            raise ConnectionInterrupted("cache down")

    with pytest.raises(WorldChatInfrastructureError, match="history backend unavailable"):
        append_history_sync(
            {"type": "message", "ts": 1, "text": "hello"},
            _BrokenRedis(),
            history_key=HISTORY_KEY,
            delivery_marker_key="delivery:cache-failure",
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )


def test_world_chat_append_fallback_does_not_mark_history_when_list_write_fails():
    marker_key = "delivery:wrong-history-type"
    response_error = ResponseError("WRONGTYPE history is not a list")

    class _WrongTypeRedis(_FakeRedis):
        def lpush(self, key: str, value: str):
            raise response_error

    fake = _WrongTypeRedis()

    with pytest.raises(ResponseError) as exc_info:
        append_history_sync(
            {"type": "message", "ts": 1, "text": "hello"},
            fake,
            history_key=HISTORY_KEY,
            delivery_marker_key=marker_key,
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )

    assert exc_info.value is response_error
    assert fake.get(marker_key) is None


def test_world_chat_append_fallback_rejects_failed_marker_write():
    marker_key = "delivery:marker-write-rejected"

    class _MarkerWriteRejectedRedis(_FakeRedis):
        def set(self, key: str, value: str):
            return False

    fake = _MarkerWriteRejectedRedis()

    with pytest.raises(WorldChatInfrastructureError, match="marker update failed"):
        append_history_sync(
            {"type": "message", "ts": 1, "text": "hello"},
            fake,
            history_key=HISTORY_KEY,
            delivery_marker_key=marker_key,
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )


def test_world_chat_append_history_sync_runtime_marker_bubbles_up(monkeypatch):
    class _BrokenRedis(_FakeRedis):
        def pipeline(self):
            raise RuntimeError("cache down")

    with pytest.raises(RuntimeError, match="cache down"):
        append_history_sync(
            {"type": "message", "ts": 1, "text": "hello"},
            _BrokenRedis(),
            history_key=HISTORY_KEY,
            delivery_marker_key="delivery:runtime-failure",
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )


def test_world_chat_append_history_sync_rejects_unknown_delivery_stage():
    fake = _FakeRedis()
    marker_key = "delivery:unknown-stage"
    fake.set(marker_key, "corrupt")

    with pytest.raises(ResponseError, match="unexpected world chat delivery stage"):
        append_history_sync(
            {"type": "message", "ts": 1, "text": "hello"},
            fake,
            history_key=HISTORY_KEY,
            delivery_marker_key=marker_key,
            history_limit=HISTORY_LIMIT,
            history_message_ttl_seconds=HISTORY_TTL_SECONDS,
        )


def test_world_chat_rate_limit_sync_handles_no_user_id():
    allowed, retry_after = rate_limit_sync(
        None,
        _FakeRedis(),
        rate_limit_window_seconds=8,
        rate_limit_max_messages=6,
    )

    assert allowed is False
    assert retry_after == 3


def test_world_chat_rate_limit_sync_raises_when_redis_errors(monkeypatch):
    class _BrokenRedis(_FakeRedis):
        def eval(self, *args, **kwargs):
            raise RedisError("down")

    try:
        rate_limit_sync(
            1,
            _BrokenRedis(),
            rate_limit_window_seconds=8,
            rate_limit_max_messages=6,
        )
    except WorldChatInfrastructureError as exc:
        assert "rate limit backend unavailable" in str(exc)
    else:  # pragma: no cover - defensive failure path
        raise AssertionError("expected WorldChatInfrastructureError when Redis is unavailable")


def test_world_chat_rate_limit_sync_rejects_after_limit(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 2000.0)

    def _rate_limit():
        return rate_limit_sync(
            1,
            fake,
            rate_limit_window_seconds=8,
            rate_limit_max_messages=2,
        )

    assert _rate_limit() == (True, None)
    assert _rate_limit() == (True, None)
    assert _rate_limit() == (False, 8)


def test_world_chat_rate_limit_sync_blocks_burst_across_sliding_window_boundary(monkeypatch):
    fake = _FakeRedis()

    def _rate_limit():
        return rate_limit_sync(
            1,
            fake,
            rate_limit_window_seconds=8,
            rate_limit_max_messages=2,
        )

    # Exhaust quota near a nominal bucket boundary.
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 15.999)
    assert _rate_limit() == (True, None)
    assert _rate_limit() == (True, None)

    # Sliding-window throttling keeps the previous sends inside the last 8s window,
    # so the quota is still exhausted immediately after t=16.0.
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 16.0)
    allowed, retry_after = _rate_limit()
    assert allowed is False
    assert retry_after == 8

    # Once the oldest send has fallen out of the 8s sliding window, sends are
    # admitted again.
    monkeypatch.setattr("websocket.consumers.time.time", lambda: 24.0)
    assert _rate_limit() == (True, None)


def test_world_chat_get_display_name_tolerates_cache_errors(monkeypatch):
    consumer = _build_consumer(_FakeRedis())

    def _raise_cache_error(*_args, **_kwargs):
        raise ConnectionInterrupted("cache down")

    monkeypatch.setattr("websocket.consumers.world_chat.cache.get", _raise_cache_error)
    monkeypatch.setattr("websocket.consumers.world_chat.cache.set", _raise_cache_error)

    class _FakeUser:
        def __init__(self):
            self.manor = type("_Manor", (), {"display_name": "测试庄园"})()
            self.username = "tester"

        def get_full_name(self):
            return ""

    class _FakeManager:
        def select_related(self, *_args, **_kwargs):
            return self

        def get(self, **_kwargs):
            return _FakeUser()

    class _FakeUserModel:
        DoesNotExist = type("DoesNotExist", (Exception,), {})
        objects = _FakeManager()

    monkeypatch.setattr("websocket.consumers.world_chat.User", _FakeUserModel)

    resolved = consumer._get_display_name.__wrapped__(consumer, 1)
    assert resolved == "测试庄园"


def test_world_chat_get_display_name_runtime_marker_cache_error_bubbles_up(monkeypatch):
    consumer = _build_consumer(_FakeRedis())

    monkeypatch.setattr(
        "websocket.consumers.world_chat.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        consumer._get_display_name.__wrapped__(consumer, 1)
