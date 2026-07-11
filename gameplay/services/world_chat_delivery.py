from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import DataError, IntegrityError, ProgrammingError, transaction
from django.utils import timezone
from django_redis import get_redis_connection
from redis.exceptions import ResponseError

from core.exceptions import GameError
from core.utils.infrastructure import DATABASE_CACHE_INFRASTRUCTURE_EXCEPTIONS
from core.utils.side_effects import schedule_best_effort_after_commit
from gameplay.models import Manor, WorldChatSendAttempt
from gameplay.services.chat import TRUMPET_ITEM_KEY, normalize_world_chat_text
from gameplay.services.inventory.core import add_item_to_inventory_locked, consume_inventory_item_for_manor_locked
from gameplay.services.manor.bootstrap import ManorNotFoundError
from websocket.backends.chat_history import WorldChatDeliveryStage
from websocket.backends.chat_history import append_history_sync as append_history_to_backend_sync
from websocket.backends.chat_history import expire_delivery_marker_sync as expire_delivery_marker_in_backend_sync
from websocket.backends.chat_history import mark_delivery_broadcasted_sync as mark_delivery_broadcasted_in_backend_sync
from websocket.exceptions import WorldChatInfrastructureError

logger = logging.getLogger(__name__)

WORLD_CHAT_GROUP_NAME = "chat_world"
WORLD_CHAT_HISTORY_KEY = "chat:world:history"
WORLD_CHAT_HISTORY_LIMIT = 200
WORLD_CHAT_HISTORY_TTL_SECONDS = 24 * 60 * 60
WORLD_CHAT_PUBLISH_CLAIM_LEASE_SECONDS = 5 * 60
WORLD_CHAT_DELIVERY_MARKER_PREFIX = "chat:world:delivery:"


@dataclass(frozen=True, slots=True)
class WorldChatPublishClaim:
    attempt_id: int
    token: UUID
    payload: dict
    marker_key: str


class WorldChatValidationError(GameError):
    error_code = "WORLD_CHAT_VALIDATION_ERROR"
    default_message = "世界聊天发送参数无效"


class WorldChatOperationConflictError(GameError):
    error_code = "WORLD_CHAT_OPERATION_CONFLICT"
    default_message = "世界聊天操作ID已用于其他消息"


def _is_expected_infrastructure_error(exc: Exception) -> bool:
    return not isinstance(
        exc,
        (ProgrammingError, IntegrityError, DataError, ResponseError),
    ) and isinstance(
        exc,
        (WorldChatInfrastructureError, *DATABASE_CACHE_INFRASTRUCTURE_EXCEPTIONS),
    )


def _error_text(exc: Exception) -> str:
    return str(exc)[:2000]


def append_history_sync(
    payload: dict,
    *,
    delivery_marker_key: str,
) -> WorldChatDeliveryStage:
    return append_history_to_backend_sync(
        payload,
        get_redis_connection("default"),
        history_key=WORLD_CHAT_HISTORY_KEY,
        delivery_marker_key=delivery_marker_key,
        history_limit=WORLD_CHAT_HISTORY_LIMIT,
        history_message_ttl_seconds=WORLD_CHAT_HISTORY_TTL_SECONDS,
    )


def mark_delivery_broadcasted_sync(delivery_marker_key: str) -> None:
    mark_delivery_broadcasted_in_backend_sync(
        get_redis_connection("default"),
        delivery_marker_key=delivery_marker_key,
    )


def expire_delivery_marker_sync(
    delivery_marker_key: str,
    *,
    ttl_seconds: int,
) -> None:
    expire_delivery_marker_in_backend_sync(
        get_redis_connection("default"),
        delivery_marker_key=delivery_marker_key,
        ttl_seconds=ttl_seconds,
    )


def _build_world_chat_payload(attempt: WorldChatSendAttempt) -> dict:
    return {
        "type": "message",
        "channel": "world",
        "id": str(attempt.message_id),
        "operation_id": str(attempt.operation_id),
        "ts": int(attempt.created_at.timestamp() * 1000),
        "sender": {
            "id": attempt.user_id,
            "name": attempt.manor.display_name,
        },
        "text": attempt.text,
    }


def _is_publish_claim_active(
    attempt: WorldChatSendAttempt,
    *,
    now,
) -> bool:
    return bool(
        attempt.publish_claim_token
        and attempt.publish_claimed_at
        and attempt.publish_claimed_at > now - timedelta(seconds=WORLD_CHAT_PUBLISH_CLAIM_LEASE_SECONDS)
    )


