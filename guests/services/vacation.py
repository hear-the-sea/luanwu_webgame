"""逍遥山庄门客归闲与召回。"""

from __future__ import annotations

from django.db import transaction

from core.exceptions import (
    GameError,
    GuestCapacityFullError,
    GuestNotFoundError,
    GuestNotIdleError,
    WorldUniqueGuestError,
)
from gameplay.models import Manor

from ..models import Guest, GuestStatus
from .status import persist_guest_status_transition
from .world_unique import is_world_unique_guest


def get_xiaoyao_action_context(manor: Manor, guest: Guest) -> dict[str, bool]:
    """为门客详情及异步更新提供一致的归闲按钮状态。"""
    is_unique = is_world_unique_guest(guest)
    has_room = manor.guests.filter(status=GuestStatus.VACATION).count() < manor.xiaoyao_capacity
    return {
        "can_retreat": guest.status == GuestStatus.IDLE and not is_unique and has_room,
        "is_world_unique_guest": is_unique,
    }


def move_guest_to_xiaoyao(manor: Manor, guest_id: int) -> str:
    """仅让空闲门客归闲，并在庄园锁下检查位置。"""
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        guest = (
            Guest.objects.select_for_update()
            .select_related("template")
            .filter(pk=guest_id, manor_id=locked_manor.pk)
            .first()
        )
        if guest is None:
            raise GuestNotFoundError()
        if guest.status != GuestStatus.IDLE:
            raise GuestNotIdleError(guest)
        if is_world_unique_guest(guest):
            raise WorldUniqueGuestError(message=f"{guest.display_name}为全服唯一门客，不能归闲")
        occupied = locked_manor.guests.filter(status=GuestStatus.VACATION).count()
        if occupied >= locked_manor.xiaoyao_capacity:
            raise GameError("逍遥山庄位置已满，请先升级建筑")
        persist_guest_status_transition(guest, GuestStatus.VACATION, source="xiaoyao_check_in")
        return guest.display_name


def return_guest_from_xiaoyao(manor: Manor, guest_id: int) -> str:
    """庄园有空位时召回归闲门客。"""
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        guest = (
            Guest.objects.select_for_update()
            .select_related("template")
            .filter(pk=guest_id, manor_id=locked_manor.pk)
            .first()
        )
        if guest is None:
            raise GuestNotFoundError()
        if guest.status != GuestStatus.VACATION:
            raise GameError("该门客当前不在逍遥山庄")
        if locked_manor.active_guest_count >= locked_manor.guest_capacity:
            raise GuestCapacityFullError()
        persist_guest_status_transition(guest, GuestStatus.IDLE, source="xiaoyao_check_out")
        return guest.display_name
