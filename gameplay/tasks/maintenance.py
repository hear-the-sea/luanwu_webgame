from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from celery import shared_task
from django.conf import settings
from django.db.models import Case, F, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.config import MESSAGE
from gameplay.record_retention import (
    MAX_RECORD_RETENTION_DAYS,
    bounded_retention_days,
    delete_eligible_record_batch,
    eligible_record_queryset,
)
from gameplay.services.jail import (
    JAIL_CLEANUP_DEFAULT_BATCH_SIZE,
    JAIL_CLEANUP_DEFAULT_MAX_BATCHES,
    cleanup_expired_jail_prisoners,
)

logger = logging.getLogger(__name__)

RESOURCE_EVENT_RETENTION_DAYS = MAX_RECORD_RETENTION_DAYS
ARENA_EXCHANGE_RETENTION_DAYS = MAX_RECORD_RETENTION_DAYS
BATTLE_REPORT_RETENTION_DAYS = MAX_RECORD_RETENTION_DAYS
ARENA_FILL_SERVICE_RETENTION_DAYS = bounded_retention_days(
    getattr(settings, "ARENA_FILL_SERVICE_RETENTION_DAYS", MAX_RECORD_RETENTION_DAYS)
)
VIRTUAL_PLAYER_SAFETY_AUDIT_RETENTION_DAYS = bounded_retention_days(
    getattr(settings, "VIRTUAL_PLAYER_SAFETY_AUDIT_RETENTION_DAYS", MAX_RECORD_RETENTION_DAYS)
)
CLEANUP_TASK_BATCH_SIZE = 500


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
    batch_size: int | None = None,
) -> int:
    """Delete at most one scheduled batch of rows older than cutoff."""
    effective_batch_size = CLEANUP_TASK_BATCH_SIZE if batch_size is None else batch_size
    filter_kwargs = {f"{time_field}__lt": cutoff}
    ids_to_delete = list(
        model.objects.filter(**filter_kwargs)
        .order_by(time_field, "pk")
        .values_list("pk", flat=True)[:effective_batch_size]
    )
    if not ids_to_delete:
        return 0
    deleted, _ = model.objects.filter(pk__in=ids_to_delete, **filter_kwargs).delete()
    return int(deleted)


@shared_task(name="gameplay.cleanup_old_data")
def cleanup_old_data_task():
    """
    Clean up expired transaction records to save database space.

    Runs hourly and deletes one bounded batch per model:
    - Historical transaction records: keep no more than 15 days
    - Message: keep MESSAGE.RETENTION_DAYS days, except unclaimed attachments
    - Terminal guest, mission, and virtual-player records: keep no more than 15 days
    - Open work, unpublished evidence, and uncertain commits remain protected
    """
    from battle.models import BattleReport
    from gameplay.models import ArenaExchangeRecord, BotSafetyDecisionAudit, ResourceEvent
    from gameplay.services.arena.fill_service_retention import cleanup_fill_service_rows
    from gameplay.services.utils.messages import delete_expired_messages

    now = timezone.now()

    resource_cutoff = now - timedelta(days=RESOURCE_EVENT_RETENTION_DAYS)
    arena_exchange_cutoff = now - timedelta(days=ARENA_EXCHANGE_RETENTION_DAYS)
    battle_report_cutoff = now - timedelta(days=BATTLE_REPORT_RETENTION_DAYS)
    fill_service_cutoff = now - timedelta(days=ARENA_FILL_SERVICE_RETENTION_DAYS)
    safety_audit_cutoff = now - timedelta(days=VIRTUAL_PLAYER_SAFETY_AUDIT_RETENTION_DAYS)
    message_retention_days = min(MAX_RECORD_RETENTION_DAYS, int(MESSAGE.RETENTION_DAYS))
    message_cutoff = now - timedelta(days=message_retention_days)

    resource_deleted = _batched_delete_before(ResourceEvent, time_field="created_at", cutoff=resource_cutoff)
    arena_exchange_deleted = _batched_delete_before(
        ArenaExchangeRecord, time_field="created_at", cutoff=arena_exchange_cutoff
    )
    battle_report_deleted = _batched_delete_before(BattleReport, time_field="created_at", cutoff=battle_report_cutoff)
    message_result = delete_expired_messages(
        message_cutoff,
        batch_size=CLEANUP_TASK_BATCH_SIZE,
        max_batches=1,
    )
    message_deleted = message_result.deleted_count
    fill_service_deleted = 0
    fill_service_episodes_deleted = 0
    fill_service_outcomes_deleted = 0
    fill_batch = cleanup_fill_service_rows(
        cutoff=fill_service_cutoff,
        batch_size=CLEANUP_TASK_BATCH_SIZE,
    )
    fill_service_deleted += fill_batch.deleted
    fill_service_episodes_deleted += fill_batch.episodes
    fill_service_outcomes_deleted += fill_batch.outcomes
    safety_audit_deleted = _batched_delete_before(
        BotSafetyDecisionAudit,
        time_field="created_at",
        cutoff=safety_audit_cutoff,
    )

    terminal_deleted: dict[str, int] = {}
    for model_path, time_field in (
        ("guests.GuestRecruitment", "finished_at"),
        ("gameplay.MissionRun", "completed_at"),
        ("gameplay.BotMaintenanceCompletionEvent", "processed_at"),
        ("gameplay.BotMaintenanceCycle", "completed_at"),
        ("gameplay.BotMaintenanceAttempt", "completed_at"),
    ):
        terminal_cutoff = now - timedelta(days=MAX_RECORD_RETENTION_DAYS)
        queryset = eligible_record_queryset(model_path, cutoff=terminal_cutoff)
        ids = list(queryset.order_by(time_field, "pk").values_list("pk", flat=True)[:CLEANUP_TASK_BATCH_SIZE])
        terminal_deleted[model_path] = delete_eligible_record_batch(
            model_path,
            cutoff=terminal_cutoff,
            record_ids=ids,
        )

    total_deleted = (
        resource_deleted
        + arena_exchange_deleted
        + battle_report_deleted
        + message_deleted
        + fill_service_deleted
        + safety_audit_deleted
        + sum(terminal_deleted.values())
    )
    logger.info(
        "Cleaned old data: total=%d (resource_events=%d, arena_exchange_records=%d, battle_reports=%d, "
        "messages=%d, protected_messages=%d, fill_service_episodes=%d, fill_service_outcomes=%d, "
        "safety_audits=%d, terminal_records=%s, scheduled_batch_size=%d)",
        total_deleted,
        resource_deleted,
        arena_exchange_deleted,
        battle_report_deleted,
        message_deleted,
        message_result.protected_count,
        fill_service_episodes_deleted,
        fill_service_outcomes_deleted,
        safety_audit_deleted,
        terminal_deleted,
        CLEANUP_TASK_BATCH_SIZE,
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
