from __future__ import annotations

from django.core.paginator import Paginator

from guests.models import GuestStatus

from ..models import InventoryItem, ItemTemplate
from ..models.items import LEGACY_TOOL_EFFECT_TYPES, get_item_effect_type_label
from ..services.inventory.core import get_warehouse_grain_quantity
from ..services.inventory.guest_item_selector import build_guest_item_selection_context
from ..services.manor.treasury import get_treasury_capacity, get_treasury_deposit_block_reason, get_treasury_used_space

# 每页显示的物品数量
WAREHOUSE_PAGE_SIZE = 20
GRAIN_ITEM_KEY = "grain"
WAREHOUSE_CATEGORY_ORDER = (
    "equip_weapon",
    "equip_armor",
    "equip_helmet",
    "equip_shoes",
    "equip_ornament",
    "equip_mount",
    "equip_device",
    ItemTemplate.EffectType.MEDICINE,
    ItemTemplate.EffectType.EXPERIENCE_ITEM,
    ItemTemplate.EffectType.SKILL_BOOK,
    "tool",
    ItemTemplate.EffectType.RESOURCE,
    "other",
)
WAREHOUSE_CATEGORY_RANK = {key: rank for rank, key in enumerate(WAREHOUSE_CATEGORY_ORDER)}


def _distinct_effect_types(items):
    """Return distinct effect types without inheriting potentially expensive/invalid ordering."""
    return items.order_by().values_list("template__effect_type", flat=True).distinct()


def _build_legacy_grain_item(manor, quantity: int):
    grain_template = ItemTemplate.objects.filter(key=GRAIN_ITEM_KEY).first()
    if not grain_template:
        return None

    legacy_item = InventoryItem(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=quantity,
    )
    setattr(legacy_item, "display_quantity", quantity)
    return legacy_item


def _ensure_warehouse_grain_item(items, manor, *, current_tab: str, selected_category: str):
    items_list = list(items)
    if current_tab != "warehouse":
        return items_list, None

    grain_item = next((item for item in items_list if item.template.key == GRAIN_ITEM_KEY), None)
    if grain_item is None:
        warehouse_grain = get_warehouse_grain_quantity(manor)
        if warehouse_grain > 0:
            legacy_item = _build_legacy_grain_item(manor, warehouse_grain)
            if legacy_item is not None:
                effect_type = legacy_item.template.effect_type
                if selected_category in {"all", effect_type}:
                    items_list.append(legacy_item)
                    items_list.sort(key=lambda item: (item.template.name, item.id or 0))
                    grain_item = legacy_item
    if grain_item is None:
        return items_list, None

    setattr(grain_item, "display_quantity", max(0, int(getattr(grain_item, "quantity", 0) or 0)))
    return items_list, grain_item


def _append_missing_category_option(
    categories: list[dict], *, effect_type: str | None, tool_effect_types, tool_category_key
):
    key = effect_type or "other"
    if key in tool_effect_types:
        if not any(item["key"] == tool_category_key for item in categories):
            categories.append({"key": tool_category_key, "label": "道具"})
        return
    if not any(item["key"] == key for item in categories):
        categories.append({"key": key, "label": get_item_effect_type_label(key)})


