from __future__ import annotations

import logging
import math

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from common.utils.celery import safe_apply_async_with_dedup
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)

logger = logging.getLogger(__name__)


class PvpTaskRetryRequested(RuntimeError):
    """Explicit retry marker for infrastructure-driven reschedule failures."""


PVP_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    PvpTaskRetryRequested,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def _dedup_timeout_for_countdown(countdown: int) -> int:
    return max(int(countdown) + 60, 5)


# ============ Scout Tasks ============


@shared_task(name="gameplay.complete_scout", bind=True, max_retries=2, default_retry_delay=30)
def complete_scout_task(self, record_id: int):
    """
    Complete scout background task.
    """
    from gameplay.models import ScoutRecord
    from gameplay.services.raid import finalize_scout

    try:
        record = ScoutRecord.objects.select_related("attacker", "defender").filter(pk=record_id).first()
        if not record:
            logger.warning("ScoutRecord %d not found", record_id)
            return "not_found"

        now = timezone.now()
        if record.status != ScoutRecord.Status.SCOUTING:
            return "already_completed"

        if record.complete_at and record.complete_at > now:
            remaining = math.ceil((record.complete_at - now).total_seconds())
            if remaining > 0:
                # 使用去重机制避免并发重复调度
                dispatched = safe_apply_async_with_dedup(
                    complete_scout_task,
                    dedup_key=f"pvp:scout:complete:{record_id}",
                    dedup_timeout=_dedup_timeout_for_countdown(remaining),
                    args=[record_id],
                    countdown=remaining,
                    logger=logger,
                    log_message=f"scout task reschedule failed: record_id={record_id}",
                )
                if not dispatched:
                    raise PvpTaskRetryRequested(f"scout reschedule dispatch failed: record_id={record_id}")
                return "rescheduled"

        finalize_scout(record, now=now)
        return "completed"
    except PVP_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete scout %d: %s", record_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.complete_scout_return", bind=True, max_retries=2, default_retry_delay=30)
def complete_scout_return_task(self, record_id: int):
    """
    Complete scout return background task.

    After scout return completes, send result message to attacker.
    """
    from gameplay.models import ScoutRecord
    from gameplay.services.raid import finalize_scout_return

    try:
        record = ScoutRecord.objects.select_related("attacker", "defender").filter(pk=record_id).first()
        if not record:
            logger.warning("ScoutRecord %d not found", record_id)
            return "not_found"

        now = timezone.now()
        if record.status != ScoutRecord.Status.RETURNING:
            return "invalid_status"

        if record.return_at and record.return_at > now:
            remaining = math.ceil((record.return_at - now).total_seconds())
            if remaining > 0:
                # 使用去重机制避免并发重复调度
                dispatched = safe_apply_async_with_dedup(
                    complete_scout_return_task,
                    dedup_key=f"pvp:scout:return:{record_id}",
                    dedup_timeout=_dedup_timeout_for_countdown(remaining),
                    args=[record_id],
                    countdown=remaining,
                    logger=logger,
                    log_message=f"scout return task reschedule failed: record_id={record_id}",
                )
                if not dispatched:
                    raise PvpTaskRetryRequested(f"scout return reschedule dispatch failed: record_id={record_id}")
                return "rescheduled"

        finalize_scout_return(record, now=now)
        return "completed"
    except PVP_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete scout return %d: %s", record_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_scout_records")
