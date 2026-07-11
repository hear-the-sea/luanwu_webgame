from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from gameplay.models import WorldChatSendAttempt
from gameplay.services.world_chat_delivery import (
    WORLD_CHAT_PUBLISH_CLAIM_LEASE_SECONDS,
    _is_expected_infrastructure_error,
    publish_world_chat_attempt,
    refund_world_chat_attempt,
)

logger = logging.getLogger(__name__)

DEFAULT_SCAN_BATCH_SIZE = 100
MAX_SCAN_BATCH_SIZE = 500
PENDING_GRACE_SECONDS = 30


def _validate_batch_size(batch_size: int) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    return min(batch_size, MAX_SCAN_BATCH_SIZE)


@shared_task(
    name="gameplay.publish_world_chat_attempt",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def publish_world_chat_attempt_task(self, attempt_id: int) -> bool:
    try:
        return publish_world_chat_attempt(attempt_id)
    except Exception as exc:
        if not _is_expected_infrastructure_error(exc):
            raise
        logger.exception("World chat publish task failed: attempt_id=%s", attempt_id)
        raise self.retry(exc=exc)


@shared_task(
    name="gameplay.refund_world_chat_attempt",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def refund_world_chat_attempt_task(self, attempt_id: int) -> bool:
    try:
        return refund_world_chat_attempt(attempt_id)
    except Exception as exc:
        if not _is_expected_infrastructure_error(exc):
            raise
        logger.exception("World chat refund task failed: attempt_id=%s", attempt_id)
        raise self.retry(exc=exc)


@shared_task(
    name="gameplay.scan_world_chat_attempts",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def scan_world_chat_attempts_task(self, batch_size: int = DEFAULT_SCAN_BATCH_SIZE) -> dict[str, int]:
    limit = _validate_batch_size(batch_size)
    try:
        refund_ids = list(
            WorldChatSendAttempt.objects.filter(status=WorldChatSendAttempt.Status.REFUND_PENDING)
            .order_by("created_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        refunds = sum(bool(refund_world_chat_attempt(attempt_id)) for attempt_id in refund_ids)

        remaining = limit - len(refund_ids)
        publishes = 0
        if remaining > 0:
            scan_time = timezone.now()
            pending_before = scan_time - timedelta(seconds=PENDING_GRACE_SECONDS)
            claim_expired_before = scan_time - timedelta(seconds=WORLD_CHAT_PUBLISH_CLAIM_LEASE_SECONDS)
            pending_ids = list(
                WorldChatSendAttempt.objects.filter(
                    status=WorldChatSendAttempt.Status.PENDING,
                    created_at__lte=pending_before,
                )
                .filter(
                    Q(publish_claim_token__isnull=True)
                    | Q(publish_claimed_at__isnull=True)
                    | Q(publish_claimed_at__lte=claim_expired_before)
                )
                .order_by("created_at", "id")
                .values_list("id", flat=True)[:remaining]
            )
            publishes = sum(bool(publish_world_chat_attempt(attempt_id)) for attempt_id in pending_ids)
        return {"refunds": refunds, "publishes": publishes}
    except Exception as exc:
        if not _is_expected_infrastructure_error(exc):
            raise
        logger.exception("World chat attempt scanner failed")
        raise self.retry(exc=exc)
