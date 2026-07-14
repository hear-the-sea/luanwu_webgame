from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import SimpleTestCase
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError

from websocket.backends.connection_limiter import ConnectionCapacityDecision
from websocket.consumers import NotificationConsumer, OnlineStatsConsumer, WorldChatConsumer
from websocket.consumers.session_guard import WebSocketSessionValidationResult, WebSocketSessionValidationUnavailable
from websocket.exceptions import WebSocketConnectionLimitUnavailable


class NotificationConsumerTests(SimpleTestCase):
    def test_connection_slot_redis_translates_client_acquisition_failure(self):
        consumer = NotificationConsumer()

        with (
            patch(
                "websocket.consumers.session_guard.get_redis_connection",
                side_effect=ConnectionError("redis down"),
            ),
            self.assertRaises(WebSocketConnectionLimitUnavailable),
        ):
            consumer._connection_slot_redis()

    def test_dispatch_releases_user_capacity_slot_on_disconnect(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/notifications/"}
        consumer.channel_name = "capacity-lifecycle"
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.VALID)
        consumer._worker_lease_manager = AsyncMock()
        consumer._worker_lease_manager.ensure_started.return_value = "a" * 32
        consumer._acquire_connection_slot = AsyncMock(return_value=ConnectionCapacityDecision(True, 1, 0, 2, 0))
        consumer._refresh_connection_slot = AsyncMock(return_value=True)
        consumer._release_connection_slot_backend = AsyncMock()

        async def _scenario():
            with patch("channels.consumer.AsyncConsumer.dispatch", new_callable=AsyncMock) as base_dispatch:
                await consumer.dispatch({"type": "websocket.connect"})
                assert consumer._connection_slot_acquired is True
                assert consumer._connection_slot_heartbeat_task is not None

                await consumer.dispatch({"type": "websocket.disconnect"})

                assert base_dispatch.await_count == 2

        with patch("websocket.consumers.session_guard.logger.info") as info_log:
            asyncio.run(_scenario())

        consumer._acquire_connection_slot.assert_awaited_once_with(7, "capacity-lifecycle", "a" * 32)
        consumer._release_connection_slot_backend.assert_awaited_once_with(7, "capacity-lifecycle", "a" * 32)
        assert consumer._connection_slot_acquired is False
        assert consumer._connection_slot_heartbeat_task is None
        assert info_log.call_args.args[0] == "WebSocket user dead worker slots pruned"
        assert info_log.call_args.kwargs["extra"]["dead_worker_pruned"] == 2

    def test_user_capacity_heartbeat_interval_stays_below_configured_ttl(self):
        consumer = NotificationConsumer()
        consumer.close = AsyncMock()
        consumer._refresh_connection_slot = AsyncMock(return_value=False)
        sleep = AsyncMock()

        with (
            patch("websocket.consumers.session_guard.settings.WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS", 6),
            patch("websocket.consumers.session_guard.asyncio.sleep", sleep),
        ):
            asyncio.run(consumer._connection_slot_heartbeat_loop(7, "connection", "a" * 32))

        sleep.assert_awaited_once_with(2)
        consumer.close.assert_awaited_once_with(code=1013)

    def test_dispatch_rejects_connection_when_user_capacity_is_full(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/notifications/"}
        consumer.channel_name = "capacity-full"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.VALID)
        consumer._worker_lease_manager = AsyncMock()
        consumer._worker_lease_manager.ensure_started.return_value = "b" * 32
        consumer._acquire_connection_slot = AsyncMock(return_value=ConnectionCapacityDecision(False, 9, 1, 2, 3))

        with patch("websocket.consumers.session_guard.logger.info") as info_log:
            asyncio.run(consumer.dispatch({"type": "websocket.connect"}))

        consumer.accept.assert_awaited_once_with()
        consumer.close.assert_awaited_once_with(code=4429)
        assert info_log.call_args.kwargs["extra"] == {
            "user_id": 7,
            "path": "/ws/notifications/",
            "close_code": 4429,
            "active_slots": 9,
            "expired_pruned": 1,
            "dead_worker_pruned": 2,
            "malformed_members": 3,
            "worker_id": "bbbbbbbb",
        }

    def test_dispatch_rejects_connection_when_capacity_backend_is_unavailable(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/notifications/"}
        consumer.channel_name = "capacity-unavailable"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.VALID)
        consumer._worker_lease_manager = AsyncMock()
        consumer._worker_lease_manager.ensure_started.return_value = "c" * 32
        consumer._acquire_connection_slot = AsyncMock(side_effect=WebSocketConnectionLimitUnavailable("redis down"))

        asyncio.run(consumer.dispatch({"type": "websocket.connect"}))

        consumer.accept.assert_awaited_once_with()
        consumer.close.assert_awaited_once_with(code=1013)

    def test_asgi_connect_accepts_then_closes_1013_when_session_validation_is_unavailable(self):
        async def _scenario():
            communicator = WebsocketCommunicator(NotificationConsumer.as_asgi(), "/ws/notifications/")
            validation = AsyncMock(return_value=WebSocketSessionValidationResult.UNAVAILABLE)
            with (
                patch.object(NotificationConsumer, "_ensure_valid_session", validation),
                patch("channels.consumer.aclose_old_connections", new_callable=AsyncMock) as close_connections,
                patch(
                    "channels.generic.websocket.aclose_old_connections",
                    new_callable=AsyncMock,
                ) as websocket_close_connections,
            ):
                try:
                    connected, _subprotocol = await communicator.connect(timeout=1)
                    assert connected is True
                    output = await communicator.receive_output(timeout=1)
                    assert output == {"type": "websocket.close", "code": 1013}
                    await communicator.disconnect(code=1013, timeout=1)
                finally:
                    if not communicator.future.done():
                        await communicator.disconnect(code=1013, timeout=1)

                assert close_connections.await_count >= 1
                assert websocket_close_connections.await_count >= 1
                assert communicator.future.done()
                assert not communicator.future.cancelled()

        async_to_sync(_scenario)()

    @staticmethod
    async def _assert_session_guard_rejection(
        user,
        validation_result,
        message_type,
        expected_close_code,
    ):
        consumer = NotificationConsumer()
        consumer.scope = {"user": user, "path": "/ws/notifications/"}
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=validation_result)

        allowed = await consumer._guard_single_session({"type": message_type})

        assert allowed is False
        if message_type == "websocket.connect":
            consumer.accept.assert_awaited_once_with()
        else:
            consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once_with(code=expected_close_code)

    def test_session_guard_rejects_unauthenticated_connect_with_terminal_code(self):
        asyncio.run(
            self._assert_session_guard_rejection(
                None,
                WebSocketSessionValidationResult.INVALID,
                "websocket.connect",
                4401,
            )
        )

    def test_session_guard_rejects_invalid_authenticated_connect_with_terminal_code(self):
        class _User:
            id = 7
            is_authenticated = True

        asyncio.run(
            self._assert_session_guard_rejection(
                _User(),
                WebSocketSessionValidationResult.INVALID,
                "websocket.connect",
                4403,
            )
        )

    def test_session_validation_converts_sync_unavailable_to_result(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/notifications/"}
        unavailable = WebSocketSessionValidationUnavailable("session backend unavailable")

        def _database_sync_to_async_adapter(_func, *, thread_sensitive):
            assert thread_sensitive is True

            async def _raise_unavailable(_scope):
                raise unavailable

            return _raise_unavailable

        with (
            patch(
                "websocket.consumers.session_guard.database_sync_to_async",
                side_effect=_database_sync_to_async_adapter,
            ),
            patch(
                "websocket.consumers.session_guard.should_fail_open_on_single_session_unavailable",
                return_value=False,
            ),
            patch("websocket.consumers.session_guard.record_degradation") as record_degradation_mock,
        ):
            result = asyncio.run(consumer._ensure_valid_session(force=True))

        assert result is WebSocketSessionValidationResult.UNAVAILABLE
        record_degradation_mock.assert_called_once()
        assert record_degradation_mock.call_args.kwargs["component"] == "single_session_websocket"

    def test_session_guard_closes_connect_with_transient_code_when_validation_is_unavailable(self):
        class _User:
            id = 7
            is_authenticated = True

        asyncio.run(
            self._assert_session_guard_rejection(
                _User(),
                WebSocketSessionValidationResult.UNAVAILABLE,
                "websocket.connect",
                1013,
            )
        )

    def test_session_guard_closes_active_connection_with_transient_code_when_validation_is_unavailable(self):
        class _User:
            id = 7
            is_authenticated = True

        asyncio.run(
            self._assert_session_guard_rejection(
                _User(),
                WebSocketSessionValidationResult.UNAVAILABLE,
                "websocket.receive",
                1013,
            )
        )

    def test_session_guard_does_not_accept_again_when_active_session_becomes_invalid(self):
        class _User:
            id = 7
            is_authenticated = True

        asyncio.run(
            self._assert_session_guard_rejection(
                _User(),
                WebSocketSessionValidationResult.INVALID,
                "websocket.receive",
                4403,
            )
        )

    def test_session_guard_rejects_unknown_validation_result_without_side_effects(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/notifications/"}
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=object())

        with self.assertRaisesRegex(RuntimeError, "Unexpected websocket session validation result"):
            asyncio.run(consumer._guard_single_session({"type": "websocket.connect"}))

        consumer.accept.assert_not_awaited()
        consumer.close.assert_not_awaited()

    def test_connect_rejects_unauthenticated(self):
        consumer = NotificationConsumer()
        consumer.scope = {"user": None, "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.channel_layer = AsyncMock()

        asyncio.run(consumer.connect())

        consumer.close.assert_awaited_once_with(code=4401)
        consumer.accept.assert_not_awaited()

    def test_connect_adds_group_for_authenticated_user(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer.channel_name = "chan"
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.channel_layer = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.VALID)

        asyncio.run(consumer.connect())

        assert consumer.group_name == "user_7"
        consumer.channel_layer.group_add.assert_awaited_once_with("user_7", "chan")
        consumer.accept.assert_awaited_once()
        consumer.close.assert_not_awaited()

    def test_connect_rejects_stale_single_session(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.channel_layer = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.INVALID)

        asyncio.run(consumer.connect())

        consumer.close.assert_awaited_once_with(code=4403)
        consumer.accept.assert_not_awaited()

    def test_connect_closes_1013_when_session_validation_is_unavailable(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.channel_layer = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.UNAVAILABLE)

        asyncio.run(consumer.connect())

        consumer.close.assert_awaited_once_with(code=1013)
        consumer.accept.assert_not_awaited()

    def test_connect_rejects_unknown_validation_result_without_side_effects(self):
        class _User:
            id = 7
            is_authenticated = True

        consumer = NotificationConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.channel_layer = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=object())

        with self.assertRaisesRegex(RuntimeError, "Unexpected websocket session validation result"):
            asyncio.run(consumer.connect())

        consumer.channel_layer.group_add.assert_not_awaited()
        consumer.accept.assert_not_awaited()
        consumer.close.assert_not_awaited()
        assert consumer.group_name is None

    def test_notification_close_codes_are_class_hooks(self):
        assert NotificationConsumer.UNAUTHENTICATED_CLOSE_CODE == 4401
        assert NotificationConsumer.INVALID_SESSION_CLOSE_CODE == 4403

    def test_notify_message_emits_only_canonical_top_level_fields(self):
        consumer = NotificationConsumer()
        consumer.send_json = AsyncMock()

        event = {
            "payload": {
                "type": "info",
                "kind": "system",
                "title": "t",
                "message": "m",
                "data": {"a": 1},
                "timestamp": "2026-07-10T12:00:00+00:00",
                "extra": "drop",
            }
        }

        asyncio.run(consumer.notify_message(event))

        consumer.send_json.assert_awaited_once_with(
            {
                "type": "notification",
                "kind": "system",
                "title": "t",
                "body": "m",
                "data": {"extra": "drop", "a": 1},
                "timestamp": "2026-07-10T12:00:00+00:00",
            }
        )


class WorldChatSessionValidationTests(SimpleTestCase):
    @staticmethod
    def _build_consumer(validation_result) -> WorldChatConsumer:
        class _User:
            id = 9
            is_authenticated = True

        consumer = WorldChatConsumer()
        consumer.scope = {"user": _User(), "path": "/ws/chat/world/", "client": ("127.0.0.1", 1234)}
        consumer.channel_name = "world-chat-test"
        consumer.channel_layer = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer.send_json = AsyncMock()
        consumer._ensure_valid_session = AsyncMock(return_value=validation_result)
        consumer._get_display_name = AsyncMock(return_value="测试玩家")
        consumer._get_history = AsyncMock(return_value=[])
        return consumer

    def test_connect_uses_explicit_session_validation_states(self):
        async def _scenario():
            unavailable = self._build_consumer(WebSocketSessionValidationResult.UNAVAILABLE)
            await unavailable.connect()
            unavailable.close.assert_awaited_once_with(code=1013)
            unavailable.accept.assert_not_awaited()

            invalid = self._build_consumer(WebSocketSessionValidationResult.INVALID)
            await invalid.connect()
            invalid.close.assert_awaited_once_with()
            invalid.accept.assert_not_awaited()

            valid = self._build_consumer(WebSocketSessionValidationResult.VALID)
            await valid.connect()
            valid.close.assert_not_awaited()
            valid.accept.assert_awaited_once_with()
            valid.channel_layer.group_add.assert_awaited_once_with(valid.GROUP_NAME, valid.channel_name)

        asyncio.run(_scenario())

    def test_receive_fallback_uses_explicit_session_validation_states(self):
        async def _scenario():
            unavailable = self._build_consumer(WebSocketSessionValidationResult.UNAVAILABLE)
            unavailable._process_send_message = AsyncMock()
            await unavailable.receive_json({"type": "send", "text": "hello"})
            unavailable.close.assert_awaited_once_with(code=1013)
            unavailable._process_send_message.assert_not_awaited()

            invalid = self._build_consumer(WebSocketSessionValidationResult.INVALID)
            invalid._process_send_message = AsyncMock()
            await invalid.receive_json({"type": "send", "text": "hello"})
            invalid.close.assert_awaited_once_with()
            invalid._process_send_message.assert_not_awaited()

            valid = self._build_consumer(WebSocketSessionValidationResult.VALID)
            valid._process_send_message = AsyncMock()
            await valid.receive_json({"type": "send", "text": "hello"})
            valid.close.assert_not_awaited()
            valid._process_send_message.assert_awaited_once_with({"type": "send", "text": "hello"})

        asyncio.run(_scenario())

    def test_connect_rejects_unknown_validation_result_without_side_effects(self):
        consumer = self._build_consumer(object())

        with self.assertRaisesRegex(RuntimeError, "Unexpected websocket session validation result"):
            asyncio.run(consumer.connect())

        consumer.channel_layer.group_add.assert_not_awaited()
        consumer._get_display_name.assert_not_awaited()
        consumer._get_history.assert_not_awaited()
        consumer.accept.assert_not_awaited()
        consumer.close.assert_not_awaited()

    def test_receive_fallback_rejects_unknown_validation_result_without_side_effects(self):
        consumer = self._build_consumer(object())
        consumer._process_send_message = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "Unexpected websocket session validation result"):
            asyncio.run(consumer.receive_json({"type": "send", "text": "hello"}))

        consumer._process_send_message.assert_not_awaited()
        consumer.accept.assert_not_awaited()
        consumer.close.assert_not_awaited()


