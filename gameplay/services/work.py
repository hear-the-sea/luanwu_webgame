from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from core.exceptions import (
    GuestNotIdleError,
    GuestNotRequirementError,
    WorkError,
    WorkLimitExceededError,
    WorkNotCompletedError,
    WorkNotInProgressError,
    WorkRewardClaimedError,
)
from core.utils.time_scale import scale_duration
from gameplay.models import Manor, ResourceEvent, ResourceType, WorkAssignment, WorkTemplate
from gameplay.services.action_points import ACTION_POINT_EXPEDITION_COST, consume_action_points
from gameplay.services.inventory.core import add_item_to_inventory_locked
from gameplay.services.resources import grant_resources_locked
from gameplay.services.work_requirements import evaluate_work_requirements
from guests.models import Guest, GuestStatus
from guests.services.status import persist_guest_status_transition, persist_guest_status_transitions

MAX_CONCURRENT_WORKERS = 3  # 最多同时打工人数

WORK_TIER_CHEST_KEYS: dict[str, str] = {
    WorkTemplate.Tier.JUNIOR.value: "work_chest_small",
    WorkTemplate.Tier.INTERMEDIATE.value: "work_chest_medium",
    WorkTemplate.Tier.SENIOR.value: "work_chest_large",
}

WORK_TIER_ACTION_POINT_COSTS: dict[str, int] = {
    WorkTemplate.Tier.JUNIOR.value: 10,
    WorkTemplate.Tier.INTERMEDIATE.value: 20,
    WorkTemplate.Tier.SENIOR.value: 30,
}


def get_work_action_point_cost(tier: str) -> int:
    """返回工作区对应的派遣行动力消耗。"""
    return WORK_TIER_ACTION_POINT_COSTS.get(str(tier), ACTION_POINT_EXPEDITION_COST)


def _ensure_guest_meets_work_requirements(guest: Guest, work_template: WorkTemplate) -> None:
    """校验门客满足打工要求。"""
    eligibility = evaluate_work_requirements(guest, work_template)
    if eligibility.missing_requirements:
        missing = eligibility.missing_requirements[0]
        raise GuestNotRequirementError(guest, missing.key, missing.required, missing.actual)


def get_available_works_for_guest(guest: Guest) -> List[WorkTemplate]:
    """获取门客可接受的工作列表"""
    return list(
        WorkTemplate.objects.filter(
            required_level__lte=guest.level,
            required_force__lte=guest.force,
            required_intellect__lte=guest.intellect,
            required_defense__lte=guest.defense_stat,
            required_agility__lte=guest.agility,
        ).order_by("tier", "display_order")
    )


def assign_guest_to_work(guest: Guest, work_template: WorkTemplate) -> WorkAssignment:
    """派遣门客打工"""
    # 检查门客状态（初步检查，事务内会再次验证）
    if guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)

    # 检查门客是否满足工作要求（事务内会再次验证）
    _ensure_guest_meets_work_requirements(guest, work_template)

    # 使用事务确保原子性
    with transaction.atomic():
        # 先锁庄园，再锁门客，保证同庄园派遣上限检查串行化
        locked_manor = Manor.objects.select_for_update().get(pk=guest.manor_id)

        # 锁定门客，防止并发问题
        guest = Guest.objects.select_for_update().get(pk=guest.pk, manor=locked_manor)

        # 再次检查状态
        if guest.status != GuestStatus.IDLE:
            raise GuestNotIdleError(guest)

        # 锁内再次检查要求，避免并发更新属性后绕过验证
        _ensure_guest_meets_work_requirements(guest, work_template)

        action_point_cost = get_work_action_point_cost(work_template.tier)
        consume_action_points(
            locked_manor,
            action_point_cost,
            insufficient_message=f"行动力不足，无法开始打工（需要 {action_point_cost} 点）",
        )

        # 在事务内检查打工人数限制，防止并发超限
        current_working = WorkAssignment.objects.filter(
            manor=locked_manor,
            status=WorkAssignment.Status.WORKING,
        ).count()
        if current_working >= MAX_CONCURRENT_WORKERS:
            raise WorkLimitExceededError(MAX_CONCURRENT_WORKERS)

        # 同一种工作同一时间仅允许一名门客进行
        if WorkAssignment.objects.filter(
            manor=locked_manor,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
        ).exists():
            raise WorkError(f"{work_template.name} 当前已有门客在打工")

        # 计算完成时间
        now = timezone.now()
        complete_at = now + timedelta(seconds=scale_duration(work_template.work_duration, minimum=1))

        # 创建打工记录
        try:
            assignment = WorkAssignment.objects.create(
                manor=locked_manor,
                guest=guest,
                work_template=work_template,
                status=WorkAssignment.Status.WORKING,
                complete_at=complete_at,
            )
        except IntegrityError:
            # 防并发兜底：若唯一约束被击穿，转为业务可读错误
            raise WorkError(f"{work_template.name} 当前已有门客在打工")

        # 更新门客状态
        persist_guest_status_transition(
            guest,
            GuestStatus.WORKING,
            source="work_assign",
        )

    return assignment


