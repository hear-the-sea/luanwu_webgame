from __future__ import annotations

import logging
from datetime import datetime

from celery import shared_task
from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.arena.core import cleanup_expired_tournaments, run_due_arena_rounds, start_ready_tournaments
from gameplay.services.arena.virtual_reserve_observability import (
    ARENA_SHORTAGE_METRIC_RETRY_MAX_ATTEMPTS,
    queue_arena_shortage_metric_retry,
    record_arena_shortage_metric_failure,
    record_arena_shortage_observation,
)
from gameplay.services.arena.virtual_reserve_pool import (
    create_due_virtual_reserve_profiles,
    grow_due_virtual_reserves,
    replenish_virtual_reserve,
)
from gameplay.services.arena.virtual_reserve_reconcile import reconcile_coop_demand, reconcile_tournament_demand
from gameplay.services.arena.virtual_reserve_scan import scan_virtual_reserve_demands

logger = logging.getLogger(__name__)


@shared_task(name="gameplay.scan_arena_tournaments")
def scan_arena_tournaments(limit: int = 20) -> dict[str, int]:
    started = 0
    processed = 0
    cleaned = 0
    failed_stages: list[str] = []

    try:
        started = start_ready_tournaments(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena tournament start scan failed")
        failed_stages.append("start_ready_tournaments")

    try:
        processed = run_due_arena_rounds(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena tournament round scan failed")
        failed_stages.append("run_due_arena_rounds")

    try:
        cleaned = cleanup_expired_tournaments(limit=max(20, int(limit)))
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena tournament cleanup failed")
        failed_stages.append("cleanup_expired_tournaments")

    if failed_stages:
        raise RuntimeError(f"arena scan failed stages: {', '.join(failed_stages)}")

    return {
        "started": int(started),
        "processed_rounds": int(processed),
        "cleaned_tournaments": int(cleaned),
    }


@shared_task(name="gameplay.scan_arena_coop_events")
def scan_arena_coop_events(limit: int = 20) -> dict[str, int]:
    processed_coop = 0
    cleaned_coop = 0
    failed_stages: list[str] = []

    try:
        processed_coop = arena_coop_core.run_due_arena_coop_events(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena coop scan failed")
        failed_stages.append("run_due_arena_coop_events")

    try:
        cleaned_coop = arena_coop_core.cleanup_expired_arena_coop_events(
            now=timezone.now(),
            grace_seconds=arena_coop_core.ARENA_COOP_COMPLETED_RETENTION_SECONDS,
            limit=max(20, int(limit)),
        )
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena coop cleanup failed")
        failed_stages.append("cleanup_expired_arena_coop_events")

    if failed_stages:
        raise RuntimeError(f"arena coop scan failed stages: {', '.join(failed_stages)}")

    return {
        "processed_coop_events": int(processed_coop),
        "cleaned_coop_events": int(cleaned_coop),
    }


@shared_task(name="gameplay.reconcile_arena_virtual_reserve")
def reconcile_arena_virtual_reserve(mode: str, event_id: int) -> dict[str, int]:
    if mode == "tournament":
        demand = reconcile_tournament_demand(event_id)
    elif mode == "coop":
        demand = reconcile_coop_demand(event_id)
    else:
        raise ValueError(f"unsupported arena virtual reserve mode: {mode}")
    if demand is None:
        return {"reconciled": 0, "ready": 0, "training": 0}
    result = replenish_virtual_reserve(demand.id)
    return {
        "reconciled": 1,
        "ready": int(result.ready_count),
        "training": int(result.training_count),
    }


@shared_task(name="gameplay.scan_arena_virtual_reserves")
def scan_arena_virtual_reserves(limit: int = 20) -> dict[str, int]:
    try:
        return scan_virtual_reserve_demands(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena virtual reserve scan failed")
        raise RuntimeError("arena virtual reserve scan failed")


@shared_task(name="gameplay.retry_arena_shortage_metric")
def retry_arena_shortage_metric(
    demand_id: int,
    mode: str,
    event_id: int,
    capacity: int,
    missing_count: int,
    population_prestige: int,
    operation_id: str,
    observed_at: str,
    retry_attempt: int = 1,
    real_entry_count: int | None = None,
    virtual_entry_count: int | None = None,
    reserve_ready_count: int | None = None,
    reserve_training_count: int | None = None,
) -> dict[str, int]:
    parsed_observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if timezone.is_naive(parsed_observed_at):
        raise ValueError("observed_at must be timezone-aware")
    try:
        record_arena_shortage_observation(
            demand_id=int(demand_id),
            mode=mode,
            event_id=int(event_id),
            capacity=int(capacity),
            missing_count=int(missing_count),
            population_prestige=int(population_prestige),
            operation_id=operation_id,
            observed_at=parsed_observed_at,
            real_entry_count=real_entry_count,
            virtual_entry_count=virtual_entry_count,
            reserve_ready_count=reserve_ready_count,
            reserve_training_count=reserve_training_count,
        )
    except Exception as exc:
        logger.exception(
            "arena shortage metric retry failed",
            extra={
                "event": "arena_shortage_metric_retry_failed",
                "operation_id": operation_id,
                "retry_attempt": int(retry_attempt),
            },
        )
        try:
            record_arena_shortage_metric_failure(
                operation_id=operation_id,
                observed_at=parsed_observed_at,
                exc=exc,
            )
        except Exception:
            logger.exception(
                "arena shortage metric failure marker could not be persisted",
                extra={
                    "event": "arena_shortage_metric_failure_marker_error",
                    "operation_id": operation_id,
                    "retry_attempt": int(retry_attempt),
                },
            )
        if int(retry_attempt) < ARENA_SHORTAGE_METRIC_RETRY_MAX_ATTEMPTS - 1:
            queued = queue_arena_shortage_metric_retry(
                demand_id=int(demand_id),
                mode=mode,
                event_id=int(event_id),
                capacity=int(capacity),
                missing_count=int(missing_count),
                population_prestige=int(population_prestige),
                operation_id=operation_id,
                observed_at=parsed_observed_at,
                retry_attempt=int(retry_attempt) + 1,
                real_entry_count=real_entry_count,
                virtual_entry_count=virtual_entry_count,
                reserve_ready_count=reserve_ready_count,
                reserve_training_count=reserve_training_count,
            )
            if queued:
                return {"recorded": 0, "retry_scheduled": 1}
        raise RuntimeError("arena shortage metric retry exhausted") from exc
    return {"recorded": 1, "retry_scheduled": 0}


@shared_task(name="gameplay.grow_arena_virtual_reserves")
def grow_arena_virtual_reserves(limit: int = 100) -> dict[str, int]:
    try:
        grown = grow_due_virtual_reserves(limit=limit)
        created = create_due_virtual_reserve_profiles(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena virtual reserve growth failed")
        raise RuntimeError("arena virtual reserve growth failed")
    return {"grown": int(grown), "created": int(created)}
