"""Raid travel and protection helpers (split from legacy combat.py)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from importlib import import_module
from typing import Dict, List

from django.db import transaction
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import MessageError
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from core.utils.side_effects import schedule_best_effort_after_commit
from guests.models import Guest

from ....models import Manor, RaidRun
from ...pvp_runtime.lifecycle import TravelTimeline
from ...pvp_runtime.messages import build_blocked_target_body
from ...pvp_runtime.protection import build_daily_cap_result
from ...pvp_runtime.travel import calculate_pvp_travel_time
from ...utils.messages import create_message
from ..utils import calculate_distance, is_same_region
from .config import PVPConstants

logger = logging.getLogger(__name__)
RAID_BLOCKED_TARGET_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def resolve_complete_raid_task(*, logger: logging.Logger) -> object | None:
    try:
        return import_module("gameplay.tasks").complete_raid_task
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning("complete_raid_task import failed for dismissed raids; skip async completion", exc_info=True)
        return None


def _get_defender_battle_block_reason(defender: Manor, *, now: datetime | None = None) -> str | None:
    """Return the protection reason that should cancel an arriving raid, if any."""
    if defender.is_under_newbie_protection:
        return "对方处于新手保护期"
    if defender.is_under_defeat_protection:
        return "对方处于战败保护期"
    if defender.is_under_peace_shield:
        return "对方处于免战牌保护期"

    now = now or timezone.now()
    recent_attacks = (
        RaidRun.objects.filter(defender=defender, started_at__gte=now - timedelta(hours=24))
        .exclude(status=RaidRun.Status.MARCHING)
        .count()
    )
    cap_result = build_daily_cap_result(
        current_count=recent_attacks,
        max_count=PVPConstants.RAID_MAX_DAILY_ATTACKS_RECEIVED,
        blocked_reason="该目标今日已被多次攻击，暂时无法攻击",
    )
    if cap_result.blocked:
        return cap_result.reason
    return None


def _retreat_raid_run_due_to_blocked_target(
    locked_run: RaidRun,
    *,
    now: datetime | None = None,
    reason: str,
) -> int:
    """
    Mark a marching raid as retreated because the defender became ineligible before battle.

    Returns:
        Countdown seconds until the run finishes returning.
    """
    now = now or timezone.now()
    retreat_schedule = TravelTimeline.from_activity(locked_run).retreat_schedule(now=now)
    return_time = retreat_schedule.elapsed_seconds

    locked_run.status = RaidRun.Status.RETREATED
    locked_run.return_at = retreat_schedule.return_at
    locked_run.save(update_fields=["status", "return_at"])

    def _send_blocked_target_message() -> None:
        create_message(
            manor=locked_run.attacker,
            kind="system",
            title="部队已遣返",
            body=build_blocked_target_body(target_name=locked_run.defender.display_name, reason=reason),
        )

    schedule_best_effort_after_commit(
        _send_blocked_target_message,
        logger=logger,
        log_message=(
            "raid blocked-target message failed: "
            f"run_id={locked_run.id} attacker={locked_run.attacker_id} defender={locked_run.defender_id}"
        ),
        expected_exceptions=RAID_BLOCKED_TARGET_MESSAGE_EXCEPTIONS,
        degraded_component="raid_blocked_target_message",
    )

    return return_time


def calculate_raid_travel_time(
    attacker: Manor, defender: Manor, guests: List[Guest], troop_loadout: Dict[str, int]
) -> int:
    """
    计算踢馆行军时间（单程，秒）。

    公式：
    单程时间 = (30分钟 + 距离 × 15秒) × 敏捷系数 × 规模系数 × 跨区系数
    """
    distance = calculate_distance(attacker, defender)
    base_time = PVPConstants.RAID_BASE_TRAVEL_TIME
    distance_time = distance * PVPConstants.RAID_TRAVEL_TIME_PER_DISTANCE

    # 跨区惩罚
    cross_region_mult = 1.0
    if not is_same_region(attacker, defender):
        cross_region_mult = PVPConstants.RAID_CROSS_REGION_MULTIPLIER

    estimate = calculate_pvp_travel_time(
        route_seconds=base_time + distance_time,
        guests=guests,
        troop_loadout=troop_loadout,
        external_factor=cross_region_mult,
    )
    return estimate.scaled_seconds


def get_active_raid_count(manor: Manor) -> int:
    """获取当前进行中的踢馆数量"""
    return RaidRun.objects.filter(
        attacker=manor,
        status__in=[
            RaidRun.Status.MARCHING,
            RaidRun.Status.BATTLING,
            RaidRun.Status.RETURNING,
            RaidRun.Status.RETREATED,
        ],
    ).count()


def get_incoming_raids(manor: Manor) -> List[RaidRun]:
    """获取来袭的敌军列表"""
    return list(
        RaidRun.objects.filter(defender=manor, status=RaidRun.Status.MARCHING)
        .select_related("attacker")
        .order_by("battle_at")
    )


def _dismiss_marching_raids_if_protected(defender: Manor) -> int:
    """
    检查防守方是否进入保护态，如果是则遣返所有正在行军的进攻队伍。

    Args:
        defender: 防守方庄园

    Returns:
        遣返的队伍数量
    """
    now = timezone.now()
    reason = _get_defender_battle_block_reason(defender, now=now)
    if reason is None:
        return 0

    # 查找所有正在行军中的、目标是该防守方的队伍
    marching_runs = list(
        RaidRun.objects.filter(defender=defender, status=RaidRun.Status.MARCHING)
        .select_related("attacker")
        .prefetch_related("guests")
    )

    if not marching_runs:
        return 0

    complete_raid_task = resolve_complete_raid_task(logger=logger)
    dismissed_count = 0
    for run in marching_runs:
        with transaction.atomic():
            # 重新锁定该记录
            locked_run = RaidRun.objects.select_for_update().filter(pk=run.pk, status=RaidRun.Status.MARCHING).first()
            if not locked_run:
                continue

            return_time = _retreat_raid_run_due_to_blocked_target(locked_run, now=now, reason=reason)

        # 调度返程完成任务（事务外）
        if complete_raid_task is not None:
            safe_apply_async(
                complete_raid_task,
                args=[run.id],
                countdown=return_time,
                logger=logger,
                log_message="complete_raid_task dispatch failed for dismissed raid",
            )

        dismissed_count += 1

    if dismissed_count > 0:
        logger.info(
            "Dismissed %s marching raids to %s due to protection trigger", dismissed_count, defender.display_name
        )

    return dismissed_count
