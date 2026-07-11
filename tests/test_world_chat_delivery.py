from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from django.db import (
    DatabaseError,
    DataError,
    IntegrityError,
    OperationalError,
    ProgrammingError,
    connection,
    models,
    transaction,
)
from django.test import TestCase
from django.utils import timezone
from redis.exceptions import ResponseError

from core.exceptions import InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate, Manor, WorldChatSendAttempt
from gameplay.services.manor.core import ensure_manor
from websocket.backends.chat_history import WorldChatDeliveryStage

pytestmark = pytest.mark.django_db


@pytest.fixture
def trumpet_template():
    return ItemTemplate.objects.create(key="small_trumpet", name="小喇叭")


def _create_sender(user_factory, trumpet_template, *, quantity: int = 3):
    user = user_factory()
    manor = ensure_manor(user)
    inventory = InventoryItem.objects.create(
        manor=manor,
        template=trumpet_template,
        quantity=quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    return user, manor, inventory


def _trumpet_quantity(manor) -> int:
    quantity = (
        InventoryItem.objects.filter(
            manor=manor,
            template__key="small_trumpet",
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .values_list("quantity", flat=True)
        .first()
    )
    return int(quantity or 0)


def test_world_chat_send_attempt_model_contract():
    assert WorldChatSendAttempt.Status.values == [
        "pending",
        "published",
        "refund_pending",
        "refunded",
    ]

    user_field = WorldChatSendAttempt._meta.get_field("user")
    manor_field = WorldChatSendAttempt._meta.get_field("manor")
    operation_id_field = WorldChatSendAttempt._meta.get_field("operation_id")
    message_id_field = WorldChatSendAttempt._meta.get_field("message_id")
    text_field = WorldChatSendAttempt._meta.get_field("text")
    status_field = WorldChatSendAttempt._meta.get_field("status")
    consumed_field = WorldChatSendAttempt._meta.get_field("trumpet_consumed")
    attempts_field = WorldChatSendAttempt._meta.get_field("attempts")
    last_error_field = WorldChatSendAttempt._meta.get_field("last_error")
    claim_token_field = WorldChatSendAttempt._meta.get_field("publish_claim_token")
    claimed_at_field = WorldChatSendAttempt._meta.get_field("publish_claimed_at")

    assert user_field.remote_field.related_name == "world_chat_send_attempts"
    assert manor_field.remote_field.related_name == "world_chat_send_attempts"
    assert isinstance(operation_id_field, models.UUIDField)
    assert isinstance(message_id_field, models.UUIDField)
    assert message_id_field.default is uuid.uuid4
    assert message_id_field.unique is True
    assert message_id_field.editable is False
    assert isinstance(text_field, models.CharField)
    assert text_field.max_length == 200
    assert status_field.default == WorldChatSendAttempt.Status.PENDING
    assert consumed_field.default is False
    assert isinstance(attempts_field, models.PositiveIntegerField)
    assert attempts_field.default == 0
    assert last_error_field.blank is True
    assert isinstance(claim_token_field, models.UUIDField)
    assert claim_token_field.null is True
    assert claim_token_field.blank is True
    assert claim_token_field.editable is False
    assert isinstance(claimed_at_field, models.DateTimeField)
    assert claimed_at_field.null is True
    assert claimed_at_field.blank is True
    assert WorldChatSendAttempt._meta.get_field("published_at").null is True
    assert WorldChatSendAttempt._meta.get_field("refunded_at").null is True

    constraints = {constraint.name: constraint for constraint in WorldChatSendAttempt._meta.constraints}
    unique_constraint = constraints["world_chat_user_operation_uniq"]
    assert isinstance(unique_constraint, models.UniqueConstraint)
    assert tuple(unique_constraint.fields) == ("user", "operation_id")

    indexes = {index.name: index for index in WorldChatSendAttempt._meta.indexes}
    assert indexes["world_chat_status_created_idx"].fields == ["status", "created_at"]


def test_world_chat_send_attempt_database_constraint_and_index(user_factory):
    user = user_factory()
    manor = ensure_manor(user)
    operation_id = uuid.uuid4()

    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, WorldChatSendAttempt._meta.db_table)

    unique_constraint = constraints["world_chat_user_operation_uniq"]
    assert unique_constraint["unique"] is True
    assert unique_constraint["columns"] == ["user_id", "operation_id"]
    status_index = constraints["world_chat_status_created_idx"]
    assert status_index["index"] is True
    assert status_index["columns"] == ["status", "created_at"]

    WorldChatSendAttempt.objects.create(
        user=user,
        manor=manor,
        operation_id=operation_id,
        text="first",
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorldChatSendAttempt.objects.create(
                user=user,
                manor=manor,
                operation_id=operation_id,
                text="duplicate",
            )


def test_create_world_chat_attempt_consumes_one_trumpet_and_persists_pending_attempt(
    user_factory,
    trumpet_template,
):
    from gameplay.services.world_chat_delivery import create_world_chat_attempt

    user, manor, inventory = _create_sender(user_factory, trumpet_template, quantity=3)
    operation_id = uuid.uuid4()

    attempt, created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=str(operation_id),
        text="  <b>hello</b>\x01\r\n\r\n\r\n\r\nworld  ",
    )

    inventory.refresh_from_db()
    assert created is True
    assert attempt.user_id == user.id
    assert attempt.manor_id == manor.id
    assert attempt.operation_id == operation_id
    assert isinstance(attempt.message_id, uuid.UUID)
    assert attempt.text == "&lt;b&gt;hello&lt;/b&gt;\n\n\nworld"
    assert attempt.status == WorldChatSendAttempt.Status.PENDING
    assert attempt.trumpet_consumed is True
    assert attempt.attempts == 0
    assert attempt.last_error == ""
    assert attempt.published_at is None
    assert attempt.refunded_at is None
    assert inventory.quantity == 2


def test_create_world_chat_attempt_maps_missing_manor_to_stable_business_error(user_factory):
    from gameplay.services.manor.bootstrap import ManorNotFoundError
    from gameplay.services.world_chat_delivery import create_world_chat_attempt

    user = user_factory()
    Manor.objects.filter(user_id=user.id).delete()

    with pytest.raises(ManorNotFoundError) as exc_info:
        create_world_chat_attempt(
            user_id=user.id,
            operation_id=uuid.uuid4(),
            text="missing manor",
        )

    assert isinstance(exc_info.value.__cause__, Manor.DoesNotExist)
    assert WorldChatSendAttempt.objects.filter(user_id=user.id).count() == 0


def test_exact_world_chat_attempt_replay_returns_same_row_without_second_charge(
    user_factory,
    trumpet_template,
):
    from gameplay.services.world_chat_delivery import create_world_chat_attempt

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    operation_id = uuid.uuid4()

    first, first_created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=operation_id,
        text="hello",
    )
    replay, replay_created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=operation_id,
        text="hello",
    )

    inventory.refresh_from_db()
    assert first_created is True
    assert replay_created is False
    assert replay.pk == first.pk
    assert replay.message_id == first.message_id
    assert inventory.quantity == 1
    assert WorldChatSendAttempt.objects.count() == 1


