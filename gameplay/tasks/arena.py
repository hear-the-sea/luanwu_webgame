from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.arena.core import cleanup_expired_tournaments, run_due_arena_rounds, start_ready_tournaments
from gameplay.services.arena.virtual_reserve import (
    create_due_virtual_reserve_profiles,
    grow_due_virtual_reserves,
    reconcile_coop_demand,
    reconcile_tournament_demand,
    replenish_virtual_reserve,
    scan_virtual_reserve_demands,
)

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
    return scan_virtual_reserve_demands(limit=limit)


@shared_task(name="gameplay.grow_arena_virtual_reserves")
def grow_arena_virtual_reserves(limit: int = 100) -> dict[str, int]:
    grown = grow_due_virtual_reserves(limit=limit)
    created = create_due_virtual_reserve_profiles(limit=limit)
    return {"grown": int(grown), "created": int(created)}
