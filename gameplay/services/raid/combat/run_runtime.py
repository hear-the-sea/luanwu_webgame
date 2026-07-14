from __future__ import annotations

from typing import Any, Callable


def try_dispatch_raid_refresh_task(
    task: Any,
    run_id: int,
    stage: str,
    *,
    safe_apply_async_with_dedup: Callable[..., bool],
    logger: Any,
    dedup_seconds: int,
) -> bool:
    return safe_apply_async_with_dedup(
        task,
        dedup_key=f"pvp:refresh_dispatch:raid:{stage}:{run_id}",
        dedup_timeout=dedup_seconds,
        args=[run_id],
        countdown=0,
        logger=logger,
        log_message=f"raid refresh dispatch failed: stage={stage} run_id={run_id}",
    )


def import_raid_refresh_tasks() -> tuple[Any, Any]:
    from gameplay.tasks import complete_raid_task, process_raid_battle_task

    return complete_raid_task, process_raid_battle_task


def load_locked_raid_run(*, raid_run_model: Any, run_pk: int) -> Any:
    from gameplay.models import Manor

    manor_ids = raid_run_model.objects.filter(pk=run_pk).values_list("attacker_id", "defender_id").first()
    if manor_ids is None:
        return None
    ordered_manor_ids = sorted(set(manor_ids))
    list(Manor.objects.select_for_update().filter(pk__in=ordered_manor_ids).order_by("pk"))
    return (
        raid_run_model.objects.select_for_update()
        .select_related("attacker", "defender", "battle_report")
        .prefetch_related("guests")
        .filter(pk=run_pk)
        .first()
    )


def load_locked_attacker(*, manor_model: Any, attacker_id: int) -> Any:
    return manor_model.objects.select_for_update().get(pk=attacker_id)


def schedule_raid_retreat_completion_entry(
    run_id: int,
    countdown: int,
    *,
    schedule_retreat_completion: Callable[[int, int], None],
) -> None:
    schedule_retreat_completion(run_id, countdown)


def request_raid_retreat_entry(
    run: Any,
    *,
    request_raid_retreat_command: Callable[..., None],
    raid_run_model: Any,
    schedule_retreat_completion: Callable[[int, int], None],
) -> None:
    request_raid_retreat_command(
        run,
        raid_run_model=raid_run_model,
        schedule_retreat_completion=schedule_retreat_completion,
    )


def can_raid_retreat_entry(
    run: Any,
    *,
    can_raid_retreat_command: Callable[..., bool],
    marching_status: Any,
    now: Any = None,
) -> bool:
    return can_raid_retreat_command(run, marching_status=marching_status, now=now)


def refresh_raid_runs_entry(
    manor: Any,
    *,
    prefer_async: bool,
    refresh_raid_runs_command: Callable[..., None],
    now_func: Callable[..., Any],
    raid_run_model: Any,
    collect_due_raid_run_ids: Callable[..., Any],
    dispatch_async_raid_refresh: Callable[..., Any],
    logger: Any,
    import_raid_refresh_tasks: Callable[..., tuple[Any, Any]],
    try_dispatch_raid_refresh_task: Callable[..., bool],
    process_due_raid_run_ids: Callable[..., Any],
    process_raid_battle: Callable[..., Any],
    finalize_raid: Callable[..., Any],
) -> None:
    refresh_raid_runs_command(
        manor,
        prefer_async=prefer_async,
        now_func=now_func,
        raid_run_model=raid_run_model,
        collect_due_raid_run_ids=collect_due_raid_run_ids,
        dispatch_async_raid_refresh=dispatch_async_raid_refresh,
        logger=logger,
        import_raid_refresh_tasks=import_raid_refresh_tasks,
        try_dispatch_raid_refresh_task=try_dispatch_raid_refresh_task,
        process_due_raid_run_ids=process_due_raid_run_ids,
        process_raid_battle=process_raid_battle,
        finalize_raid=finalize_raid,
    )
