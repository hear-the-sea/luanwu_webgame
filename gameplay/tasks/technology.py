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
from gameplay.services.technology import finalize_technology_upgrade
from gameplay.services.virtual_player_core.maintenance_completion import record_virtual_player_maintenance_completion

from ._scheduled import DEFAULT_TASK_DEDUP_TIMEOUT, count_finalized_records, maybe_reschedule_for_future

logger = logging.getLogger(__name__)


class TechnologyTaskRetryRequested(RuntimeError):
    """Explicit retry marker for infrastructure-driven technology task failures."""


TECHNOLOGY_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    TechnologyTaskRetryRequested,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


@shared_task(name="gameplay.complete_technology_upgrade", bind=True, max_retries=2, default_retry_delay=30)
def complete_technology_upgrade(self, tech_id: int):
    """
    Complete technology upgrade background task.
    """
    from gameplay.models import PlayerTechnology

    try:
        tech = PlayerTechnology.objects.select_related("manor", "manor__user").filter(pk=tech_id).first()
        if not tech:
            logger.warning("PlayerTechnology %d not found", tech_id)
            return "not_found"
        try:
            rescheduled, now = maybe_reschedule_for_future(
                task_func=complete_technology_upgrade,
                record_id=tech_id,
                eta_value=tech.upgrade_complete_at,
                dedup_key=f"technology:upgrade:{tech_id}",
                schedule_func=safe_apply_async_with_dedup,
                logger=logger,
                now_func=timezone.now,
                log_message=f"technology upgrade reschedule failed: tech_id={tech_id}",
                failure_message=f"technology reschedule dispatch failed: tech_id={tech_id}",
                dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
            )
        except RuntimeError as exc:
            if str(exc) != f"technology reschedule dispatch failed: tech_id={tech_id}":
                raise
            raise TechnologyTaskRetryRequested(str(exc)) from exc
        if rescheduled is not None:
            return rescheduled
        origin_completed_at = tech.upgrade_complete_at
        finalized = finalize_technology_upgrade(tech, send_notification=True)
        if finalized:
            record_virtual_player_maintenance_completion(
                manor_id=getattr(tech, "manor_id", None),
                domain_event_kind="technology_upgrade",
                domain_object_id=tech_id,
                origin_completed_at=origin_completed_at,
            )
        return "completed" if finalized else "skipped"
    except TECHNOLOGY_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete technology upgrade %d: %s", tech_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_technology_upgrades")
def scan_technology_upgrades(limit: int = 200):
    """
    Scan and complete all overdue technology upgrades (for worker downtime recovery).
    """
    from gameplay.models import PlayerTechnology

    now = timezone.now()
    qs = (
        PlayerTechnology.objects.select_related("manor", "manor__user")
        .filter(is_upgrading=True, upgrade_complete_at__lte=now)
        .order_by("upgrade_complete_at")[:limit]
    )

    def _finalize_technology(tech: PlayerTechnology) -> bool:
        origin_completed_at = getattr(tech, "upgrade_complete_at", None)
        finalized = finalize_technology_upgrade(tech, send_notification=True)
        if finalized:
            record_virtual_player_maintenance_completion(
                manor_id=getattr(tech, "manor_id", None),
                domain_event_kind="technology_upgrade",
                domain_object_id=int(tech.id),
                origin_completed_at=origin_completed_at,
            )
        return finalized

    return count_finalized_records(
        qs,
        finalize=_finalize_technology,
        logger=logger,
        error_message="Failed to finalize technology %s: %s",
        expected_exceptions=TECHNOLOGY_TASK_RETRY_EXCEPTIONS,
    )