def get_warehouse_context(manor, current_tab: str, selected_category: str, page: int = 1) -> dict:
    # Get frozen gold bars for display adjustment
    from trade.services.auction_service import get_frozen_gold_bars

    context = {
        "manor": manor,
        "frozen_gold_bars": get_frozen_gold_bars(manor),
        "current_tab": current_tab,
    }

    # Load eligible guests once, then derive specific lists in memory to avoid repeated DB queries.
    eligible_guests = list(
        manor.guests.select_related("template")
        .filter(status__in=[GuestStatus.IDLE, GuestStatus.INJURED])
        .order_by("-level", "template__name", "id")
    )
    guest_item_selection = build_guest_item_selection_context(manor, eligible_guests=eligible_guests)
    context["guests_for_rebirth"] = guest_item_selection.guests_for_rebirth
    context["guests_for_xisuidan"] = guest_item_selection.guests_for_xisuidan
    context["guests_for_xidianka"] = guest_item_selection.guests_for_xidianka
    context["guests_for_soul_fusion"] = guest_item_selection.guests_for_soul_fusion
    context["guests_for_rarity_upgrade"] = guest_item_selection.guests_for_rarity_upgrade

    tool_effect_types = LEGACY_TOOL_EFFECT_TYPES
    tool_category_key = "tool"
    if selected_category == "tools":  # 兼容旧参数
        selected_category = tool_category_key

    if current_tab == "treasury":
        items = (
            manor.inventory_items.filter(storage_location=InventoryItem.StorageLocation.TREASURY, quantity__gt=0)
            .select_related("template")
            .order_by("template__name")
        )

        treasury_capacity = get_treasury_capacity(manor)
        treasury_used = get_treasury_used_space(manor)
        space_delta = treasury_capacity - treasury_used
        context["treasury_capacity"] = treasury_capacity
        context["treasury_used"] = treasury_used
        context["treasury_remaining"] = max(0, space_delta)
        context["treasury_over_capacity"] = max(0, -space_delta)
        context["treasury_usage_percent"] = (
            min(100, int(treasury_used * 100 / treasury_capacity)) if treasury_capacity > 0 else 0
        )
    else:
        items = (
            manor.inventory_items.filter(storage_location=InventoryItem.StorageLocation.WAREHOUSE, quantity__gt=0)
            .select_related("template")
            .order_by("template__name")
        )

    all_items = items
    if selected_category != "all":
        if selected_category in tool_effect_types:
            selected_category = tool_category_key
            items = items.filter(template__effect_type__in=tool_effect_types)
        else:
            items = items.filter(template__effect_type=selected_category)

    # 使用 values + distinct 只查询 effect_type，避免加载全部对象
    # 并清除既有排序，避免 PostgreSQL 下 DISTINCT + 非选择列 ORDER BY 的兼容性问题。
    effect_types = _distinct_effect_types(all_items)

    categories = []
    has_tools = False
    for effect_type in effect_types:
        key = effect_type or "other"
        if key in tool_effect_types:
            has_tools = True
            continue
        label = get_item_effect_type_label(key)
        categories.append({"key": key, "label": label})
    if has_tools:
        categories.append({"key": tool_category_key, "label": "道具"})

    frozen_gold = context["frozen_gold_bars"] if current_tab == "warehouse" else 0

    # 分页处理
    warehouse_items, warehouse_grain_item = _ensure_warehouse_grain_item(
        items,
        manor,
        current_tab=current_tab,
        selected_category=selected_category,
    )
    for item in warehouse_items:
        deposit_block_reason = get_treasury_deposit_block_reason(item.template)
        item.treasury_deposit_blocked = deposit_block_reason is not None
        item.treasury_deposit_hint = deposit_block_reason

    if warehouse_grain_item is not None:
        _append_missing_category_option(
            categories,
            effect_type=warehouse_grain_item.template.effect_type,
            tool_effect_types=tool_effect_types,
            tool_category_key=tool_category_key,
        )
    categories.sort(
        key=lambda category: (
            WAREHOUSE_CATEGORY_RANK.get(category["key"], len(WAREHOUSE_CATEGORY_RANK)),
            category["label"],
            category["key"],
        )
    )

    paginator = Paginator(warehouse_items, WAREHOUSE_PAGE_SIZE)
    page_obj = paginator.get_page(page)
    items_list = list(page_obj)

    for item in items_list:
        if item.template.key == "gold_bar" and frozen_gold > 0:
            setattr(item, "display_quantity", max(0, item.quantity - frozen_gold))
        else:
            item.display_quantity = item.quantity

    context["inventory_items"] = items_list
    context["warehouse_grain_quantity"] = get_warehouse_grain_quantity(manor)
    context["categories"] = categories
    context["selected_category"] = selected_category

    # 分页信息
    context["pagination"] = {
        "page": page_obj.number,
        "total_pages": paginator.num_pages,
        "total_count": paginator.count,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "previous_page": page_obj.previous_page_number() if page_obj.has_previous() else None,
        "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
    }

    return context
