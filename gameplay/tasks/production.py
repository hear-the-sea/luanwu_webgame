from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from common.utils.celery import safe_apply_async_with_dedup
from core.exceptions import InsufficientSpaceError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)

from ._scheduled import DEFAULT_TASK_DEDUP_TIMEOUT, count_finalized_records, maybe_reschedule_for_future

logger = logging.getLogger(__name__)


class ProductionTaskRetryRequested(RuntimeError):
    """Explicit retry marker for infrastructure-driven production task failures."""


PRODUCTION_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    ProductionTaskRetryRequested,
    InsufficientSpaceError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


# ============ Horse Production ============


@shared_task(name="gameplay.complete_horse_production", bind=True, max_retries=2, default_retry_delay=30)
def complete_horse_production(self, production_id: int):
    """
    Complete horse production background task.
    """
    from gameplay.models import HorseProduction
    from gameplay.services.buildings.stable import finalize_horse_production

    try:
        production = HorseProduction.objects.select_related("manor", "manor__user").filter(pk=production_id).first()
        if not production:
            logger.warning("HorseProduction %d not found", production_id)
            return "not_found"

        try:
            rescheduled, _now = maybe_reschedule_for_future(
                task_func=complete_horse_production,
                record_id=production_id,
                eta_value=production.complete_at,
                dedup_key=f"production:horse:{production_id}",
                schedule_func=safe_apply_async_with_dedup,
                logger=logger,
                now_func=timezone.now,
                log_message=f"horse production reschedule failed: id={production_id}",
                failure_message=f"horse production reschedule dispatch failed: id={production_id}",
                dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
            )
        except RuntimeError as exc:
            if str(exc) != f"horse production reschedule dispatch failed: id={production_id}":
                raise
            raise ProductionTaskRetryRequested(str(exc)) from exc
        if rescheduled is not None:
            return rescheduled

        finalized = finalize_horse_production(production, send_notification=True)
        return "completed" if finalized else "skipped"
    except PRODUCTION_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete horse production %d: %s", production_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_horse_productions")
def scan_horse_productions(limit: int = 200):
    """
    Scan and complete all overdue horse productions (for worker downtime recovery).
    """
    from gameplay.models import HorseProduction
    from gameplay.services.buildings.stable import finalize_horse_production

    now = timezone.now()
    qs = (
        HorseProduction.objects.select_related("manor", "manor__user")
        .filter(status=HorseProduction.Status.PRODUCING, complete_at__lte=now)
        .order_by("complete_at")[:limit]
    )
    return count_finalized_records(
        qs,
        finalize=lambda production: finalize_horse_production(production, send_notification=True),
        logger=logger,
        error_message="Failed to finalize horse production %s: %s",
        expected_exceptions=PRODUCTION_TASK_RETRY_EXCEPTIONS,
    )


# ============ Livestock Production ============


@shared_task(name="gameplay.complete_livestock_production", bind=True, max_retries=2, default_retry_delay=30)
def complete_livestock_production(self, production_id: int):
    """
    Complete livestock production background task.
    """
    from gameplay.models import LivestockProduction
    from gameplay.services.buildings.ranch import finalize_livestock_production

    try:
        production = LivestockProduction.objects.select_related("manor", "manor__user").filter(pk=production_id).first()
        if not production:
            logger.warning("LivestockProduction %d not found", production_id)
            return "not_found"

        try:
            rescheduled, _now = maybe_reschedule_for_future(
                task_func=complete_livestock_production,
                record_id=production_id,
                eta_value=production.complete_at,
                dedup_key=f"production:livestock:{production_id}",
                schedule_func=safe_apply_async_with_dedup,
                logger=logger,
                now_func=timezone.now,
                log_message=f"livestock production reschedule failed: id={production_id}",
                failure_message=f"livestock production reschedule dispatch failed: id={production_id}",
                dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
            )
        except RuntimeError as exc:
            if str(exc) != f"livestock production reschedule dispatch failed: id={production_id}":
                raise
            raise ProductionTaskRetryRequested(str(exc)) from exc
        if rescheduled is not None:
            return rescheduled

        finalized = finalize_livestock_production(production, send_notification=True)
        return "completed" if finalized else "skipped"
    except PRODUCTION_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete livestock production %d: %s", production_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_livestock_productions")
def scan_livestock_productions(limit: int = 200):
    """
    Scan and complete all overdue livestock productions (for worker downtime recovery).
    """
    from gameplay.models import LivestockProduction
    from gameplay.services.buildings.ranch import finalize_livestock_production

    now = timezone.now()
    qs = (
        LivestockProduction.objects.select_related("manor", "manor__user")
        .filter(status=LivestockProduction.Status.PRODUCING, complete_at__lte=now)
        .order_by("complete_at")[:limit]
    )
    return count_finalized_records(
        qs,
        finalize=lambda production: finalize_livestock_production(production, send_notification=True),
        logger=logger,
        error_message="Failed to finalize livestock production %s: %s",
        expected_exceptions=PRODUCTION_TASK_RETRY_EXCEPTIONS,
    )


