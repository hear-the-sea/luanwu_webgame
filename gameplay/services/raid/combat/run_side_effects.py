from __future__ import annotations

from typing import Any, Callable

from django.utils import timezone

from core.utils.imports import is_missing_target_import


def send_raid_incoming_message(run: Any, *, create_message: Callable[..., Any]) -> None:
    battle_at = run.battle_at
    arrive_time = timezone.localtime(battle_at).strftime("%Y-%m-%d %H:%M:%S") if battle_at else "未知"

    body = f"""来自 {run.attacker.location_display} 的 {run.attacker.display_name} 正在向你发起进攻！

预计抵达时间：{arrive_time}

请立即做好防守准备！"""

    create_message(
        manor=run.defender,
        kind="system",
        title="紧急警报 - 敌军来袭！",
        body=body,
    )


def dispatch_raid_battle_task_best_effort(
    run: Any,
    travel_time: int,
    *,
    logger: Any,
    import_process_raid_battle_task: Callable[[], Any],
    safe_apply_async: Callable[..., bool],
    process_raid_battle: Callable[..., Any],
) -> None:
    def _fallback_sync_when_due() -> None:
        if travel_time > 0:
            return
        logger.warning(
            "process_raid_battle_task dispatch failed for due raid; processing synchronously: run_id=%s", run.id
        )
        process_raid_battle(run)

    process_raid_battle_task: Any
    try:
        process_raid_battle_task = import_process_raid_battle_task()
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning(
            "process_raid_battle_task dispatch failed: run_id=%s error=%s",
            run.id,
            exc,
            exc_info=True,
        )
        _fallback_sync_when_due()
        return

    dispatched = safe_apply_async(
        process_raid_battle_task,
        args=[run.id],
        countdown=travel_time,
        defer_long_eta=True,
        logger=logger,
        log_message="process_raid_battle_task dispatch failed",
    )
    if not dispatched:
        logger.error(
            "process_raid_battle_task dispatch returned False; raid battle may not execute",
            extra={
                "task_name": "process_raid_battle_task",
                "run_id": run.id,
                "attacker_id": getattr(run, "attacker_id", None),
                "defender_id": getattr(run, "defender_id", None),
            },
        )
        _fallback_sync_when_due()


def schedule_raid_retreat_completion_best_effort(
    run_id: int,
    countdown: int,
    *,
    logger: Any,
    import_complete_raid_task: Callable[[], Any],
    safe_apply_async: Callable[..., bool],
) -> None:
    complete_raid_task: Any
    try:
        complete_raid_task = import_complete_raid_task()
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning(
            "complete_raid_task dispatch failed for retreat: run_id=%s error=%s",
            run_id,
            exc,
            exc_info=True,
        )
        return

    dispatched = safe_apply_async(
        complete_raid_task,
        args=[run_id],
        countdown=countdown,
        defer_long_eta=True,
        logger=logger,
        log_message="complete_raid_task dispatch failed for retreat",
    )
    if not dispatched:
        logger.error(
            "complete_raid_task dispatch returned False after retreat request; raid may remain retreated",
            extra={
                "task_name": "complete_raid_task",
                "run_id": run_id,
            },
        )
