from __future__ import annotations

import math
from typing import Any, Callable, Dict, List

from core.utils.imports import is_missing_target_import


def _require_guest_config_payload(
    raw: Any,
    *,
    field_name: str,
    normalize_guest_configs: Callable[[Any], List[Any]],
) -> List[Any]:
    if raw is None:
        return []
    try:
        normalized = normalize_guest_configs(raw)
    except AssertionError as exc:
        raise AssertionError(f"invalid mission {field_name}: {raw!r}") from exc
    if normalized == [] and not isinstance(raw, (list, tuple, set)):
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    return normalized


def _require_mapping_payload(
    raw: Any, *, field_name: str, normalize_mapping: Callable[[Any], Dict[str, object]]
) -> Dict[str, object]:
    if raw is None:
        return {}
    try:
        normalized = normalize_mapping(raw)
    except AssertionError as exc:
        raise AssertionError(f"invalid mission {field_name}: {raw!r}") from exc
    if normalized == {} and not isinstance(raw, dict):
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    return normalized


def build_defender_setup_and_drop_table(
    mission: Any,
    loadout: Dict[str, int],
    *,
    normalize_guest_configs: Callable[[Any], List[Any]],
    normalize_mapping: Callable[[Any], Dict[str, object]],
) -> tuple[dict, Dict[str, object]]:
    if mission.is_defense:
        return {"troop_loadout": loadout}, {}

    return (
        {
            "guest_keys": _require_guest_config_payload(
                mission.enemy_guests,
                field_name="enemy_guests",
                normalize_guest_configs=normalize_guest_configs,
            ),
            "troop_loadout": _require_mapping_payload(
                mission.enemy_troops,
                field_name="enemy_troops",
                normalize_mapping=normalize_mapping,
            ),
            "technology": _require_mapping_payload(
                mission.enemy_technology,
                field_name="enemy_technology",
                normalize_mapping=normalize_mapping,
            ),
        },
        _require_mapping_payload(
            mission.drop_table,
            field_name="drop_table",
            normalize_mapping=normalize_mapping,
        ),
    )


def sync_report_for_launch(
    manor: Any,
    mission: Any,
    battle_guests: List[Any],
    loadout: Dict[str, int],
    defender_setup: dict,
    travel_seconds: int,
    seed: Any,
    *,
    generate_sync_battle_report: Callable[..., Any],
) -> Any:
    return generate_sync_battle_report(
        manor=manor,
        mission=mission,
        guests=battle_guests,
        loadout=loadout,
        defender_setup=defender_setup,
        travel_seconds=travel_seconds,
        seed=seed,
    )


def dispatch_or_sync_launch_report(
    manor: Any,
    mission: Any,
    run: Any,
    guests: List[Any],
    loadout: Dict[str, int],
    defender_setup: dict,
    drop_table: Dict[str, object],
    travel_seconds: int,
    seed: Any,
    *,
    generate_report_task: Any,
    build_guest_snapshot_proxies: Callable[..., List[Any]],
    build_guest_battle_snapshots: Callable[..., List[Dict[str, Any]]],
    generate_sync_battle_report: Callable[..., Any],
    safe_apply_async: Callable[..., bool],
    logger: Any,
    force_sync: bool,
) -> Any:
    if mission.is_defense:
        return None

    battle_guests = build_guest_snapshot_proxies(
        run.guest_snapshots or build_guest_battle_snapshots(guests, include_identity=True),
        include_guest_identity=True,
    )
    if not battle_guests:
        battle_guests = guests

    if force_sync or generate_report_task is None:
        return sync_report_for_launch(
            manor,
            mission,
            battle_guests,
            loadout,
            defender_setup,
            travel_seconds,
            seed,
            generate_sync_battle_report=generate_sync_battle_report,
        )

    ok = safe_apply_async(
        generate_report_task,
        kwargs={
            "manor_id": manor.id,
            "mission_id": mission.id,
            "run_id": run.id,
            "guest_ids": [g.id for g in guests],
            "troop_loadout": loadout,
            "fill_default_troops": False,
            "battle_type": mission.battle_type or "task",
            "opponent_name": mission.name,
            "defender_setup": defender_setup,
            "drop_table": drop_table,
            "travel_seconds": travel_seconds,
            "seed": seed,
        },
        logger=logger,
        log_message="generate_report_task dispatch failed; falling back to sync generation",
    )
    if ok:
        return None

    return sync_report_for_launch(
        manor,
        mission,
        battle_guests,
        loadout,
        defender_setup,
        travel_seconds,
        seed,
        generate_sync_battle_report=generate_sync_battle_report,
    )


def attach_run_report_if_empty(run: Any, report: Any, *, mission_run_model: Any) -> None:
    if not report:
        return

    updated = mission_run_model.objects.filter(pk=run.pk, battle_report__isnull=True).update(battle_report=report)
    if updated:
        run.battle_report = report


def _resolve_completion_countdown(*, run: Any, now_func: Callable[[], Any]) -> int:
    if run.return_at is None:
        raise RuntimeError("Mission run was not created correctly")

    countdown = math.ceil((run.return_at - now_func()).total_seconds())
    if countdown < 0:
        raise AssertionError("mission completion return_at cannot be in the past")
    return countdown


def schedule_mission_completion_task(
    run: Any,
    complete_mission_task: Any,
    *,
    safe_apply_async: Callable[..., bool],
    logger: Any,
    finalize_mission_run: Callable[..., object],
    now_func: Callable[[], Any],
) -> None:
    countdown = _resolve_completion_countdown(run=run, now_func=now_func)
    dispatched = safe_apply_async(
        complete_mission_task,
        args=[run.id],
        countdown=countdown,
        logger=logger,
        log_message="complete_mission_task dispatch failed; relying on refresh_mission_runs",
    )
    if not dispatched and countdown == 0:
        logger.warning("complete_mission_task dispatch failed for due run; finalizing synchronously: run_id=%s", run.id)
        finalize_mission_run(run)


def import_launch_post_action_tasks(*, logger: Any) -> tuple[Any | None, Any | None]:
    generate_report_task = None
    complete_mission_task = None

    try:
        from battle.tasks import generate_report_task as imported_generate_report_task

        generate_report_task = imported_generate_report_task
    except ImportError as exc:
        if not is_missing_target_import(exc, "battle.tasks"):
            raise
        logger.error(
            "Failed to import generate_report_task during mission launch: %s",
            exc,
            exc_info=True,
            extra={"degraded": True, "component": "mission_launch_report_import"},
        )

    try:
        from gameplay.tasks import complete_mission_task as imported_complete_mission_task

        complete_mission_task = imported_complete_mission_task
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.error(
            "Failed to import complete_mission_task during mission launch: %s",
            exc,
            exc_info=True,
            extra={"degraded": True, "component": "mission_launch_completion_import"},
        )

    return generate_report_task, complete_mission_task