def _complete_assignment_batch(assignment_ids: List[int], now, *, locked_guests: List[Guest]) -> int:
    """调用方按庄园、门客、任务顺序持锁后，完成本批任务并恢复实际释放的门客。"""
    updated_count = WorkAssignment.objects.filter(
        id__in=assignment_ids,
        status=WorkAssignment.Status.WORKING,
        complete_at__lte=now,
    ).update(status=WorkAssignment.Status.COMPLETED, finished_at=now)

    if updated_count <= 0:
        return 0

    completed_guest_ids = set(
        WorkAssignment.objects.filter(
            id__in=assignment_ids,
            status=WorkAssignment.Status.COMPLETED,
            finished_at=now,
        )
        .order_by()
        .values_list("guest_id", flat=True)
        .distinct()
    )
    if completed_guest_ids:
        active_guest_ids = set(
            WorkAssignment.objects.filter(
                guest_id__in=completed_guest_ids,
                status=WorkAssignment.Status.WORKING,
            )
            .order_by()
            .values_list("guest_id", flat=True)
            .distinct()
        )
        persist_guest_status_transitions(
            [
                guest
                for guest in locked_guests
                if guest.pk in completed_guest_ids
                and guest.pk not in active_guest_ids
                and guest.status == GuestStatus.WORKING
            ],
            GuestStatus.IDLE,
            now=now,
            source="work_complete",
        )

    return updated_count


