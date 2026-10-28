from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping

from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS, INFRASTRUCTURE_EXCEPTIONS


def should_force_sync_launch_report(*, settings_obj: Any, environ: Mapping[str, str]) -> bool:
    return bool(getattr(settings_obj, "DEBUG", False) or environ.get("PYTEST_CURRENT_TEST"))


def prepare_launch_report_best_effort(
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
    build_defender_setup_and_drop_table: Callable[..., tuple[dict, Dict[str, object]]],
    normalize_guest_configs: Callable[[Any], List[Any]],
    normalize_mapping: Callable[[Any], Dict[str, object]],
    dispatch_or_sync_launch_report: Callable[..., Any],
    attach_run_report_if_empty: Callable[..., None],
    build_guest_snapshot_proxies: Callable[..., List[Any]],
    build_guest_battle_snapshots: Callable[..., List[Dict[str, Any]]],
    generate_sync_battle_report: Callable[..., Any],
    safe_apply_async: Callable[..., bool],
    settings_obj: Any,
    environ: Mapping[str, str],
    mission_run_model: Any,
) -> None:
    try:
        defender_setup, drop_table = build_defender_setup_and_drop_table(
            mission,
            loadout,
            normalize_guest_configs=normalize_guest_configs,
            normalize_mapping=normalize_mapping,
            now=run.started_at,
        )
        report = dispatch_or_sync_launch_report(
            manor,
            mission,
            run,
            guests,
            loadout,
            defender_setup,
            drop_table,
            travel_seconds,
            seed,
            generate_report_task=generate_report_task,
            build_guest_snapshot_proxies=build_guest_snapshot_proxies,
            build_guest_battle_snapshots=build_guest_battle_snapshots,
            generate_sync_battle_report=generate_sync_battle_report,
            safe_apply_async=safe_apply_async,
            logger=logger,
            force_sync=should_force_sync_launch_report(settings_obj=settings_obj, environ=environ),
        )
        attach_run_report_if_empty(run, report, mission_run_model=mission_run_model)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.error(
            "Mission launch report preparation failed: run_id=%s manor_id=%s mission_id=%s error=%s",
            run.id,
            manor.id,
            mission.id,
            exc,
            exc_info=True,
            extra={
                "degraded": True,
                "component": "mission_launch_report",
                "run_id": run.id,
                "manor_id": manor.id,
                "mission_id": mission.id,
            },
        )


def dispatch_completion_task_best_effort(
    run: Any,
    complete_mission_task: Any,
    *,
    logger: Any,
    schedule_mission_completion_task: Callable[[Any, Any], None],
) -> None:
    if complete_mission_task is None:
        logger.error(
            "Mission completion task unavailable after launch: run_id=%s manor_id=%s",
            run.id,
            run.manor_id,
            extra={
                "degraded": True,
                "component": "mission_completion_dispatch",
                "run_id": run.id,
                "manor_id": run.manor_id,
            },
        )
        return

    try:
        schedule_mission_completion_task(run, complete_mission_task)
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.error(
            "Mission completion dispatch failed after launch: run_id=%s manor_id=%s error=%s",
            run.id,
            run.manor_id,
            exc,
            exc_info=True,
            extra={
                "degraded": True,
                "component": "mission_completion_dispatch",
                "run_id": run.id,
                "manor_id": run.manor_id,
            },
        )