def test_equivalent_world_chat_operation_id_replay_returns_same_row_without_second_charge(
    user_factory,
    trumpet_template,
):
    from gameplay.services.world_chat_delivery import create_world_chat_attempt

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    operation_id = uuid.UUID("abcdef12-3456-7890-abcd-ef1234567890")
    braced_uppercase_operation_id = f"{{{str(operation_id).upper()}}}"

    first, first_created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=braced_uppercase_operation_id,
        text="same operation representation",
    )
    replay, replay_created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=operation_id,
        text="same operation representation",
    )

    inventory.refresh_from_db()
    assert first_created is True
    assert replay_created is False
    assert first.operation_id == operation_id
    assert replay.pk == first.pk
    assert replay.message_id == first.message_id
    assert inventory.quantity == 1
    assert WorldChatSendAttempt.objects.count() == 1


def test_world_chat_attempt_replay_conflict_preserves_attempt_and_inventory(
    user_factory,
    trumpet_template,
):
    from gameplay.services.world_chat_delivery import WorldChatOperationConflictError, create_world_chat_attempt

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    operation_id = uuid.uuid4()
    first, _created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=operation_id,
        text="first text",
    )

    with pytest.raises(WorldChatOperationConflictError):
        create_world_chat_attempt(
            user_id=user.id,
            operation_id=operation_id,
            text="different text",
        )

    inventory.refresh_from_db()
    first.refresh_from_db()
    assert inventory.quantity == 1
    assert first.text == "first text"
    assert WorldChatSendAttempt.objects.count() == 1


def test_world_chat_attempt_replay_compares_normalized_text(
    user_factory,
    trumpet_template,
):
    from gameplay.services.world_chat_delivery import create_world_chat_attempt

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    operation_id = uuid.uuid4()
    first, _created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=operation_id,
        text="  hello\x01\r\nworld  ",
    )
    replay, replay_created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=operation_id,
        text="hello\nworld",
    )

    inventory.refresh_from_db()
    assert replay_created is False
    assert replay.pk == first.pk
    assert replay.text == "hello\nworld"
    assert inventory.quantity == 1


