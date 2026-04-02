from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.arena.coop_core import (
    ARENA_COOP_COMPLETED_RETENTION_SECONDS,
    cleanup_expired_arena_coop_events,
    run_due_arena_coop_events,
)
from gameplay.services.arena.core import cleanup_expired_tournaments, run_due_arena_rounds, start_ready_tournaments

logger = logging.getLogger(__name__)


@shared_task(name="gameplay.scan_arena_tournaments")
def scan_arena_tournaments(limit: int = 20) -> dict[str, int]:
    started = 0
    processed = 0
    processed_coop = 0
    cleaned = 0
    cleaned_coop = 0
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

    try:
        processed_coop = run_due_arena_coop_events(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena coop scan failed")
        failed_stages.append("run_due_arena_coop_events")

    try:
        cleaned_coop = cleanup_expired_arena_coop_events(
            now=timezone.now(),
            grace_seconds=ARENA_COOP_COMPLETED_RETENTION_SECONDS,
            limit=max(20, int(limit)),
        )
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena coop cleanup failed")
        failed_stages.append("cleanup_expired_arena_coop_events")

    if failed_stages:
        raise RuntimeError(f"arena scan failed stages: {', '.join(failed_stages)}")

    return {
        "started": int(started),
        "processed_rounds": int(processed),
        "processed_coop_events": int(processed_coop),
        "cleaned_tournaments": int(cleaned),
        "cleaned_coop_events": int(cleaned_coop),
    }
