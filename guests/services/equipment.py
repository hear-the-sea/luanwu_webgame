"""
门客装备管理服务。
"""

from __future__ import annotations

import logging

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
    GuestOwnershipError,
    ItemNotFoundError,
)
from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.inventory import core as inventory_core
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

from ..models import GearItem, GearSlot, GearTemplate, Guest, GuestStatus
from .equipment_inventory import (
    consume_warehouse_item_for_gear,
    ensure_inventory_gears,
    list_available_equippable_gear_options,
    resolve_equippable_gear,
    resolve_inventory_equippable_gear_locked,
)
from .equipment_payloads import GEAR_EXTRA_STAT_FIELDS, build_gear_template_preview, normalize_extra_stats
from .equipment_stats import apply_set_bonuses, apply_template_stats_to_guest, slot_capacity

logger = logging.getLogger(__name__)

__all__ = [
    "apply_set_bonuses",
    "build_gear_template_preview",
    "ensure_inventory_gears",
    "equip_guest",
    "equip_guest_from_inventory_locked",
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
        item.inventory_backed = True
        item.save(update_fields=["inventory_backed"])


def _require_atomic_block(name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{name} must be called inside transaction.atomic()")


def _apply_gear_to_locked_guest(
    manor: Manor,
    guest: Guest,
    gear: GearItem,
    *,
    inventory_item: InventoryItem | None = None,
) -> GearItem:
    _require_atomic_block("_apply_gear_to_locked_guest")
    if not manor.pk or not guest.pk or guest.manor_id != manor.pk:
        raise GuestOwnershipError(message="门客不存在或不属于该庄园")
    if guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)

    resolved_slot = gear.template.slot
    capacity = slot_capacity(resolved_slot)
    if gear.manor_id != manor.pk:
        raise EquipmentError("无法装备其他庄园的装备")
    if gear.guest_id and gear.guest_id != guest.id:
        raise EquipmentAlreadyEquippedError()
    if gear.guest_id == guest.id:
        return gear

    existing_items = list(
        guest.gear_items.select_for_update().select_related("template").filter(template__slot=resolved_slot)
    )
    updates = {"attack_bonus", "defense_bonus"}
    for item in existing_items:
        if item.template.name == gear.template.name:
            raise DuplicateEquipmentError()
    if capacity > 1 and len(existing_items) >= capacity:
        raise EquipmentSlotFullError(resolved_slot)

    if inventory_item is not None:
        if (
            inventory_item.manor_id != manor.pk
            or inventory_item.storage_location != InventoryItem.StorageLocation.WAREHOUSE
            or inventory_item.template.key != gear.template.key
        ):
            raise EquipmentError("装备库存已发生变化，请刷新后重试")
        inventory_core.consume_inventory_item_locked(inventory_item, 1)
    elif gear.inventory_backed:
        if not consume_warehouse_item_for_gear(guest, gear):
            raise EquipmentError("装备库存已发生变化，请刷新后重试")

    if capacity == 1 and existing_items:
        _clear_replaced_items(guest, existing_items, updates)

    gear.guest = guest
    gear.inventory_backed = False
    gear.save(update_fields=["guest", "inventory_backed"])

    apply_template_stats_to_guest(guest, gear.template, +1, updates)
    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)

    if guest.current_hp > guest.max_hp:
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])

    _schedule_gear_options_cache_clear(guest.manor_id, slots={resolved_slot})
    return gear


def equip_guest_from_inventory_locked(
    manor: Manor,
    locked_guest: Guest,
    inventory_item_id: int,
    *,
    expected_template_key: str | None = None,
    expected_slot: str | None = None,
) -> GearItem:
    """在已锁 Manor -> Guest 的事务内，消费一件现有仓库装备并穿戴。"""
    _require_atomic_block("equip_guest_from_inventory_locked")
    if not manor.pk or not locked_guest.pk or locked_guest.manor_id != manor.pk:
        raise GuestOwnershipError(message="门客不存在或不属于该庄园")
    resolved = resolve_inventory_equippable_gear_locked(
        manor,
        inventory_item_id,
        expected_template_key=expected_template_key,
        expected_slot=expected_slot,
    )
    return _apply_gear_to_locked_guest(
        manor,
        locked_guest,
        resolved.gear,
        inventory_item=resolved.inventory_item,
    )


def _warehouse_item_id_for_template_key(manor: Manor, template_key: str) -> int | None:
    return (
        InventoryItem.objects.filter(
            manor=manor,
            template__key=template_key,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,
        )
        .order_by("id")
        .values_list("id", flat=True)
        .first()
    )


@transaction.atomic
def equip_guest(gear: str | GearItem, guest: Guest, *, slot: str | None = None) -> GearItem:
    """为门客装备道具。"""
    locked_manor = Manor.objects.select_for_update().get(pk=guest.manor_id)
    locked_guest = Guest.objects.select_for_update().select_related("template").get(pk=guest.pk)
    if locked_guest.manor_id != locked_manor.pk:
        raise GuestOwnershipError(message="门客不存在或不属于该庄园")

    if isinstance(gear, GearItem):
        locked_gear = GearItem.objects.select_for_update().select_related("template").get(pk=gear.pk)
        return _apply_gear_to_locked_guest(locked_manor, locked_guest, locked_gear)

    if not isinstance(gear, str):
        raise AssertionError(f"invalid guest equipment choice: {gear!r}")
    raw_choice = gear.strip()
    if not raw_choice:
        raise EquipmentError("请选择可用装备")

    if raw_choice.isdigit():
        locked_numeric_gear = (
            locked_manor.gears.select_for_update()
            .select_related("template")
            .filter(pk=int(raw_choice), guest__isnull=True)
            .first()
        )
        if locked_numeric_gear is not None:
            if slot and locked_numeric_gear.template.slot != slot:
                raise EquipmentError("装备槽位不匹配")
            return _apply_gear_to_locked_guest(locked_manor, locked_guest, locked_numeric_gear)

    inventory_item_id = _warehouse_item_id_for_template_key(locked_manor, raw_choice)
    if inventory_item_id is None:
        raise ItemNotFoundError("未找到可用装备")
    return equip_guest_from_inventory_locked(
        locked_manor,
        locked_guest,
        inventory_item_id,
        expected_template_key=raw_choice,
        expected_slot=slot,
    )


@transaction.atomic
def unequip_guest_item(gear: GearItem, guest: Guest, *, allow_injured: bool = False) -> GearItem:
    """按 Manor -> Guest -> Gear 锁序卸装并返还库存。"""
    locked_manor = Manor.objects.select_for_update().get(pk=guest.manor_id)
    guest = Guest.objects.select_for_update().get(
        pk=guest.pk,
        manor_id=locked_manor.pk,
    )
    gear = GearItem.objects.select_for_update().get(pk=gear.pk)
    allowed_statuses = {GuestStatus.IDLE}
    if allow_injured:
        allowed_statuses.add(GuestStatus.INJURED)
    if guest.status not in allowed_statuses:
        raise GuestNotIdleError(guest)

    if gear.manor_id != locked_manor.pk:
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
    gear.inventory_backed = True
    gear.save(update_fields=["guest", "inventory_backed"])
    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)

    if guest.current_hp > guest.max_hp:
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])

    updated = InventoryItem.objects.filter(
        manor=locked_manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).update(quantity=F("quantity") + 1)

    if updated == 0:
        InventoryItem.objects.create(
            manor=locked_manor,
            template=item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity=1,
        )

    _schedule_gear_options_cache_clear(guest.manor_id, slots={gear.template.slot})
    return gear
