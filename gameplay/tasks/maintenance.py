from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from celery import shared_task
from django.db.models import Case, F, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.config import MESSAGE
from gameplay.services.jail import (
    JAIL_CLEANUP_DEFAULT_BATCH_SIZE,
    JAIL_CLEANUP_DEFAULT_MAX_BATCHES,
    cleanup_expired_jail_prisoners,
)

logger = logging.getLogger(__name__)

RESOURCE_EVENT_RETENTION_DAYS = 30
ARENA_EXCHANGE_RETENTION_DAYS = 30
BATTLE_REPORT_RETENTION_DAYS = 30
DELETE_BATCH_SIZE = 10000


def _jail_cleanup_as_of(value: str | datetime | None) -> datetime:
    if value is None:
        return timezone.now()
    if isinstance(value, datetime):
        return value
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        raise ValueError("as_of must be an ISO-8601 timezone-aware datetime")
    return parsed


def _batched_delete_before(
    model: type[Any],
    *,
    time_field: str,
    cutoff,
    batch_size: int = DELETE_BATCH_SIZE,
) -> int:
    """Delete rows older than cutoff in small batches to reduce lock pressure."""
    filter_kwargs = {f"{time_field}__lt": cutoff}
    deleted_total = 0

    while True:
        ids_to_delete = list(model.objects.filter(**filter_kwargs).values_list("id", flat=True)[:batch_size])
        if not ids_to_delete:
            break

        deleted, _ = model.objects.filter(id__in=ids_to_delete).delete()
        deleted_total += int(deleted)

        if len(ids_to_delete) < batch_size:
            break

    return deleted_total


@shared_task(name="gameplay.cleanup_old_data")
def cleanup_old_data_task():
    """
    Clean up expired transaction records to save database space.

    Runs daily and cleans up:
    - ResourceEvent: keep 30 days
    - ArenaExchangeRecord: keep 30 days
    - BattleReport: keep 30 days
    - Message: keep MESSAGE.RETENTION_DAYS days, except unclaimed attachments
    """
    from battle.models import BattleReport
    from gameplay.models import ArenaExchangeRecord, ResourceEvent
    from gameplay.services.utils.messages import delete_expired_messages

    now = timezone.now()

    resource_cutoff = now - timedelta(days=RESOURCE_EVENT_RETENTION_DAYS)
    arena_exchange_cutoff = now - timedelta(days=ARENA_EXCHANGE_RETENTION_DAYS)
    battle_report_cutoff = now - timedelta(days=BATTLE_REPORT_RETENTION_DAYS)
    message_cutoff = now - timedelta(days=MESSAGE.RETENTION_DAYS)

    resource_deleted = _batched_delete_before(ResourceEvent, time_field="created_at", cutoff=resource_cutoff)
    arena_exchange_deleted = _batched_delete_before(
        ArenaExchangeRecord, time_field="created_at", cutoff=arena_exchange_cutoff
    )
    battle_report_deleted = _batched_delete_before(BattleReport, time_field="created_at", cutoff=battle_report_cutoff)
    message_result = delete_expired_messages(message_cutoff, batch_size=DELETE_BATCH_SIZE)
    message_deleted = message_result.deleted_count

    total_deleted = resource_deleted + arena_exchange_deleted + battle_report_deleted + message_deleted
    logger.info(
        "Cleaned old data: total=%d (resource_events=%d, arena_exchange_records=%d, battle_reports=%d, "
        "messages=%d, protected_messages=%d)",
        total_deleted,
        resource_deleted,
        arena_exchange_deleted,
        battle_report_deleted,
        message_deleted,
        message_result.protected_count,
    )
    return total_deleted


@shared_task(name="gameplay.decay_prisoner_loyalty")
def decay_prisoner_loyalty_task():
    """
    Daily decay of prisoner loyalty.

    Runs daily, reduces loyalty of all held prisoners by specified amount (default 5).
    Loyalty cannot go below 0.
    """
    from gameplay.constants import PVPConstants
    from gameplay.models import JailPrisoner
    from gameplay.services.jail_expiration import jail_expiration_cutoff

    decay_amount = int(getattr(PVPConstants, "JAIL_LOYALTY_DAILY_DECAY", 5) or 5)
    expiration_cutoff = jail_expiration_cutoff()

    # Keep the subtraction out of the low-loyalty branch. MySQL evaluates
    # unsigned arithmetic before Greatest(), which can underflow below zero.
    updated = JailPrisoner.objects.filter(
        status=JailPrisoner.Status.HELD,
        captured_at__gt=expiration_cutoff,
    ).update(
        loyalty=Case(
            When(loyalty__lte=decay_amount, then=Value(0)),
            default=F("loyalty") - decay_amount,
        )
    )

    logger.info(
        "Prisoner loyalty daily decay: updated %d prisoners, each reduced by %d",
        updated,
        decay_amount,
    )
    return updated


@shared_task(name="gameplay.cleanup_expired_jail_prisoners")
def cleanup_expired_jail_prisoners_task(
    as_of: str | datetime | None = None,
    batch_size: int = JAIL_CLEANUP_DEFAULT_BATCH_SIZE,
    max_batches: int = JAIL_CLEANUP_DEFAULT_MAX_BATCHES,
) -> dict[str, object]:
    """Release 30-day-expired prisoners across real and virtual-player manors."""
    frozen_as_of = _jail_cleanup_as_of(as_of)
    result = cleanup_expired_jail_prisoners(
        as_of=frozen_as_of,
        batch_size=batch_size,
        max_batches=max_batches,
    )
    payload = result.to_payload()
    logger.info(
        "Cleaned expired jail prisoners: as_of=%s cutoff=%s released=%d skipped=%d failed=%d",
        frozen_as_of.isoformat(),
        payload["cutoff"],
        payload["released"],
        payload["skipped"],
        payload["failed"],
    )
    return payload