class OnlineStatsConsumerTests(SimpleTestCase):
    class _PresenceRedis:
        def __init__(self):
            self._zsets: dict[str, dict[str, float]] = {}

        def zadd(self, key: str, mapping: dict[object, float]):
            zset = self._zsets.setdefault(key, {})
            for member, score in mapping.items():
                zset[str(member)] = float(score)
            return len(mapping)

        def expire(self, *_args, **_kwargs):
            return True

        def zcard(self, key: str):
            return len(self._zsets.get(key, {}))

        def zremrangebyscore(self, key: str, min_score, max_score):
            zset = self._zsets.setdefault(key, {})
            lower = float("-inf") if min_score == "-inf" else float(min_score)
            upper = float(max_score)
            removed = [member for member, score in zset.items() if lower <= score <= upper]
            for member in removed:
                zset.pop(member, None)
            return len(removed)

        def zunionstore(self, dest: str, keys, aggregate=None):
            del aggregate
            union: dict[str, float] = {}
            for key in keys:
                for member, score in self._zsets.get(key, {}).items():
                    union[member] = max(union.get(member, float("-inf")), float(score))
            self._zsets[dest] = union
            return len(union)

    def _build_consumer(self) -> OnlineStatsConsumer:
        consumer = OnlineStatsConsumer()
        # Disable debouncing so assertions on group_send are deterministic.
        consumer.BROADCAST_DEBOUNCE_SECONDS = 0
        consumer.channel_name = "test-channel"
        consumer.channel_layer = AsyncMock()
        consumer.send_json = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        return consumer

    def test_connect_rejects_unauthenticated(self):
        consumer = self._build_consumer()
        consumer.scope = {"user": None, "path": "/ws/", "client": ("127.0.0.1", 1234)}

        asyncio.run(consumer.connect())

        consumer.close.assert_awaited_once()
        consumer.accept.assert_not_awaited()

    def test_connect_sends_stats_and_broadcasts(self):
        class _User:
            id = 11
            is_authenticated = True
            is_staff = False
            is_superuser = False

        consumer = self._build_consumer()
        consumer.scope = {"user": _User(), "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.VALID)

        async def _noop_heartbeat():
            return None

        consumer._heartbeat_loop = _noop_heartbeat
        consumer.add_online_connection = AsyncMock()
        consumer.get_stats = AsyncMock(return_value={"online_count": 1, "total_count": 2})

        asyncio.run(consumer.connect())

        consumer.channel_layer.group_add.assert_awaited_once_with(consumer.STATS_GROUP, consumer.channel_name)
        consumer.accept.assert_awaited_once()
        consumer.add_online_connection.assert_awaited_once_with(11)
        consumer.send_json.assert_awaited_once_with({"online_count": 1, "total_count": 2})
        consumer.channel_layer.group_send.assert_awaited_once()

    def test_connect_rejects_stale_single_session(self):
        class _User:
            id = 11
            is_authenticated = True
            is_staff = False
            is_superuser = False

        consumer = self._build_consumer()
        consumer.scope = {"user": _User(), "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.INVALID)

        asyncio.run(consumer.connect())

        consumer.close.assert_awaited_once_with()
        consumer.accept.assert_not_awaited()

    def test_connect_closes_1013_when_session_validation_is_unavailable(self):
        class _User:
            id = 11
            is_authenticated = True
            is_staff = False
            is_superuser = False

        consumer = self._build_consumer()
        consumer.scope = {"user": _User(), "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer._ensure_valid_session = AsyncMock(return_value=WebSocketSessionValidationResult.UNAVAILABLE)

        asyncio.run(consumer.connect())

        consumer.close.assert_awaited_once_with(code=1013)
        consumer.accept.assert_not_awaited()

    def test_connect_rejects_unknown_validation_result_without_side_effects(self):
        class _User:
            id = 11
            is_authenticated = True
            is_staff = False
            is_superuser = False

        consumer = self._build_consumer()
        consumer.scope = {"user": _User(), "path": "/ws/", "client": ("127.0.0.1", 1234)}
        consumer._ensure_valid_session = AsyncMock(return_value=object())

        with self.assertRaisesRegex(RuntimeError, "Unexpected websocket session validation result"):
            asyncio.run(consumer.connect())

        consumer.channel_layer.group_add.assert_not_awaited()
        consumer.accept.assert_not_awaited()
        consumer.close.assert_not_awaited()
        assert consumer.user_id is None

    def test_disconnect_removes_connection_and_broadcasts(self):
        consumer = self._build_consumer()
        consumer.is_real_user = True
        consumer.user_id = 12
        consumer.remove_online_connection = AsyncMock()
        consumer.get_stats = AsyncMock(return_value={"online_count": 0, "total_count": 1})

        async def _run():
            consumer.heartbeat_task = asyncio.create_task(asyncio.sleep(3600))
            await consumer.disconnect(1000)

        asyncio.run(_run())

        consumer.remove_online_connection.assert_awaited_once_with(12)
        consumer.channel_layer.group_discard.assert_awaited_once_with(consumer.STATS_GROUP, consumer.channel_name)
        consumer.channel_layer.group_send.assert_awaited_once()

    def test_get_online_count_sync_uses_cache(self):
        consumer = OnlineStatsConsumer()
        cache.delete(consumer.ONLINE_COUNT_CACHE_KEY)

        calls = {"zcard": 0, "zunionstore": 0}
        redis = self._PresenceRedis()
        now = time.time()
        redis.zadd("online_users_http_zset", {"1": now, "2": now})
        redis.zadd("online_users_ws_zset", {"2": now + 1, "3": now + 1})

        original_zcard = redis.zcard
        original_zunionstore = redis.zunionstore

        def _zcard(*args, **kwargs):
            calls["zcard"] += 1
            return original_zcard(*args, **kwargs)

        def _zunionstore(*args, **kwargs):
            calls["zunionstore"] += 1
            return original_zunionstore(*args, **kwargs)

        redis.zcard = _zcard  # type: ignore[method-assign]
        redis.zunionstore = _zunionstore  # type: ignore[method-assign]

        consumer._get_redis = lambda: redis  # type: ignore[method-assign]

        # First call should hit Redis.
        assert consumer._get_online_count_sync() == 3
        # Second call should hit cache.
        assert consumer._get_online_count_sync() == 3
        assert calls["zcard"] == 1
        assert calls["zunionstore"] == 1

    def test_remove_online_connection_keeps_recent_http_presence_counted(self):
        consumer = OnlineStatsConsumer()
        cache.delete(consumer.ONLINE_COUNT_CACHE_KEY)
        redis = self._PresenceRedis()
        user_id = 7
        now = time.time()
        redis.zadd("online_users_http_zset", {str(user_id): now})
        redis.zadd("online_users_ws_zset", {str(user_id): now + 1})

        class _RedisWithScript(self._PresenceRedis):
            def __init__(self, backing):
                self._zsets = backing._zsets
                self._counters = {f"{consumer.ONLINE_USER_CONN_COUNT_KEY_PREFIX}{user_id}": 1}

            def script_load(self, *_args, **_kwargs):
                return "sha"

            def evalsha(self, *_args, **_kwargs):
                self._zsets["online_users_ws_zset"].pop(str(user_id), None)
                self._counters.pop(f"{consumer.ONLINE_USER_CONN_COUNT_KEY_PREFIX}{user_id}", None)
                return 0

        redis_with_script = _RedisWithScript(redis)
        consumer._get_redis = lambda: redis_with_script  # type: ignore[method-assign]

        assert consumer._remove_online_connection_sync(user_id) == 0
        assert consumer._get_online_count_sync() == 1

    def test_cleanup_expired_users_sync_handles_redis_error(self):
        consumer = OnlineStatsConsumer()

        class _Redis:
            def zremrangebyscore(self, *_args, **_kwargs):
                raise RedisError("down")

        consumer._get_redis = lambda: _Redis()  # type: ignore[method-assign]

        assert consumer._cleanup_expired_users_sync(1000.0) == 0

    def test_add_online_connection_sync_tolerates_cache_delete_failure(self):
        consumer = OnlineStatsConsumer()

        class _Redis:
            def pipeline(self):
                class _Pipeline:
                    def incr(self, *_args, **_kwargs):
                        return self

                    def expire(self, *_args, **_kwargs):
                        return self

                    def zadd(self, *_args, **_kwargs):
                        return self

                    def execute(self):
                        return []

                return _Pipeline()

        consumer._get_redis = lambda: _Redis()  # type: ignore[method-assign]
        original_delete = cache.delete
        cache.delete = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
        try:
            consumer._add_online_connection_sync(7, 1000.0)
        finally:
            cache.delete = original_delete

    def test_remove_online_connection_sync_tolerates_cache_delete_failure(self):
        consumer = OnlineStatsConsumer()

        class _Redis:
            def script_load(self, *_args, **_kwargs):
                return "sha"

            def evalsha(self, *_args, **_kwargs):
                return 0

        consumer._get_redis = lambda: _Redis()  # type: ignore[method-assign]
        original_delete = cache.delete
        cache.delete = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
        try:
            assert consumer._remove_online_connection_sync(7) == 0
        finally:
            cache.delete = original_delete
