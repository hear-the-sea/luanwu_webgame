from __future__ import annotations

import threading
import time
import uuid

import pytest
from django.db import connection

from core.exceptions import EquipmentNotEquippedError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.services import equipment as equipment_service

pytestmark = [pytest.mark.integration]


@pytest.mark.django_db(transaction=True)
def test_equip_and_unequip_same_guest_slot_complete_without_deadlock(django_user_model, monkeypatch):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    user = django_user_model.objects.create_user(
        username=f"guest_equipment_concurrent_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    manor = ensure_manor(user)
    guest_template = GuestTemplate.objects.create(
        key=f"guest_equipment_concurrent_tpl_{uuid.uuid4().hex[:8]}",
        name="并发装备门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        base_hp=1000,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=guest_template,
        level=10,
        force=120,
        intellect=90,
        defense_stat=80,
        current_hp=5000,
    )
    old_item_template = ItemTemplate.objects.create(
        key=f"guest_equipment_old_weapon_{uuid.uuid4().hex[:8]}",
        name="旧佩刀",
        effect_type="equip_weapon",
        effect_payload={"force": 8},
        rarity=GuestRarity.GREEN,
    )
    new_item_template = ItemTemplate.objects.create(
        key=f"guest_equipment_new_weapon_{uuid.uuid4().hex[:8]}",
        name="新佩刀",
        effect_type="equip_weapon",
        effect_payload={"force": 12},
        rarity=GuestRarity.BLUE,
    )
    old_gear_template = GearTemplate.objects.create(
        key=old_item_template.key,
        name=old_item_template.name,
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        extra_stats={"force": 8},
    )
    new_gear_template = GearTemplate.objects.create(
        key=new_item_template.key,
        name=new_item_template.name,
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.BLUE,
        extra_stats={"force": 12},
    )

    InventoryItem.objects.create(
        manor=manor,
        template=old_item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )
    old_gear = GearItem.objects.create(manor=manor, template=old_gear_template)
    equipment_service.equip_guest(old_gear, guest)

    InventoryItem.objects.create(
        manor=manor,
        template=new_item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )
    new_gear = GearItem.objects.create(manor=manor, template=new_gear_template)

    equip_pause_reached = threading.Event()
    allow_equip_to_continue = threading.Event()
    unequip_started = threading.Event()
    pause_guard = threading.Lock()
    pause_used = {"value": False}
    original_slot_capacity = equipment_service.slot_capacity
    successes: list[str] = []
    errors: list[Exception] = []

    def _slot_capacity_with_pause(slot: str) -> int:
        with pause_guard:
            should_pause = slot == GearSlot.WEAPON and pause_used["value"] is False
            if should_pause:
                pause_used["value"] = True
                equip_pause_reached.set()
        if should_pause:
            allow_equip_to_continue.wait(timeout=5)
        return original_slot_capacity(slot)

    monkeypatch.setattr(equipment_service, "slot_capacity", _slot_capacity_with_pause)

    def _equip_worker() -> None:
        try:
            local_guest = Guest.objects.get(pk=guest.pk)
            local_new_gear = GearItem.objects.get(pk=new_gear.pk)
            equipment_service.equip_guest(local_new_gear, local_guest)
            successes.append("equip")
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    def _unequip_worker() -> None:
        try:
            local_guest = Guest.objects.get(pk=guest.pk)
            local_old_gear = GearItem.objects.get(pk=old_gear.pk)
            unequip_started.set()
            equipment_service.unequip_guest_item(local_old_gear, local_guest)
            successes.append("unequip")
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    equip_thread = threading.Thread(target=_equip_worker, daemon=True)
    unequip_thread = threading.Thread(target=_unequip_worker, daemon=True)

    equip_thread.start()
    assert equip_pause_reached.wait(timeout=5)

    unequip_thread.start()
    assert unequip_started.wait(timeout=5)
    time.sleep(0.2)
    allow_equip_to_continue.set()

    equip_thread.join(timeout=5)
    unequip_thread.join(timeout=5)

    assert equip_thread.is_alive() is False
    assert unequip_thread.is_alive() is False
    assert successes == ["equip"]
    assert len(errors) == 1
    assert isinstance(errors[0], EquipmentNotEquippedError)

    guest.refresh_from_db()
    old_gear.refresh_from_db()
    new_gear.refresh_from_db()
    assert new_gear.guest_id == guest.id
    assert old_gear.guest_id is None
    assert guest.force == 132
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=old_item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 1
    )
    assert (
        InventoryItem.objects.filter(
            manor=manor,
            template=new_item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).exists()
        is False
    )
