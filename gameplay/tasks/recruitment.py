from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from common.utils.celery import safe_apply_async_with_dedup
from core.exceptions import TroopCapacityFullError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)

from ._scheduled import DEFAULT_TASK_DEDUP_TIMEOUT, count_finalized_records, maybe_reschedule_for_future

logger = logging.getLogger(__name__)


class RecruitmentTaskRetryRequested(RuntimeError):
    """Explicit retry marker for infrastructure-driven troop recruitment task failures."""


RECRUITMENT_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    RecruitmentTaskRetryRequested,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


@shared_task(name="gameplay.complete_troop_recruitment", bind=True, max_retries=2, default_retry_delay=30)
def complete_troop_recruitment(self, recruitment_id: int):
    """
    Complete troop recruitment background task.
    """
    from core.exceptions.recruitment_extended import (
        TroopRecruitmentNotFoundError,
        TroopRecruitmentNotReadyError,
        TroopTemplateNotFoundError,
    )
    from gameplay.models import TroopRecruitment
    from gameplay.services.recruitment.recruitment import finalize_troop_recruitment

    try:
        recruitment = TroopRecruitment.objects.select_related("manor", "manor__user").filter(pk=recruitment_id).first()
        if not recruitment:
            logger.warning("TroopRecruitment %d not found", recruitment_id)
            return "not_found"

        try:
            rescheduled, _now = maybe_reschedule_for_future(
                task_func=complete_troop_recruitment,
                record_id=recruitment_id,
                eta_value=recruitment.complete_at,
                dedup_key=f"recruitment:troop:{recruitment_id}",
                schedule_func=safe_apply_async_with_dedup,
                logger=logger,
                now_func=timezone.now,
                log_message=f"troop recruitment reschedule failed: id={recruitment_id}",
                failure_message=f"troop recruitment reschedule dispatch failed: id={recruitment_id}",
                dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
            )
        except RuntimeError as exc:
            if str(exc) != f"troop recruitment reschedule dispatch failed: id={recruitment_id}":
                raise
            raise RecruitmentTaskRetryRequested(str(exc)) from exc
        if rescheduled is not None:
            return rescheduled

        try:
            finalize_troop_recruitment(recruitment, send_notification=True)
        except (
            TroopRecruitmentNotFoundError,
            TroopRecruitmentNotReadyError,
            TroopTemplateNotFoundError,
            TroopCapacityFullError,
        ) as exc:
            logger.warning("Failed to finalize troop recruitment %s: %s", recruitment_id, exc)
            return "skipped"
        return "completed"
    except RECRUITMENT_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete troop recruitment %s: %s", recruitment_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_troop_recruitments")
def scan_troop_recruitments(limit: int = 200):
    """
    Scan and complete all overdue troop recruitments (for worker downtime recovery).
    """
    from core.exceptions.recruitment_extended import (
        TroopRecruitmentNotFoundError,
        TroopRecruitmentNotReadyError,
        TroopTemplateNotFoundError,
    )
    from gameplay.models import TroopRecruitment
    from gameplay.services.recruitment.recruitment import finalize_troop_recruitment

    now = timezone.now()
    qs = (
        TroopRecruitment.objects.select_related("manor", "manor__user")
        .filter(status=TroopRecruitment.Status.RECRUITING, complete_at__lte=now)
        .order_by("complete_at")[:limit]
    )

    def _finalize_recruitment(recruitment: TroopRecruitment) -> bool:
        """Wrapper that returns True on success, False on expected business errors."""
        try:
            finalize_troop_recruitment(recruitment, send_notification=True)
            return True
        except (
            TroopRecruitmentNotFoundError,
            TroopRecruitmentNotReadyError,
            TroopTemplateNotFoundError,
            TroopCapacityFullError,
        ):
            # Skip recruitments that can't be finalized
            return False

    return count_finalized_records(
        qs,
        finalize=_finalize_recruitment,
        logger=logger,
        error_message="Failed to finalize troop recruitment %s: %s",
        expected_exceptions=RECRUITMENT_TASK_RETRY_EXCEPTIONS,
    )