def test_different_users_can_reuse_world_chat_operation_id(user_factory, trumpet_template):
    from gameplay.services.world_chat_delivery import create_world_chat_attempt

    first_user, first_manor, _first_inventory = _create_sender(user_factory, trumpet_template, quantity=1)
    second_user, second_manor, _second_inventory = _create_sender(user_factory, trumpet_template, quantity=1)
    operation_id = uuid.uuid4()

    first, first_created = create_world_chat_attempt(
        user_id=first_user.id,
        operation_id=operation_id,
        text="same operation",
    )
    second, second_created = create_world_chat_attempt(
        user_id=second_user.id,
        operation_id=operation_id,
        text="same operation",
    )

    assert first_created is True
    assert second_created is True
    assert first.pk != second.pk
    assert _trumpet_quantity(first_manor) == 0
    assert _trumpet_quantity(second_manor) == 0


def test_world_chat_attempt_insufficient_stock_rolls_back_attempt(user_factory, trumpet_template):
    from gameplay.services.world_chat_delivery import create_world_chat_attempt

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=0)

    with pytest.raises(InsufficientStockError):
        create_world_chat_attempt(
            user_id=user.id,
            operation_id=uuid.uuid4(),
            text="no stock",
        )

    inventory.refresh_from_db()
    assert inventory.quantity == 0
    assert WorldChatSendAttempt.objects.count() == 0


def test_world_chat_attempt_programming_error_after_charge_rolls_back_inventory_and_attempt(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    programming_error = RuntimeError("inventory consume follow-up bug")
    real_consume = world_chat_delivery.consume_inventory_item_for_manor_locked

    def _consume_then_raise(manor, item_key, amount):
        real_consume(manor, item_key, amount)
        raise programming_error

    monkeypatch.setattr(
        world_chat_delivery,
        "consume_inventory_item_for_manor_locked",
        _consume_then_raise,
    )

    with pytest.raises(RuntimeError) as exc_info:
        world_chat_delivery.create_world_chat_attempt(
            user_id=user.id,
            operation_id=uuid.uuid4(),
            text="rollback programming error",
        )

    assert exc_info.value is programming_error
    inventory.refresh_from_db()
    assert inventory.quantity == 2
    assert WorldChatSendAttempt.objects.count() == 0


@pytest.mark.parametrize(
    "save_error",
    [
        pytest.param(DatabaseError("attempt update failed"), id="database-error"),
        pytest.param(IntegrityError("attempt update integrity failure"), id="integrity-error"),
    ],
)
def test_world_chat_attempt_update_failure_rolls_back_inventory_and_attempt(
    monkeypatch,
    user_factory,
    trumpet_template,
    save_error,
):
    from gameplay.services.world_chat_delivery import create_world_chat_attempt

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    original_save = WorldChatSendAttempt.save

    def _fail_consumed_update(self, *args, **kwargs):
        if self.trumpet_consumed:
            raise save_error
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(WorldChatSendAttempt, "save", _fail_consumed_update)

    with pytest.raises(type(save_error)) as exc_info:
        create_world_chat_attempt(
            user_id=user.id,
            operation_id=uuid.uuid4(),
            text="rollback me",
        )

    assert exc_info.value is save_error
    inventory.refresh_from_db()
    assert inventory.quantity == 2
    assert WorldChatSendAttempt.objects.count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("user_id", 0, id="zero-user"),
        pytest.param("user_id", -1, id="negative-user"),
        pytest.param("user_id", True, id="boolean-user"),
        pytest.param("user_id", "1", id="string-user"),
        pytest.param("operation_id", "not-a-uuid", id="malformed-operation"),
        pytest.param("operation_id", 123, id="non-string-operation"),
        pytest.param("text", None, id="non-string-text"),
    ],
)
def test_create_world_chat_attempt_rejects_invalid_input(
    user_factory,
    trumpet_template,
    field,
    value,
):
    from gameplay.services.world_chat_delivery import WorldChatValidationError, create_world_chat_attempt

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    kwargs = {"user_id": user.id, "operation_id": uuid.uuid4(), "text": "hello"}
    kwargs[field] = value

    with pytest.raises(WorldChatValidationError):
        create_world_chat_attempt(**kwargs)

    inventory.refresh_from_db()
    assert inventory.quantity == 2
    assert WorldChatSendAttempt.objects.count() == 0


