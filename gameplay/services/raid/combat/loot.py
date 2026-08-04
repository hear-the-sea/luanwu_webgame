"""Raid loot calculation/apply helpers (split from legacy combat.py)."""

from __future__ import annotations

import random
from typing import Any, Dict, Sequence, Tuple

from django.db import transaction
from django.db.models import QuerySet

from core.utils import safe_positive_int

from ....models import InventoryItem, ItemTemplate, Manor, ResourceEvent
from ...inventory.core import (
    GRAIN_ITEM_KEY,
    add_item_to_inventory_locked,
    get_warehouse_grain_quantity_locked,
    set_warehouse_grain_quantity_locked,
)
from ...pvp_runtime.loot import (
    WeightedLootCandidate,
    calculate_item_loot_capacity,
    calculate_item_loot_draw_count,
    draw_weighted_item_loot,
    normalize_positive_int_mapping,
)
from ...resources import log_resource_gain
from .config import PVPConstants
from .troops import _extract_raid_troops_lost, _normalize_mapping


def _safe_loot_ratio(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _calculate_resource_loot_cap(
    *,
    guests: Sequence[Any] | None,
    troop_loadout: Dict[str, int] | None,
    battle_report: Any = None,
) -> int:
    max_per_type = safe_positive_int(getattr(PVPConstants, "LOOT_RESOURCE_MAX_PER_TYPE", 30000), 30000)
    full_guest_count = safe_positive_int(getattr(PVPConstants, "LOOT_FULL_GUEST_COUNT", 18), 18)
    full_troop_count = safe_positive_int(getattr(PVPConstants, "LOOT_FULL_TROOP_COUNT", 3600), 3600)
    min_cap_ratio = _safe_loot_ratio(getattr(PVPConstants, "LOOT_MIN_CAP_RATIO", 0.08), 0.08)
    survival_base = _safe_loot_ratio(getattr(PVPConstants, "LOOT_SURVIVAL_BASE_RATIO", 0.35), 0.35)
    survival_scale = _safe_loot_ratio(getattr(PVPConstants, "LOOT_SURVIVAL_SCALING_RATIO", 0.65), 0.65)

    if max_per_type <= 0:
        return 0
    if full_guest_count <= 0 or full_troop_count <= 0:
        return max_per_type
    if guests is None and troop_loadout is None and battle_report is None:
        return max_per_type

    guest_count = len(guests or [])
    normalized_loadout = normalize_positive_int_mapping(troop_loadout)
    deployed_troops = sum(normalized_loadout.values())
    if guest_count <= 0 or deployed_troops <= 0:
        return int(max_per_type * max(0.0, min_cap_ratio))

    guest_factor = min(guest_count / full_guest_count, 1.0)
    troop_factor = min(deployed_troops / full_troop_count, 1.0)
    departure_ratio = max(min_cap_ratio, guest_factor * troop_factor)

    troop_losses = _extract_raid_troops_lost(normalized_loadout, battle_report)
    surviving_troops = max(0, deployed_troops - sum(troop_losses.values()))
    survival_ratio = surviving_troops / deployed_troops
    survival_multiplier = survival_base + survival_scale * survival_ratio

    return int(max_per_type * departure_ratio * survival_multiplier)


def _calculate_item_loot_capacity(
    *,
    guests: Sequence[Any] | None,
    troop_loadout: Dict[str, int] | None,
    battle_report: Any = None,
) -> int:
    max_capacity = safe_positive_int(getattr(PVPConstants, "LOOT_ITEM_CAPACITY_MAX", 30000), 30000)
    if guests is None and troop_loadout is None and battle_report is None:
        return max_capacity
    resource_max = safe_positive_int(getattr(PVPConstants, "LOOT_RESOURCE_MAX_PER_TYPE", 2_000_000), 2_000_000)
    if resource_max <= 0:
        return max_capacity

    normalized_loadout = normalize_positive_int_mapping(troop_loadout)
    deployed_troops = sum(normalized_loadout.values())
    troop_losses = _extract_raid_troops_lost(normalized_loadout, battle_report)
    surviving_troop_count = max(0, deployed_troops - sum(troop_losses.values()))
    return calculate_item_loot_capacity(
        max_capacity=max_capacity,
        guest_count=len(guests or []),
        troop_loadout=normalized_loadout,
        surviving_troop_count=surviving_troop_count,
        full_guest_count=PVPConstants.LOOT_FULL_GUEST_COUNT,
        full_troop_count=PVPConstants.LOOT_FULL_TROOP_COUNT,
        min_cap_ratio=PVPConstants.LOOT_MIN_CAP_RATIO,
        survival_base_ratio=PVPConstants.LOOT_SURVIVAL_BASE_RATIO,
        survival_scaling_ratio=PVPConstants.LOOT_SURVIVAL_SCALING_RATIO,
    )


def _calculate_resource_loot(
    defender: Manor,
    loot_percent: float,
    *,
    guests: Sequence[Any] | None = None,
    troop_loadout: Dict[str, int] | None = None,
    battle_report: Any = None,
) -> Dict[str, int]:
    loot_resources: Dict[str, int] = {}
    loot_cap = _calculate_resource_loot_cap(
        guests=guests,
        troop_loadout=troop_loadout,
        battle_report=battle_report,
    )

    if defender.silver > 0:
        loot_silver = min(int(defender.silver * loot_percent), loot_cap)
        if loot_silver > 0:
            loot_resources["silver"] = loot_silver

    return loot_resources


def _build_loot_item_queryset(defender: Manor) -> QuerySet[InventoryItem]:
    return InventoryItem.objects.filter(
        manor=defender,
        template__tradeable=True,
        quantity__gt=0,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


def _build_weighted_loot_candidates(base_qs: QuerySet[InventoryItem]) -> list[WeightedLootCandidate]:
    candidates: list[WeightedLootCandidate] = []
    rows = base_qs.order_by("id").values("quantity", "template__key", "template__storage_space")
    for row in rows:
        item_key = str(row.get("template__key") or "").strip()
        quantity = safe_positive_int(row.get("quantity"), 0)
        if not item_key or quantity <= 0:
            continue
        candidates.append(
            {
                "item_key": item_key,
                "remaining_quantity": quantity,
                "storage_space": safe_positive_int(row.get("template__storage_space"), 1),
            }
        )
    return candidates


def _calculate_loot_items(
    base_qs: QuerySet[InventoryItem],
    *,
    rng: random.Random,
    guests: Sequence[Any] | None = None,
    troop_loadout: Dict[str, int] | None = None,
    battle_report: Any = None,
) -> Dict[str, int]:
    capacity = _calculate_item_loot_capacity(
        guests=guests,
        troop_loadout=troop_loadout,
        battle_report=battle_report,
    )
    candidates = _build_weighted_loot_candidates(base_qs)
    total_quantity = sum(int(candidate["remaining_quantity"]) for candidate in candidates)
    draw_count = calculate_item_loot_draw_count(
        total_quantity,
        PVPConstants.LOOT_ITEM_MAX_QUANTITY_PERCENT,
    )
    return draw_weighted_item_loot(
        candidates,
        draw_count=draw_count,
        capacity=capacity,
        rng=rng,
    )


def _calculate_loot(
    defender: Manor,
    *,
    rng: random.Random,
    guests: Sequence[Any] | None = None,
    troop_loadout: Dict[str, int] | None = None,
    battle_report: Any = None,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    计算战利品。

    Returns:
        (掠夺的资源, 掠夺的物品)
    """
    loot_percent = rng.uniform(
        PVPConstants.LOOT_RESOURCE_MIN_PERCENT,
        PVPConstants.LOOT_RESOURCE_MAX_PERCENT,
    )
    loot_resources = _calculate_resource_loot(
        defender,
        loot_percent,
        guests=guests,
        troop_loadout=troop_loadout,
        battle_report=battle_report,
    )
    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
        rng=rng,
        guests=guests,
        troop_loadout=troop_loadout,
        battle_report=battle_report,
    )
    return loot_resources, loot_items


def _apply_loot(
    defender: Manor, loot_resources: Dict[str, int], loot_items: Dict[str, int], locked_manor: Manor | None = None
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    从防守方扣除被掠夺的资源和物品，返回实际扣除量。
    """
    loot_resources = normalize_positive_int_mapping(loot_resources)
    loot_items = normalize_positive_int_mapping(loot_items)
    manor = locked_manor or Manor.objects.select_for_update().get(pk=defender.pk)
    actual_resources: Dict[str, int] = {}
    actual_items: Dict[str, int] = {}
    legacy_grain_requested = loot_resources.pop(GRAIN_ITEM_KEY, 0)
    item_grain_requested = loot_items.pop(GRAIN_ITEM_KEY, 0)

    # 扣除资源（按当前可用量裁剪，避免不足导致回滚）
    for resource_key, amount in loot_resources.items():
        if amount <= 0:
            continue
        current_value = getattr(manor, resource_key, 0)
        deducted = min(current_value, amount)
        if deducted <= 0:
            continue
        setattr(manor, resource_key, current_value - deducted)
        actual_resources[resource_key] = deducted

    # 粮食虽然来自物品池，但仓库粮食行是唯一业务账本；兼容旧记录中的资源粮食字段。
    current_grain = get_warehouse_grain_quantity_locked(manor)
    legacy_grain_deducted = min(current_grain, legacy_grain_requested)
    remaining_grain = current_grain - legacy_grain_deducted
    item_grain_deducted = min(remaining_grain, item_grain_requested)
    total_grain_deducted = legacy_grain_deducted + item_grain_deducted
    if legacy_grain_deducted > 0:
        actual_resources[GRAIN_ITEM_KEY] = legacy_grain_deducted
    if item_grain_deducted > 0:
        actual_items[GRAIN_ITEM_KEY] = item_grain_deducted
    if total_grain_deducted > 0:
        set_warehouse_grain_quantity_locked(manor, current_grain - total_grain_deducted)

    resource_deductions = dict(actual_resources)
    if item_grain_deducted > 0:
        resource_deductions[GRAIN_ITEM_KEY] = resource_deductions.get(GRAIN_ITEM_KEY, 0) + item_grain_deducted

    update_fields = set(actual_resources.keys())
    if update_fields:
        manor.save(update_fields=sorted(update_fields))
    if resource_deductions:
        log_resource_gain(
            manor,
            {key: -val for key, val in resource_deductions.items()},
            ResourceEvent.Reason.ADMIN_ADJUST,
            note="踢馆被掠夺",
        )

    # 扣除物品（按当前库存裁剪）
    for item_key, qty in loot_items.items():
        if qty <= 0:
            continue
        try:
            item = InventoryItem.objects.select_for_update().get(
                manor=manor,
                template__key=item_key,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
        except InventoryItem.DoesNotExist:
            continue
        deducted = min(item.quantity, qty)
        if deducted <= 0:
            continue
        item.quantity -= deducted
        if item.quantity <= 0:
            item.delete()
        else:
            item.save(update_fields=["quantity", "updated_at"])
        actual_items[item_key] = deducted

    return actual_resources, actual_items


def _format_loot_description(resources: Dict[str, int], items: Dict[str, int]) -> str:
    """格式化战利品描述"""
    resources = normalize_positive_int_mapping(resources)
    items = normalize_positive_int_mapping(items)
    parts = []

    if resources.get("grain"):
        parts.append(f"粮食 {resources['grain']}")
    if resources.get("silver"):
        parts.append(f"银两 {resources['silver']}")

    if items:
        templates = {t.key: t.name for t in ItemTemplate.objects.filter(key__in=items.keys()).only("key", "name")}
        for key, qty in items.items():
            name = templates.get(key, "未知物品")
            parts.append(f"{name} ×{qty}")

    return "\n".join(parts) if parts else "无"


def _format_battle_rewards_description(battle_rewards: Dict[str, Any]) -> str:
    """格式化战斗通用奖励描述"""
    normalized_rewards = _normalize_mapping(battle_rewards)
    if not normalized_rewards:
        return ""

    parts = []
    exp_fruit = safe_positive_int(normalized_rewards.get("exp_fruit", 0), 0)
    equipment = normalize_positive_int_mapping(normalized_rewards.get("equipment"))

    if exp_fruit > 0:
        parts.append(f"经验果 ×{exp_fruit}")

    if equipment:
        templates = {t.key: t.name for t in ItemTemplate.objects.filter(key__in=equipment.keys()).only("key", "name")}
        for key, qty in equipment.items():
            name = templates.get(key, "未知装备")
            parts.append(f"{name} ×{qty}")

    return "\n".join(parts) if parts else ""


def _format_capture_description(capture_payload: Any) -> str:
    if not isinstance(capture_payload, dict):
        return ""
    name = (capture_payload.get("guest_name") or "").strip()
    if not name:
        return ""
    return f"{name}（已押入监牢，装备尽失）"


def _grant_loot_items(manor: Manor, items: Dict[str, int]) -> None:
    """批量发放掠夺的物品"""
    if not transaction.get_connection().in_atomic_block:
        with transaction.atomic():
            _grant_loot_items(manor, items)
        return

    items = normalize_positive_int_mapping(items)
    if not items:
        return

    from core.utils.template_loader import load_templates_by_key

    templates = load_templates_by_key(ItemTemplate, keys=items.keys(), only_fields=["id", "key"])

    if not templates:
        return

    for key, qty in items.items():
        template = templates.get(key)
        if not template:
            continue
        add_item_to_inventory_locked(manor, key, qty, template=template)
