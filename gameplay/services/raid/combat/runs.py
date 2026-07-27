"""Raid run lifecycle helpers (start/finalize/retreat/list) split from legacy combat.py."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from common.utils.celery import safe_apply_async, safe_apply_async_with_dedup
from gameplay.services.action_points import consume_action_points_for_expedition
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.raid import utils as raid_utils
from gameplay.services.resources import grant_resources_locked
from guests.models import Guest, GuestStatus

from ....models import Manor, RaidRun, ResourceEvent
from ...utils.messages import create_message
from .config import PVPConstants
from .finalize import finalize_raid as _finalize_raid_command
from .loot import _grant_loot_items
from .raid_inputs import (
    _load_and_validate_attacker_guests,
    _normalize_and_validate_raid_loadout,
    _validate_and_normalize_raid_inputs,
)
from .refresh import refresh_raid_runs as _refresh_raid_runs_command
from .refresh_flow import collect_due_raid_run_ids, dispatch_async_raid_refresh, process_due_raid_run_ids
from .retreat import can_raid_retreat as _can_raid_retreat_command
from .retreat import finalize_raid_retreat as _finalize_raid_retreat_command
from .retreat import request_raid_retreat as _request_raid_retreat_command
from .run_persistence import create_raid_run_record as persistence_create_raid_run_record
from .run_persistence import get_active_raids as persistence_get_active_raids
from .run_persistence import get_raid_history as persistence_get_raid_history
from .run_persistence import (
    invalidate_recent_attacks_cache_on_commit as persistence_invalidate_recent_attacks_cache_on_commit,
)
from .run_persistence import lock_manor_pair as persistence_lock_manor_pair
from .run_persistence import recheck_can_attack_target as persistence_recheck_can_attack_target
from .run_runtime import can_raid_retreat_entry
from .run_runtime import import_raid_refresh_tasks as runtime_import_raid_refresh_tasks
from .run_runtime import (
    load_locked_attacker,
    load_locked_raid_run,
    request_raid_retreat_entry,
    schedule_raid_retreat_completion_entry,
)
from .run_runtime import try_dispatch_raid_refresh_task as runtime_try_dispatch_raid_refresh_task
from .run_side_effects import (
    dispatch_raid_battle_task_best_effort,
    schedule_raid_retreat_completion_best_effort,
    send_raid_incoming_message,
)
from .start import start_raid as _start_raid_command
from .travel import calculate_raid_travel_time, get_active_raid_count

# Import troop management helpers (moved to troop_ops, re-exported for callers).
from .troop_ops import (  # noqa: F401
    _add_troops,
    _add_troops_batch,
    _bulk_create_troops_with_fallback,
    _deduct_troops,
    _return_surviving_troops,
)

# Import normalization helpers from the troops sub-module (still used in this file).
from .troops import _normalize_mapping, _normalize_positive_int_mapping  # noqa: F401

logger = logging.getLogger(__name__)


_REFRESH_DISPATCH_DEDUP_SECONDS = 5


def _try_dispatch_raid_refresh_task(task: Any, run_id: int, stage: str) -> bool:
    return runtime_try_dispatch_raid_refresh_task(
        task,
        run_id,
        stage,
        safe_apply_async_with_dedup=safe_apply_async_with_dedup,
        logger=logger,
        dedup_seconds=_REFRESH_DISPATCH_DEDUP_SECONDS,
    )


def _import_raid_refresh_tasks() -> tuple[Any, Any]:
    return runtime_import_raid_refresh_tasks()


def _lock_manor_pair(attacker_id: int, defender_id: int) -> tuple[Manor, Manor]:
    return persistence_lock_manor_pair(attacker_id, defender_id, manor_model=Manor)


def _recheck_can_attack_target(attacker: Manor, defender: Manor, now: datetime) -> tuple[bool, str]:
    return persistence_recheck_can_attack_target(
        attacker, defender, now, can_attack_target=raid_utils.can_attack_target
    )


def _invalidate_recent_attacks_cache_on_commit(defender_id: int) -> None:
    persistence_invalidate_recent_attacks_cache_on_commit(
        defender_id,
        on_commit=transaction.on_commit,
        invalidate_recent_attacks_cache=raid_utils.invalidate_recent_attacks_cache,
    )


def _consume_raid_action_points(manor: Manor) -> int:
    if not hasattr(manor, "action_points"):
        return 0
    return consume_action_points_for_expedition(manor)


def _create_raid_run_record(
    attacker: Manor,
    defender: Manor,
    guests: list[Guest],
    loadout: Dict[str, int],
    travel_time: int,
) -> RaidRun:
    return persistence_create_raid_run_record(
        attacker,
        defender,
        guests,
        loadout,
        travel_time,
        guest_model=Guest,
        deployed_status=GuestStatus.DEPLOYED,
        build_guest_battle_snapshots=build_guest_battle_snapshots,
        raid_run_model=RaidRun,
        now_func=timezone.now,
    )


def _dispatch_raid_battle_task(run: RaidRun, travel_time: int) -> None:
    from .battle import process_raid_battle

    dispatch_raid_battle_task_best_effort(
        run,
        travel_time,
        logger=logger,
        import_process_raid_battle_task=lambda: __import__(
            "gameplay.tasks", fromlist=["process_raid_battle_task"]
        ).process_raid_battle_task,
        safe_apply_async=safe_apply_async,
        process_raid_battle=process_raid_battle,
    )


# ============ Public API: raid lifecycle ============


def _load_locked_raid_run(run_pk: int) -> RaidRun | None:
    return load_locked_raid_run(raid_run_model=RaidRun, run_pk=run_pk)


def _load_locked_attacker(attacker_id: int) -> Manor:
    return load_locked_attacker(manor_model=Manor, attacker_id=attacker_id)


def start_raid(
    attacker: Manor, defender: Manor, guest_ids: List[int], troop_loadout: Dict[str, int], seed: Optional[int] = None
) -> RaidRun:
    """
    发起踢馆出征。

    Args:
        attacker: 进攻方庄园
        defender: 防守方庄园
        guest_ids: 出征门客ID列表
        troop_loadout: 兵种配置
        seed: 已弃用且始终忽略；业务随机种子由服务端生成

    Returns:
        踢馆记录

    Raises:
        GameError: 无法发起踢馆时
    """
    # 兼容旧调用签名；不得让玩家输入影响业务随机结果。
    del seed
    return _start_raid_command(
        attacker,
        defender,
        guest_ids,
        troop_loadout,
        validate_and_normalize_inputs=_validate_and_normalize_raid_inputs,
        transaction_atomic=transaction.atomic,
        lock_manor_pair=_lock_manor_pair,
        now_func=timezone.now,
        recheck_can_attack_target=_recheck_can_attack_target,
        get_active_raid_count=get_active_raid_count,
        raid_max_concurrent=PVPConstants.RAID_MAX_CONCURRENT,
        consume_action_points=_consume_raid_action_points,
        load_and_validate_attacker_guests=_load_and_validate_attacker_guests,
        normalize_and_validate_raid_loadout=_normalize_and_validate_raid_loadout,
        deduct_troops=_deduct_troops,
        calculate_raid_travel_time=calculate_raid_travel_time,
        create_raid_run_record=_create_raid_run_record,
        invalidate_recent_attacks_cache_on_commit=_invalidate_recent_attacks_cache_on_commit,
        send_raid_incoming_message=_send_raid_incoming_message,
        dispatch_raid_battle_task=_dispatch_raid_battle_task,
        logger=logger,
    )


def _send_raid_incoming_message(run: RaidRun) -> None:
    """发送来袭警报消息"""
    send_raid_incoming_message(run, create_message=create_message)


def finalize_raid(run: RaidRun, now: Optional[datetime] = None) -> None:
    """
    完成踢馆返程，释放门客和发放战利品。

    Args:
        run: 踢馆记录
        now: 当前时间（可选）
    """
    _finalize_raid_command(
        run,
        now=now,
        load_locked_raid_run=_load_locked_raid_run,
        normalize_positive_int_mapping=_normalize_positive_int_mapping,
        return_surviving_troops=_return_surviving_troops,
        load_locked_attacker=_load_locked_attacker,
        grant_resources_locked=grant_resources_locked,
        grant_loot_items=_grant_loot_items,
        battle_reward_reason=ResourceEvent.Reason.BATTLE_REWARD,
    )


def _schedule_raid_retreat_completion(run_id: int, countdown: int) -> None:
    schedule_raid_retreat_completion_entry(
        run_id,
        countdown,
        schedule_retreat_completion=lambda scheduled_run_id, scheduled_countdown: schedule_raid_retreat_completion_best_effort(
            scheduled_run_id,
            scheduled_countdown,
            logger=logger,
            import_complete_raid_task=lambda: __import__(
                "gameplay.tasks", fromlist=["complete_raid_task"]
            ).complete_raid_task,
            safe_apply_async=safe_apply_async,
        ),
    )


def request_raid_retreat(run: RaidRun) -> None:
    """
    请求踢馆撤退（仅在行军阶段可用）。

    Args:
        run: 踢馆记录

    Raises:
        RaidRetreatStateError: 无法撤退时
    """
    request_raid_retreat_entry(
        run,
        request_raid_retreat_command=_request_raid_retreat_command,
        raid_run_model=RaidRun,
        schedule_retreat_completion=_schedule_raid_retreat_completion,
    )


def _finalize_raid_retreat(run: RaidRun, now: Optional[datetime] = None) -> None:
    """完成撤退，归还所有护院和门客"""
    _finalize_raid_retreat_command(
        run,
        now=now,
        normalize_positive_int_mapping=_normalize_positive_int_mapping,
        add_troops_batch=_add_troops_batch,
        completed_status=RaidRun.Status.COMPLETED,
    )


def can_raid_retreat(run: RaidRun, now: Optional[datetime] = None) -> bool:
    """判断踢馆是否可以撤退"""
    return can_raid_retreat_entry(
        run,
        can_raid_retreat_command=_can_raid_retreat_command,
        marching_status=RaidRun.Status.MARCHING,
        now=now,
    )


def refresh_raid_runs(manor: Manor, *, prefer_async: bool = False) -> None:
    """刷新庄园的踢馆状态（支持异步优先结算）。"""
    from .battle import process_raid_battle

    _refresh_raid_runs_command(
        manor,
        prefer_async=prefer_async,
        now_func=timezone.now,
        raid_run_model=RaidRun,
        collect_due_raid_run_ids=collect_due_raid_run_ids,
        dispatch_async_raid_refresh=dispatch_async_raid_refresh,
        logger=logger,
        import_raid_refresh_tasks=_import_raid_refresh_tasks,
        try_dispatch_raid_refresh_task=_try_dispatch_raid_refresh_task,
        process_due_raid_run_ids=process_due_raid_run_ids,
        process_raid_battle=process_raid_battle,
        finalize_raid=finalize_raid,
    )


def get_active_raids(manor: Manor) -> List[RaidRun]:
    """获取进行中的踢馆列表"""
    return persistence_get_active_raids(manor, raid_run_model=RaidRun)


def get_raid_history(manor: Manor, limit: int = 20) -> List[RaidRun]:
    """获取踢馆历史记录"""
    return persistence_get_raid_history(manor, raid_run_model=RaidRun, q_object=Q, limit=limit)