@pytest.mark.parametrize("text", ["", "   ", "\x00\x01\r\n\t"])
def test_create_world_chat_attempt_rejects_empty_normalized_text(
    user_factory,
    trumpet_template,
    text,
):
    from gameplay.services.world_chat_delivery import WorldChatValidationError, create_world_chat_attempt

    user, _manor, inventory = _create_sender(user_factory, trumpet_template, quantity=2)

    with pytest.raises(WorldChatValidationError):
        create_world_chat_attempt(
            user_id=user.id,
            operation_id=uuid.uuid4(),
            text=text,
        )

    inventory.refresh_from_db()
    assert inventory.quantity == 2
    assert WorldChatSendAttempt.objects.count() == 0


def test_world_chat_normalization_has_one_source_and_truncates_to_200_characters():
    from gameplay.services.chat import normalize_world_chat_text
    from websocket.services.message_builder import normalize_text

    raw = f"  <b>{'x' * 205}</b>\x01\r\n\r\n\r\n\r\nend  "
    normalized = normalize_world_chat_text(raw)

    assert normalize_text(raw) == normalized
    assert normalized.startswith("&lt;b&gt;")
    assert "\x01" not in normalized
    assert len(normalized) == 200


def test_claim_world_chat_attempt_persists_owner_and_stable_contract(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="claim contract",
    )
    claimed_at = timezone.now()
    monkeypatch.setattr(world_chat_delivery.timezone, "now", lambda: claimed_at)

    claim = world_chat_delivery._claim_world_chat_attempt(attempt.pk)

    assert isinstance(claim, world_chat_delivery.WorldChatPublishClaim)
    assert claim.attempt_id == attempt.pk
    assert isinstance(claim.token, uuid.UUID)
    assert claim.marker_key == f"chat:world:delivery:{attempt.message_id}"
    assert claim.payload == {
        "type": "message",
        "channel": "world",
        "id": str(attempt.message_id),
        "operation_id": str(attempt.operation_id),
        "ts": int(attempt.created_at.timestamp() * 1000),
        "sender": {"id": user.id, "name": manor.display_name},
        "text": "claim contract",
    }
    with pytest.raises(FrozenInstanceError):
        claim.attempt_id = attempt.pk + 1

    attempt.refresh_from_db()
    assert attempt.publish_claim_token == claim.token
    assert attempt.publish_claimed_at == claimed_at
    assert attempt.attempts == 1


def test_claim_world_chat_attempt_active_claim_is_noop(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="active claim",
    )
    now = timezone.now()
    active_token = uuid.uuid4()
    WorldChatSendAttempt.objects.filter(pk=attempt.pk).update(
        publish_claim_token=active_token,
        publish_claimed_at=now - timedelta(minutes=4),
        attempts=3,
    )
    monkeypatch.setattr(world_chat_delivery.timezone, "now", lambda: now)

    assert world_chat_delivery._claim_world_chat_attempt(attempt.pk) is None

    attempt.refresh_from_db()
    assert attempt.publish_claim_token == active_token
    assert attempt.attempts == 3


def test_claim_world_chat_attempt_replaces_expired_owner(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="expired claim",
    )
    now = timezone.now()
    expired_token = uuid.uuid4()
    WorldChatSendAttempt.objects.filter(pk=attempt.pk).update(
        publish_claim_token=expired_token,
        publish_claimed_at=now - timedelta(minutes=5, seconds=1),
        attempts=2,
    )
    monkeypatch.setattr(world_chat_delivery.timezone, "now", lambda: now)

    claim = world_chat_delivery._claim_world_chat_attempt(attempt.pk)

    assert claim is not None
    assert claim.token != expired_token
    attempt.refresh_from_db()
    assert attempt.publish_claim_token == claim.token
    assert attempt.publish_claimed_at == now
    assert attempt.attempts == 3


def test_finalize_world_chat_claim_rejects_replacement_owner(
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="replacement owner",
    )
    claim = world_chat_delivery._claim_world_chat_attempt(attempt.pk)
    assert claim is not None
    replacement_token = uuid.uuid4()
    WorldChatSendAttempt.objects.filter(pk=attempt.pk).update(
        publish_claim_token=replacement_token,
        publish_claimed_at=timezone.now(),
    )

    assert world_chat_delivery._finalize_world_chat_claim(claim) is False

    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.PENDING
    assert attempt.publish_claim_token == replacement_token
    assert attempt.published_at is None


