from __future__ import annotations

from itertools import count

import pytest
from django.db import transaction

from core.exceptions import DuplicateEquipmentError, ItemNotFoundError
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
from guests.services.equipment import (
    equip_guest,
    equip_guest_from_inventory_locked,
    equip_guest_from_virtual_template_locked,
)

_COUNTER = count(1)


def _unique(prefix: str) -> str:
    return f"{prefix}_{next(_COUNTER)}"


def _create_guest(manor: Manor, *, status: str = GuestStatus.IDLE) -> Guest:
    template = GuestTemplate.objects.create(
        key=_unique("locked_equipment_guest_tpl"),
        name="锁内装备门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        base_hp=1000,
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        force=100,
        intellect=90,
        defense_stat=80,
        agility=85,
        status=status,
    )


def _create_item_template(
    *,
    key: str,
    name: str,
    effect_type: str,
    payload: dict[str, object],
) -> ItemTemplate:
    return ItemTemplate.objects.create(
        key=key,
        name=name,
        effect_type=effect_type,
        effect_payload=payload,
        rarity=GuestRarity.GREEN,
    )


@pytest.mark.django_db(transaction=True)
def test_equip_guest_from_inventory_locked_requires_outer_transaction(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("locked_equipment_atomic"),
        password="pass123",
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    item_template = _create_item_template(
        key=_unique("locked_equipment_atomic_weapon"),
        name="事务装备刀",
        effect_type="equip_weapon",
        payload={"force": 5},
    )
    item = InventoryItem.objects.create(manor=manor, template=item_template, quantity=1)

    with pytest.raises(RuntimeError, match="inside transaction.atomic"):
        equip_guest_from_inventory_locked(manor, guest, item.pk)


@pytest.mark.django_db
def test_equip_guest_from_inventory_locked_replaces_to_warehouse_and_recalculates_set_bonus(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("locked_equipment_replace"),
        password="pass123",
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    set_bonus = {"pieces": 2, "bonus": {"force": 10}}

    old_item_template = _create_item_template(
        key=_unique("locked_equipment_old_weapon"),
        name="旧套装刀",
        effect_type="equip_weapon",
        payload={"force": 5},
    )
    old_template = GearTemplate.objects.create(
        key=old_item_template.key,
        name=old_item_template.name,
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        set_key="locked_equipment_set",
        set_bonus=set_bonus,
        extra_stats={"force": 5},
    )
    companion_template = GearTemplate.objects.create(
        key=_unique("locked_equipment_set_helmet"),
        name="旧套装盔",
        slot=GearSlot.HELMET,
        rarity=GuestRarity.GREEN,
        set_key="locked_equipment_set",
        set_bonus=set_bonus,
        extra_stats={"force": 2},
    )
    old_gear = GearItem.objects.create(manor=manor, template=old_template)
    companion = GearItem.objects.create(manor=manor, template=companion_template)
    equip_guest(companion, guest)
    equip_guest(old_gear, guest)

    new_item_template = _create_item_template(
        key=_unique("locked_equipment_new_weapon"),
        name="新散装刀",
        effect_type="equip_weapon",
        payload={"force": 8},
    )
    new_item = InventoryItem.objects.create(
        manor=manor,
        template=new_item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        locked_guest = Guest.objects.select_for_update().get(pk=guest.pk)
        equipped = equip_guest_from_inventory_locked(
            locked_manor,
            locked_guest,
            new_item.pk,
            expected_template_key=new_item_template.key,
            expected_slot=GearSlot.WEAPON,
        )

    guest.refresh_from_db()
    old_gear.refresh_from_db()
    companion.refresh_from_db()
    equipped.refresh_from_db()
    assert guest.force == 110
    assert guest.gear_set_bonus == {}
    assert old_gear.guest_id is None
    assert old_gear.inventory_backed is True
    assert companion.guest_id == guest.pk
    assert equipped.guest_id == guest.pk
    assert equipped.inventory_backed is False
    assert not InventoryItem.objects.filter(pk=new_item.pk).exists()
    returned = InventoryItem.objects.get(
        manor=manor,
        template=old_item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert returned.quantity == 1


@pytest.mark.django_db
def test_virtual_equipment_replacement_discards_projection_without_item_template(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("locked_equipment_virtual_replace"),
        password="pass123",
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    old_template = GearTemplate.objects.create(
        key=_unique("locked_equipment_virtual_old"),
        name="虚拟旧武器",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        extra_stats={"force": 5},
    )
    old_gear = GearItem.objects.create(
        manor=manor,
        template=old_template,
        inventory_backed=False,
    )
    equip_guest(old_gear, guest)
    new_template = GearTemplate.objects.create(
        key=_unique("locked_equipment_virtual_new"),
        name="虚拟新武器",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        extra_stats={"force": 8},
    )

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        locked_guest = Guest.objects.select_for_update().get(pk=guest.pk)
        equipped = equip_guest_from_virtual_template_locked(
            locked_manor,
            locked_guest,
            new_template.pk,
            expected_template_key=new_template.key,
            expected_slot=GearSlot.WEAPON,
        )

    equipped.refresh_from_db()
    assert not GearItem.objects.filter(pk=old_gear.pk).exists()
    assert equipped.guest_id == guest.pk
    assert equipped.inventory_backed is False
    assert not ItemTemplate.objects.filter(key=old_template.key).exists()


@pytest.mark.django_db
def test_inventory_backed_replacement_without_item_template_rolls_back(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("locked_equipment_missing_item_template"),
        password="pass123",
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    old_template = GearTemplate.objects.create(
        key=_unique("locked_equipment_missing_item"),
        name="缺失物品模板的真实装备",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        extra_stats={"force": 5},
    )
    old_gear = GearItem.objects.create(
        manor=manor,
        template=old_template,
        guest=guest,
        inventory_backed=True,
    )
    new_template = GearTemplate.objects.create(
        key=_unique("locked_equipment_missing_item_new"),
        name="替换装备",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        extra_stats={"force": 8},
    )
    new_item_template = _create_item_template(
        key=new_template.key,
        name=new_template.name,
        effect_type="equip_weapon",
        payload={"force": 8},
    )
    new_item = InventoryItem.objects.create(
        manor=manor,
        template=new_item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )
    guest_force_before = guest.force

    with pytest.raises(ItemNotFoundError, match="找不到对应的装备模板"):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            locked_guest = Guest.objects.select_for_update().get(pk=guest.pk)
            equip_guest_from_inventory_locked(
                locked_manor,
                locked_guest,
                new_item.pk,
                expected_template_key=new_template.key,
                expected_slot=GearSlot.WEAPON,
            )

    guest.refresh_from_db()
    old_gear.refresh_from_db()
    assert guest.force == guest_force_before
    assert old_gear.guest_id == guest.pk
    assert old_gear.inventory_backed is True
    new_item.refresh_from_db()
    assert new_item.quantity == 1
    assert not GearItem.objects.filter(manor=manor, template=new_template).exists()


@pytest.mark.django_db
def test_equip_guest_from_inventory_locked_rolls_back_consumption_when_equipment_validation_fails(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("locked_equipment_rollback"),
        password="pass123",
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    duplicate_name = "重复佩刀"
    existing_template = GearTemplate.objects.create(
        key=_unique("locked_equipment_existing_weapon"),
        name=duplicate_name,
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        extra_stats={"force": 3},
    )
    existing = GearItem.objects.create(manor=manor, template=existing_template)
    equip_guest(existing, guest)

    new_item_template = _create_item_template(
        key=_unique("locked_equipment_duplicate_weapon"),
        name=duplicate_name,
        effect_type="equip_weapon",
        payload={"force": 7},
    )
    new_item = InventoryItem.objects.create(
        manor=manor,
        template=new_item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )

    with pytest.raises(DuplicateEquipmentError):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            locked_guest = Guest.objects.select_for_update().get(pk=guest.pk)
            equip_guest_from_inventory_locked(
                locked_manor,
                locked_guest,
                new_item.pk,
                expected_template_key=new_item_template.key,
                expected_slot=GearSlot.WEAPON,
            )

    new_item.refresh_from_db(fields=["quantity"])
    guest.refresh_from_db()
    existing.refresh_from_db()
    assert new_item.quantity == 1
    assert guest.force == 103
    assert existing.guest_id == guest.pk
    assert not GearTemplate.objects.filter(key=new_item_template.key).exists()
    assert not GearItem.objects.filter(manor=manor, template__key=new_item_template.key).exists()


@pytest.mark.django_db
def test_equip_guest_from_inventory_locked_rejects_non_warehouse_item_without_writes(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("locked_equipment_treasury"),
        password="pass123",
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    item_template = _create_item_template(
        key=_unique("locked_equipment_treasury_weapon"),
        name="藏宝阁佩刀",
        effect_type="equip_weapon",
        payload={"force": 5},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.TREASURY,
        quantity=1,
    )

    with pytest.raises(ItemNotFoundError):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            locked_guest = Guest.objects.select_for_update().get(pk=guest.pk)
            equip_guest_from_inventory_locked(locked_manor, locked_guest, item.pk)

    item.refresh_from_db(fields=["quantity"])
    guest.refresh_from_db()
    assert item.quantity == 1
    assert guest.force == 100
    assert not guest.gear_items.exists()


@pytest.mark.django_db
def test_equip_guest_preserves_numeric_string_free_gear_path(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("locked_equipment_free_gear"),
        password="pass123",
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    template = GearTemplate.objects.create(
        key=_unique("locked_equipment_free_weapon"),
        name="自由佩刀",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        extra_stats={"force": 6},
    )
    gear = GearItem.objects.create(manor=manor, template=template)

    equipped = equip_guest(str(gear.pk), guest, slot=GearSlot.WEAPON)

    guest.refresh_from_db()
    equipped.refresh_from_db()
    assert equipped.pk == gear.pk
    assert equipped.guest_id == guest.pk
    assert guest.force == 106
    assert not InventoryItem.objects.filter(manor=manor).exists()
