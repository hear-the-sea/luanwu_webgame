from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from core.utils.imports import is_missing_target_import

if TYPE_CHECKING:
    from ....models import Manor, RaidRun


def collect_due_raid_run_ids(
    manor: Manor, now: Any, raid_run_model: type[RaidRun]
) -> tuple[list[int], list[int], list[int]]:
    marching_ids = list(
        raid_run_model.objects.filter(
            attacker=manor, status=raid_run_model.Status.MARCHING, battle_at__lte=now
        ).values_list("id", flat=True)
    )
    returning_ids = list(
        raid_run_model.objects.filter(
            attacker=manor, status=raid_run_model.Status.RETURNING, return_at__lte=now
        ).values_list("id", flat=True)
    )
    retreated_ids = list(
        raid_run_model.objects.filter(
            attacker=manor, status=raid_run_model.Status.RETREATED, return_at__lte=now
        ).values_list("id", flat=True)
    )
    return marching_ids, returning_ids, retreated_ids


def dispatch_async_raid_refresh(
    marching_ids: list[int],
    returning_ids: list[int],
    retreated_ids: list[int],
    *,
    logger: logging.Logger,
    import_tasks: Callable[[], tuple[Any, Any]],
    dispatch_refresh_task: Callable[[Any, int, str], bool],
) -> tuple[list[int], list[int], list[int], bool]:
    complete_raid_task: Any
    process_raid_battle_task: Any
    try:
        complete_raid_task, process_raid_battle_task = import_tasks()
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning("Failed to import raid tasks, falling back to sync refresh", exc_info=True)
        return marching_ids, returning_ids, retreated_ids, False

    sync_marching_ids: list[int] = []
    for run_id in marching_ids:
        if not dispatch_refresh_task(process_raid_battle_task, run_id, "battle"):
            sync_marching_ids.append(run_id)

    sync_finalizing_ids: list[int] = []
    for run_id in returning_ids + retreated_ids:
        if not dispatch_refresh_task(complete_raid_task, run_id, "return"):
            sync_finalizing_ids.append(run_id)

    if not sync_marching_ids and not sync_finalizing_ids:
        return [], [], [], True

    sync_finalizing_set = set(sync_finalizing_ids)
    return (
        sync_marching_ids,
        [run_id for run_id in returning_ids if run_id in sync_finalizing_set],
        [run_id for run_id in retreated_ids if run_id in sync_finalizing_set],
        False,
    )


def process_due_raid_run_ids(
    now: Any,
    marching_ids: list[int],
    returning_ids: list[int],
    retreated_ids: list[int],
    *,
    raid_run_model: type[RaidRun],
    process_raid_battle: Callable[..., None],
    finalize_raid: Callable[..., Any],
) -> None:
    if marching_ids:
        for run in raid_run_model.objects.filter(id__in=marching_ids).order_by("battle_at"):
            process_raid_battle(run, now=now)
    if returning_ids:
        for run in raid_run_model.objects.filter(id__in=returning_ids).order_by("return_at"):
            finalize_raid(run, now=now)
    if retreated_ids:
        for run in raid_run_model.objects.filter(id__in=retreated_ids).order_by("return_at"):
            finalize_raid(run, now=now)