def scan_scout_records(limit: int = 200):
    """
    Scan and complete all overdue scout tasks (for worker downtime recovery).

    Handles two statuses:
    - SCOUTING: Outbound arrival, determine success/failure, enter return
    - RETURNING: Return complete, send result message to attacker
    """
    from gameplay.models import ScoutRecord
    from gameplay.services.raid import finalize_scout, finalize_scout_return

    now = timezone.now()
    count = 0

    # Handle outbound arrival (SCOUTING -> RETURNING)
    scouting_qs = (
        ScoutRecord.objects.select_related("attacker", "defender")
        .filter(status=ScoutRecord.Status.SCOUTING, complete_at__lte=now)
        .order_by("complete_at")[:limit]
    )
    for record in scouting_qs:
        try:
            finalize_scout(record, now=now)
            count += 1
        except PVP_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to finalize scout record %d", record.id)

    # Handle return complete (RETURNING -> SUCCESS/FAILED)
    returning_qs = (
        ScoutRecord.objects.select_related("attacker", "defender")
        .filter(status=ScoutRecord.Status.RETURNING, return_at__lte=now)
        .order_by("return_at")[:limit]
    )
    for record in returning_qs:
        try:
            finalize_scout_return(record, now=now)
            count += 1
        except PVP_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to finalize scout return %d", record.id)

    return count


# ============ Raid Tasks ============


@shared_task(name="gameplay.process_raid_battle", bind=True, max_retries=2, default_retry_delay=30)
def process_raid_battle_task(self, run_id: int):
    """
    Process raid battle background task.
    """
    from gameplay.models import RaidRun
    from gameplay.services.raid import process_raid_battle

    try:
        run = (
            RaidRun.objects.select_related("attacker", "defender").prefetch_related("guests").filter(pk=run_id).first()
        )
        if not run:
            logger.warning("RaidRun %d not found", run_id)
            return "not_found"

        now = timezone.now()
        if run.status not in [RaidRun.Status.MARCHING, RaidRun.Status.RETREATED]:
            return "invalid_status"

        # Retreating troops should not be settled early at battle_at; wait for return_at
        if run.status == RaidRun.Status.RETREATED:
            if run.return_at and run.return_at > now:
                remaining = math.ceil((run.return_at - now).total_seconds())
                if remaining > 0:
                    dispatched = safe_apply_async_with_dedup(
                        complete_raid_task,
                        dedup_key=f"pvp:raid:complete:{run_id}",
                        dedup_timeout=_dedup_timeout_for_countdown(remaining),
                        args=[run_id],
                        countdown=remaining,
                        logger=logger,
                        log_message=f"raid complete task reschedule failed: run_id={run_id}",
                    )
                    if not dispatched:
                        raise PvpTaskRetryRequested(f"raid complete reschedule dispatch failed: run_id={run_id}")
                    return "retreated_rescheduled"
            dispatched = safe_apply_async_with_dedup(
                complete_raid_task,
                dedup_key=f"pvp:raid:complete:{run_id}",
                dedup_timeout=_dedup_timeout_for_countdown(0),
                args=[run_id],
                countdown=0,
                logger=logger,
                log_message=f"raid complete task forward failed: run_id={run_id}",
            )
            if not dispatched:
                raise PvpTaskRetryRequested(f"raid complete forward dispatch failed: run_id={run_id}")
            return "retreated_forwarded"

        if run.status == RaidRun.Status.MARCHING and run.battle_at and run.battle_at > now:
            remaining = math.ceil((run.battle_at - now).total_seconds())
            if remaining > 0:
                dispatched = safe_apply_async_with_dedup(
                    process_raid_battle_task,
                    dedup_key=f"pvp:raid:battle:{run_id}",
                    dedup_timeout=_dedup_timeout_for_countdown(remaining),
                    args=[run_id],
                    countdown=remaining,
                    logger=logger,
                    log_message=f"raid battle task reschedule failed: run_id={run_id}",
                )
                if not dispatched:
                    raise PvpTaskRetryRequested(f"raid battle reschedule dispatch failed: run_id={run_id}")
                return "rescheduled"

        process_raid_battle(run, now=now)
        return "completed"
    except PVP_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to process raid battle %d: %s", run_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.complete_raid", bind=True, max_retries=2, default_retry_delay=30)
