from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from django.db import transaction

from core.exceptions import EquipmentError, GuestNotFoundError, GuestNotIdleError, WorldUniqueGuestError
from gameplay.models import Manor

from ..models import Guest, GuestStatus
from . import equipment as equipment_service
from .world_unique import (
    WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
    ensure_guest_not_world_unique,
    is_world_unique_guest,
    release_world_unique_guest_after_dismissal,
)


@dataclass(frozen=True)
class DismissGuestResult:
    guest_name: str
    gear_summary: Counter[str]


def dismiss_guest(guest: Guest) -> DismissGuestResult:
    """按 Manor -> Guest 锁序辞退门客并返还装备。"""
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().filter(pk=guest.manor_id).first()
        if locked_manor is None:
            raise GuestNotFoundError()
        locked_guest = Guest.objects.select_for_update().filter(pk=guest.pk, manor_id=locked_manor.pk).first()
        if not locked_guest:
            raise GuestNotFoundError()
        if locked_guest.status not in {GuestStatus.IDLE, GuestStatus.INJURED, GuestStatus.VACATION}:
            raise GuestNotIdleError(locked_guest)

        guest_name = locked_guest.display_name
        gear_items = list(locked_guest.gear_items.select_related("template"))
        gear_summary = Counter(gear.template.name for gear in gear_items)
        if is_world_unique_guest(locked_guest):
            if locked_guest.template.key != WORLD_UNIQUE_LUBU_TEMPLATE_KEY:
                ensure_guest_not_world_unique(locked_guest, action="辞退")
            released = release_world_unique_guest_after_dismissal(
                locked_manor,
                guest_id=locked_guest.pk,
            )
            if released is None:
                raise WorldUniqueGuestError(message="唯一门客归属状态异常，无法辞退")
        else:
            if any(gear.manor_id != locked_manor.pk for gear in gear_items):
                raise EquipmentError("无法卸下其他庄园的装备")
            equipment_service.return_guest_gear_to_inventory_locked(locked_manor, locked_guest)
            locked_guest.delete()

    return DismissGuestResult(guest_name=guest_name, gear_summary=gear_summary)
