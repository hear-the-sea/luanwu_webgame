from __future__ import annotations

from itertools import count

import pytest

from core.exceptions import GuestNotFoundError, GuestNotIdleError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.manor.core import ensure_manor
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestArchetype,
    GuestRarity,
    GuestStatus,
    GuestTemplate,
)
from guests.services.roster import dismiss_guest

_COUNTER = count(1)


def _unique(prefix: str) -> str:
    return f"{prefix}_{next(_COUNTER)}"


def _create_guest(manor, *, status: str = GuestStatus.IDLE) -> Guest:
    template = GuestTemplate.objects.create(
        key=_unique("roster_service_guest_tpl"),
        name="名册服务门客",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    return Guest.objects.create(manor=manor, template=template, status=status)


@pytest.mark.django_db
def test_dismiss_guest_allows_injured_guest_and_returns_equipment(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("roster_service_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, status=GuestStatus.INJURED)

    gear_template = GearTemplate.objects.create(
        key=_unique("roster_service_gear_tpl"),
        name="名册服务装备",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GRAY,
    )
    item_template = ItemTemplate.objects.create(
        key=gear_template.key,
        name="名册服务装备道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        effect_payload={},
        is_usable=True,
    )
    GearItem.objects.create(manor=manor, template=gear_template, guest=guest)

    result = dismiss_guest(guest)

    assert result.guest_name == guest.display_name
    assert result.gear_summary == {"名册服务装备": 1}
    assert not Guest.objects.filter(pk=guest.pk).exists()
    returned_item = InventoryItem.objects.get(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert returned_item.quantity == 1


@pytest.mark.django_db
def test_dismiss_guest_locks_manor_before_guest(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(
        username=_unique("roster_service_lock_order_user"),
        password="pass123",
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    lock_order: list[str] = []
    manor_manager = Manor.objects
    guest_manager = Guest.objects
    original_manor_lock = manor_manager.select_for_update
    original_guest_lock = guest_manager.select_for_update

    def _lock_manor(*args, **kwargs):
        lock_order.append("manor")
        return original_manor_lock(*args, **kwargs)

    def _lock_guest(*args, **kwargs):
        lock_order.append("guest")
        return original_guest_lock(*args, **kwargs)

    monkeypatch.setattr(manor_manager, "select_for_update", _lock_manor)
    monkeypatch.setattr(guest_manager, "select_for_update", _lock_guest)

    dismiss_guest(guest)

    assert lock_order[:2] == ["manor", "guest"]


@pytest.mark.django_db
def test_dismiss_guest_rejects_busy_guest(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("roster_service_busy_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, status=GuestStatus.WORKING)

    with pytest.raises(GuestNotIdleError, match="打工中"):
        dismiss_guest(guest)

    assert Guest.objects.filter(pk=guest.pk).exists()


@pytest.mark.django_db
def test_dismiss_guest_rejects_missing_guest(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("roster_service_missing_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    guest.delete()

    with pytest.raises(GuestNotFoundError, match="门客不存在"):
        dismiss_guest(guest)
