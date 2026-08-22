from __future__ import annotations

from typing import Any, Callable


def refresh_raid_runs(
    manor: Any,
    *,
    prefer_async: bool = False,
    now_func: Callable[[], Any],
    raid_run_model: Any,
    collect_due_raid_run_ids: Callable[[Any, Any, Any], tuple[list[int], list[int], list[int]]],
    dispatch_async_raid_refresh: Callable[..., tuple[list[int], list[int], list[int], bool]],
    logger: Any,
    import_raid_refresh_tasks: Callable[[], tuple[Any, Any]],
    try_dispatch_raid_refresh_task: Callable[[Any, int, str], bool],
    process_due_raid_run_ids: Callable[..., None],
    process_raid_battle: Callable[..., None],
    finalize_raid: Callable[..., Any],
) -> None:
    now = now_func()
    marching_ids, returning_ids, retreated_ids = collect_due_raid_run_ids(manor, now, raid_run_model)

    if not marching_ids and not returning_ids and not retreated_ids:
        return

    if prefer_async:
        marching_ids, returning_ids, retreated_ids, done_async = dispatch_async_raid_refresh(
            marching_ids,
            returning_ids,
            retreated_ids,
            logger=logger,
            import_tasks=import_raid_refresh_tasks,
            dispatch_refresh_task=try_dispatch_raid_refresh_task,
        )
        if done_async:
            return

    process_due_raid_run_ids(
        now,
        marching_ids,
        returning_ids,
        retreated_ids,
        raid_run_model=raid_run_model,
        process_raid_battle=process_raid_battle,
        finalize_raid=finalize_raid,
    )
