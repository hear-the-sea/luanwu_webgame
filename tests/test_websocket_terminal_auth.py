from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync
from channels.auth import AuthMiddlewareStack
from channels.layers import InMemoryChannelLayer
from channels.security.websocket import AllowedHostsOriginValidator
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from websocket.backends.ip_capacity import IPCapacityDecision, IPCapacityResult
from websocket.consumers import NotificationConsumer, OnlineStatsConsumer, WorldChatConsumer
from websocket.consumers.session_guard import WebSocketSessionValidationResult
from websocket.middleware.ip_capacity import WebSocketIPCapacityMiddleware


@pytest.mark.parametrize("consumer_class", [NotificationConsumer, OnlineStatsConsumer, WorldChatConsumer])
@pytest.mark.parametrize("authenticated,close_code", [(False, 4401), (True, 4403)])
def test_invalid_authentication_handshake_delivers_terminal_code_without_business_payload(
    consumer_class, authenticated, close_code
):
    async def scenario():
        communicator = WebsocketCommunicator(consumer_class.as_asgi(), "/ws/test/")
        communicator.scope["user"] = SimpleNamespace(id=7, is_authenticated=authenticated)
        with (
            patch.object(
                consumer_class,
                "_ensure_valid_session",
                AsyncMock(return_value=WebSocketSessionValidationResult.INVALID),
            ),
            patch.object(consumer_class, "connect", new_callable=AsyncMock) as connect,
            patch.object(consumer_class, "disconnect", new_callable=AsyncMock),
            patch.object(consumer_class, "send_json", new_callable=AsyncMock) as send_json,
            patch.object(consumer_class, "_guard_connection_capacity", new_callable=AsyncMock) as capacity,
            patch("channels.consumer.aclose_old_connections", new_callable=AsyncMock),
            patch("channels.generic.websocket.aclose_old_connections", new_callable=AsyncMock),
        ):
            try:
                connected, _ = await communicator.connect(timeout=1)
                assert connected is True
                assert await communicator.receive_output(timeout=1) == {"type": "websocket.close", "code": close_code}
                connect.assert_not_awaited()
                send_json.assert_not_awaited()
                capacity.assert_not_awaited()
            finally:
                await communicator.disconnect(code=close_code, timeout=1)
            assert communicator.future.done()

    async_to_sync(scenario)()


@pytest.mark.parametrize("consumer_class", [NotificationConsumer, OnlineStatsConsumer, WorldChatConsumer])
def test_active_invalid_session_closes_without_accepting_again(consumer_class):
    async def scenario():
        consumer = consumer_class()
        consumer.scope = {"user": SimpleNamespace(id=7, is_authenticated=True)}
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.INVALID)
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        assert await consumer._guard_single_session({"type": "websocket.receive"}) is False
        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once_with(code=4403)

    async_to_sync(scenario)()


