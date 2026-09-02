from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from django.db import transaction

from core.exceptions import GuestNotFoundError, GuestNotIdleError
from gameplay.models import Manor

from ..models import Guest, GuestStatus
from . import equipment as equipment_service
from .world_unique import ensure_guest_not_world_unique


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
        if locked_guest.status not in {GuestStatus.IDLE, GuestStatus.INJURED}:
            raise GuestNotIdleError(locked_guest)
        ensure_guest_not_world_unique(locked_guest, action="辞退")

        guest_name = locked_guest.display_name
        gear_items = list(locked_guest.gear_items.select_related("template"))
        gear_summary = Counter(gear.template.name for gear in gear_items)
        for gear in gear_items:
            equipment_service.unequip_guest_item(gear, locked_guest, allow_injured=True)
        locked_guest.delete()

    return DismissGuestResult(guest_name=guest_name, gear_summary=gear_summary)
