from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.db.models import Case, F, Value, When
from django.utils import timezone

from core.config import MESSAGE

logger = logging.getLogger(__name__)

RESOURCE_EVENT_RETENTION_DAYS = 30
ARENA_EXCHANGE_RETENTION_DAYS = 30
BATTLE_REPORT_RETENTION_DAYS = 30
DELETE_BATCH_SIZE = 10000


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

    decay_amount = int(getattr(PVPConstants, "JAIL_LOYALTY_DAILY_DECAY", 5) or 5)

    # Keep the subtraction out of the low-loyalty branch. MySQL evaluates
    # unsigned arithmetic before Greatest(), which can underflow below zero.
    updated = JailPrisoner.objects.filter(status=JailPrisoner.Status.HELD).update(
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
