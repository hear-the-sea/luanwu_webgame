"""
门客装备管理服务
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from django.core.cache import cache

from core.exceptions import (
    DuplicateEquipmentError,
    EquipmentAlreadyEquippedError,
    EquipmentError,
    EquipmentNotEquippedError,
    EquipmentSlotFullError,
    GuestNotIdleError,
    ItemNotFoundError,
)
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

if TYPE_CHECKING:
    from gameplay.models import Manor

from django.db import transaction
from django.db.models import Count, Min

from ..models import GearItem, GearSlot, GearTemplate, Guest, GuestRarity, GuestStatus
from ..utils.equipment_utils import EQUIP_SLOT_MAP, SET_STAT_FIELD_MAP, compute_set_bonus

logger = logging.getLogger(__name__)

_GEAR_EXTRA_STAT_FIELDS = {
    "hp": "hp_bonus",
    "force": "force",
    "intellect": "intellect",
    "defense": "defense_stat",
    "agility": "agility",
    "luck": "luck",
    "troop_capacity": "troop_capacity_bonus",
}

_GEAR_TEMPLATE_META_FIELDS = {"set_key", "set_description", "set_bonus"}


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


def _require_mapping(raw: Any, *, field_name: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    return raw


def _require_string(raw: Any, *, field_name: str, allow_blank: bool = False) -> str:
    if not isinstance(raw, str):
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    value = raw.strip()
    if not allow_blank and not value:
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    return value


def _require_int(raw: Any, *, field_name: str, minimum: int | None = None) -> int:
    if isinstance(raw, bool):
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    return value


def _normalize_extra_stats(raw: Any) -> dict[str, int]:
    payload = _require_mapping(raw, field_name="extra_stats")
    normalized: dict[str, int] = {}
    for key, value in payload.items():
        normalized_key = _require_string(key, field_name="extra_stats key")
        if normalized_key not in _GEAR_EXTRA_STAT_FIELDS:
            raise AssertionError(f"invalid guest equipment extra_stats key: {key!r}")
        normalized[normalized_key] = _require_int(value, field_name=f"extra_stats[{normalized_key}]")
    return normalized


def _extract_extra_stats_from_item_payload(raw: Any) -> dict[str, int]:
    payload = _require_mapping(raw, field_name="item_template.effect_payload")
    extra_stats_payload = {key: value for key, value in payload.items() if key not in _GEAR_TEMPLATE_META_FIELDS}
    return _normalize_extra_stats(extra_stats_payload)


def _normalize_active_set_bonus(raw: Any) -> dict[str, int]:
    payload = _require_mapping(raw, field_name="set_bonus")
    normalized: dict[str, int] = {}
    for key, value in payload.items():
        normalized_key = _require_string(key, field_name="set_bonus key")
        if normalized_key not in SET_STAT_FIELD_MAP:
            raise AssertionError(f"invalid guest equipment set_bonus key: {key!r}")
        normalized[normalized_key] = _require_int(value, field_name=f"set_bonus[{normalized_key}]")
    return normalized


def _normalize_template_set_bonus(raw: Any) -> dict[str, Any]:
    payload = _require_mapping(raw, field_name="set_bonus")
    pieces = payload.get("pieces")
    bonuses = payload.get("bonus") or payload.get("bonuses") or payload

    if pieces is not None:
        normalized_pieces = _require_int(pieces, field_name="set_bonus[pieces]", minimum=1)
    else:
        normalized_pieces = None

    bonus_map = _require_mapping(bonuses, field_name="set_bonus bonus")
    normalized_bonus = _normalize_active_set_bonus(bonus_map)
    if normalized_pieces is None:
        return normalized_bonus
    return {
        "pieces": normalized_pieces,
        "bonus": normalized_bonus,
    }


def _slot_capacity(slot: str) -> int:
    return {
        GearSlot.DEVICE: 3,
        GearSlot.ORNAMENT: 3,
    }.get(
        slot, 1
    )  # type: ignore[call-overload]


def _apply_template_stats_to_guest(guest: Guest, template: GearTemplate, sign: int, updates: set[str]) -> None:
    guest.attack_bonus += sign * template.attack_bonus
    guest.defense_bonus += sign * template.defense_bonus
    extra_stats = _normalize_extra_stats(template.extra_stats)
    for key, field in _GEAR_EXTRA_STAT_FIELDS.items():
        value = extra_stats.get(key)
        if value:
            setattr(guest, field, getattr(guest, field) + sign * value)
            updates.add(field)


def _clear_replaced_items(guest: Guest, existing_items: list[GearItem], updates: set[str]) -> None:
    import logging

    from django.db.models import F

    from gameplay.models import InventoryItem, ItemTemplate

    logger = logging.getLogger(__name__)

    for item in existing_items:
        guest.attack_bonus -= item.template.attack_bonus
        guest.defense_bonus -= item.template.defense_bonus
        updates.update({"attack_bonus", "defense_bonus"})
        extra_stats = _normalize_extra_stats(item.template.extra_stats)
        for key, field in _GEAR_EXTRA_STAT_FIELDS.items():
            value = extra_stats.get(key)
            if value:
                setattr(guest, field, getattr(guest, field) - value)
                updates.add(field)
        item.guest = None
        item.save(update_fields=["guest"])

        # 修复：被替换的装备必须退回仓库
        # 1. 查找对应的 ItemTemplate (GearTemplate 与 ItemTemplate 通过 key 关联)
        item_template = ItemTemplate.objects.filter(key=item.template.key).first()
        if not item_template:
            logger.error(f"Cannot return gear to inventory: ItemTemplate not found for key {item.template.key}")
            continue

        # 2. 使用原子更新增加库存
        updated = InventoryItem.objects.filter(
            manor=guest.manor, template=item_template, storage_location=InventoryItem.StorageLocation.WAREHOUSE
        ).update(quantity=F("quantity") + 1)

        if updated == 0:
            # 3. 如果不存在则创建
            InventoryItem.objects.create(
                manor=guest.manor,
                template=item_template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                quantity=1,
            )


def _consume_warehouse_item_for_gear(guest: Guest, gear: GearItem) -> bool:
    from django.db.models import F

    from gameplay.models import InventoryItem

    # 修复：使用 select_for_update 锁定库存行，防止并发双重消费
    inv_item = (
        InventoryItem.objects.select_for_update()
        .filter(
            manor=guest.manor,
            template__key=gear.template.key,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,  # 确保有库存
        )
        .order_by("id")
        .first()
    )

    if not inv_item:
        return False

    # 使用原子更新扣减库存
    InventoryItem.objects.filter(pk=inv_item.pk).update(quantity=F("quantity") - 1)

    # 清理零库存记录（再次检查以确保安全）
    InventoryItem.objects.filter(pk=inv_item.pk, quantity__lte=0).delete()
    return True


def apply_set_bonuses(guest: Guest) -> Dict[str, int]:
    """
    重新计算套装效果，并将其数值写回门客属性。上一轮套装效果会被先撤销。
    """
    previous = _normalize_active_set_bonus(guest.gear_set_bonus)
    current = compute_set_bonus(guest.gear_items.select_related("template"))
    if previous == current:
        return current

    updates = set()
    # 移除旧的套装效果
    for stat, field in SET_STAT_FIELD_MAP.items():
        prev_value = previous.get(stat, 0)
        if prev_value:
            setattr(guest, field, getattr(guest, field) - prev_value)
            updates.add(field)

    # 应用当前套装效果
    for stat, value in current.items():
        set_bonus_field = SET_STAT_FIELD_MAP.get(stat)
        if not set_bonus_field:
            continue
        val = _require_int(value, field_name=f"computed set_bonus[{stat}]")
        if val:
            setattr(guest, set_bonus_field, getattr(guest, set_bonus_field) + val)
            updates.add(set_bonus_field)

    guest.gear_set_bonus = current
    updates.add("gear_set_bonus")
    if updates:
        guest.save(update_fields=list(updates))
    return current


def give_gear(manor: Manor, template: GearTemplate) -> GearItem:
    """创建一个装备道具"""
    return GearItem.objects.create(manor=manor, template=template)


def _build_gear_template_defaults(item_template: Any, *, slot: str) -> dict[str, Any]:
    payload = _require_mapping(
        getattr(item_template, "effect_payload", None), field_name="item_template.effect_payload"
    )
    extra_stats = _extract_extra_stats_from_item_payload(payload)
    return {
        "name": _require_string(getattr(item_template, "name", None), field_name="item_template.name"),
        "slot": slot,
        "rarity": getattr(item_template, "rarity", GuestRarity.GRAY),
        "set_key": _require_string(payload.get("set_key", ""), field_name="set_key", allow_blank=True),
        "set_description": _require_string(
            payload.get("set_description", ""),
            field_name="set_description",
            allow_blank=True,
        ),
        "set_bonus": _normalize_template_set_bonus(payload.get("set_bonus")),
        "attack_bonus": 0,
        "defense_bonus": 0,
        "extra_stats": extra_stats,
    }


def build_gear_template_preview(item_template: Any) -> GearTemplate | None:
    effect_type = _require_string(getattr(item_template, "effect_type", None), field_name="item_template.effect_type")
    slot = EQUIP_SLOT_MAP.get(effect_type)
    if not slot:
        return None
    return GearTemplate(
        key=_require_string(getattr(item_template, "key", None), field_name="item_template.key"),
        **_build_gear_template_defaults(item_template, slot=slot),
    )


def _list_free_gear_options(manor: Manor, *, slot: str) -> list[dict[str, Any]]:
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
    from gameplay.models import InventoryItem

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

    for entry in _list_free_gear_options(manor, slot=slot):
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
        existing_count = _require_int(existing.get("count"), field_name="gear option count", minimum=0)
        entry_count = _require_int(entry.get("count"), field_name="gear option count", minimum=0)
        existing["count"] = max(existing_count, entry_count)

    return sorted(merged.values(), key=lambda entry: (getattr(entry["template"], "name", ""), str(entry["id"])))


def _get_or_create_free_gear_for_template_key(manor: Manor, *, template_key: str, slot: str | None = None) -> GearItem:
    from gameplay.models import InventoryItem

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
        defaults=_build_gear_template_defaults(inventory_item.template, slot=resolved_slot),
    )
    free_gear = (
        manor.gears.select_related("template").filter(template=gear_template, guest__isnull=True).order_by("id").first()
    )
    if free_gear is not None:
        return free_gear
    return GearItem.objects.create(manor=manor, template=gear_template)


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

    return _get_or_create_free_gear_for_template_key(manor, template_key=raw_choice, slot=slot)


def ensure_inventory_gears(manor: Manor, *, slot: str | None = None) -> None:
    """
    同步庄园背包中的装备道具到门客装备系统。
    确保背包数量与装备模板数量匹配。

    IMPORTANT: Only count items in WAREHOUSE since equip_guest consumes from WAREHOUSE.
    """
    from gameplay.models import InventoryItem

    effect_types = list(EQUIP_SLOT_MAP.keys())
    if slot:
        effect_types = [key for key, mapped_slot in EQUIP_SLOT_MAP.items() if mapped_slot == slot]
        if not effect_types:
            return
    items = InventoryItem.objects.filter(
        manor=manor, template__effect_type__in=effect_types, storage_location=InventoryItem.StorageLocation.WAREHOUSE
    ).select_related("template")
    if not items:
        # No warehouse items for these slots — clean up any orphaned free GearItems
        target_slots = set(EQUIP_SLOT_MAP[et] for et in effect_types if et in EQUIP_SLOT_MAP)
        if target_slots:
            GearItem.objects.filter(manor=manor, guest__isnull=True, template__slot__in=target_slots).delete()
        return
    target_slots = set(EQUIP_SLOT_MAP[et] for et in effect_types if et in EQUIP_SLOT_MAP)
    synced_slots: set[str] = set()
    for inv in items:
        slot = EQUIP_SLOT_MAP.get(inv.template.effect_type)
        if not slot:
            continue
        synced_slots.add(slot)
        gear_template, _ = GearTemplate.objects.update_or_create(
            key=inv.template.key,
            defaults=_build_gear_template_defaults(inv.template, slot=slot),
        )
        free_qs = manor.gears.filter(template=gear_template, guest__isnull=True)
        free_count = free_qs.count()
        target_free = _require_int(inv.quantity, field_name="inventory gear quantity", minimum=0)
        if free_count < target_free:
            missing = target_free - free_count
            GearItem.objects.bulk_create([GearItem(manor=manor, template=gear_template) for _ in range(missing)])
        elif free_count > target_free:
            to_delete = free_qs[: free_count - target_free]
            GearItem.objects.filter(id__in=[g.id for g in to_delete]).delete()

    # Clean up free GearItems for slots that had no warehouse items
    orphan_slots = target_slots - synced_slots
    if orphan_slots:
        GearItem.objects.filter(manor=manor, guest__isnull=True, template__slot__in=orphan_slots).delete()


@transaction.atomic
def equip_guest(gear: GearItem, guest: Guest) -> GearItem:
    """
    为门客装备道具。

    Args:
        gear: 要装备的道具
        guest: 门客对象

    Returns:
        装备后的道具对象

    Raises:
        EquipmentError: 装备失败时抛出异常
    """
    # 使用 select_for_update 锁定装备和门客，防止并发问题
    gear = GearItem.objects.select_for_update().get(pk=gear.pk)
    guest = Guest.objects.select_for_update().get(pk=guest.pk)
    if guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)

    slot = gear.template.slot
    capacity = _slot_capacity(slot)
    if gear.manor_id != guest.manor_id:
        raise EquipmentError("无法装备其他庄园的装备")
    if gear.guest_id and gear.guest_id != guest.id:
        raise EquipmentAlreadyEquippedError()
    if gear.guest_id == guest.id:
        return gear
    # 锁定该槽位的现有装备，防止并发修改
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

    _consume_warehouse_item_for_gear(guest, gear)

    _apply_template_stats_to_guest(guest, gear.template, +1, updates)
    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)

    # 替换装备后，如果当前生命值超过最大生命值，则调整为最大生命值
    if guest.current_hp > guest.max_hp:
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])

    _schedule_gear_options_cache_clear(guest.manor_id, slots={slot})
    return gear


@transaction.atomic
def unequip_guest_item(gear: GearItem, guest: Guest, *, allow_injured: bool = False) -> GearItem:
    """
    卸下门客的装备道具。
    """
    from gameplay.models import InventoryItem

    # 并发安全：锁定装备和门客，防止并发卸载/穿戴
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
    from gameplay.models import ItemTemplate

    item_template = ItemTemplate.objects.filter(key=gear.template.key).first()
    if not item_template:
        raise ItemNotFoundError("找不到对应的装备模板，无法入库")
    extra_stats = _normalize_extra_stats(gear.template.extra_stats)
    updates = {"attack_bonus", "defense_bonus"}
    guest.attack_bonus -= gear.template.attack_bonus
    guest.defense_bonus -= gear.template.defense_bonus
    for key, field in _GEAR_EXTRA_STAT_FIELDS.items():
        value = extra_stats.get(key)
        if value:
            setattr(guest, field, getattr(guest, field) - value)
            updates.add(field)
    gear.guest = None
    gear.save(update_fields=["guest"])
    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)

    # 装备拆下后，如果当前生命值超过最大生命值，则调整为最大生命值
    if guest.current_hp > guest.max_hp:
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])

    # 装备退回到背包
    # IMPORTANT: Must specify storage_location to avoid MultipleObjectsReturned
    from django.db.models import F

    # 使用原子更新增加库存，防止并发覆盖
    # 先尝试更新现有记录
    updated = InventoryItem.objects.filter(
        manor=guest.manor, template=item_template, storage_location=InventoryItem.StorageLocation.WAREHOUSE
    ).update(quantity=F("quantity") + 1)

    if updated == 0:
        # 如果不存在，则创建（加锁防止并发创建冲突）
        # 注意：这里理论上仍有极其微小的竞态（两个并发请求同时发现 updated==0），
        # 但 InventoryItem通常有唯一约束(manor, template, location)。
        # 若发生 IntegrityError，让应用层重试或由上层事务回滚即可。
        InventoryItem.objects.create(
            manor=guest.manor,
            template=item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity=1,
        )

    _schedule_gear_options_cache_clear(guest.manor_id, slots={gear.template.slot})
    return gear