def complete_raid_task(self, run_id: int):
    """
    Complete raid return background task.
    """
    from gameplay.models import RaidRun
    from gameplay.services.raid import finalize_raid

    try:
        run = (
            RaidRun.objects.select_related("attacker", "defender", "battle_report")
            .prefetch_related("guests")
            .filter(pk=run_id)
            .first()
        )
        if not run:
            logger.warning("RaidRun %d not found", run_id)
            return "not_found"

        now = timezone.now()
        if run.status == RaidRun.Status.COMPLETED:
            return "already_completed"

        # Retreated status completes directly
        if run.status == RaidRun.Status.RETREATED:
            if run.return_at and run.return_at > now:
                remaining = math.ceil((run.return_at - now).total_seconds())
                if remaining > 0:
                    dispatched = safe_apply_async_with_dedup(
                        complete_raid_task,
                        dedup_key=f"pvp:raid:complete:{run_id}",
                        dedup_timeout=_dedup_timeout_for_countdown(remaining),
                        args=[run_id],
                        countdown=remaining,
                        logger=logger,
                        log_message=f"raid complete task reschedule failed: run_id={run_id}",
                    )
                    if not dispatched:
                        raise PvpTaskRetryRequested(f"raid complete reschedule dispatch failed: run_id={run_id}")
                    return "rescheduled"
            finalized = finalize_raid(run, now=now)
            return "completed" if finalized is not False else "capacity_blocked"

        # Returning status checks time
        if run.status == RaidRun.Status.RETURNING:
            if run.return_at and run.return_at > now:
                remaining = math.ceil((run.return_at - now).total_seconds())
                if remaining > 0:
                    dispatched = safe_apply_async_with_dedup(
                        complete_raid_task,
                        dedup_key=f"pvp:raid:complete:{run_id}",
                        dedup_timeout=_dedup_timeout_for_countdown(remaining),
                        args=[run_id],
                        countdown=remaining,
                        logger=logger,
                        log_message=f"raid complete task reschedule failed: run_id={run_id}",
                    )
                    if not dispatched:
                        raise PvpTaskRetryRequested(f"raid complete reschedule dispatch failed: run_id={run_id}")
                    return "rescheduled"
            finalized = finalize_raid(run, now=now)
            return "completed" if finalized is not False else "capacity_blocked"

        return "invalid_status"
    except PVP_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete raid %d: %s", run_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_raid_runs")
def scan_raid_runs(limit: int = 200):
    """
    Scan and process all overdue raid tasks (for worker downtime recovery).
    """
    from gameplay.models import RaidRun
    from gameplay.services.raid import finalize_raid, process_raid_battle

    now = timezone.now()
    count = 0

    # A missing battle deadline is an invalid durable state; recover it immediately.
    marching_qs = (
        RaidRun.objects.select_related("attacker", "defender")
        .prefetch_related("guests")
        .filter(Q(battle_at__lte=now) | Q(battle_at__isnull=True), status=RaidRun.Status.MARCHING)
        .order_by("battle_at")[:limit]
    )
    for run in marching_qs:
        try:
            process_raid_battle(run, now=now)
            count += 1
        except PVP_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to process raid battle %d", run.id)

    # Handle returning but completed
    returning_qs = (
        RaidRun.objects.select_related("attacker", "defender", "battle_report")
        .prefetch_related("guests")
        .filter(Q(return_at__lte=now) | Q(return_at__isnull=True), status=RaidRun.Status.RETURNING)
        .order_by("return_at")[:limit]
    )
    for run in returning_qs:
        try:
            if finalize_raid(run, now=now) is not False:
                count += 1
        except PVP_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to finalize raid %d", run.id)

    # Handle retreated but completed
    retreated_qs = (
        RaidRun.objects.select_related("attacker", "defender")
        .prefetch_related("guests")
        .filter(Q(return_at__lte=now) | Q(return_at__isnull=True), status=RaidRun.Status.RETREATED)
        .order_by("return_at")[:limit]
    )
    for run in retreated_qs:
        try:
            if finalize_raid(run, now=now) is not False:
                count += 1
        except PVP_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to finalize retreated raid %d", run.id)

    return count
