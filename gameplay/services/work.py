from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from django.db import IntegrityError, transaction
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
from gameplay.services.inventory.core import add_item_to_inventory_locked
from guests.models import Guest, GuestStatus

MAX_CONCURRENT_WORKERS = 3  # 最多同时打工人数

WORK_TIER_CHEST_KEYS: dict[str, str] = {
    WorkTemplate.Tier.JUNIOR.value: "work_chest_small",
    WorkTemplate.Tier.INTERMEDIATE.value: "work_chest_medium",
    WorkTemplate.Tier.SENIOR.value: "work_chest_large",
}


def _ensure_guest_meets_work_requirements(guest: Guest, work_template: WorkTemplate) -> None:
    """校验门客满足打工要求。"""
    if guest.level < work_template.required_level:
        raise GuestNotRequirementError(guest, "level", work_template.required_level, guest.level)
    if guest.force < work_template.required_force:
        raise GuestNotRequirementError(guest, "force", work_template.required_force, guest.force)
    if guest.intellect < work_template.required_intellect:
        raise GuestNotRequirementError(guest, "intellect", work_template.required_intellect, guest.intellect)


def get_available_works_for_guest(guest: Guest) -> List[WorkTemplate]:
    """获取门客可接受的工作列表"""
    return list(
        WorkTemplate.objects.filter(
            required_level__lte=guest.level,
            required_force__lte=guest.force,
            required_intellect__lte=guest.intellect,
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
        guest.status = GuestStatus.WORKING
        guest.save(update_fields=["status"])

    return assignment


def _complete_assignment_batch(assignment_ids: List[int], now) -> int:
    """批量完成仍处于打工中的任务，并仅回写实际完成的门客状态。"""
    updated_count = WorkAssignment.objects.filter(
        id__in=assignment_ids,
        status=WorkAssignment.Status.WORKING,
    ).update(status=WorkAssignment.Status.COMPLETED, finished_at=now)

    if updated_count <= 0:
        return 0

    completed_guest_ids = list(
        WorkAssignment.objects.filter(
            id__in=assignment_ids,
            status=WorkAssignment.Status.COMPLETED,
        )
        .values_list("guest_id", flat=True)
        .distinct()
    )
    if completed_guest_ids:
        Guest.objects.filter(id__in=completed_guest_ids, status=GuestStatus.WORKING).update(status=GuestStatus.IDLE)

    return updated_count


def complete_work_assignments() -> int:
    """
    完成所有到期的打工任务
    由定时任务调用
    返回完成的任务数量

    性能优化：使用批量更新替代逐条更新，减少数据库查询
    """
    now = timezone.now()

    with transaction.atomic():
        # 查找所有到期的打工任务
        assignments = list(
            WorkAssignment.objects.select_for_update()
            .filter(status=WorkAssignment.Status.WORKING, complete_at__lte=now)
            .select_related("guest")
        )

        if not assignments:
            return 0

        assignment_ids = [a.id for a in assignments]

        return _complete_assignment_batch(assignment_ids, now)


def recall_guest_from_work(assignment: WorkAssignment) -> bool:
    """
    召回打工中的门客
    不发放任何报酬
    """
    with transaction.atomic():
        locked_assignment = (
            WorkAssignment.objects.select_for_update().select_related("guest").filter(pk=assignment.pk).first()
        )
        if not locked_assignment or locked_assignment.status != WorkAssignment.Status.WORKING:
            raise WorkNotInProgressError()

        # 更新任务状态
        finished_at = timezone.now()
        locked_assignment.status = WorkAssignment.Status.RECALLED
        locked_assignment.finished_at = finished_at
        locked_assignment.save(update_fields=["status", "finished_at"])

        # 更新门客状态
        locked_assignment.guest.status = GuestStatus.IDLE
        locked_assignment.guest.save(update_fields=["status"])

    # 同步传入对象状态，避免调用方使用旧值
    assignment.status = WorkAssignment.Status.RECALLED
    assignment.finished_at = finished_at
    assignment.guest.status = GuestStatus.IDLE

    return True


def claim_work_reward(assignment: WorkAssignment) -> Dict[str, Any]:
    """
    领取打工报酬
    返回获得的资源
    """
    with transaction.atomic():
        locked_assignment = (
            WorkAssignment.objects.select_for_update()
            .select_related("manor", "guest", "work_template")
            .filter(pk=assignment.pk)
            .first()
        )
        if not locked_assignment or locked_assignment.status != WorkAssignment.Status.COMPLETED:
            raise WorkNotCompletedError()
        if locked_assignment.reward_claimed:
            raise WorkRewardClaimedError()

        reward_silver = locked_assignment.work_template.reward_silver
        chest_key = WORK_TIER_CHEST_KEYS[locked_assignment.work_template.tier]

        # 增加庄园银两
        manor = Manor.objects.select_for_update().get(pk=locked_assignment.manor_id)
        manor.silver += reward_silver
        manor.save(update_fields=["silver"])
        chest_item = add_item_to_inventory_locked(manor, chest_key, 1)

        # 记录资源流水
        ResourceEvent.objects.create(
            manor=manor,
            resource_type=ResourceType.SILVER,
            delta=reward_silver,
            reason=ResourceEvent.Reason.WORK_REWARD,
            note=f"{locked_assignment.guest.display_name} 在 {locked_assignment.work_template.name} 打工获得报酬",
        )

        # 标记为已领取
        locked_assignment.reward_claimed = True
        locked_assignment.save(update_fields=["reward_claimed"])

    # 让传入对象状态保持同步，避免调用方误判
    assignment.reward_claimed = True

    return {
        "silver": reward_silver,
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


def refresh_work_assignments(manor: Manor) -> None:
    """
    刷新打工状态
    自动完成到期的任务

    性能优化：使用批量更新替代逐条更新
    """
    now = timezone.now()

    with transaction.atomic():
        # 查找该庄园所有到期的打工任务
        assignments = list(
            WorkAssignment.objects.select_for_update().filter(
                manor=manor,
                status=WorkAssignment.Status.WORKING,
                complete_at__lte=now,
            )
        )

        if not assignments:
            return

        assignment_ids = [a.id for a in assignments]

        _complete_assignment_batch(assignment_ids, now)
