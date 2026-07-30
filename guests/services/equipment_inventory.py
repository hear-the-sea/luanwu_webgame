"""
门客装备候选查询、背包同步与模板物化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db.models import Count, F, Min

from core.exceptions import EquipmentError, ItemNotFoundError
from gameplay.models import InventoryItem

from ..models import GearItem, GearTemplate
from ..utils.equipment_utils import EQUIP_SLOT_MAP
from .equipment_payloads import build_gear_template_defaults, build_gear_template_preview, require_int

if TYPE_CHECKING:
    from gameplay.models import Manor

    from ..models import Guest


@dataclass(frozen=True)
class ResolvedEquippableGear:
    gear: GearItem
    consumed_inventory: bool


@dataclass(frozen=True)
class ResolvedWarehouseGear:
    gear: GearItem
    inventory_item: InventoryItem


def list_free_gear_options(manor: Manor, *, slot: str) -> list[dict[str, Any]]:
    rows = (
        manor.gears.filter(guest__isnull=True, template__slot=slot)
        .values("template_id", "template__key")
        .annotate(count=Count("id"), gear_id=Min("id"))
        .order_by("template__name", "template_id")
    )
    template_ids = [row["template_id"] for row in rows]
    templates = {
        template.id: template
        for template in GearTemplate.objects.filter(id__in=template_ids).only(
            "id",
            "key",
            "name",
            "rarity",
            "set_key",
            "set_description",
            "set_bonus",
            "attack_bonus",
            "defense_bonus",
            "extra_stats",
        )
    }

    options: list[dict[str, Any]] = []
    for row in rows:
        template = templates.get(row["template_id"])
        if template is None:
            continue
        options.append(
            {
                "id": row["gear_id"],
                "template_key": row["template__key"],
                "count": row["count"],
                "template": template,
            }
        )
    return options


def list_available_inventory_gear_options(manor: Manor, *, slot: str) -> list[dict[str, Any]]:
    effect_types = [key for key, mapped_slot in EQUIP_SLOT_MAP.items() if mapped_slot == slot]
    if not effect_types:
        return []

    items = (
        InventoryItem.objects.filter(
            manor=manor,
            template__effect_type__in=effect_types,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,
        )
        .select_related("template")
        .order_by("template__name", "id")
    )

    options: list[dict[str, Any]] = []
    for item in items:
        preview = build_gear_template_preview(item.template)
        if preview is None:
            continue
        options.append(
            {
                "template_key": item.template.key,
                "count": item.quantity,
                "template": preview,
            }
        )
    return options


def list_available_equippable_gear_options(manor: Manor, *, slot: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for entry in list_free_gear_options(manor, slot=slot):
        merged[entry["template_key"]] = entry

    for entry in list_available_inventory_gear_options(manor, slot=slot):
        template_key = entry["template_key"]
        existing = merged.get(template_key)
        if existing is None:
            merged[template_key] = {
                "id": entry["template_key"],
                **entry,
            }
            continue
        existing_count = require_int(existing.get("count"), field_name="gear option count", minimum=0)
        entry_count = require_int(entry.get("count"), field_name="gear option count", minimum=0)
        existing["count"] = max(existing_count, entry_count)

    return sorted(merged.values(), key=lambda entry: (getattr(entry["template"], "name", ""), str(entry["id"])))


def get_or_create_free_gear_for_template_key(manor: Manor, *, template_key: str, slot: str | None = None) -> GearItem:
    inventory_item = (
        InventoryItem.objects.select_related("template")
        .filter(
            manor=manor,
            template__key=template_key,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,
        )
        .first()
    )
    if inventory_item is None:
        raise ItemNotFoundError("未找到可用装备")

    resolved_slot = EQUIP_SLOT_MAP.get(inventory_item.template.effect_type)
    if not resolved_slot:
        raise ItemNotFoundError("未找到可用装备")
    if slot and resolved_slot != slot:
        raise EquipmentError("装备槽位不匹配")

    gear_template, _ = GearTemplate.objects.update_or_create(
        key=inventory_item.template.key,
        defaults=build_gear_template_defaults(inventory_item.template, slot=resolved_slot),
    )
    free_gear = (
        manor.gears.select_related("template")
        .filter(template=gear_template, guest__isnull=True, inventory_backed=True)
        .order_by("id")
        .first()
    )
    if free_gear is not None:
        return free_gear
    return GearItem.objects.create(manor=manor, template=gear_template, inventory_backed=True)


def resolve_equippable_gear(manor: Manor, choice: str | GearItem, *, slot: str | None = None) -> GearItem:
    if isinstance(choice, GearItem):
        if slot and choice.template.slot != slot:
            raise EquipmentError("装备槽位不匹配")
        return choice
    if not isinstance(choice, str):
        raise AssertionError(f"invalid guest equipment choice: {choice!r}")

    raw_choice = choice.strip()
    if not raw_choice:
        raise EquipmentError("请选择可用装备")

    if raw_choice.isdigit():
        gear = manor.gears.select_related("template").filter(pk=int(raw_choice), guest__isnull=True).first()
        if gear is not None:
            if slot and gear.template.slot != slot:
                raise EquipmentError("装备槽位不匹配")
            return gear

    return get_or_create_free_gear_for_template_key(manor, template_key=raw_choice, slot=slot)


def resolve_equippable_gear_locked(
    manor: Manor,
    choice: str,
    *,
    slot: str | None = None,
) -> ResolvedEquippableGear:
    raw_choice = choice.strip()
    if not raw_choice:
        raise EquipmentError("请选择可用装备")

    if raw_choice.isdigit():
        gear = (
            manor.gears.select_for_update()
            .select_related("template")
            .filter(pk=int(raw_choice), guest__isnull=True)
            .first()
        )
        if gear is not None:
            if slot and gear.template.slot != slot:
                raise EquipmentError("装备槽位不匹配")
            return ResolvedEquippableGear(gear=gear, consumed_inventory=False)

    inventory_item = (
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(
            manor=manor,
            template__key=raw_choice,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,
        )
        .order_by("id")
        .first()
    )
    if inventory_item is None:
        raise ItemNotFoundError("未找到可用装备")

    resolved = _materialize_warehouse_gear_locked(
        manor,
        inventory_item,
        expected_slot=slot,
    )
    InventoryItem.objects.filter(pk=inventory_item.pk).update(quantity=F("quantity") - 1)
    InventoryItem.objects.filter(pk=inventory_item.pk, quantity__lte=0).delete()
    return ResolvedEquippableGear(gear=resolved.gear, consumed_inventory=True)


def _materialize_warehouse_gear_locked(
    manor: Manor,
    inventory_item: InventoryItem,
    *,
    expected_template_key: str | None = None,
    expected_slot: str | None = None,
) -> ResolvedWarehouseGear:
    if expected_template_key is not None and inventory_item.template.key != expected_template_key:
        raise EquipmentError("装备库存已发生变化，请刷新后重试")

    resolved_slot = EQUIP_SLOT_MAP.get(inventory_item.template.effect_type)
    if not resolved_slot:
        raise ItemNotFoundError("未找到可用装备")
    if expected_slot and resolved_slot != expected_slot:
        raise EquipmentError("装备槽位不匹配")

    gear_template, _ = GearTemplate.objects.update_or_create(
        key=inventory_item.template.key,
        defaults=build_gear_template_defaults(inventory_item.template, slot=resolved_slot),
    )
    gear = (
        manor.gears.select_for_update()
        .select_related("template")
        .filter(
            template=gear_template,
            guest__isnull=True,
            inventory_backed=True,
        )
        .order_by("id")
        .first()
    )
    if gear is None:
        gear = GearItem.objects.create(manor=manor, template=gear_template, inventory_backed=True)

    return ResolvedWarehouseGear(gear=gear, inventory_item=inventory_item)


def resolve_inventory_equippable_gear_locked(
    manor: Manor,
    inventory_item_id: int,
    *,
    expected_template_key: str | None = None,
    expected_slot: str | None = None,
) -> ResolvedWarehouseGear:
    """锁定精确仓库装备行，并物化可装备实例；库存消费由调用方提交。"""
    inventory_item = (
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(
            pk=inventory_item_id,
            manor=manor,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,
        )
        .first()
    )
    if inventory_item is None:
        raise ItemNotFoundError("未找到可用装备")
    return _materialize_warehouse_gear_locked(
        manor,
        inventory_item,
        expected_template_key=expected_template_key,
        expected_slot=expected_slot,
    )


def ensure_inventory_gears(manor: Manor, *, slot: str | None = None) -> None:
    """
    同步庄园背包中的装备道具到门客装备系统。
    确保背包数量与装备模板数量匹配。

    IMPORTANT: Only count items in WAREHOUSE since equip_guest consumes from WAREHOUSE.
    """
    effect_types = list(EQUIP_SLOT_MAP.keys())
    if slot:
        effect_types = [key for key, mapped_slot in EQUIP_SLOT_MAP.items() if mapped_slot == slot]
        if not effect_types:
            return
    items = InventoryItem.objects.filter(
        manor=manor, template__effect_type__in=effect_types, storage_location=InventoryItem.StorageLocation.WAREHOUSE
    ).select_related("template")
    if not items:
        target_slots = {EQUIP_SLOT_MAP[effect_type] for effect_type in effect_types if effect_type in EQUIP_SLOT_MAP}
        if target_slots:
            GearItem.objects.filter(
                manor=manor,
                guest__isnull=True,
                inventory_backed=True,
                template__slot__in=target_slots,
            ).delete()
        return
    target_slots = {EQUIP_SLOT_MAP[effect_type] for effect_type in effect_types if effect_type in EQUIP_SLOT_MAP}
    synced_slots: set[str] = set()
    for inventory_item in items:
        resolved_slot = EQUIP_SLOT_MAP.get(inventory_item.template.effect_type)
        if not resolved_slot:
            continue
        synced_slots.add(resolved_slot)
        gear_template, _ = GearTemplate.objects.update_or_create(
            key=inventory_item.template.key,
            defaults=build_gear_template_defaults(inventory_item.template, slot=resolved_slot),
        )
        free_qs = manor.gears.filter(template=gear_template, guest__isnull=True, inventory_backed=True)
        free_count = free_qs.count()
        target_free = require_int(inventory_item.quantity, field_name="inventory gear quantity", minimum=0)
        if free_count < target_free:
            missing = target_free - free_count
            GearItem.objects.bulk_create(
                [GearItem(manor=manor, template=gear_template, inventory_backed=True) for _ in range(missing)]
            )
        elif free_count > target_free:
            to_delete = free_qs[: free_count - target_free]
            GearItem.objects.filter(id__in=[gear.id for gear in to_delete]).delete()

    orphan_slots = target_slots - synced_slots
    if orphan_slots:
        GearItem.objects.filter(
            manor=manor,
            guest__isnull=True,
            inventory_backed=True,
            template__slot__in=orphan_slots,
        ).delete()


def consume_warehouse_item_for_gear(guest: Guest, gear: GearItem) -> bool:
    inventory_item = (
        InventoryItem.objects.select_for_update()
        .filter(
            manor=guest.manor,
            template__key=gear.template.key,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,
        )
        .order_by("id")
        .first()
    )

    if not inventory_item:
        return False

    InventoryItem.objects.filter(pk=inventory_item.pk).update(quantity=F("quantity") - 1)
    InventoryItem.objects.filter(pk=inventory_item.pk, quantity__lte=0).delete()
    return True
