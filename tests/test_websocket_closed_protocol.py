from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from autobahn.exception import Disconnected

from websocket.consumers import NotificationConsumer, OnlineStatsConsumer, WorldChatConsumer


@pytest.mark.parametrize("consumer_type", [NotificationConsumer, OnlineStatsConsumer, WorldChatConsumer])
def test_server_close_skips_late_events_but_still_releases_capacity(consumer_type):
    consumer = consumer_type()
    consumer.base_send = AsyncMock()
    consumer._ensure_valid_session = AsyncMock()
    consumer._release_connection_slot = AsyncMock()

    async def scenario():
        with patch("channels.consumer.AsyncConsumer.dispatch", new_callable=AsyncMock) as dispatch:
            await consumer.close(code=4503)
            await consumer.close(code=4503)
            await consumer.dispatch({"type": "stats_update", "stats": {"online_count": 1}})
            await consumer.dispatch({"type": "websocket.receive", "text": "{}"})
            await consumer.send_json({"online_count": 1})
            consumer.base_send.assert_awaited_once_with({"type": "websocket.close", "code": 4503})
            dispatch.assert_not_awaited()
            consumer._ensure_valid_session.assert_not_awaited()
            await consumer.dispatch({"type": "websocket.disconnect", "code": 1000})
            dispatch.assert_awaited_once_with({"type": "websocket.disconnect", "code": 1000})
            consumer._release_connection_slot.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("consumer_type", [NotificationConsumer, OnlineStatsConsumer, WorldChatConsumer])
def test_client_closed_protocol_stops_pushes_and_preserves_disconnect_cleanup(consumer_type):
    consumer = consumer_type()
    consumer.base_send = AsyncMock(side_effect=Disconnected("Attempt to send on a closed protocol"))
    consumer._ensure_valid_session = AsyncMock()
    consumer._release_connection_slot = AsyncMock()

    async def scenario():
        with patch("channels.consumer.AsyncConsumer.dispatch", new_callable=AsyncMock) as dispatch:
            await consumer.send_json({"online_count": 1})
            await consumer.send_json({"online_count": 2})
            await consumer.dispatch({"type": "stats_update", "stats": {"online_count": 3}})
            assert consumer._websocket_closing is True
            assert consumer.base_send.await_count == 1
            dispatch.assert_not_awaited()
            consumer._ensure_valid_session.assert_not_awaited()
            await consumer.dispatch({"type": "websocket.disconnect", "code": 1006})
            consumer._release_connection_slot.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [RuntimeError("broken sender"), AssertionError("invalid payload")])
def test_push_does_not_hide_programming_errors(failure):
    consumer = OnlineStatsConsumer()
    consumer.base_send = AsyncMock(side_effect=failure)
    with pytest.raises(type(failure), match=str(failure)):
        asyncio.run(consumer.send_json({"online_count": 1}))
    assert consumer._websocket_closing is False


def test_send_with_close_preserves_payload_and_close_order():
    consumer = NotificationConsumer()
    consumer.base_send = AsyncMock()
    asyncio.run(consumer.send(text_data="final message", close=True))
    assert [call.args[0] for call in consumer.base_send.await_args_list] == [
        {"type": "websocket.send", "text": "final message"},
        {"type": "websocket.close"},
    ]
    assert consumer._websocket_closing is True


def test_disconnect_cleanup_runs_even_when_handler_fails():
    consumer = OnlineStatsConsumer()
    consumer._release_connection_slot = AsyncMock()
    with patch("channels.consumer.AsyncConsumer.dispatch", new_callable=AsyncMock) as dispatch:
        dispatch.side_effect = RuntimeError("disconnect cleanup failed")
        with pytest.raises(RuntimeError, match="disconnect cleanup failed"):
            asyncio.run(consumer.dispatch({"type": "websocket.disconnect", "code": 1000}))
    assert consumer._websocket_closing is True
    consumer._release_connection_slot.assert_awaited_once()


def test_close_during_inflight_push_does_not_raise_or_send_more_messages():
    async def scenario():
        consumer = OnlineStatsConsumer()
        started = asyncio.Event()
        finish = asyncio.Event()
        messages = []

        async def sender(message):
            messages.append(message)
            if message["type"] == "websocket.send":
                started.set()
                await finish.wait()
                raise Disconnected("Attempt to send on a closed protocol")

        consumer.base_send = sender
        pushing = asyncio.create_task(consumer.send_json({"online_count": 1}))
        await started.wait()
        await consumer.close(code=4503)
        finish.set()
        await pushing
        await consumer.send_json({"online_count": 2})
        assert [message["type"] for message in messages] == ["websocket.send", "websocket.close"]

    asyncio.run(scenario())


def test_closed_connection_does_not_suppress_another_clients_push():
    closed = OnlineStatsConsumer()
    closed.base_send = AsyncMock()
    active = OnlineStatsConsumer()
    active.base_send = AsyncMock()

    async def scenario():
        await closed.close()
        await active.send_json({"online_count": 2})

    asyncio.run(scenario())
    assert active._websocket_closing is False
    assert active.base_send.await_args.args[0]["type"] == "websocket.send"