def test_finalize_world_chat_claim_expires_marker_only_after_commit(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="marker expiry",
    )
    claim = world_chat_delivery._claim_world_chat_attempt(attempt.pk)
    assert claim is not None
    expire_marker = MagicMock()
    monkeypatch.setattr(
        world_chat_delivery,
        "expire_delivery_marker_sync",
        expire_marker,
        raising=False,
    )

    with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
        assert world_chat_delivery._finalize_world_chat_claim(claim) is True
        expire_marker.assert_not_called()

    assert len(callbacks) == 1
    callbacks[0]()
    expire_marker.assert_called_once_with(
        claim.marker_key,
        ttl_seconds=world_chat_delivery.WORLD_CHAT_HISTORY_TTL_SECONDS + 60,
    )


def test_record_world_chat_claim_failure_clears_only_matching_owner(
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="matching failure owner",
    )
    claim = world_chat_delivery._claim_world_chat_attempt(attempt.pk)
    assert claim is not None

    world_chat_delivery._record_attempt_failure(claim, exc=OSError("channel down"))

    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.PENDING
    assert attempt.attempts == 1
    assert attempt.last_error == "channel down"
    assert attempt.publish_claim_token is None
    assert attempt.publish_claimed_at is None


def test_record_world_chat_claim_failure_preserves_replacement_owner(
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="replacement failure owner",
    )
    claim = world_chat_delivery._claim_world_chat_attempt(attempt.pk)
    assert claim is not None
    replacement_token = uuid.uuid4()
    replacement_claimed_at = timezone.now()
    WorldChatSendAttempt.objects.filter(pk=attempt.pk).update(
        publish_claim_token=replacement_token,
        publish_claimed_at=replacement_claimed_at,
        last_error="replacement intact",
    )

    world_chat_delivery._record_attempt_failure(claim, exc=OSError("old owner failed"))

    attempt.refresh_from_db()
    assert attempt.last_error == "replacement intact"
    assert attempt.publish_claim_token == replacement_token
    assert attempt.publish_claimed_at == replacement_claimed_at


@pytest.mark.django_db(transaction=True)
def test_publish_world_chat_external_io_runs_outside_database_transactions(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery
    from websocket.backends.chat_history import WorldChatDeliveryStage

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="transaction boundary",
    )
    observations: list[tuple[str, bool]] = []

    def _observe(name: str):
        observations.append((name, transaction.get_connection().in_atomic_block))

    monkeypatch.setattr(
        world_chat_delivery,
        "append_history_sync",
        lambda *_args, **_kwargs: _observe("history") or WorldChatDeliveryStage.HISTORY,
    )
    monkeypatch.setattr(
        world_chat_delivery,
        "async_to_sync",
        lambda _callback: lambda *_args, **_kwargs: _observe("broadcast"),
    )
    monkeypatch.setattr(
        world_chat_delivery,
        "get_channel_layer",
        lambda: type("ChannelLayer", (), {"group_send": object()})(),
    )
    monkeypatch.setattr(
        world_chat_delivery,
        "mark_delivery_broadcasted_sync",
        lambda *_args, **_kwargs: _observe("mark-broadcasted"),
        raising=False,
    )
    monkeypatch.setattr(
        world_chat_delivery,
        "expire_delivery_marker_sync",
        lambda *_args, **_kwargs: _observe("expire-marker"),
        raising=False,
    )

    assert world_chat_delivery.publish_world_chat_attempt(attempt.pk) is True
    assert observations == [
        ("history", False),
        ("broadcast", False),
        ("mark-broadcasted", False),
        ("expire-marker", False),
    ]


def test_mark_world_chat_refund_pending_blocks_active_and_expired_claim_owners(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="claim refund race",
    )
    now = timezone.now()
    claim_token = uuid.uuid4()
    WorldChatSendAttempt.objects.filter(pk=attempt.pk).update(
        publish_claim_token=claim_token,
        publish_claimed_at=now - timedelta(minutes=4),
    )
    monkeypatch.setattr(world_chat_delivery.timezone, "now", lambda: now)

    assert world_chat_delivery.mark_world_chat_refund_pending(attempt.pk, "operator") is False

    WorldChatSendAttempt.objects.filter(pk=attempt.pk).update(
        publish_claimed_at=now - timedelta(minutes=5, seconds=1),
    )
    assert world_chat_delivery.mark_world_chat_refund_pending(attempt.pk, "operator") is False

    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.PENDING
    assert attempt.publish_claim_token == claim_token
    assert attempt.publish_claimed_at == now - timedelta(minutes=5, seconds=1)
    assert attempt.last_error == ""


