from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.db import DatabaseError, ProgrammingError
from django.test import SimpleTestCase

from core.exceptions import InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate, WorldChatSendAttempt
from gameplay.services.manor.bootstrap import ManorNotFoundError
from gameplay.services.manor.core import ensure_manor
from gameplay.services.world_chat_delivery import WorldChatOperationConflictError, WorldChatValidationError
from websocket.consumers import WorldChatConsumer
from websocket.consumers.session_guard import WebSocketSessionValidationResult
from websocket.consumers.world_chat import WorldChatInfrastructureError


class WorldChatConsumerTests(SimpleTestCase):
    def test_history_ttl_is_24_hours(self):
        self.assertEqual(WorldChatConsumer.HISTORY_MESSAGE_TTL_SECONDS, 24 * 60 * 60)

    def _build_consumer(self) -> WorldChatConsumer:
        consumer = WorldChatConsumer()
        consumer.user_id = 1
        consumer.display_name = "玩家A"
        consumer.channel_name = "test-channel"
        consumer.channel_layer = AsyncMock()
        consumer.send_json = AsyncMock()
        consumer.close = AsyncMock()
        object.__setattr__(
            consumer,
            "_ensure_valid_session",
            AsyncMock(return_value=WebSocketSessionValidationResult.VALID),
        )
        return consumer

    def test_chat_message_forwards_expected_fields(self):
        consumer = WorldChatConsumer()
        consumer.send_json = AsyncMock()

        event = {
            "payload": {
                "type": "message",
                "channel": "world",
                "id": 123,
                "operation_id": "18ec9e6a-fefd-4a29-8a26-cba7d87e14f2",
                "ts": 1700000000000,
                "sender": {"id": 7, "name": "玩家A"},
                "text": "hello",
            }
        }

        asyncio.run(consumer.chat_message(event))

        consumer.send_json.assert_awaited_once_with(
            {
                "type": "message",
                "channel": "world",
                "id": 123,
                "operation_id": "18ec9e6a-fefd-4a29-8a26-cba7d87e14f2",
                "ts": 1700000000000,
                "sender": {"id": 7, "name": "玩家A"},
                "text": "hello",
            }
        )

    def test_chat_message_supports_legacy_keys(self):
        consumer = WorldChatConsumer()
        consumer.send_json = AsyncMock()

        event = {
            "payload": {
                "type": "message",
                "message": "legacy",
                "timestamp": 1700000000001,
                "sender": {"id": 8, "name": "玩家B"},
            }
        }

        asyncio.run(consumer.chat_message(event))

        payload = consumer.send_json.await_args.args[0]
        self.assertEqual(payload["text"], "legacy")
        self.assertEqual(payload["ts"], 1700000000001)

    def test_normalize_text_escapes_and_removes_controls(self):
        from websocket.services.message_builder import normalize_text

        raw = "<b>hi</b>\x01\n\n\n\nworld  "
        normalized = normalize_text(raw)
        self.assertEqual(normalized, "&lt;b&gt;hi&lt;/b&gt;\n\n\nworld")

    def test_receive_json_ping_returns_pong(self):
        consumer = self._build_consumer()

        asyncio.run(consumer.receive_json({"type": "ping"}))

        consumer.send_json.assert_awaited_once_with({"type": "pong"})

    def test_receive_json_ping_skips_session_revalidation(self):
        consumer = self._build_consumer()
        consumer._ensure_valid_session = AsyncMock(side_effect=AssertionError("should not validate ping"))

        asyncio.run(consumer.receive_json({"type": "ping"}))

        consumer.send_json.assert_awaited_once_with({"type": "pong"})

    def test_receive_json_rejects_non_string_text(self):
        consumer = self._build_consumer()
        operation_id = str(uuid.uuid4())

        asyncio.run(consumer.receive_json({"type": "send", "text": {"bad": True}, "operation_id": operation_id}))

        consumer.send_json.assert_awaited_once_with(
            {
                "type": "error",
                "code": "invalid_text",
                "message": "消息格式错误",
                "operation_id": operation_id,
            }
        )

    def test_receive_json_rejects_invalid_operation_id_before_rate_limit(self):
        consumer = self._build_consumer()
        consumer._rate_limit = AsyncMock(side_effect=AssertionError("rate limit should not run"))

        asyncio.run(consumer.receive_json({"type": "send", "text": "hello", "operation_id": "bad"}))

        consumer.send_json.assert_awaited_once_with(
            {"type": "error", "code": "invalid_operation_id", "message": "消息格式错误"}
        )

    def test_receive_json_rate_limited_short_circuits(self):
        consumer = self._build_consumer()
        consumer._rate_limit = AsyncMock(return_value=(False, 8))
        consumer._create_world_chat_attempt = AsyncMock()
        operation_id = str(uuid.uuid4())

        asyncio.run(consumer.receive_json({"type": "send", "text": "hello", "operation_id": operation_id}))

        payload = consumer.send_json.await_args.args[0]
        self.assertEqual(payload["code"], "rate_limited")
        self.assertEqual(payload["operation_id"], operation_id)
        self.assertEqual(payload["message"], "发送太快，请 8 秒后再试")
        consumer._create_world_chat_attempt.assert_not_awaited()

    def test_receive_json_rate_limit_backend_failure_returns_chat_unavailable(self):
        consumer = self._build_consumer()
        consumer._rate_limit = AsyncMock(side_effect=WorldChatInfrastructureError("redis down"))
        consumer._create_world_chat_attempt = AsyncMock()
        operation_id = str(uuid.uuid4())

        asyncio.run(consumer.receive_json({"type": "send", "text": "hello", "operation_id": operation_id}))

        consumer.send_json.assert_awaited_once_with(
            {
                "type": "error",
                "code": "chat_unavailable",
                "message": consumer.CHAT_UNAVAILABLE_MESSAGE,
                "operation_id": operation_id,
            }
        )
        consumer._create_world_chat_attempt.assert_not_awaited()

    def test_receive_json_queues_raw_text_and_returns_ack_without_immediate_publish(self):
        consumer = self._build_consumer()
        consumer._rate_limit = AsyncMock(return_value=(True, None))
        consumer._create_world_chat_attempt = AsyncMock(
            return_value={"operation_id": "unused", "status": "queued", "created": True}
        )
        operation_id = str(uuid.uuid4())
        raw_text = "  <b>single escape</b>  "

        for legacy_method in (
            "_normalize_text",
            "_consume_trumpet",
            "_refund_trumpet",
            "_trim_history_by_time_sync",
            "_trim_history_by_time_fallback",
            "_append_history_sync",
            "_append_history",
            "_remove_history_sync",
            "_remove_history_compensation",
            "_compensate_failed_publish",
            "_next_id_sync",
            "_build_message",
        ):
            self.assertFalse(hasattr(WorldChatConsumer, legacy_method), legacy_method)

        asyncio.run(consumer.receive_json({"type": "send", "text": raw_text, "operation_id": operation_id}))

        consumer._create_world_chat_attempt.assert_awaited_once_with(
            operation_id=operation_id,
            raw_text=raw_text,
        )
        consumer.send_json.assert_awaited_once_with(
            {"type": "send_ack", "operation_id": operation_id, "status": "queued", "created": True}
        )
        consumer.channel_layer.group_send.assert_not_awaited()

    def test_receive_json_send_reuses_dispatch_session_validation(self):
        consumer = self._build_consumer()
        consumer._single_session_checked_by_dispatch = True
        consumer._ensure_valid_session = AsyncMock(side_effect=AssertionError("should reuse dispatch validation"))
        consumer._rate_limit = AsyncMock(return_value=(True, None))
        operation_id = str(uuid.uuid4())
        consumer._create_world_chat_attempt = AsyncMock(
            return_value={"operation_id": operation_id, "status": "queued", "created": True}
        )

        asyncio.run(consumer.receive_json({"type": "send", "text": "hello", "operation_id": operation_id}))

        consumer.send_json.assert_awaited_once()

    def test_receive_json_terminal_replay_returns_actual_status_without_publish(self):
        consumer = self._build_consumer()
        consumer._rate_limit = AsyncMock(return_value=(True, None))
        operation_id = str(uuid.uuid4())
        consumer._create_world_chat_attempt = AsyncMock(
            return_value={"operation_id": operation_id, "status": "published", "created": False}
        )

        asyncio.run(consumer.receive_json({"type": "send", "text": "hello", "operation_id": operation_id}))

        consumer.send_json.assert_awaited_once_with(
            {"type": "send_ack", "operation_id": operation_id, "status": "published", "created": False}
        )

    def test_receive_json_maps_durable_create_business_errors(self):
        consumer = self._build_consumer()
        consumer._rate_limit = AsyncMock(return_value=(True, None))
        operation_id = str(uuid.uuid4())
        cases = [
            (WorldChatValidationError("bad text"), "invalid_text"),
            (WorldChatOperationConflictError(), "operation_conflict"),
            (ManorNotFoundError(), "manor_not_found"),
            (InsufficientStockError("小喇叭", 1, 0), "no_trumpet"),
            (DatabaseError("db down"), "chat_unavailable"),
        ]
        for error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                consumer.send_json.reset_mock()
                consumer._create_world_chat_attempt = AsyncMock(side_effect=error)
                asyncio.run(consumer.receive_json({"type": "send", "text": "hello", "operation_id": operation_id}))
                payload = consumer.send_json.await_args.args[0]
                self.assertEqual(payload["code"], expected_code)
                self.assertEqual(payload["operation_id"], operation_id)

    def test_receive_json_programming_errors_bubble_without_conversion(self):
        for error in (RuntimeError("consumer bug"), ProgrammingError("bad query")):
            with self.subTest(error=type(error).__name__):
                consumer = self._build_consumer()
                consumer._rate_limit = AsyncMock(return_value=(True, None))
                consumer._create_world_chat_attempt = AsyncMock(side_effect=error)
                operation_id = str(uuid.uuid4())

                with self.assertRaises(type(error)) as exc_info:
                    asyncio.run(consumer.receive_json({"type": "send", "text": "hello", "operation_id": operation_id}))

                self.assertIs(exc_info.exception, error)

    def test_connect_reports_history_degraded_status(self):
        consumer = WorldChatConsumer()
        consumer.scope = {"user": SimpleNamespace(is_authenticated=True, id=7)}
        consumer.channel_name = "test-channel"
        consumer.channel_layer = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer.send_json = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.VALID)
        consumer._get_display_name = AsyncMock(return_value="玩家A")
        consumer._get_history = AsyncMock(return_value=[])
        consumer._history_degraded = True

        asyncio.run(consumer.connect())

        consumer.channel_layer.group_add.assert_awaited_once_with(consumer.GROUP_NAME, consumer.channel_name)
        consumer.accept.assert_awaited_once_with()
        self.assertEqual(consumer.send_json.await_count, 2)
        history_payload = consumer.send_json.await_args_list[0].args[0]
        status_payload = consumer.send_json.await_args_list[1].args[0]
        self.assertEqual(history_payload["type"], "history")
        self.assertTrue(status_payload["history_degraded"])
        self.assertEqual(status_payload["history_status_message"], consumer.HISTORY_UNAVAILABLE_MESSAGE)

    def test_connect_closes_when_single_session_is_invalid(self):
        consumer = WorldChatConsumer()
        consumer.scope = {"user": SimpleNamespace(is_authenticated=True, id=7)}
        consumer.channel_layer = AsyncMock()
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.INVALID)

        asyncio.run(consumer.connect())

        consumer.close.assert_awaited_once_with()
        consumer.accept.assert_not_awaited()