def _claim_world_chat_attempt(attempt_id: int) -> WorldChatPublishClaim | None:
    with transaction.atomic():
        attempt = (
            WorldChatSendAttempt.objects.select_for_update()
            .select_related("manor", "user")
            .filter(pk=attempt_id)
            .first()
        )
        if attempt is None or attempt.status != WorldChatSendAttempt.Status.PENDING:
            return None

        claimed_at = timezone.now()
        if _is_publish_claim_active(attempt, now=claimed_at):
            return None

        token = uuid4()
        attempt.publish_claim_token = token
        attempt.publish_claimed_at = claimed_at
        attempt.attempts += 1
        attempt.save(
            update_fields=[
                "publish_claim_token",
                "publish_claimed_at",
                "attempts",
                "updated_at",
            ]
        )
        return WorldChatPublishClaim(
            attempt_id=attempt.pk,
            token=token,
            payload=_build_world_chat_payload(attempt),
            marker_key=f"{WORLD_CHAT_DELIVERY_MARKER_PREFIX}{attempt.message_id}",
        )


def _finalize_world_chat_claim(claim: WorldChatPublishClaim) -> bool:
    with transaction.atomic():
        attempt = WorldChatSendAttempt.objects.select_for_update().filter(pk=claim.attempt_id).first()
        if (
            attempt is None
            or attempt.status != WorldChatSendAttempt.Status.PENDING
            or attempt.publish_claim_token != claim.token
        ):
            return False

        attempt.status = WorldChatSendAttempt.Status.PUBLISHED
        attempt.last_error = ""
        attempt.published_at = timezone.now()
        attempt.publish_claim_token = None
        attempt.publish_claimed_at = None
        attempt.save(
            update_fields=[
                "status",
                "last_error",
                "published_at",
                "publish_claim_token",
                "publish_claimed_at",
                "updated_at",
            ]
        )
        schedule_best_effort_after_commit(
            lambda: expire_delivery_marker_sync(
                claim.marker_key,
                ttl_seconds=WORLD_CHAT_HISTORY_TTL_SECONDS + 60,
            ),
            logger=logger,
            log_message=(
                "Failed to expire world chat delivery marker after finalize: " f"attempt_id={claim.attempt_id}"
            ),
            expected_exceptions=(WorldChatInfrastructureError,),
            degraded_component="world_chat_delivery_marker_expiry",
        )
        return True


def _record_attempt_failure(claim: WorldChatPublishClaim, *, exc: Exception) -> None:
    try:
        with transaction.atomic():
            attempt = (
                WorldChatSendAttempt.objects.select_for_update()
                .filter(
                    pk=claim.attempt_id,
                    status=WorldChatSendAttempt.Status.PENDING,
                    publish_claim_token=claim.token,
                )
                .first()
            )
            if attempt is None:
                return
            attempt.last_error = _error_text(exc)
            attempt.publish_claim_token = None
            attempt.publish_claimed_at = None
            attempt.save(
                update_fields=[
                    "last_error",
                    "publish_claim_token",
                    "publish_claimed_at",
                    "updated_at",
                ]
            )
    except Exception as secondary_exc:
        if not _is_expected_infrastructure_error(secondary_exc):
            secondary_exc.__context__ = exc
            raise secondary_exc
        logger.exception(
            "Failed to record world chat publish failure: attempt_id=%s",
            claim.attempt_id,
        )


def _record_refund_attempt_failure(attempt_id: int, *, exc: Exception) -> None:
    try:
        with transaction.atomic():
            attempt = (
                WorldChatSendAttempt.objects.select_for_update()
                .filter(
                    pk=attempt_id,
                    status=WorldChatSendAttempt.Status.REFUND_PENDING,
                )
                .first()
            )
            if attempt is None:
                return
            attempt.attempts += 1
            attempt.last_error = _error_text(exc)
            attempt.save(
                update_fields=[
                    "attempts",
                    "last_error",
                    "updated_at",
                ]
            )
    except Exception as secondary_exc:
        if not _is_expected_infrastructure_error(secondary_exc):
            secondary_exc.__context__ = exc
            raise secondary_exc
        logger.exception(
            "Failed to record world chat refund failure: attempt_id=%s",
            attempt_id,
        )


