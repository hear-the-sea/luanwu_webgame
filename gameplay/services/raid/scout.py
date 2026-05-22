"""
侦察系统服务

提供侦察相关功能：发起侦察、完成侦察、撤退等。
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from django.db import IntegrityError, models
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import ScoutStartError
from core.utils.time_scale import scale_duration

from ...constants import PVPConstants
from ...models import Manor, PlayerTroop, ScoutCooldown, ScoutRecord
from ..technology import get_player_technology_level
from ..virtual_players import record_virtual_player_backfill_demand_for_search
from . import scout_finalize as scout_finalize_command
from . import scout_followups
from . import scout_refresh as scout_refresh_command
from . import scout_return as scout_return_command
from . import scout_start as scout_start_command
from .utils import calculate_distance, can_attack_target, get_asset_level, get_troop_description

logger = logging.getLogger(__name__)


def _roll_scout_success() -> float:
    """Expose scout success sampling through a stable helper for orchestration/tests."""
    return random.random()


def _lock_manor_pair(attacker_id: int, defender_id: int) -> tuple[Manor, Manor]:
    """Lock attacker/defender rows in a stable order to avoid deadlocks."""
    ids = [attacker_id] if attacker_id == defender_id else sorted([attacker_id, defender_id])
    locked = {m.pk: m for m in Manor.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    attacker = locked.get(attacker_id)
    defender = locked.get(defender_id)
    if attacker is None or defender is None:
        raise ScoutStartError("目标庄园不存在")
    return attacker, defender


def _restore_scout_troops(attacker: Manor, quantity: int, *, now: datetime | None = None) -> None:
    """Return scout troops to the attacker, recreating the row when it was removed."""
    if quantity <= 0:
        return

    restored_at = now or timezone.now()
    scout_template, _ = TroopTemplate.objects.get_or_create(
        key=PVPConstants.SCOUT_TROOP_KEY,
        defaults={"name": "探子"},
    )
    updated = PlayerTroop.objects.filter(manor=attacker, troop_template=scout_template).update(
        count=models.F("count") + quantity,
        updated_at=restored_at,
    )
    if updated:
        return

    try:
        PlayerTroop.objects.create(
            manor=attacker,
            troop_template=scout_template,
            count=quantity,
        )
    except IntegrityError:
        updated = PlayerTroop.objects.filter(manor=attacker, troop_template=scout_template).update(
            count=models.F("count") + quantity,
            updated_at=restored_at,
        )
        if updated:
            return
        raise RuntimeError("探子返还失败，请稍后重试")


def get_scout_tech_level(manor: Manor) -> int:
    """获取庄园的侦察术等级"""
    return get_player_technology_level(manor, "scout_art")


def calculate_scout_success_rate(attacker: Manor, defender: Manor) -> float:
    """
    计算侦察成功率。

    公式：
    基础成功率 = 50%
    成功率修正 = (我方侦察术等级 - 对方侦察术等级) × 5%
    最终成功率 = min(95%, max(10%, 基础成功率 + 成功率修正))
    """
    attacker_level = get_scout_tech_level(attacker)
    defender_level = get_scout_tech_level(defender)

    base_rate = PVPConstants.SCOUT_BASE_SUCCESS_RATE
    rate_modifier = (attacker_level - defender_level) * PVPConstants.SCOUT_TECH_RATE_PER_LEVEL

    final_rate = base_rate + rate_modifier
    return max(PVPConstants.SCOUT_MIN_SUCCESS_RATE, min(PVPConstants.SCOUT_MAX_SUCCESS_RATE, final_rate))


def calculate_scout_travel_time(attacker: Manor, defender: Manor) -> int:
    """
    计算侦察所需时间（秒）。

    公式：侦察时间 = 距离 × 1 + 30
    """
    distance = calculate_distance(attacker, defender)
    raw = int(distance * PVPConstants.SCOUT_TRAVEL_TIME_PER_DISTANCE + PVPConstants.SCOUT_BASE_TRAVEL_TIME)
    return scale_duration(raw, minimum=1)


def check_scout_cooldown(attacker: Manor, defender: Manor) -> Tuple[bool, Optional[int]]:
    """
    检查对同一目标的侦察冷却。

    Returns:
        (是否在冷却中, 剩余冷却秒数)
    """
    now = timezone.now()
    cooldown = ScoutCooldown.objects.filter(attacker=attacker, defender=defender, cooldown_until__gt=now).first()

    if cooldown:
        remaining = int((cooldown.cooldown_until - now).total_seconds())
        return True, remaining

    return False, None


def get_scout_count(manor: Manor) -> int:
    """获取庄园的探子数量"""
    try:
        troop = PlayerTroop.objects.select_related("troop_template").get(
            manor=manor, troop_template__key=PVPConstants.SCOUT_TROOP_KEY
        )
        return troop.count
    except PlayerTroop.DoesNotExist:
        return 0


def start_scout(attacker: Manor, defender: Manor) -> ScoutRecord:
    """
    发起侦察。

    Args:
        attacker: 进攻方庄园
        defender: 防守方庄园

    Returns:
        侦察记录

    Raises:
        ScoutStartError: 无法发起侦察时
    """
    record = scout_start_command.start_scout_command(
        attacker,
        defender,
        can_attack_target_fn=can_attack_target,
        check_scout_cooldown_fn=check_scout_cooldown,
        get_scout_count_fn=get_scout_count,
        lock_manor_pair_fn=_lock_manor_pair,
        calculate_success_rate_fn=calculate_scout_success_rate,
        calculate_travel_time_fn=calculate_scout_travel_time,
        schedule_completion_fn=scout_followups.schedule_scout_completion,
        now_fn=timezone.now,
        scout_cooldown_model=ScoutCooldown,
        player_troop_model=PlayerTroop,
        scout_record_model=ScoutRecord,
        scout_troop_key=PVPConstants.SCOUT_TROOP_KEY,
    )
    record_virtual_player_backfill_demand_for_search(
        searcher=attacker,
        region=defender.region,
        candidate_count=1,
    )
    return record


def finalize_scout(record: ScoutRecord, now: Optional[datetime] = None) -> None:
    """
    侦察到达目标，判定成功/失败并进入返程阶段。

    流程：
    1. 去程到达 → 判定成功/失败
    2. 如果失败 → 事务提交后给防守方发送警告消息
    3. 设置状态为 RETURNING，计算 return_at
    4. 返程完成后才给进攻方发送结果消息（通过 finalize_scout_return）

    Args:
        record: 侦察记录
        now: 当前时间（可选）
    """
    scout_finalize_command.finalize_scout_command(
        record,
        now=now,
        scout_record_model=ScoutRecord,
        scout_cooldown_model=ScoutCooldown,
        random_fn=_roll_scout_success,
        gather_intel_fn=_gather_scout_intel,
        schedule_followup_fn=scout_followups.schedule_scout_followup,
        schedule_return_completion_fn=scout_followups.schedule_scout_return_completion,
    )


def finalize_scout_return(record: ScoutRecord, now: Optional[datetime] = None) -> None:
    """
    侦察返程完成，发送结果消息给进攻方。

    Args:
        record: 侦察记录
        now: 当前时间（可选）
    """
    scout_return_command.finalize_scout_return_command(
        record,
        now=now,
        scout_record_model=ScoutRecord,
        restore_scout_troops_fn=_restore_scout_troops,
        schedule_followup_fn=scout_followups.schedule_scout_followup,
    )


def _gather_scout_intel(defender: Manor) -> Dict[str, Any]:
    """收集目标庄园的情报"""
    # 护院数量（模糊）
    total_troops = PlayerTroop.objects.filter(manor=defender).aggregate(total=models.Sum("count"))["total"] or 0

    # 门客数量和平均等级
    guests = defender.guests.all()
    guest_count = guests.count()
    avg_guest_level = 0
    if guest_count > 0:
        total_level = sum(g.level for g in guests)
        avg_guest_level = round(total_level / guest_count)

    # 资产等级
    asset_level, _ = get_asset_level(defender)

    return {
        "troop_description": get_troop_description(total_troops),
        "guest_count": guest_count,
        "avg_guest_level": avg_guest_level,
        "asset_level": asset_level,
        "scouted_at": timezone.now().isoformat(),
    }


def refresh_scout_records(manor: Manor, *, prefer_async: bool = False) -> None:
    """刷新庄园的侦察记录状态（支持异步优先结算）。"""

    def _collect_due_ids(target_manor: Manor, current_time: datetime) -> tuple[list[int], list[int]]:
        return scout_refresh_command.collect_due_scout_record_ids(
            target_manor,
            current_time,
            scout_record_model=ScoutRecord,
        )

    def _dispatch_async(scouting_ids: list[int], returning_ids: list[int]) -> tuple[list[int], list[int], bool]:
        return scout_refresh_command.dispatch_async_scout_refresh(
            scouting_ids,
            returning_ids,
            resolve_tasks_fn=lambda: scout_refresh_command.resolve_scout_refresh_tasks(logger=logger),
            try_dispatch_fn=lambda task, record_id, phase: scout_refresh_command.try_dispatch_scout_refresh_task(
                task,
                record_id,
                phase,
                logger=logger,
            ),
        )

    def _finalize_due(current_time: datetime, scouting_ids: list[int], returning_ids: list[int]) -> None:
        scout_refresh_command.finalize_due_scout_records(
            current_time,
            scouting_ids,
            returning_ids,
            scout_record_model=ScoutRecord,
            finalize_scout_fn=finalize_scout,
            finalize_scout_return_fn=finalize_scout_return,
        )

    scout_refresh_command.refresh_scout_records_command(
        manor,
        prefer_async=prefer_async,
        now_fn=timezone.now,
        collect_due_ids_fn=_collect_due_ids,
        dispatch_async_fn=_dispatch_async,
        finalize_due_fn=_finalize_due,
    )


def get_active_scouts(manor: Manor) -> List[ScoutRecord]:
    """获取进行中的侦察列表（包括去程和返程）"""
    return list(
        ScoutRecord.objects.filter(
            attacker=manor, status__in=[ScoutRecord.Status.SCOUTING, ScoutRecord.Status.RETURNING]
        )
        .select_related("defender")
        .order_by("-started_at")
    )


def get_scout_history(manor: Manor, limit: int = 20) -> List[ScoutRecord]:
    """获取侦察历史记录"""
    return list(ScoutRecord.objects.filter(attacker=manor).select_related("defender").order_by("-started_at")[:limit])


def can_scout_retreat(record: ScoutRecord) -> bool:
    """判断侦察是否可以撤退（仅在去程阶段可撤退）"""
    return record.status == ScoutRecord.Status.SCOUTING


def request_scout_retreat(record: ScoutRecord) -> None:
    """
    请求侦察撤退（仅在去程阶段可用）。

    撤退后探子立即返程，不消耗探子（撤退时归还）。

    Args:
        record: 侦察记录

    Raises:
        ScoutRetreatStateError: 无法撤退时
    """
    scout_return_command.request_scout_retreat_command(
        record,
        now_fn=timezone.now,
        scout_record_model=ScoutRecord,
        restore_scout_troops_fn=_restore_scout_troops,
        schedule_return_completion_fn=scout_followups.schedule_scout_return_completion_after_retreat,
    )