# ============ Smelting Production ============


@shared_task(name="gameplay.complete_smelting_production", bind=True, max_retries=2, default_retry_delay=30)
def complete_smelting_production(self, production_id: int):
    """
    Complete metal smelting background task.
    """
    from gameplay.models import SmeltingProduction
    from gameplay.services.buildings.smithy import finalize_smelting_production

    try:
        production = SmeltingProduction.objects.select_related("manor", "manor__user").filter(pk=production_id).first()
        if not production:
            logger.warning("SmeltingProduction %d not found", production_id)
            return "not_found"

        try:
            rescheduled, _now = maybe_reschedule_for_future(
                task_func=complete_smelting_production,
                record_id=production_id,
                eta_value=production.complete_at,
                dedup_key=f"production:smelting:{production_id}",
                schedule_func=safe_apply_async_with_dedup,
                logger=logger,
                now_func=timezone.now,
                log_message=f"smelting production reschedule failed: id={production_id}",
                failure_message=f"smelting production reschedule dispatch failed: id={production_id}",
                dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
            )
        except RuntimeError as exc:
            if str(exc) != f"smelting production reschedule dispatch failed: id={production_id}":
                raise
            raise ProductionTaskRetryRequested(str(exc)) from exc
        if rescheduled is not None:
            return rescheduled

        finalized = finalize_smelting_production(production, send_notification=True)
        return "completed" if finalized else "skipped"
    except PRODUCTION_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete smelting production %d: %s", production_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_smelting_productions")
def scan_smelting_productions(limit: int = 200):
    """
    Scan and complete all overdue metal smelting (for worker downtime recovery).
    """
    from gameplay.models import SmeltingProduction
    from gameplay.services.buildings.smithy import finalize_smelting_production

    now = timezone.now()
    qs = (
        SmeltingProduction.objects.select_related("manor", "manor__user")
        .filter(status=SmeltingProduction.Status.PRODUCING, complete_at__lte=now)
        .order_by("complete_at")[:limit]
    )
    return count_finalized_records(
        qs,
        finalize=lambda production: finalize_smelting_production(production, send_notification=True),
        logger=logger,
        error_message="Failed to finalize smelting production %s: %s",
        expected_exceptions=PRODUCTION_TASK_RETRY_EXCEPTIONS,
    )


# ============ Equipment Forging ============


@shared_task(name="gameplay.complete_equipment_forging", bind=True, max_retries=2, default_retry_delay=30)
def complete_equipment_forging(self, production_id: int):
    """
    Complete equipment forging background task.
    """
    from gameplay.models import EquipmentProduction
    from gameplay.services.buildings.forge import finalize_equipment_forging

    try:
        production = EquipmentProduction.objects.select_related("manor", "manor__user").filter(pk=production_id).first()
        if not production:
            logger.warning("EquipmentProduction %d not found", production_id)
            return "not_found"

        try:
            rescheduled, _now = maybe_reschedule_for_future(
                task_func=complete_equipment_forging,
                record_id=production_id,
                eta_value=production.complete_at,
                dedup_key=f"production:equipment:{production_id}",
                schedule_func=safe_apply_async_with_dedup,
                logger=logger,
                now_func=timezone.now,
                log_message=f"equipment forging reschedule failed: id={production_id}",
                failure_message=f"equipment forging reschedule dispatch failed: id={production_id}",
                dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
            )
        except RuntimeError as exc:
            if str(exc) != f"equipment forging reschedule dispatch failed: id={production_id}":
                raise
            raise ProductionTaskRetryRequested(str(exc)) from exc
        if rescheduled is not None:
            return rescheduled

        finalized = finalize_equipment_forging(production, send_notification=True)
        return "completed" if finalized else "skipped"
    except PRODUCTION_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete equipment forging %d: %s", production_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="gameplay.scan_equipment_forgings")
def scan_equipment_forgings(limit: int = 200):
    """
    Scan and complete all overdue equipment forging (for worker downtime recovery).
    """
    from gameplay.models import EquipmentProduction
    from gameplay.services.buildings.forge import finalize_equipment_forging

    now = timezone.now()
    qs = (
        EquipmentProduction.objects.select_related("manor", "manor__user")
        .filter(status=EquipmentProduction.Status.FORGING, complete_at__lte=now)
        .order_by("complete_at")[:limit]
    )
    return count_finalized_records(
        qs,
        finalize=lambda production: finalize_equipment_forging(production, send_notification=True),
        logger=logger,
        error_message="Failed to finalize equipment forging %s: %s",
        expected_exceptions=PRODUCTION_TASK_RETRY_EXCEPTIONS,
    )


# ============ Work Assignments ============


@shared_task(name="gameplay.complete_work_assignments")
def complete_work_assignments_task():
    """
    Periodically complete expired work assignments.
    Runs every minute.
    """
    from gameplay.services.work import complete_work_assignments

    count = complete_work_assignments()
    return f"完成 {count} 个打工任务"
