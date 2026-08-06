from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from common.utils.celery import safe_apply_async_with_dedup
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.models import MissionRun
from gameplay.services.missions import finalize_mission_run

from ._scheduled import DEFAULT_TASK_DEDUP_TIMEOUT, maybe_reschedule_for_future

logger = logging.getLogger(__name__)


class MissionTaskRetryRequested(RuntimeError):
    """Explicit retry marker for infrastructure-driven task reschedule failures."""


MISSION_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MissionTaskRetryRequested,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


@shared_task(name="gameplay.complete_mission", bind=True, max_retries=2, default_retry_delay=30)
def complete_mission_task(self, run_id: int):
    try:
        run = MissionRun.objects.select_related("mission", "manor").prefetch_related("guests").filter(pk=run_id).first()
        if not run:
            logger.warning("MissionRun %d not found", run_id)
            return "not_found"
        try:
            rescheduled, now = maybe_reschedule_for_future(
                task_func=complete_mission_task,
                record_id=run_id,
                eta_value=run.return_at,
                dedup_key=f"mission:complete:{run_id}",
                schedule_func=safe_apply_async_with_dedup,
                logger=logger,
                now_func=timezone.now,
                log_message=f"mission task reschedule failed: run_id={run_id}",
                failure_message=f"mission reschedule dispatch failed: run_id={run_id}",
                dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
            )
        except RuntimeError as exc:
            if str(exc) != f"mission reschedule dispatch failed: run_id={run_id}":
                raise
            raise MissionTaskRetryRequested(str(exc)) from exc
        if rescheduled is not None:
            return rescheduled
        finalize_mission_run(run, now=now)
        return "completed"
    except MISSION_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete mission %d: %s", run_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_due_missions")
def scan_due_missions(limit: int = 200) -> int:
    now = timezone.now()
    qs = (
        MissionRun.objects.select_related("mission", "manor", "manor__user")
        .prefetch_related("guests")
        .filter(
            status=MissionRun.Status.ACTIVE,
            return_at__isnull=False,
            return_at__lte=now,
        )
        .order_by("return_at")[:limit]
    )
    count = 0
    for run in qs:
        try:
            if finalize_mission_run(run, now=now):
                count += 1
        except MISSION_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to finalize mission run %d", run.id)
    return count