def test_publish_world_chat_attempt_builds_stable_payload_and_marks_published(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="stable payload",
    )
    append_history = MagicMock(return_value=WorldChatDeliveryStage.HISTORY)
    mark_broadcasted = MagicMock()
    group_send = AsyncMock()
    monkeypatch.setattr(world_chat_delivery, "append_history_sync", append_history)
    monkeypatch.setattr(
        world_chat_delivery,
        "mark_delivery_broadcasted_sync",
        mark_broadcasted,
    )
    monkeypatch.setattr(
        world_chat_delivery,
        "get_channel_layer",
        lambda: type("ChannelLayer", (), {"group_send": group_send})(),
    )

    world_chat_delivery.publish_world_chat_attempt(attempt.pk)

    attempt.refresh_from_db()
    expected_payload = {
        "type": "message",
        "channel": "world",
        "id": str(attempt.message_id),
        "operation_id": str(attempt.operation_id),
        "ts": int(attempt.created_at.timestamp() * 1000),
        "sender": {"id": user.id, "name": manor.display_name},
        "text": "stable payload",
    }
    marker_key = f"chat:world:delivery:{attempt.message_id}"
    append_history.assert_called_once_with(
        expected_payload,
        delivery_marker_key=marker_key,
    )
    group_send.assert_awaited_once_with(
        "chat_world",
        {"type": "chat_message", "payload": expected_payload},
    )
    mark_broadcasted.assert_called_once_with(marker_key)
    assert attempt.status == WorldChatSendAttempt.Status.PUBLISHED
    assert attempt.attempts == 1
    assert attempt.last_error == ""
    assert attempt.published_at is not None
    assert attempt.publish_claim_token is None
    assert attempt.publish_claimed_at is None


def test_publish_world_chat_attempt_retries_broadcast_after_history_success(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="recover broadcast",
    )
    append_results: list[tuple[dict, str]] = []

    def _append_history(payload, *, delivery_marker_key):
        append_results.append((payload, delivery_marker_key))
        return WorldChatDeliveryStage.HISTORY

    monkeypatch.setattr(
        world_chat_delivery,
        "append_history_sync",
        _append_history,
    )
    mark_broadcasted = MagicMock()
    monkeypatch.setattr(
        world_chat_delivery,
        "mark_delivery_broadcasted_sync",
        mark_broadcasted,
    )
    group_send = AsyncMock(side_effect=[OSError("channel down"), None])
    monkeypatch.setattr(
        world_chat_delivery,
        "get_channel_layer",
        lambda: type("ChannelLayer", (), {"group_send": group_send})(),
    )

    with pytest.raises(OSError, match="channel down"):
        world_chat_delivery.publish_world_chat_attempt(attempt.pk)

    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.PENDING
    assert attempt.attempts == 1
    assert "channel down" in attempt.last_error
    assert attempt.publish_claim_token is None
    assert attempt.publish_claimed_at is None

    world_chat_delivery.publish_world_chat_attempt(attempt.pk)

    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.PUBLISHED
    assert attempt.attempts == 2
    assert len(append_results) == 2
    assert append_results[0] == append_results[1]
    assert group_send.await_count == 2
    mark_broadcasted.assert_called_once_with(append_results[0][1])


def test_publish_world_chat_attempt_retries_after_broadcast_when_status_save_fails(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="recover status save",
    )
    append_history = MagicMock(
        side_effect=[
            WorldChatDeliveryStage.HISTORY,
            WorldChatDeliveryStage.BROADCASTED,
        ]
    )
    mark_broadcasted = MagicMock()
    group_send = AsyncMock()
    monkeypatch.setattr(world_chat_delivery, "append_history_sync", append_history)
    monkeypatch.setattr(
        world_chat_delivery,
        "mark_delivery_broadcasted_sync",
        mark_broadcasted,
    )
    monkeypatch.setattr(
        world_chat_delivery,
        "get_channel_layer",
        lambda: type("ChannelLayer", (), {"group_send": group_send})(),
    )
    original_save = WorldChatSendAttempt.save
    failures_remaining = 1

    def _fail_first_published_save(self, *args, **kwargs):
        nonlocal failures_remaining
        if self.status == WorldChatSendAttempt.Status.PUBLISHED and failures_remaining:
            failures_remaining -= 1
            raise DatabaseError("publish status save failed")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(WorldChatSendAttempt, "save", _fail_first_published_save)

    with pytest.raises(DatabaseError, match="publish status save failed"):
        world_chat_delivery.publish_world_chat_attempt(attempt.pk)

    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.PENDING
    assert attempt.attempts == 1
    assert "publish status save failed" in attempt.last_error
    assert attempt.publish_claim_token is None
    assert attempt.publish_claimed_at is None

    assert world_chat_delivery.publish_world_chat_attempt(attempt.pk) is True
    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.PUBLISHED
    assert attempt.attempts == 2
    assert append_history.call_count == 2
    assert group_send.await_count == 1
    assert mark_broadcasted.call_count == 1


