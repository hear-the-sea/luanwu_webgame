from __future__ import annotations

import threading
import time
import uuid

import pytest
from django.db import close_old_connections, connection
from django_redis import get_redis_connection

from gameplay.models import InventoryItem, ItemTemplate, WorldChatSendAttempt
from gameplay.services.manor.core import ensure_manor
from gameplay.services.world_chat_delivery import (
    WORLD_CHAT_HISTORY_TTL_SECONDS,
    WorldChatOperationConflictError,
    create_world_chat_attempt,
)
from websocket.backends.chat_history import (
    WorldChatDeliveryStage,
    append_history_sync,
    expire_delivery_marker_sync,
    mark_delivery_broadcasted_sync,
)

pytestmark = [pytest.mark.integration]


def _create_chat_sender(django_user_model, *, quantity: int):
    suffix = uuid.uuid4().hex[:8]
    user = django_user_model.objects.create_user(
        username=f"world_chat_delivery_{suffix}",
        password="pass123",
    )
    manor = ensure_manor(user)
    trumpet_template, _created = ItemTemplate.objects.get_or_create(
        key="small_trumpet",
        defaults={"name": "小喇叭"},
    )
    inventory, _created = InventoryItem.objects.update_or_create(
        manor=manor,
        template=trumpet_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": quantity},
    )
    return user, inventory


@pytest.mark.django_db(transaction=True)
def test_concurrent_exact_world_chat_replay_creates_and_charges_once(django_user_model):
    if connection.vendor != "mysql":
        pytest.skip("world chat delivery concurrency requires MySQL select_for_update semantics")

    user, inventory = _create_chat_sender(django_user_model, quantity=3)
    operation_id = uuid.uuid4()
    barrier = threading.Barrier(2)
    results: list[tuple[int, uuid.UUID, bool]] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            attempt, created = create_world_chat_attempt(
                user_id=user.id,
                operation_id=operation_id,
                text="concurrent exact replay",
            )
            results.append((attempt.pk, attempt.message_id, created))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert sorted(created for _pk, _message_id, created in results) == [False, True]
    assert len({pk for pk, _message_id, _created in results}) == 1
    assert len({message_id for _pk, message_id, _created in results}) == 1
    assert WorldChatSendAttempt.objects.filter(user=user, operation_id=operation_id).count() == 1
    inventory.refresh_from_db()
    assert inventory.quantity == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_conflicting_world_chat_replay_keeps_winner_and_charges_once(django_user_model):
    if connection.vendor != "mysql":
        pytest.skip("world chat delivery concurrency requires MySQL select_for_update semantics")

    user, inventory = _create_chat_sender(django_user_model, quantity=3)
    operation_id = uuid.uuid4()
    barrier = threading.Barrier(2)
    submitted_texts = ("first concurrent text", "second concurrent text")
    results: list[tuple[int, uuid.UUID, bool, str]] = []
    errors: list[BaseException] = []

    def _worker(text: str) -> None:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            attempt, created = create_world_chat_attempt(
                user_id=user.id,
                operation_id=operation_id,
                text=text,
            )
            results.append((attempt.pk, attempt.message_id, created, attempt.text))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, args=(text,), daemon=True) for text in submitted_texts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1
    assert results[0][2] is True
    assert len(errors) == 1
    assert isinstance(errors[0], WorldChatOperationConflictError)

    persisted = WorldChatSendAttempt.objects.get(user=user, operation_id=operation_id)
    assert persisted.pk == results[0][0]
    assert persisted.message_id == results[0][1]
    assert persisted.text == results[0][3]
    assert persisted.text in submitted_texts
    inventory.refresh_from_db()
    assert inventory.quantity == 2


def test_real_redis_delivery_marker_survives_bounded_history_eviction():
    redis = get_redis_connection("default")
    suffix = uuid.uuid4().hex
    history_key = f"integration:world-chat:history:{suffix}"
    original_marker = f"integration:world-chat:delivery:{suffix}:original"
    marker_keys = [original_marker]
    original = {
        "type": "message",
        "operation_id": f"operation-{suffix}",
        "sender": {"id": 1},
        "ts": int(time.time() * 1000),
        "text": "original",
    }

    try:
        assert (
            append_history_sync(
                original,
                redis,
                history_key=history_key,
                delivery_marker_key=original_marker,
                history_limit=200,
                history_message_ttl_seconds=WORLD_CHAT_HISTORY_TTL_SECONDS,
            )
            is WorldChatDeliveryStage.HISTORY
        )

        for index in range(201):
            marker_key = f"integration:world-chat:delivery:{suffix}:{index}"
            marker_keys.append(marker_key)
            assert (
                append_history_sync(
                    {
                        "type": "message",
                        "operation_id": f"later-operation-{suffix}-{index}",
                        "sender": {"id": 2},
                        "ts": int(time.time() * 1000) + index,
                        "text": f"later-{index}",
                    },
                    redis,
                    history_key=history_key,
                    delivery_marker_key=marker_key,
                    history_limit=200,
                    history_message_ttl_seconds=WORLD_CHAT_HISTORY_TTL_SECONDS,
                )
                is WorldChatDeliveryStage.HISTORY
            )

        history_before_replay = redis.lrange(history_key, 0, -1)
        assert len(history_before_replay) == 200
        assert redis.ttl(original_marker) == -1

        assert (
            append_history_sync(
                original,
                redis,
                history_key=history_key,
                delivery_marker_key=original_marker,
                history_limit=200,
                history_message_ttl_seconds=WORLD_CHAT_HISTORY_TTL_SECONDS,
            )
            is WorldChatDeliveryStage.HISTORY
        )
        assert redis.lrange(history_key, 0, -1) == history_before_replay

        mark_delivery_broadcasted_sync(redis, delivery_marker_key=original_marker)
        assert redis.get(original_marker) in (b"broadcasted", "broadcasted")
        assert redis.ttl(original_marker) == -1

        expire_delivery_marker_sync(
            redis,
            delivery_marker_key=original_marker,
            ttl_seconds=WORLD_CHAT_HISTORY_TTL_SECONDS + 60,
        )
        ttl_after_finalize = redis.ttl(original_marker)
        assert ttl_after_finalize > 0

        mark_delivery_broadcasted_sync(redis, delivery_marker_key=original_marker)
        assert 0 < redis.ttl(original_marker) <= ttl_after_finalize
    finally:
        redis.delete(history_key, *marker_keys)


def test_real_redis_wrongtype_history_does_not_leave_false_delivery_marker():
    from redis.exceptions import ResponseError

    redis = get_redis_connection("default")
    suffix = uuid.uuid4().hex
    history_key = f"integration:world-chat:wrongtype-history:{suffix}"
    marker_key = f"integration:world-chat:wrongtype-delivery:{suffix}"
    redis.set(history_key, "not-a-list")

    try:
        with pytest.raises(ResponseError, match="WRONGTYPE"):
            append_history_sync(
                {
                    "type": "message",
                    "operation_id": f"wrongtype-operation-{suffix}",
                    "sender": {"id": 1},
                    "ts": int(time.time() * 1000),
                    "text": "must not be marked",
                },
                redis,
                history_key=history_key,
                delivery_marker_key=marker_key,
                history_limit=200,
                history_message_ttl_seconds=WORLD_CHAT_HISTORY_TTL_SECONDS,
            )
        assert redis.get(marker_key) is None
    finally:
        redis.delete(history_key, marker_key)
