from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.arena.core import (
    cleanup_expired_tournaments,
    run_due_arena_rounds,
    start_due_virtual_backfill_tournaments,
    start_ready_tournaments,
)

logger = logging.getLogger(__name__)


@shared_task(name="gameplay.scan_arena_tournaments")
def scan_arena_tournaments(limit: int = 20) -> dict[str, int]:
    started = 0
    processed = 0
    virtual_started = 0
    cleaned = 0
    failed_stages: list[str] = []

    try:
        started = start_ready_tournaments(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena tournament start scan failed")
        failed_stages.append("start_ready_tournaments")

    try:
        virtual_started = start_due_virtual_backfill_tournaments(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena virtual tournament backfill scan failed")
        failed_stages.append("start_due_virtual_backfill_tournaments")

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
        "virtual_started": int(virtual_started),
        "processed_rounds": int(processed),
        "cleaned_tournaments": int(cleaned),
    }


@shared_task(name="gameplay.scan_arena_coop_events")
def scan_arena_coop_events(limit: int = 20) -> dict[str, int]:
    virtual_coop_prepared = 0
    processed_coop = 0
    cleaned_coop = 0
    failed_stages: list[str] = []

    try:
        virtual_coop_prepared = arena_coop_core.start_due_virtual_backfill_coop_events(limit=limit)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("arena virtual coop backfill scan failed")
        failed_stages.append("start_due_virtual_backfill_coop_events")

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
        "virtual_coop_prepared": int(virtual_coop_prepared),
        "processed_coop_events": int(processed_coop),
        "cleaned_coop_events": int(cleaned_coop),
    }
