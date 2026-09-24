from __future__ import annotations

from datetime import datetime
from importlib import import_module
from typing import Any, Callable

from django.utils import timezone

from common.utils.celery import safe_apply_async_with_dedup
from core.utils.imports import is_missing_target_import

from ...models import ScoutRecord

_REFRESH_DISPATCH_DEDUP_SECONDS = 5


def try_dispatch_scout_refresh_task(
    task: Any,
    record_id: int,
    phase: str,
    *,
    logger: Any,
) -> bool:
    return safe_apply_async_with_dedup(
        task,
        dedup_key=f"pvp:refresh_dispatch:scout:{phase}:{record_id}",
        dedup_timeout=_REFRESH_DISPATCH_DEDUP_SECONDS,
        args=[record_id],
        countdown=0,
        logger=logger,
        log_message=f"scout refresh dispatch failed: phase={phase} record_id={record_id}",
    )


def resolve_scout_task(task_name: str) -> Any:
    tasks_module = import_module("gameplay.tasks.pvp")
    return getattr(tasks_module, task_name)


def resolve_scout_refresh_tasks(*, logger: Any) -> tuple[Any, Any] | None:
    try:
        return resolve_scout_task("complete_scout_task"), resolve_scout_task("complete_scout_return_task")
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks.pvp"):
            raise
        logger.warning("Failed to import scout tasks, falling back to sync refresh", exc_info=True)
        return None


def collect_due_scout_record_ids(
    manor: Any,
    now: datetime,
    *,
    scout_record_model: Any = ScoutRecord,
) -> tuple[list[int], list[int]]:
    scouting_ids = list(
        scout_record_model.objects.filter(
            attacker=manor,
            status=scout_record_model.Status.SCOUTING,
            complete_at__lte=now,
        ).values_list("id", flat=True)
    )
    returning_ids = list(
        scout_record_model.objects.filter(
            attacker=manor,
            status=scout_record_model.Status.RETURNING,
            return_at__lte=now,
        ).values_list("id", flat=True)
    )
    return scouting_ids, returning_ids


def dispatch_async_scout_refresh(
    scouting_ids: list[int],
    returning_ids: list[int],
    *,
    resolve_tasks_fn: Callable[[], tuple[Any, Any] | None],
    try_dispatch_fn: Callable[[Any, int, str], bool],
) -> tuple[list[int], list[int], bool]:
    tasks = resolve_tasks_fn()
    if tasks is None:
        return scouting_ids, returning_ids, False
    complete_scout_task, complete_scout_return_task = tasks

    sync_scouting_ids: list[int] = []
    for record_id in scouting_ids:
        if not try_dispatch_fn(complete_scout_task, record_id, "outbound"):
            sync_scouting_ids.append(record_id)

    sync_returning_ids: list[int] = []
    for record_id in returning_ids:
        if not try_dispatch_fn(complete_scout_return_task, record_id, "return"):
            sync_returning_ids.append(record_id)

    if not sync_scouting_ids and not sync_returning_ids:
        return [], [], True
    return sync_scouting_ids, sync_returning_ids, False


def finalize_due_scout_records(
    now: datetime,
    scouting_ids: list[int],
    returning_ids: list[int],
    *,
    scout_record_model: Any = ScoutRecord,
    finalize_scout_fn: Callable[..., None],
    finalize_scout_return_fn: Callable[..., None],
) -> None:
    if scouting_ids:
        scouting_records = scout_record_model.objects.select_related("attacker", "defender").filter(id__in=scouting_ids)
        for record in scouting_records:
            finalize_scout_fn(record, now=now)

    if returning_ids:
        returning_records = scout_record_model.objects.select_related("attacker", "defender").filter(
            id__in=returning_ids
        )
        for record in returning_records:
            finalize_scout_return_fn(record, now=now)


def refresh_scout_records_command(
    manor: Any,
    *,
    prefer_async: bool = False,
    now_fn: Callable[[], datetime] = timezone.now,
    collect_due_ids_fn: Callable[[Any, datetime], tuple[list[int], list[int]]],
    dispatch_async_fn: Callable[[list[int], list[int]], tuple[list[int], list[int], bool]],
    finalize_due_fn: Callable[[datetime, list[int], list[int]], None],
) -> int:
    now = now_fn()
    scouting_ids, returning_ids = collect_due_ids_fn(manor, now)

    if not scouting_ids and not returning_ids:
        return 0

    refreshed = len(scouting_ids) + len(returning_ids)

    if prefer_async:
        scouting_ids, returning_ids, done_async = dispatch_async_fn(scouting_ids, returning_ids)
        if done_async:
            return refreshed

    finalize_due_fn(now, scouting_ids, returning_ids)
    return refreshed
