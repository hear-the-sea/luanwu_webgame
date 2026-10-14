from __future__ import annotations

from itertools import count

import pytest

from core.exceptions import EquipmentError, GuestNotFoundError, GuestNotIdleError, ItemNotFoundError
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
@pytest.mark.parametrize("status", [GuestStatus.IDLE, GuestStatus.INJURED, GuestStatus.VACATION])
@pytest.mark.parametrize("is_bound", [False, True])
def test_dismiss_guest_allows_resting_guest_and_returns_equipment(django_user_model, status, is_bound):
    user = django_user_model.objects.create_user(username=_unique("roster_service_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, status=status)

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
    gear = GearItem.objects.create(manor=manor, template=gear_template, guest=guest, is_bound=is_bound)

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
    assert returned_item.bound_quantity == int(is_bound)
    gear.refresh_from_db()
    assert gear.guest_id is None
    assert gear.inventory_backed is True
    assert gear.is_bound == is_bound


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
@pytest.mark.parametrize("status", [GuestStatus.WORKING, GuestStatus.DEPLOYED, GuestStatus.ARENA])
def test_dismiss_guest_rejects_busy_guest(django_user_model, status):
    user = django_user_model.objects.create_user(username=_unique("roster_service_busy_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, status=status)

    with pytest.raises(GuestNotIdleError, match=guest.get_status_display()):
        dismiss_guest(guest)

    assert Guest.objects.filter(pk=guest.pk).exists()


@pytest.mark.django_db
def test_dismiss_vacation_guest_rolls_back_equipment_when_item_template_is_missing(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("vacation_dismiss_rollback"))
    manor = ensure_manor(user)
    guest = _create_guest(manor, status=GuestStatus.VACATION)
    gears = []
    for index in range(2):
        template = GearTemplate.objects.create(
            key=_unique("vacation_rollback_gear"),
            name=f"归闲装备{index}",
            slot=GearSlot.WEAPON,
        )
        gears.append(GearItem.objects.create(manor=manor, template=template, guest=guest, is_bound=True))
        if index == 0:
            ItemTemplate.objects.create(key=template.key, name=template.name)
    with pytest.raises(ItemNotFoundError):
        dismiss_guest(guest)
    guest.refresh_from_db()
    assert guest.status == GuestStatus.VACATION
    assert not InventoryItem.objects.filter(manor=manor, template__key=gears[0].template.key).exists()
    for gear in gears:
        gear.refresh_from_db()
        assert gear.guest_id == guest.pk


@pytest.mark.django_db
def test_dismiss_guest_rejects_inconsistent_equipment_ownership(django_user_model):
    owner = django_user_model.objects.create_user(username=_unique("dismiss_gear_owner"))
    other = django_user_model.objects.create_user(username=_unique("dismiss_gear_other"))
    guest = _create_guest(owner.manor, status=GuestStatus.VACATION)
    template = GearTemplate.objects.create(key=_unique("foreign_dismiss_gear"), name="异主装备", slot=GearSlot.WEAPON)
    ItemTemplate.objects.create(key=template.key, name=template.name)
    gear = GearItem.objects.create(manor=other.manor, template=template, guest=guest)
    with pytest.raises(EquipmentError, match="其他庄园"):
        dismiss_guest(guest)
    assert Guest.objects.filter(pk=guest.pk).exists()
    gear.refresh_from_db()
    assert gear.guest_id == guest.pk
    assert not InventoryItem.objects.filter(template__key=template.key).exists()


@pytest.mark.django_db
def test_dismiss_guest_rejects_missing_guest(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("roster_service_missing_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    guest.delete()

    with pytest.raises(GuestNotFoundError, match="门客不存在"):
        dismiss_guest(guest)
