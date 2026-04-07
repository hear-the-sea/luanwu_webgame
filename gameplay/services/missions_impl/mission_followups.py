from __future__ import annotations

from typing import Any, Dict, List, Mapping

from common.utils.celery import safe_apply_async, safe_apply_async_with_dedup

from .execution_launch_runtime import (
    dispatch_complete_mission_task_entry,
    import_launch_post_action_tasks_entry,
    schedule_mission_completion_task_entry,
    try_prepare_launch_report_entry,
)
from .execution_runtime import try_dispatch_mission_refresh_task as runtime_try_dispatch_mission_refresh_task
from .launch_post_actions import (
    attach_run_report_if_empty,
    build_defender_setup_and_drop_table,
    dispatch_or_sync_launch_report,
)
from .launch_post_actions import import_launch_post_action_tasks as import_launch_post_action_tasks_command
from .launch_post_actions import schedule_mission_completion_task as schedule_mission_completion_task_command
from .launch_resilience import dispatch_completion_task_best_effort, prepare_launch_report_best_effort


def try_dispatch_mission_refresh_task(task: Any, run_id: int, *, logger: Any, dedup_seconds: int) -> bool:
    return runtime_try_dispatch_mission_refresh_task(
        task,
        run_id,
        safe_apply_async_with_dedup=safe_apply_async_with_dedup,
        logger=logger,
        dedup_seconds=dedup_seconds,
    )


def prevalidate_launch_report(
    mission: Any,
    loadout: Dict[str, int],
    *,
    normalize_guest_configs,
    normalize_mapping,
) -> None:
    build_defender_setup_and_drop_table(
        mission,
        loadout,
        normalize_guest_configs=normalize_guest_configs,
        normalize_mapping=normalize_mapping,
    )


def schedule_mission_completion_task(
    run: Any,
    complete_mission_task: Any,
    *,
    logger: Any,
    finalize_mission_run,
    now_func,
) -> None:
    schedule_mission_completion_task_entry(
        run,
        complete_mission_task,
        schedule_mission_completion_task=schedule_mission_completion_task_command,
        safe_apply_async=safe_apply_async,
        logger=logger,
        finalize_mission_run=finalize_mission_run,
        now_func=now_func,
    )


def import_launch_post_action_tasks(*, logger: Any) -> tuple[Any | None, Any | None]:
    return import_launch_post_action_tasks_entry(
        import_launch_post_action_tasks=import_launch_post_action_tasks_command,
        logger=logger,
    )


def try_prepare_launch_report(
    manor: Any,
    mission: Any,
    run: Any,
    guests: List[Any],
    loadout: Dict[str, int],
    travel_seconds: int,
    seed: Any,
    generate_report_task: Any,
    *,
    logger: Any,
    normalize_guest_configs,
    normalize_mapping,
    build_guest_snapshot_proxies,
    build_guest_battle_snapshots,
    generate_sync_battle_report,
    settings_obj: Any,
    environ: Mapping[str, str],
    mission_run_model: Any,
) -> None:
    try_prepare_launch_report_entry(
        manor,
        mission,
        run,
        guests,
        loadout,
        travel_seconds,
        seed,
        generate_report_task,
        logger=logger,
        prepare_launch_report_best_effort=prepare_launch_report_best_effort,
        build_defender_setup_and_drop_table=build_defender_setup_and_drop_table,
        normalize_guest_configs=normalize_guest_configs,
        normalize_mapping=normalize_mapping,
        dispatch_or_sync_launch_report=dispatch_or_sync_launch_report,
        attach_run_report_if_empty=attach_run_report_if_empty,
        build_guest_snapshot_proxies=build_guest_snapshot_proxies,
        build_guest_battle_snapshots=build_guest_battle_snapshots,
        generate_sync_battle_report=generate_sync_battle_report,
        safe_apply_async=safe_apply_async,
        settings_obj=settings_obj,
        environ=environ,
        mission_run_model=mission_run_model,
    )


def dispatch_complete_mission_task(
    run: Any,
    complete_mission_task: Any,
    *,
    logger: Any,
    schedule_mission_completion_task,
) -> None:
    dispatch_complete_mission_task_entry(
        run,
        complete_mission_task,
        dispatch_completion_task_best_effort=dispatch_completion_task_best_effort,
        logger=logger,
        schedule_mission_completion_task=schedule_mission_completion_task,
    )
