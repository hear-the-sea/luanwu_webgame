"""
门客训练系统服务
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, TypedDict

from django.db import connection, transaction
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.config import GUEST
from core.exceptions import (
    GuestItemOwnershipError,
    GuestMaxLevelError,
    GuestNotIdleError,
    GuestOwnershipError,
    GuestTrainingInProgressError,
    GuestTrainingUnavailableError,
    InsufficientStockError,
)
from core.utils.imports import is_missing_target_import
from gameplay.services.inventory import core as inventory_core
from gameplay.services.resources import spend_resources

if TYPE_CHECKING:
    from gameplay.models import InventoryItem
    from gameplay.models import Manor

from ..growth_engine import apply_training_completion
from ..models import Guest, GuestStatus, TrainingLog
from ..utils.training_calculator import get_level_up_cost, get_training_duration
from ..utils.training_timer import ensure_training_timer, remaining_training_seconds

logger = logging.getLogger(__name__)
MAX_GUEST_LEVEL = int(GUEST.MAX_LEVEL)


class GuestTrainingReductionResult(TypedDict):
    time_reduced: int
    applied_levels: int
    next_eta: datetime | None


def _normalize_positive_training_seconds(raw_value: Any, *, contract_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    try:
        parsed_seconds = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}") from exc
    if parsed_seconds <= 0:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return parsed_seconds


def _normalize_non_negative_training_int(raw_value: Any, *, contract_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    try:
        parsed_value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}") from exc
    if parsed_value < 0:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return parsed_value


def _normalize_training_datetime(raw_value: Any, *, contract_name: str) -> datetime | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, datetime):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return raw_value


def _try_enqueue_complete_guest_training(guest: Guest, *, countdown: int, source: str) -> None:
    try:
        from guests.tasks import complete_guest_training
    except ImportError as exc:
        if not is_missing_target_import(exc, "guests.tasks"):
            raise
        logger.warning(
            "Failed to import celery task; finalize guest training immediately",
            extra={"guest_id": guest.id, "source": source},
            exc_info=True,
        )
        # 仅在开发/测试环境下允许瞬间完成训练
        # 生产环境应该保持 training_complete_at，依赖定时任务结算
        from django.conf import settings

        if settings.DEBUG:
            logger.debug("DEBUG模式：允许瞬间完成训练")
            finalize_guest_training(guest, now=guest.training_complete_at)
        else:
            # 生产环境：保持训练时间不变，让定时任务处理
            logger.warning("生产环境：保持训练完成时间，等待定时任务结算")
        return

    dispatched = safe_apply_async(
        complete_guest_training,
        args=[guest.id],
        countdown=countdown,
        logger=logger,
        log_message="guest training task dispatch failed",
    )
    if not dispatched:
        logger.warning(
            "Failed to enqueue guest training task; finalize guest training immediately",
            extra={"guest_id": guest.id, "countdown": countdown, "source": source},
        )
        # 仅在开发/测试环境下允许瞬间完成训练
        from django.conf import settings

        if settings.DEBUG:
            logger.debug("DEBUG模式：允许瞬间完成训练")
            finalize_guest_training(guest, now=guest.training_complete_at)
        else:
            # 生产环境：保持训练时间不变，让定时任务处理
            logger.warning("生产环境：保持训练完成时间，等待定时任务结算")


def ensure_auto_training(guest: Guest) -> bool:
    """
    如果没有训练计划且门客未达到最高等级，则自动开始训练到下一级。
    """
    if not getattr(guest, "pk", None):
        return False

    with transaction.atomic():
        locked_guest = Guest.objects.select_for_update().select_related("template").get(pk=guest.pk)
        guest.training_target_level = locked_guest.training_target_level
        guest.training_complete_at = locked_guest.training_complete_at

        if locked_guest.level >= MAX_GUEST_LEVEL:
            return False
        if locked_guest.status != GuestStatus.IDLE:
            return False
        if locked_guest.training_complete_at:
            return False

        duration = get_training_duration(locked_guest, levels=1)
        locked_guest.training_target_level = min(MAX_GUEST_LEVEL, locked_guest.level + 1)
        locked_guest.training_complete_at = timezone.now() + timedelta(seconds=duration)
        locked_guest.save(update_fields=["training_target_level", "training_complete_at"])

        guest.training_target_level = locked_guest.training_target_level
        guest.training_complete_at = locked_guest.training_complete_at

        def enqueue_training() -> None:
            _try_enqueue_complete_guest_training(
                locked_guest,
                countdown=max(0, int(duration)),
                source="ensure_auto_training",
            )

        transaction.on_commit(enqueue_training)
    return True


def _reduce_guest_training_once(guest: Guest, remaining_seconds: int) -> tuple[int, int, bool]:
    """单步缩减训练时间。

    Returns:
        (实际减少秒数, 消耗后的剩余秒数, 是否影响了训练进度)
    """
    remaining = remaining_training_seconds(guest, now=timezone.now())
    if remaining <= 0:
        finalized = finalize_guest_training(guest)
        if finalized:
            guest.refresh_from_db()
        return 0, remaining_seconds, finalized

    consume = min(remaining_seconds, remaining)
    if consume >= remaining:
        finalize_guest_training(guest, now=guest.training_complete_at)
        guest.refresh_from_db()
        if guest.level < MAX_GUEST_LEVEL and not guest.training_complete_at:
            ensure_auto_training(guest)
            guest.refresh_from_db()
    else:
        training_complete_at = guest.training_complete_at
        assert training_complete_at is not None
        guest.training_complete_at = training_complete_at - timedelta(seconds=consume)
        guest.save(update_fields=["training_complete_at"])
    return consume, remaining_seconds - consume, consume > 0


def _reschedule_guest_training_if_needed(guest: Guest, source: str) -> None:
    finalize_guest_training(guest)
    if guest.level < MAX_GUEST_LEVEL and not guest.training_complete_at:
        ensure_auto_training(guest)
        guest.refresh_from_db()
    if not guest.training_complete_at:
        return

    countdown = max(0, int((guest.training_complete_at - timezone.now()).total_seconds()))

    def enqueue_training() -> None:
        _try_enqueue_complete_guest_training(
            guest,
            countdown=countdown,
            source=source,
        )

    transaction.on_commit(enqueue_training)


def _load_locked_experience_item(manor: Manor, item_id: int) -> InventoryItem:
    from gameplay.models import InventoryItem, ItemTemplate

    locked_item = (
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(
            pk=item_id,
            manor=manor,
            template__effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .first()
    )
    if not locked_item:
        raise GuestItemOwnershipError()
    if locked_item.quantity <= 0:
        raise InsufficientStockError(locked_item.template.name, 1, locked_item.quantity)
    return locked_item


@transaction.atomic
def use_experience_item_for_guest(manor: Manor, guest: Guest, item_id: int, reduce_seconds: int) -> Dict[str, Any]:
    """
    对单个门客使用经验道具（原子化版本）。

    关键保证：
    - 缩短训练进度与道具扣减在同一事务中完成
    - 任一步失败都会整体回滚，避免“先生效后扣失败”导致状态不一致
    - 锁顺序统一为 Manor -> InventoryItem -> Guest
    """
    normalized_reduce_seconds = _normalize_positive_training_seconds(
        reduce_seconds,
        contract_name="guest training reduce_seconds",
    )

    from gameplay.models import Manor as ManorModel

    ManorModel.objects.select_for_update().get(pk=manor.pk)
    locked_item = _load_locked_experience_item(manor, item_id)

    locked_guest = Guest.objects.select_for_update().select_related("template").filter(pk=guest.pk, manor=manor).first()
    if not locked_guest:
        raise GuestOwnershipError(message="门客不存在或不属于您的庄园")
    if locked_guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(locked_guest)

    result = reduce_training_time_for_guest(locked_guest, normalized_reduce_seconds)
    inventory_core.consume_inventory_item_locked(locked_item, 1)

    remaining_quantity = 0
    if locked_item.pk:
        remaining_quantity = _normalize_non_negative_training_int(
            locked_item.quantity,
            contract_name="guest training remaining_item_quantity",
        )

    return {
        "time_reduced": _normalize_non_negative_training_int(
            result.get("time_reduced"),
            contract_name="guest training reduction result time_reduced",
        ),
        "applied_levels": _normalize_non_negative_training_int(
            result.get("applied_levels"),
            contract_name="guest training reduction result applied_levels",
        ),
        "next_eta": _normalize_training_datetime(
            result.get("next_eta"),
            contract_name="guest training reduction result next_eta",
        ),
        "new_level": _normalize_positive_training_seconds(
            locked_guest.level,
            contract_name="guest training new_level",
        ),
        "current_hp": _normalize_non_negative_training_int(
            locked_guest.current_hp,
            contract_name="guest training current_hp",
        ),
        "max_hp": _normalize_positive_training_seconds(
            locked_guest.max_hp,
            contract_name="guest training max_hp",
        ),
        "remaining_item_quantity": remaining_quantity,
    }


def reduce_training_time(manor: Manor, seconds: int) -> Dict[str, int]:
    """
    缩短庄园所有门客的训练时间。多余的时间会顺延到下一级。

    Args:
        manor: 庄园对象
        seconds: 要缩短的秒数

    Returns:
        包含缩短的总时间和影响的门客数量的字典

    Raises:
        GuestTrainingUnavailableError: 所有门客已达等级上限时抛出
    """
    normalized_seconds = _normalize_positive_training_seconds(
        seconds,
        contract_name="guest training seconds",
    )
    total_reduced = 0
    applied = 0
    now = timezone.now()
    remaining_seconds = normalized_seconds

    with transaction.atomic():
        candidate_ids = list(
            manor.guests.filter(level__lt=MAX_GUEST_LEVEL, status=GuestStatus.IDLE)
            .order_by("training_complete_at", "id")
            .values_list("id", flat=True)
        )
        if not candidate_ids:
            raise GuestTrainingUnavailableError()

        locked_guests_qs = (
            Guest.objects.select_related("template")
            .filter(id__in=candidate_ids, manor=manor, level__lt=MAX_GUEST_LEVEL, status=GuestStatus.IDLE)
            .order_by("id")
        )
        if connection.features.has_select_for_update_of:
            locked_guests_qs = locked_guests_qs.select_for_update(of=("self",))
        else:
            locked_guests_qs = locked_guests_qs.select_for_update()

        locked_guests_by_id = {guest.id: guest for guest in locked_guests_qs}
        touched_guest_ids: set[int] = set()

        for guest_id in candidate_ids:
            guest = locked_guests_by_id.get(guest_id)
            if guest is None:
                continue
            if not ensure_training_timer(guest, now=now):
                continue

            guest_touched = False
            while remaining_seconds > 0 and guest.training_complete_at and guest.level < MAX_GUEST_LEVEL:
                reduced, remaining_seconds, touched = _reduce_guest_training_once(guest, remaining_seconds)
                total_reduced += reduced
                if touched:
                    applied += 1
                    guest_touched = True
            if guest_touched and guest.id not in touched_guest_ids:
                touched_guest_ids.add(guest.id)
                _reschedule_guest_training_if_needed(guest, source="reduce_training_time")
            if remaining_seconds <= 0:
                break

    return {"time_reduced": total_reduced, "applied_guests": applied}


def reduce_training_time_for_guest(guest: Guest, seconds: int) -> GuestTrainingReductionResult:
    """
    缩短单个门客的训练时间。多余的时间继续用于后续等级。

    Args:
        guest: 门客对象
        seconds: 要缩短的秒数

    Returns:
        包含缩短的总时间、影响的等级数和下次完成时间的字典

    Raises:
        GuestMaxLevelError: 门客已达等级上限时抛出
    """
    normalized_seconds = _normalize_positive_training_seconds(
        seconds,
        contract_name="guest training seconds",
    )
    if guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)
    now = timezone.now()
    if not ensure_training_timer(guest, now=now):
        if guest.level >= MAX_GUEST_LEVEL:
            raise GuestMaxLevelError(guest, max_level=MAX_GUEST_LEVEL)

    remaining_seconds = normalized_seconds
    total_reduced = 0
    levels_applied = 0

    while remaining_seconds > 0 and guest.level < MAX_GUEST_LEVEL:
        if not ensure_training_timer(guest, now=now):
            break
        reduced, remaining_seconds, touched = _reduce_guest_training_once(guest, remaining_seconds)
        total_reduced += reduced
        if touched:
            levels_applied += 1

    _reschedule_guest_training_if_needed(guest, source="reduce_training_time_for_guest")
    return {
        "time_reduced": total_reduced,
        "applied_levels": levels_applied,
        "next_eta": guest.training_complete_at,
    }


def train_guest(guest: Guest, levels: int = 1) -> Guest:
    """
    开始培养门客，消耗资源并设置训练计时器。
    """
    if not getattr(guest, "pk", None):
        raise AssertionError("train_guest requires a persisted guest")
    normalized_levels = _normalize_positive_training_seconds(levels, contract_name="guest training levels")

    with transaction.atomic():
        # 死锁预防：统一锁顺序 Manor -> Guest
        # 全局规则是先锁 Manor (资源扣除) 再锁 Guest (状态变更)
        from gameplay.models import Manor

        Manor.objects.select_for_update().get(pk=guest.manor_id)

        locked_guest = Guest.objects.select_for_update().select_related("manor", "template").get(pk=guest.pk)
        if locked_guest.status != GuestStatus.IDLE:
            raise GuestNotIdleError(locked_guest)
        if locked_guest.level >= MAX_GUEST_LEVEL:
            raise GuestMaxLevelError(locked_guest, max_level=MAX_GUEST_LEVEL)
        if locked_guest.training_complete_at:
            raise GuestTrainingInProgressError(locked_guest)
        levels_to_apply = normalized_levels
        if locked_guest.level + levels_to_apply > MAX_GUEST_LEVEL:
            levels_to_apply = MAX_GUEST_LEVEL - locked_guest.level
        manor = locked_guest.manor
        cost = get_level_up_cost(locked_guest, levels_to_apply)
        from gameplay.models import ResourceEvent

        spend_resources(
            manor,
            cost,
            note=f"培养 {guest.template.name}",
            reason=ResourceEvent.Reason.TRAINING_COST,
        )
        duration = get_training_duration(locked_guest, levels_to_apply)
        locked_guest.training_target_level = locked_guest.level + levels_to_apply
        locked_guest.training_complete_at = timezone.now() + timedelta(seconds=duration)
        locked_guest.save(update_fields=["training_target_level", "training_complete_at"])
        TrainingLog.objects.create(manor=manor, guest=locked_guest, delta_level=levels_to_apply, resource_cost=cost)

    # Celery 不可用时直接完成训练，确保调用方立即看到等级变更（测试/开发环境友好）。
    def enqueue_training() -> None:
        _try_enqueue_complete_guest_training(
            locked_guest,
            countdown=max(0, int(duration)),
            source="train_guest",
        )

    transaction.on_commit(enqueue_training)
    return locked_guest


def finalize_guest_training(guest: Guest, now: datetime | None = None) -> bool:
    """
    完成门客训练，提升等级并随机增加属性。

    Args:
        guest: 门客对象
        now: 当前时间（可选）

    Returns:
        是否成功完成训练
    """
    now = now or timezone.now()
    if not getattr(guest, "pk", None):
        return False

    with transaction.atomic():
        locked_guest = Guest.objects.select_for_update().select_related("manor", "template").get(pk=guest.pk)
        if not locked_guest.training_complete_at or locked_guest.training_complete_at > now:
            return False
        if locked_guest.status != GuestStatus.IDLE:
            return False

        target_level = max(locked_guest.level, locked_guest.training_target_level or locked_guest.level)
        levels_gained = max(0, target_level - locked_guest.level)

        apply_training_completion(locked_guest, levels_gained=levels_gained)
        locked_guest.training_complete_at = None
        locked_guest.training_target_level = 0

        locked_guest.save(
            update_fields=[
                "level",
                "force",  # 新增：属性会改变
                "intellect",  # 新增：属性会改变
                "defense_stat",  # 新增：属性会改变
                "agility",  # 新增：属性会改变
                "training_complete_at",
                "training_target_level",
                "attribute_points",
                "experience",
                "current_hp",
            ]
        )

        if locked_guest.level < MAX_GUEST_LEVEL:
            ensure_auto_training(locked_guest)

    return True
