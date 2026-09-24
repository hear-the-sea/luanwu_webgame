from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from django.db import DatabaseError
from django.db.models import Count, F, Q

from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

CACHE_THROTTLE_ERRORS = CACHE_INFRASTRUCTURE_EXCEPTIONS


def cleanup_local_fallback_cache(
    local_refresh_fallback: dict[int, float],
    *,
    max_size: int,
    cleanup_batch: int,
    evict_count: int,
    now_monotonic: float,
    stale_threshold: float,
) -> None:
    stale_before = now_monotonic - stale_threshold

    stale_keys = [key for key, ts in local_refresh_fallback.items() if ts < stale_before]
    for key in stale_keys[:cleanup_batch]:
        local_refresh_fallback.pop(key, None)

    if len(local_refresh_fallback) > max_size:
        sorted_items = sorted(local_refresh_fallback.items(), key=lambda item: item[1])
        for key, _ in sorted_items[:evict_count]:
            local_refresh_fallback.pop(key, None)


def should_skip_refresh_by_local_fallback(
    local_refresh_fallback: dict[int, float],
    *,
    state_lock: Lock,
    max_size: int,
    cleanup_batch: int,
    evict_count: int,
    manor_id: int,
    min_interval: int,
    monotonic_func: Callable[[], float],
) -> bool:
    if manor_id <= 0 or min_interval <= 0:
        return False

    now_monotonic = monotonic_func()
    stale_threshold = max(min_interval * 2, 60)

    with state_lock:
        last_refresh = local_refresh_fallback.get(manor_id)
        if last_refresh is not None and now_monotonic - last_refresh < min_interval:
            return True

        local_refresh_fallback[manor_id] = now_monotonic
        if len(local_refresh_fallback) > max_size:
            cleanup_local_fallback_cache(
                local_refresh_fallback,
                max_size=max_size,
                cleanup_batch=cleanup_batch,
                evict_count=evict_count,
                now_monotonic=now_monotonic,
                stale_threshold=stale_threshold,
            )

    return False


def has_due_manor_refresh_work(
    *,
    mission_run_model: Any,
    scout_record_model: Any,
    raid_run_model: Any,
    arena_tournament_model: Any,
    manor_id: int,
    now: Any,
    logger: Any,
) -> bool:
    if manor_id <= 0:
        return False

    checks = (
        mission_run_model.objects.filter(
            manor_id=manor_id,
            status=mission_run_model.Status.ACTIVE,
            return_at__isnull=False,
            return_at__lte=now,
        ),
        scout_record_model.objects.filter(attacker_id=manor_id).filter(
            Q(status=scout_record_model.Status.SCOUTING, complete_at__lte=now)
            | Q(status=scout_record_model.Status.RETURNING, return_at__lte=now)
        ),
        raid_run_model.objects.filter(attacker_id=manor_id).filter(
            Q(status=raid_run_model.Status.MARCHING, battle_at__lte=now)
            | Q(status=raid_run_model.Status.RETURNING, return_at__lte=now)
            | Q(status=raid_run_model.Status.RETREATED, return_at__lte=now)
        ),
        arena_tournament_model.objects.filter(status=arena_tournament_model.Status.RECRUITING)
        .annotate(
            total_entry_count=Count("entries", distinct=True),
            manor_entry_count=Count("entries", filter=Q(entries__manor_id=manor_id), distinct=True),
        )
        .filter(manor_entry_count__gt=0, total_entry_count__gte=F("player_limit")),
        arena_tournament_model.objects.filter(
            status=arena_tournament_model.Status.RUNNING,
            next_round_at__isnull=False,
            next_round_at__lte=now,
        )
        .annotate(manor_entry_count=Count("entries", filter=Q(entries__manor_id=manor_id), distinct=True))
        .filter(manor_entry_count__gt=0),
    )

    for queryset in checks:
        try:
            if queryset.exists():
                return True
        except DatabaseError:
            logger.warning("due manor refresh work check failed: manor_id=%s", manor_id, exc_info=True)
    return False


def refresh_manor_state(
    manor: Any,
    *,
    prefer_async: bool,
    include_activity_refresh: bool,
    settings_obj: Any,
    cache_backend: Any,
    logger: Any,
    timezone_module: Any,
    finalize_upgrades_func: Callable[[Any], int],
    has_due_manor_refresh_work_func: Callable[..., bool],
    should_skip_refresh_by_local_fallback_func: Callable[[int, int], bool],
    sync_resource_production_func: Callable[[Any], None],
    refresh_mission_runs_func: Callable[..., int],
    refresh_scout_records_func: Callable[..., int],
    refresh_raid_runs_func: Callable[..., int],
    refresh_arena_activity_func: Callable[..., Any],
) -> None:
    finalize_upgrades_func(manor)

    min_interval = getattr(settings_obj, "MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS", 0)
    if min_interval > 0:
        now = timezone_module.now()
        cache_key = f"manor:refresh:{manor.pk}"
        try:
            if not cache_backend.add(cache_key, "1", timeout=min_interval):
                if not include_activity_refresh or not has_due_manor_refresh_work_func(manor.pk, now=now):
                    return
        except CACHE_THROTTLE_ERRORS as exc:
            logger.warning("缓存操作失败，降级为本地节流: %s", exc, exc_info=True)
            if should_skip_refresh_by_local_fallback_func(manor.pk, min_interval):
                if not include_activity_refresh or not has_due_manor_refresh_work_func(manor.pk, now=now):
                    return

    sync_resource_production_func(manor)
    if not include_activity_refresh:
        return
    if prefer_async:
        refresh_mission_runs_func(manor, prefer_async=True)
        refresh_scout_records_func(manor, prefer_async=True)
        refresh_raid_runs_func(manor, prefer_async=True)
    else:
        refresh_mission_runs_func(manor)
        refresh_scout_records_func(manor)
        refresh_raid_runs_func(manor)
    refresh_arena_activity_func(manor, now=timezone_module.now())


__all__ = [
    "cleanup_local_fallback_cache",
    "has_due_manor_refresh_work",
    "refresh_manor_state",
    "should_skip_refresh_by_local_fallback",
]