@pytest.mark.parametrize(
    "programming_error",
    [
        pytest.param(RuntimeError("publish bug"), id="runtime-error"),
        pytest.param(ProgrammingError("bad publish query"), id="programming-error"),
    ],
)
def test_publish_world_chat_attempt_programming_errors_bubble_without_failure_record(
    monkeypatch,
    user_factory,
    trumpet_template,
    programming_error,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="programming error",
    )
    monkeypatch.setattr(
        world_chat_delivery,
        "append_history_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(programming_error),
    )

    with pytest.raises(type(programming_error)) as exc_info:
        world_chat_delivery.publish_world_chat_attempt(attempt.pk)

    assert exc_info.value is programming_error
    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.PENDING
    assert attempt.attempts == 1
    assert attempt.last_error == ""
    assert attempt.publish_claim_token is not None
    assert attempt.publish_claimed_at is not None


@pytest.mark.parametrize(
    "status",
    [
        WorldChatSendAttempt.Status.PUBLISHED,
        WorldChatSendAttempt.Status.REFUND_PENDING,
        WorldChatSendAttempt.Status.REFUNDED,
    ],
)
def test_publish_world_chat_attempt_terminal_or_refund_state_is_noop(
    monkeypatch,
    user_factory,
    trumpet_template,
    status,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="do not publish",
    )
    WorldChatSendAttempt.objects.filter(pk=attempt.pk).update(status=status)
    append_history = MagicMock()
    monkeypatch.setattr(world_chat_delivery, "append_history_sync", append_history)

    assert world_chat_delivery.publish_world_chat_attempt(attempt.pk) is False
    append_history.assert_not_called()


def test_mark_and_refund_world_chat_attempt_are_idempotent(
    user_factory,
    trumpet_template,
):
    from gameplay.services.world_chat_delivery import (
        create_world_chat_attempt,
        mark_world_chat_refund_pending,
        refund_world_chat_attempt,
    )

    user, manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="refund once",
    )

    marked = mark_world_chat_refund_pending(attempt.pk, "operator requested")
    replayed = mark_world_chat_refund_pending(attempt.pk, "replacement reason")
    assert marked is True
    assert replayed is False
    attempt.refresh_from_db()
    assert attempt.last_error == "operator requested"

    assert refund_world_chat_attempt(attempt.pk) is True
    assert refund_world_chat_attempt(attempt.pk) is False

    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.REFUNDED
    assert attempt.trumpet_consumed is False
    assert attempt.last_error == ""
    assert attempt.refunded_at is not None
    assert attempt.attempts == 1
    assert _trumpet_quantity(manor) == 2


def test_refund_world_chat_attempt_rolls_back_add_when_status_save_fails_then_retries(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="refund rollback",
    )
    world_chat_delivery.mark_world_chat_refund_pending(attempt.pk, "operator requested")
    original_save = WorldChatSendAttempt.save
    save_failures_remaining = 1

    def _fail_first_refunded_save(self, *args, **kwargs):
        nonlocal save_failures_remaining
        if self.status == WorldChatSendAttempt.Status.REFUNDED and save_failures_remaining:
            save_failures_remaining -= 1
            raise DatabaseError("refund status save failed")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(WorldChatSendAttempt, "save", _fail_first_refunded_save)

    with pytest.raises(DatabaseError, match="refund status save failed"):
        world_chat_delivery.refund_world_chat_attempt(attempt.pk)

    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.REFUND_PENDING
    assert attempt.trumpet_consumed is True
    assert attempt.attempts == 1
    assert "refund status save failed" in attempt.last_error
    assert _trumpet_quantity(manor) == 1

    assert world_chat_delivery.refund_world_chat_attempt(attempt.pk) is True
    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.REFUNDED
    assert attempt.trumpet_consumed is False
    assert attempt.attempts == 2
    assert _trumpet_quantity(manor) == 2


