from __future__ import annotations

import math
from typing import Any

from core.utils.imports import is_missing_target_import


def refresh_mission_runs(
    manor,
    *,
    prefer_async: bool = False,
    mission_run_model,
    settings_obj,
    logger,
    now_func,
    try_dispatch_mission_refresh_task,
    finalize_mission_run,
) -> None:
    now = now_func()
    due_run_ids = list(
        manor.mission_runs.filter(
            status=mission_run_model.Status.ACTIVE,
            return_at__isnull=False,
            return_at__lte=now,
        ).values_list("id", flat=True)
    )
    if not due_run_ids:
        return

    sync_run_ids = due_run_ids
    raw_sync_max_runs = getattr(settings_obj, "MISSION_REFRESH_SYNC_MAX_RUNS", 3)
    if isinstance(raw_sync_max_runs, bool):
        raise AssertionError(f"invalid mission refresh sync max runs: {raw_sync_max_runs!r}")
    try:
        sync_max_runs = int(raw_sync_max_runs)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission refresh sync max runs: {raw_sync_max_runs!r}") from exc
    if sync_max_runs < 0:
        raise AssertionError(f"invalid mission refresh sync max runs: {raw_sync_max_runs!r}")
    should_try_async = prefer_async or len(due_run_ids) > sync_max_runs

    if should_try_async:
        try:
            from gameplay.tasks import complete_mission_task
        except ImportError as exc:
            if not is_missing_target_import(exc, "gameplay.tasks"):
                raise
            logger.warning(
                "Failed to import mission task, falling back to sync refresh: %s",
                exc,
                exc_info=True,
                extra={"degraded": True, "component": "mission_task_import"},
            )
        else:
            sync_run_ids = []
            for run_id in due_run_ids:
                if not try_dispatch_mission_refresh_task(complete_mission_task, run_id):
                    sync_run_ids.append(run_id)

            if not sync_run_ids:
                return

    active_runs = list(
        mission_run_model.objects.select_related("mission")
        .prefetch_related("guests")
        .filter(id__in=sync_run_ids)
        .order_by("return_at")
    )
    for run in active_runs:
        finalize_mission_run(run, now=now)


def schedule_mission_completion(
    run: Any,
    *,
    logger,
    now_func,
    safe_apply_async,
    finalize_mission_run,
) -> None:
    if run.return_at is None:
        raise RuntimeError("Mission run was not created correctly")

    countdown = math.ceil((run.return_at - now_func()).total_seconds())
    if countdown < 0:
        raise AssertionError("mission completion return_at cannot be in the past")
    try:
        from gameplay.tasks import complete_mission_task
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning(
            "Unable to import complete_mission_task; relying on sync fallback when due: %s",
            exc,
            exc_info=True,
            extra={"degraded": True, "component": "mission_task_import"},
        )
        if countdown == 0:
            finalize_mission_run(run)
        return

    dispatched = safe_apply_async(
        complete_mission_task,
        args=[run.id],
        countdown=countdown,
        defer_long_eta=True,
        logger=logger,
        log_message="complete_mission_task dispatch failed; relying on refresh_mission_runs",
    )
    if not dispatched and countdown == 0:
        logger.warning("complete_mission_task dispatch failed for due run; finalizing synchronously: run_id=%s", run.id)
        finalize_mission_run(run)