def _broadcast_world_chat_payload(payload: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        raise WorldChatInfrastructureError("world chat channel layer unavailable")
    async_to_sync(channel_layer.group_send)(
        WORLD_CHAT_GROUP_NAME,
        {"type": "chat_message", "payload": payload},
    )


def publish_world_chat_attempt(attempt_id: int) -> bool:
    claim = _claim_world_chat_attempt(attempt_id)
    if claim is None:
        return False

    try:
        stage = append_history_sync(
            claim.payload,
            delivery_marker_key=claim.marker_key,
        )
        if stage is not WorldChatDeliveryStage.BROADCASTED:
            _broadcast_world_chat_payload(claim.payload)
            mark_delivery_broadcasted_sync(claim.marker_key)
        return _finalize_world_chat_claim(claim)
    except Exception as exc:
        if not _is_expected_infrastructure_error(exc):
            raise
        _record_attempt_failure(claim, exc=exc)
        raise


def mark_world_chat_refund_pending(attempt_id: int, reason: str) -> bool:
    """Mark an irrecoverable pending attempt for refund.

    Callers must first determine that delivery cannot be recovered. Retryable
    infrastructure failures must remain pending and must not call this function.
    """
    with transaction.atomic():
        attempt = WorldChatSendAttempt.objects.select_for_update().filter(pk=attempt_id).first()
        if attempt is None or attempt.status != WorldChatSendAttempt.Status.PENDING:
            return False
        if attempt.publish_claim_token is not None:
            return False
        attempt.status = WorldChatSendAttempt.Status.REFUND_PENDING
        attempt.last_error = str(reason)[:2000]
        attempt.publish_claim_token = None
        attempt.publish_claimed_at = None
        attempt.save(
            update_fields=[
                "status",
                "last_error",
                "publish_claim_token",
                "publish_claimed_at",
                "updated_at",
            ]
        )
        return True


def refund_world_chat_attempt(attempt_id: int) -> bool:
    manor_id = WorldChatSendAttempt.objects.filter(pk=attempt_id).values_list("manor_id", flat=True).first()
    if manor_id is None:
        return False

    try:
        with transaction.atomic():
            manor = Manor.objects.select_for_update().get(pk=manor_id)
            attempt = WorldChatSendAttempt.objects.select_for_update().filter(pk=attempt_id).first()
            if attempt is None or attempt.status != WorldChatSendAttempt.Status.REFUND_PENDING:
                return False

            attempt.attempts += 1
            if attempt.trumpet_consumed:
                add_item_to_inventory_locked(manor, TRUMPET_ITEM_KEY, 1)
            attempt.trumpet_consumed = False
            attempt.status = WorldChatSendAttempt.Status.REFUNDED
            attempt.refunded_at = timezone.now()
            attempt.last_error = ""
            attempt.save(
                update_fields=[
                    "attempts",
                    "trumpet_consumed",
                    "status",
                    "refunded_at",
                    "last_error",
                    "updated_at",
                ]
            )
            return True
    except Exception as exc:
        if not _is_expected_infrastructure_error(exc):
            raise
        _record_refund_attempt_failure(attempt_id, exc=exc)
        raise


def _validate_user_id(user_id: int) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise WorldChatValidationError("user_id 必须是正整数")
    return user_id


def _validate_operation_id(operation_id: UUID | str) -> UUID:
    if isinstance(operation_id, UUID):
        return operation_id
    if not isinstance(operation_id, str):
        raise WorldChatValidationError("operation_id 必须是有效 UUID")
    try:
        return UUID(operation_id)
    except ValueError as exc:
        raise WorldChatValidationError("operation_id 必须是有效 UUID") from exc


def _validate_text(text: str) -> str:
    if not isinstance(text, str):
        raise WorldChatValidationError("text 必须是字符串")
    normalized_text = normalize_world_chat_text(text)
    if not normalized_text:
        raise WorldChatValidationError("世界聊天消息不能为空")
    return normalized_text


def create_world_chat_attempt(
    *,
    user_id: int,
    operation_id: UUID | str,
    text: str,
) -> tuple[WorldChatSendAttempt, bool]:
    validated_user_id = _validate_user_id(user_id)
    validated_operation_id = _validate_operation_id(operation_id)
    normalized_text = _validate_text(text)

    with transaction.atomic():
        try:
            manor = Manor.objects.select_for_update().get(user_id=validated_user_id)
        except Manor.DoesNotExist as exc:
            raise ManorNotFoundError() from exc
        attempt, created = WorldChatSendAttempt.objects.get_or_create(
            user_id=validated_user_id,
            operation_id=validated_operation_id,
            defaults={
                "manor": manor,
                "text": normalized_text,
                "status": WorldChatSendAttempt.Status.PENDING,
            },
        )
        if not created:
            if attempt.text != normalized_text:
                raise WorldChatOperationConflictError()
            return attempt, False

        consume_inventory_item_for_manor_locked(manor, TRUMPET_ITEM_KEY, 1)
        attempt.trumpet_consumed = True
        attempt.save(update_fields=["trumpet_consumed", "updated_at"])
        return attempt, True