def test_refund_world_chat_attempt_programming_error_bubbles_without_failure_record(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="refund programming error",
    )
    world_chat_delivery.mark_world_chat_refund_pending(attempt.pk, "operator requested")
    programming_error = RuntimeError("refund bug")
    monkeypatch.setattr(
        world_chat_delivery,
        "add_item_to_inventory_locked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(programming_error),
    )

    with pytest.raises(RuntimeError) as exc_info:
        world_chat_delivery.refund_world_chat_attempt(attempt.pk)

    assert exc_info.value is programming_error
    attempt.refresh_from_db()
    assert attempt.status == WorldChatSendAttempt.Status.REFUND_PENDING
    assert attempt.attempts == 0
    assert attempt.last_error == "operator requested"
    assert _trumpet_quantity(manor) == 1


@pytest.mark.parametrize(
    "non_infrastructure_error",
    [
        pytest.param(ProgrammingError("bad query"), id="programming-error"),
        pytest.param(IntegrityError("bad constraint"), id="integrity-error"),
        pytest.param(DataError("bad data"), id="data-error"),
        pytest.param(ResponseError("bad redis command"), id="response-error"),
    ],
)
def test_world_chat_delivery_classifier_rejects_programming_and_data_errors(
    non_infrastructure_error,
):
    from gameplay.services.world_chat_delivery import _is_expected_infrastructure_error

    assert _is_expected_infrastructure_error(non_infrastructure_error) is False


@pytest.mark.parametrize(
    "infrastructure_error",
    [
        pytest.param(DatabaseError("db down"), id="database-error"),
        pytest.param(ConnectionError("connection down"), id="connection-error"),
        pytest.param(TimeoutError("timed out"), id="timeout-error"),
    ],
)
def test_world_chat_delivery_classifier_accepts_retryable_infrastructure_errors(
    infrastructure_error,
):
    from gameplay.services.world_chat_delivery import _is_expected_infrastructure_error

    assert _is_expected_infrastructure_error(infrastructure_error) is True


@pytest.mark.parametrize(
    "secondary_error",
    [
        pytest.param(RuntimeError("record bug"), id="runtime-error"),
        pytest.param(ProgrammingError("record query bug"), id="programming-error"),
        pytest.param(IntegrityError("record constraint bug"), id="integrity-error"),
    ],
)
def test_record_world_chat_failure_does_not_swallow_secondary_programming_errors(
    monkeypatch,
    user_factory,
    trumpet_template,
    secondary_error,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="secondary error",
    )
    claim = world_chat_delivery._claim_world_chat_attempt(attempt.pk)
    assert claim is not None
    monkeypatch.setattr(
        WorldChatSendAttempt,
        "save",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(secondary_error),
    )

    with pytest.raises(type(secondary_error)) as exc_info:
        world_chat_delivery._record_attempt_failure(
            claim,
            exc=OSError("primary down"),
        )

    assert exc_info.value is secondary_error


def test_record_world_chat_failure_swallows_secondary_infrastructure_error(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="secondary infrastructure",
    )
    claim = world_chat_delivery._claim_world_chat_attempt(attempt.pk)
    assert claim is not None
    monkeypatch.setattr(
        WorldChatSendAttempt,
        "save",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OperationalError("record db down")),
    )

    world_chat_delivery._record_attempt_failure(
        claim,
        exc=OSError("primary down"),
    )


def test_publish_preserves_primary_error_as_context_when_failure_record_has_programming_error(
    monkeypatch,
    user_factory,
    trumpet_template,
):
    from gameplay.services import world_chat_delivery

    user, _manor, _inventory = _create_sender(user_factory, trumpet_template, quantity=2)
    attempt, _created = world_chat_delivery.create_world_chat_attempt(
        user_id=user.id,
        operation_id=uuid.uuid4(),
        text="preserve error context",
    )
    primary_error = OSError("channel down")
    secondary_error = RuntimeError("failure record bug")
    monkeypatch.setattr(
        world_chat_delivery,
        "append_history_sync",
        lambda *_args, **_kwargs: WorldChatDeliveryStage.HISTORY,
    )
    group_send = AsyncMock(side_effect=primary_error)
    monkeypatch.setattr(
        world_chat_delivery,
        "get_channel_layer",
        lambda: type("ChannelLayer", (), {"group_send": group_send})(),
    )
    original_save = WorldChatSendAttempt.save

    def _fail_failure_record(self, *args, **kwargs):
        if self.last_error == "channel down" and self.publish_claim_token is None:
            raise secondary_error
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(WorldChatSendAttempt, "save", _fail_failure_record)

    with pytest.raises(RuntimeError) as exc_info:
        world_chat_delivery.publish_world_chat_attempt(attempt.pk)

    assert exc_info.value is secondary_error
    assert exc_info.value.__context__ is primary_error
