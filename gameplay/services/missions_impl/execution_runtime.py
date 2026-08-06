from __future__ import annotations

from typing import Any, Callable


def try_dispatch_mission_refresh_task(
    task: Any,
    run_id: int,
    *,
    safe_apply_async_with_dedup: Callable[..., bool],
    logger: Any,
    dedup_seconds: int,
) -> bool:
    return safe_apply_async_with_dedup(
        task,
        dedup_key=f"mission:refresh_dispatch:{run_id}",
        dedup_timeout=dedup_seconds,
        args=[run_id],
        countdown=0,
        logger=logger,
        log_message=f"mission refresh dispatch failed: run_id={run_id}",
    )


def refresh_mission_runs_entry(
    manor: Any,
    *,
    prefer_async: bool,
    refresh_mission_runs_command: Callable[..., None],
    mission_run_model: Any,
    settings_obj: Any,
    logger: Any,
    now_func: Callable[[], Any],
    try_dispatch_mission_refresh_task: Callable[..., bool],
    finalize_mission_run: Callable[..., object],
) -> None:
    refresh_mission_runs_command(
        manor,
        prefer_async=prefer_async,
        mission_run_model=mission_run_model,
        settings_obj=settings_obj,
        logger=logger,
        now_func=now_func,
        try_dispatch_mission_refresh_task=try_dispatch_mission_refresh_task,
        finalize_mission_run=finalize_mission_run,
    )


def schedule_mission_completion_entry(
    run: Any,
    *,
    schedule_mission_completion_command: Callable[..., None],
    logger: Any,
    now_func: Callable[[], Any],
    safe_apply_async: Callable[..., bool],
    finalize_mission_run: Callable[..., object],
) -> None:
    schedule_mission_completion_command(
        run,
        logger=logger,
        now_func=now_func,
        safe_apply_async=safe_apply_async,
        finalize_mission_run=finalize_mission_run,
    )


def request_retreat_entry(
    run: Any,
    *,
    request_retreat_command: Callable[..., None],
    mission_run_model: Any,
    schedule_mission_completion: Callable[..., None],
) -> None:
    request_retreat_command(
        run,
        mission_run_model=mission_run_model,
        schedule_mission_completion=schedule_mission_completion,
    )


def can_retreat_entry(run: Any, *, can_retreat_command: Callable[..., bool], now: Any = None) -> bool:
    return can_retreat_command(run, now=now)