@pytest.mark.parametrize(
    "programming_error",
    [
        pytest.param(TypeError("send type bug"), id="type-error"),
        pytest.param(ValueError("send value bug"), id="value-error"),
    ],
)
def test_receive_json_does_not_convert_send_programming_errors(programming_error):
    consumer = WorldChatConsumer()
    consumer._single_session_checked_by_dispatch = True
    consumer._process_send_message = AsyncMock(side_effect=programming_error)
    consumer.send_json = AsyncMock()

    with pytest.raises(type(programming_error)) as exc_info:
        asyncio.run(consumer.receive_json({"type": "send", "text": "hello", "operation_id": str(uuid.uuid4())}))

    assert exc_info.value is programming_error
    consumer.send_json.assert_not_awaited()


@pytest.mark.parametrize("content", [[], "send", 123])
def test_receive_json_rejects_non_object_payload(content):
    consumer = WorldChatConsumer()
    consumer.send_json = AsyncMock()

    asyncio.run(consumer.receive_json(content))

    consumer.send_json.assert_awaited_once_with({"type": "error", "code": "invalid_payload", "message": "消息格式错误"})


def test_world_chat_infrastructure_error_has_neutral_owner():
    from gameplay.services import world_chat_delivery
    from websocket import exceptions
    from websocket.consumers import world_chat

    assert world_chat_delivery.WorldChatInfrastructureError is exceptions.WorldChatInfrastructureError
    assert world_chat.WorldChatInfrastructureError is exceptions.WorldChatInfrastructureError


