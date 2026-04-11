"""
门客装备管理服务。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.db import transaction
from django.db.models import F

from core.exceptions import (
    DuplicateEquipmentError,
    EquipmentAlreadyEquippedError,
    EquipmentError,
    EquipmentNotEquippedError,
    EquipmentSlotFullError,
    GuestNotIdleError,
    ItemNotFoundError,
)
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

from ..models import GearItem, GearSlot, GearTemplate, Guest, GuestStatus
from .equipment_inventory import (
    consume_warehouse_item_for_gear,
    ensure_inventory_gears,
    list_available_equippable_gear_options,
    resolve_equippable_gear,
)
from .equipment_payloads import GEAR_EXTRA_STAT_FIELDS, build_gear_template_preview, normalize_extra_stats
from .equipment_stats import apply_set_bonuses, apply_template_stats_to_guest, slot_capacity

if TYPE_CHECKING:
    from gameplay.models import Manor

logger = logging.getLogger(__name__)

__all__ = [
    "apply_set_bonuses",
    "build_gear_template_preview",
    "ensure_inventory_gears",
    "equip_guest",
    "gear_options_cache_key",
    "give_gear",
    "list_available_equippable_gear_options",
    "resolve_equippable_gear",
    "unequip_guest_item",
]


def gear_options_cache_key(manor_id: int, slot: str) -> str:
    return f"gear_options:{manor_id}:{slot}"


def _safe_cache_delete_many(keys: list[str]) -> None:
    try:
        cache.delete_many(keys)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Gear options cache.delete_many failed: keys_count=%s error=%s", len(keys), exc, exc_info=True)


def _clear_gear_options_cache(manor_id: int, *, slots: set[str] | None = None) -> None:
    slot_values = slots or {choice.value for choice in GearSlot}
    keys = [gear_options_cache_key(manor_id, value) for value in slot_values]
    _safe_cache_delete_many(keys)


def _best_effort_clear_gear_options_cache(manor_id: int, *, slots: set[str] | None = None) -> None:
    try:
        _clear_gear_options_cache(manor_id, slots=slots)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "Gear options cache invalidation skipped: manor_id=%s slots=%s error=%s",
            manor_id,
            sorted(slots) if slots else None,
            exc,
            exc_info=True,
        )


def _schedule_gear_options_cache_clear(manor_id: int, *, slots: set[str] | None = None) -> None:
    scheduled_slots = set(slots) if slots else None
    transaction.on_commit(lambda: _best_effort_clear_gear_options_cache(manor_id, slots=scheduled_slots))


def give_gear(manor: Manor, template: GearTemplate) -> GearItem:
    """创建一个装备道具。"""
    return GearItem.objects.create(manor=manor, template=template)


def _clear_replaced_items(guest: Guest, existing_items: list[GearItem], updates: set[str]) -> None:
    for item in existing_items:
        guest.attack_bonus -= item.template.attack_bonus
        guest.defense_bonus -= item.template.defense_bonus
        updates.update({"attack_bonus", "defense_bonus"})
        extra_stats = normalize_extra_stats(item.template.extra_stats)
        for key, field in GEAR_EXTRA_STAT_FIELDS.items():
            value = extra_stats.get(key)
            if value:
                setattr(guest, field, getattr(guest, field) - value)
                updates.add(field)
        item.guest = None
        item.save(update_fields=["guest"])

        item_template = ItemTemplate.objects.filter(key=item.template.key).first()
        if not item_template:
            logger.error("Cannot return gear to inventory: ItemTemplate not found for key %s", item.template.key)
            continue

        updated = InventoryItem.objects.filter(
            manor=guest.manor, template=item_template, storage_location=InventoryItem.StorageLocation.WAREHOUSE
        ).update(quantity=F("quantity") + 1)

        if updated == 0:
            InventoryItem.objects.create(
                manor=guest.manor,
                template=item_template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                quantity=1,
            )


@transaction.atomic
def equip_guest(gear: GearItem, guest: Guest) -> GearItem:
    """为门客装备道具。"""
    gear = GearItem.objects.select_for_update().get(pk=gear.pk)
    guest = Guest.objects.select_for_update().get(pk=guest.pk)
    if guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)

    slot = gear.template.slot
    capacity = slot_capacity(slot)
    if gear.manor_id != guest.manor_id:
        raise EquipmentError("无法装备其他庄园的装备")
    if gear.guest_id and gear.guest_id != guest.id:
        raise EquipmentAlreadyEquippedError()
    if gear.guest_id == guest.id:
        return gear

    existing_items = list(guest.gear_items.select_for_update().filter(template__slot=slot))
    updates = {"attack_bonus", "defense_bonus"}
    for item in existing_items:
        if item.template.name == gear.template.name:
            raise DuplicateEquipmentError()
    if capacity == 1 and existing_items:
        _clear_replaced_items(guest, existing_items, updates)
    elif capacity > 1 and len(existing_items) >= capacity:
        raise EquipmentSlotFullError(slot)

    gear.guest = guest
    gear.save(update_fields=["guest"])

    consume_warehouse_item_for_gear(guest, gear)

    apply_template_stats_to_guest(guest, gear.template, +1, updates)
    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)

    if guest.current_hp > guest.max_hp:
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])

    _schedule_gear_options_cache_clear(guest.manor_id, slots={slot})
    return gear


@transaction.atomic
def unequip_guest_item(gear: GearItem, guest: Guest, *, allow_injured: bool = False) -> GearItem:
    """卸下门客的装备道具。"""
    gear = GearItem.objects.select_for_update().get(pk=gear.pk)
    guest = Guest.objects.select_for_update().get(pk=guest.pk)
    allowed_statuses = {GuestStatus.IDLE}
    if allow_injured:
        allowed_statuses.add(GuestStatus.INJURED)
    if guest.status not in allowed_statuses:
        raise GuestNotIdleError(guest)

    if gear.manor != guest.manor:
        raise EquipmentError("无法卸下其他庄园的装备")
    if gear.guest_id != guest.id:
        raise EquipmentNotEquippedError()

    item_template = ItemTemplate.objects.filter(key=gear.template.key).first()
    if not item_template:
        raise ItemNotFoundError("找不到对应的装备模板，无法入库")

    extra_stats = normalize_extra_stats(gear.template.extra_stats)
    updates = {"attack_bonus", "defense_bonus"}
    guest.attack_bonus -= gear.template.attack_bonus
    guest.defense_bonus -= gear.template.defense_bonus
    for key, field in GEAR_EXTRA_STAT_FIELDS.items():
        value = extra_stats.get(key)
        if value:
            setattr(guest, field, getattr(guest, field) - value)
            updates.add(field)
    gear.guest = None
    gear.save(update_fields=["guest"])
    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)

    if guest.current_hp > guest.max_hp:
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])

    updated = InventoryItem.objects.filter(
        manor=guest.manor, template=item_template, storage_location=InventoryItem.StorageLocation.WAREHOUSE
    ).update(quantity=F("quantity") + 1)

    if updated == 0:
        InventoryItem.objects.create(
            manor=guest.manor,
            template=item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity=1,
        )

    _schedule_gear_options_cache_clear(guest.manor_id, slots={gear.template.slot})
    return gear