def _complete_locked_work_batch(candidates: list[tuple[int, int, int, datetime]], now, *, skip_locked: bool) -> int:
    """候选只用于定位；庄园锁内重新读取门客和任务，避免旧快照覆盖召回后的派遣。"""
    with transaction.atomic():
        manor_ids = {row[1] for row in candidates}
        manor_locks = Manor.objects.select_for_update(
            skip_locked=skip_locked and connection.features.has_select_for_update_skip_locked,
        )
        locked_manor_ids = set(manor_locks.filter(pk__in=manor_ids).order_by("pk").values_list("pk", flat=True))
        if not locked_manor_ids:
            return 0

        assignment_ids = [row[0] for row in candidates if row[1] in locked_manor_ids]
        guest_ids = {row[2] for row in candidates if row[1] in locked_manor_ids}
        locked_guests = list(
            Guest.objects.select_for_update().filter(pk__in=guest_ids, manor_id__in=locked_manor_ids).order_by("pk")
        )
        locked_assignment_ids = list(
            WorkAssignment.objects.select_for_update()
            .filter(
                pk__in=assignment_ids,
                manor_id__in=locked_manor_ids,
                guest_id__in=[guest.pk for guest in locked_guests],
                status=WorkAssignment.Status.WORKING,
                complete_at__lte=now,
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if not locked_assignment_ids:
            return 0
        return _complete_assignment_batch(locked_assignment_ids, now, locked_guests=locked_guests)


def _complete_due_work_assignments(
    *, now, limit: int, batch_size: int, manor_id: int | None = None, skip_locked: bool = False
) -> int:
    due = WorkAssignment.objects.filter(status=WorkAssignment.Status.WORKING, complete_at__lte=now)
    if manor_id is not None:
        due = due.filter(manor_id=manor_id)
    due = due.order_by("complete_at", "pk")
    cursor: tuple[datetime, int] | None = None
    inspected = 0
    completed = 0
    while inspected < limit:
        candidates_query = due
        if cursor is not None:
            candidates_query = candidates_query.filter(
                Q(complete_at__gt=cursor[0]) | Q(complete_at=cursor[0], pk__gt=cursor[1])
            )
        current_limit = min(batch_size, limit - inspected)
        candidates = list(candidates_query.values_list("pk", "manor_id", "guest_id", "complete_at")[:current_limit])
        if not candidates:
            break
        inspected += len(candidates)
        cursor = (candidates[-1][3], candidates[-1][0])
        completed += _complete_locked_work_batch(candidates, now, skip_locked=skip_locked)
        if len(candidates) < current_limit:
            break
    return completed


def complete_work_assignments(*, limit: int | None = None) -> int:
    """定时结算到期打工；每批独立提交，每次调用最多检查配置数量的候选任务。"""
    now = timezone.now()
    scan_limit = max(0, int(limit if limit is not None else getattr(settings, "WORK_COMPLETION_SCAN_LIMIT", 1000)))
    if scan_limit == 0:
        return 0
    batch_size = max(1, int(getattr(settings, "WORK_COMPLETION_BATCH_SIZE", 250)))
    from gameplay.services.inventory.target_items import release_expired_guest_occupations

    release_expired_guest_occupations(now=now, limit=min(batch_size, scan_limit))
    return _complete_due_work_assignments(now=now, limit=scan_limit, batch_size=batch_size, skip_locked=True)


def _lock_work_assignment(assignment: WorkAssignment) -> tuple[Manor, Guest, WorkAssignment] | None:
    """调用方事务内统一按庄园、门客、任务顺序获取行锁，不联结其他表扩大锁范围。"""
    manor = Manor.objects.select_for_update().filter(pk=assignment.manor_id).first()
    if manor is None:
        return None
    guest = Guest.objects.select_for_update().filter(pk=assignment.guest_id, manor_id=manor.pk).first()
    if guest is None:
        return None
    locked_assignment = (
        WorkAssignment.objects.select_for_update()
        .filter(pk=assignment.pk, manor_id=manor.pk, guest_id=guest.pk)
        .first()
    )
    if locked_assignment is None:
        return None
    return manor, guest, locked_assignment


def recall_guest_from_work(assignment: WorkAssignment) -> bool:
    """
    召回打工中的门客
    不发放任何报酬
    """
    with transaction.atomic():
        locked_state = _lock_work_assignment(assignment)
        if locked_state is None:
            raise WorkNotInProgressError()
        _manor, locked_guest, locked_assignment = locked_state
        if locked_assignment.status != WorkAssignment.Status.WORKING:
            raise WorkNotInProgressError()

        # 更新任务状态
        finished_at = timezone.now()
        locked_assignment.status = WorkAssignment.Status.RECALLED
        locked_assignment.finished_at = finished_at
        locked_assignment.save(update_fields=["status", "finished_at"])

        # 更新门客状态
        persist_guest_status_transition(
            locked_guest,
            GuestStatus.IDLE,
            source="work_recall",
        )

    # 同步传入对象状态，避免调用方使用旧值
    assignment.status = WorkAssignment.Status.RECALLED
    assignment.finished_at = finished_at
    assignment.guest.status = GuestStatus.IDLE
    assignment.guest.training_complete_at = locked_guest.training_complete_at
    assignment.guest.training_remaining_seconds = locked_guest.training_remaining_seconds

    return True


def claim_work_reward(assignment: WorkAssignment) -> Dict[str, Any]:
    """
    领取打工报酬
    返回获得的资源
    """
    with transaction.atomic():
        locked_state = _lock_work_assignment(assignment)
        if locked_state is None:
            raise WorkNotCompletedError()
        manor, guest, locked_assignment = locked_state
        if locked_assignment.status != WorkAssignment.Status.COMPLETED:
            raise WorkNotCompletedError()
        if locked_assignment.reward_claimed:
            raise WorkRewardClaimedError()

        reward_silver = locked_assignment.work_template.reward_silver
        chest_key = WORK_TIER_CHEST_KEYS[locked_assignment.work_template.tier]

        reward_note = f"{guest.display_name} 在 {locked_assignment.work_template.name} 打工获得报酬"
        credited, _overflow = grant_resources_locked(
            manor,
            {ResourceType.SILVER: reward_silver},
            reason=ResourceEvent.Reason.WORK_REWARD,
            note=reward_note,
            sync_production=False,
        )
        credited_silver = credited.get(ResourceType.SILVER, 0)
        chest_item = add_item_to_inventory_locked(manor, chest_key, 1)

        # 标记为已领取
        locked_assignment.reward_claimed = True
        locked_assignment.save(update_fields=["reward_claimed"])

    # 让传入对象状态保持同步，避免调用方误判
    assignment.reward_claimed = True

    return {
        "silver": credited_silver,
        "item_key": chest_key,
        "item_name": chest_item.template.name,
        "item_quantity": 1,
    }


def assign_guest_to_work_with_refresh(*, manor: Manor, guest: Guest, work_template: WorkTemplate) -> WorkAssignment:
    """刷新当前庄园的打工状态后，再派遣门客打工。"""
    refresh_work_assignments(manor)
    return assign_guest_to_work(guest, work_template)


def recall_guest_from_work_with_refresh(*, manor: Manor, assignment: WorkAssignment) -> bool:
    """刷新当前庄园的打工状态后，再尝试召回门客。"""
    refresh_work_assignments(manor)
    return recall_guest_from_work(assignment)


def claim_work_reward_with_refresh(*, manor: Manor, assignment: WorkAssignment) -> Dict[str, Any]:
    """刷新当前庄园的打工状态后，再尝试领取报酬。"""
    refresh_work_assignments(manor)
    return claim_work_reward(assignment)


def refresh_work_assignments(manor: Manor) -> int:
    """按同一锁顺序刷新当前庄园到期任务，与全站扫描共用有界结算。"""
    return _complete_due_work_assignments(
        now=timezone.now(),
        limit=max(1, int(getattr(settings, "WORK_COMPLETION_SCAN_LIMIT", 1000))),
        batch_size=max(1, int(getattr(settings, "WORK_COMPLETION_BATCH_SIZE", 250))),
        manor_id=manor.pk,
    )