@pytest.mark.parametrize("consumer_class", [NotificationConsumer, OnlineStatsConsumer, WorldChatConsumer])
@pytest.mark.parametrize("authenticated,close_code", [(False, 4401), (True, 4403)])
def test_real_rejected_consumer_lifecycle_releases_ip_slot_without_registering_user(
    consumer_class, authenticated, close_code
):
    async def scenario():
        instances = []

        class ObservedConsumer(consumer_class):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                instances.append(self)

        layer = InMemoryChannelLayer()
        layer.group_add = AsyncMock(wraps=layer.group_add)
        layer.group_send = AsyncMock(wraps=layer.group_send)
        middleware = WebSocketIPCapacityMiddleware(ObservedConsumer.as_asgi())
        middleware._worker_lease_manager = AsyncMock()
        middleware._worker_lease_manager.ensure_started.return_value = "a" * 32
        active_ip_slots = set()

        async def acquire_ip(client_ip, connection_id, worker_id):
            active_ip_slots.add((client_ip, connection_id, worker_id))
            return IPCapacityDecision(IPCapacityResult.ACQUIRED, 1, 0, 0, 0)

        async def release_ip(client_ip, connection_id, worker_id):
            active_ip_slots.remove((client_ip, connection_id, worker_id))

        def inline_database_call(func, *, thread_sensitive):
            async def call(*args, **kwargs):
                return func(*args, **kwargs)

            return call

        communicator = WebsocketCommunicator(middleware, "/ws/test/")
        communicator.scope.update(
            user=SimpleNamespace(id=7, is_authenticated=authenticated),
            client=("203.0.113.8", 53100),
        )
        with (
            patch("channels.consumer.get_channel_layer", return_value=layer),
            patch("channels.consumer.aclose_old_connections", new_callable=AsyncMock),
            patch("channels.generic.websocket.aclose_old_connections", new_callable=AsyncMock),
            patch("channels.db.close_old_connections"),
            patch("websocket.consumers.session_guard.database_sync_to_async", inline_database_call),
            patch("websocket.consumers.online_stats.sync_to_async", inline_database_call),
            patch("websocket.consumers.session_guard.is_websocket_session_valid", return_value=False),
            patch.object(middleware, "_acquire_capacity", AsyncMock(side_effect=acquire_ip)),
            patch.object(middleware, "_release_capacity", AsyncMock(side_effect=release_ip)) as release,
            patch.object(ObservedConsumer, "_acquire_connection_slot_sync") as acquire_user,
            patch.object(ObservedConsumer, "_release_connection_slot_sync") as release_user,
            patch.object(OnlineStatsConsumer, "_add_online_connection_sync") as add_presence,
            patch.object(OnlineStatsConsumer, "_remove_online_connection_sync") as remove_presence,
            patch.object(OnlineStatsConsumer, "_get_online_count_sync", return_value=0) as read_online,
            patch.object(OnlineStatsConsumer, "_acquire_broadcast_debounce", AsyncMock(return_value=True)),
            patch.object(OnlineStatsConsumer, "get_total_users", AsyncMock(return_value=0)) as read_total,
        ):
            try:
                connected, _ = await communicator.connect(timeout=1)
                assert connected is True
                assert await communicator.receive_output(timeout=1) == {"type": "websocket.close", "code": close_code}
                assert len(active_ip_slots) == 1
                layer.group_add.assert_not_awaited()
                read_online.assert_not_called()
                read_total.assert_not_called()
            finally:
                await communicator.disconnect(code=close_code, timeout=1)

            acquire_user.assert_not_called()
            release_user.assert_not_called()
            add_presence.assert_not_called()
            remove_presence.assert_not_called()
            layer.group_add.assert_not_awaited()
            assert layer.groups == {}
            assert active_ip_slots == set()
            release.assert_awaited_once()
            assert communicator.future.done()
            assert not communicator.future.cancelled()
            assert len(instances) == 1
            consumer = instances[0]
            assert consumer._connection_slot_acquired is False
            assert consumer._connection_slot_heartbeat_task is None
            assert consumer._connection_slot_worker_id is None
            assert consumer._connection_capacity_state is None
            assert not any(
                "WebSocketIPCapacityMiddleware._run_slot_heartbeat" in task.get_coro().__qualname__
                for task in asyncio.all_tasks()
            )
            if consumer_class is OnlineStatsConsumer:
                # 原有断开流程仍读取并广播统计，但未注册在线人数或发送到被拒绝的连接。
                assert consumer.heartbeat_task is None
                read_online.assert_called_once()
                read_total.assert_called_once()
                layer.group_send.assert_awaited_once()
            else:
                read_online.assert_not_called()
                read_total.assert_not_called()
                layer.group_send.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("headers", [[(b"origin", b"https://game.example.test.attacker.invalid")], []])
@override_settings(DEBUG=False, ALLOWED_HOSTS=["game.example.test"])
def test_origin_rejection_precedes_ip_authentication_and_terminal_consumer(headers):
    async def scenario():
        consumer = AsyncMock()
        ip_middleware = WebSocketIPCapacityMiddleware(AuthMiddlewareStack(consumer))
        ip_middleware._worker_lease_manager = AsyncMock()
        application = AllowedHostsOriginValidator(ip_middleware)
        communicator = WebsocketCommunicator(application, "/ws/test/", headers=headers)

        with (
            patch("channels.consumer.aclose_old_connections", new_callable=AsyncMock),
            patch("channels.generic.websocket.aclose_old_connections", new_callable=AsyncMock),
        ):
            try:
                connected, _ = await communicator.connect(timeout=1)
                assert connected is False
                consumer.assert_not_awaited()
                ip_middleware._worker_lease_manager.ensure_started.assert_not_awaited()
            finally:
                await communicator.disconnect(timeout=1)
            assert communicator.future.done()
            assert not communicator.future.cancelled()

    asyncio.run(scenario())