def test_world_chat_legacy_support_and_backend_apis_are_removed():
    from websocket.backends import chat_history
    from websocket.consumers import world_chat_support
    from websocket.services import message_builder

    for legacy_wrapper in (
        "append_history_sync_for_consumer",
        "trim_history_by_time_sync_for_consumer",
        "trim_history_by_time_fallback_for_consumer",
        "remove_history_sync_for_consumer",
        "next_id_sync_for_consumer",
        "build_message_sync_for_consumer",
        "remove_history_compensation",
    ):
        assert not hasattr(world_chat_support, legacy_wrapper), legacy_wrapper
    assert not hasattr(chat_history, "remove_history_sync")
    assert not hasattr(message_builder, "next_id_sync")
    assert not hasattr(message_builder, "build_message_sync")


@pytest.mark.django_db(transaction=True)
def test_broker_dispatch_failure_still_acks_queued_and_leaves_durable_pending(
    monkeypatch,
    user_factory,
):
    user = user_factory()
    manor = ensure_manor(user)
    trumpet_template, _created = ItemTemplate.objects.get_or_create(
        key="small_trumpet",
        defaults={"name": "小喇叭"},
    )
    InventoryItem.objects.create(
        manor=manor,
        template=trumpet_template,
        quantity=2,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    dispatched = []
    monkeypatch.setattr(
        "websocket.consumers.world_chat.safe_apply_async",
        lambda task, **kwargs: dispatched.append((task, kwargs)) or False,
        raising=False,
    )
    consumer = WorldChatConsumer()
    consumer.user_id = user.id
    consumer.display_name = manor.display_name
    consumer.channel_name = "test-channel"
    consumer.channel_layer = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer.close = AsyncMock()
    consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.VALID)
    consumer._rate_limit = AsyncMock(return_value=(True, None))
    operation_id = str(uuid.uuid4())
    raw_text = "  <b>durable</b>  "

    ack = consumer._create_world_chat_attempt.__wrapped__(
        consumer,
        operation_id=operation_id,
        raw_text=raw_text,
    )
    consumer._create_world_chat_attempt = AsyncMock(return_value=ack)

    asyncio.run(consumer.receive_json({"type": "send", "text": raw_text, "operation_id": operation_id}))

    consumer.send_json.assert_awaited_once_with(
        {"type": "send_ack", "operation_id": operation_id, "status": "queued", "created": True}
    )
    attempt = WorldChatSendAttempt.objects.get(user=user, operation_id=operation_id)
    assert attempt.status == WorldChatSendAttempt.Status.PENDING
    assert attempt.text == "&lt;b&gt;durable&lt;/b&gt;"
    assert attempt.trumpet_consumed is True
    assert len(dispatched) == 1
