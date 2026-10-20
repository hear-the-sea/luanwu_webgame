from __future__ import annotations

from copy import copy

from django.core.paginator import Paginator
from django.db.models import Q

from guests.avatar import get_guest_disguise_avatar_options
from guests.models import GuestStatus, GuestTemplate
from guests.services.world_unique import WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY, get_world_unique_guest_status

from ..models import InventoryItem, ItemTemplate
from ..models.items import (
    LEGACY_MISSION_CHEST_KEYS,
    LEGACY_TOOL_EFFECT_TYPES,
    MISSION_CHEST_CATEGORY_KEY,
    MISSION_CHEST_KEY_PREFIX,
    get_item_effect_type_label,
)
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
    MISSION_CHEST_CATEGORY_KEY,
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


def _annotate_world_unique_scroll_use_status(items: list) -> None:
    if not any(item.template.key == WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY for item in items):
        return

    status = get_world_unique_guest_status()
    for item in items:
        if item.template.key != WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY:
            continue
        # 仅供点击“使用”时的确认弹窗读取，仓库物品提示不展示唯一门客状态。
        item.world_unique_use_guest_name = status["guest_name"]
        item.world_unique_use_status_summary = status["summary"]


def _annotate_player_choice_guest_cards(items: list) -> None:
    """读取自选兑换的真实门客模板及唯一状态，不创建全服状态记录。"""
    cards = []
    choice_keys = set()
    for item in items:
        payload = item.template.effect_payload
        if (
            not isinstance(payload, dict)
            or payload.get("action") != "summon_guest"
            or payload.get("player_choice") is not True
        ):
            continue
        choices = payload.get("choices", [])
        entries = choices if isinstance(choices, list) else []
        raw_keys = [entry.get("template_key") for entry in entries if isinstance(entry, dict)]
        keys = list(dict.fromkeys(str(key) for key in raw_keys if isinstance(key, str) and key))
        cards.append((item, payload, keys))
        choice_keys.update(keys)
    if not cards:
        return
    templates = {template.key: template for template in GuestTemplate.objects.filter(key__in=choice_keys)}
    statuses = {
        key: get_world_unique_guest_status(template_key=key)
        for key, template in templates.items()
        if template.is_world_unique
    }
    for item, payload, keys in cards:
        options = []
        for key in keys:
            template = templates.get(key)
            status = statuses.get(key)
            options.append(
                {
                    "template_key": key,
                    "name": template.name if template else "门客暂不可用",
                    "rarity": template.rarity if template else "",
                    "rarity_label": template.get_rarity_display() if template else "",
                    "flavor": template.flavor if template else "",
                    "avatar_url": template.avatar.url if template and template.avatar else "",
                    "growth_range": template.growth_range if template else [],
                    "is_world_unique": bool(template and template.is_world_unique),
                    "status_summary": status["summary"] if status else "可招募" if template else "配置缺失",
                    "is_available": bool(template and (status is None or status["is_available"])),
                }
            )
        requirements = payload.get("required_items")
        own_cost = requirements.get(item.template.key, 0) if isinstance(requirements, dict) else 0
        own_cost = own_cost if isinstance(own_cost, int) and not isinstance(own_cost, bool) and own_cost >= 0 else 0
        item.player_choice_options = options
        item.player_choice_cost = 1 + own_cost
        item.player_choice_has_stock = item.quantity >= item.player_choice_cost
        item.player_choice_has_available_guest = any(option["is_available"] for option in options)


def _split_inventory_display_rows(items: list, *, frozen_gold: int) -> list:
    """按绑定状态投影仓库行，保留同一库存 ID，不修改库存账本。"""
    rows = []
    for item in items:
        for binding_mode, quantity in (("bound", item.bound_quantity), ("unbound", item.unbound_quantity)):
            if quantity <= 0:
                continue
            row = copy(item)
            row.binding_mode = binding_mode
            row.display_is_bound = binding_mode == "bound"
            row.display_quantity = quantity
            row.transferable_quantity = 0 if row.display_is_bound else quantity
            if item.template.key == "gold_bar" and not row.display_is_bound:
                row.display_quantity = max(0, quantity - frozen_gold)
                row.transferable_quantity = row.display_quantity
            rows.append(row)
    return rows


def get_warehouse_context(manor, current_tab: str, selected_category: str, page: int = 1) -> dict:
    # Get frozen gold bars for display adjustment
    from trade.services.auction_service import get_frozen_gold_bars

    context = {
        "manor": manor,
        "frozen_gold_bars": get_frozen_gold_bars(manor),
        "current_tab": current_tab,
    }

    # Load guests once, then preserve the existing eligible subset for legacy tools while
    # allowing the disguise card to target any guest that still belongs to the manor.
    all_guests = list(manor.guests.select_related("template").order_by("-level", "template__name", "id"))
    eligible_guests = [guest for guest in all_guests if guest.status in {GuestStatus.IDLE, GuestStatus.INJURED}]
    guest_item_selection = build_guest_item_selection_context(
        manor,
        eligible_guests=eligible_guests,
        disguise_guests=all_guests,
        soul_fusion_guests=all_guests,
    )
    context["guests_for_rebirth"] = guest_item_selection.guests_for_rebirth
    context["guests_for_xisuidan"] = guest_item_selection.guests_for_xisuidan
    context["guests_for_xidianka"] = guest_item_selection.guests_for_xidianka
    context["guests_for_soul_fusion"] = guest_item_selection.guests_for_soul_fusion
    context["guests_for_rarity_upgrade"] = guest_item_selection.guests_for_rarity_upgrade
    context["guests_for_disguise"] = guest_item_selection.guests_for_disguise
    context["guest_disguise_avatar_options"] = (
        get_guest_disguise_avatar_options()
        if (
            current_tab == "warehouse"
            and guest_item_selection.has_guest_avatar_disguise_card
            and guest_item_selection.guests_for_disguise
        )
        else []
    )

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
    mission_chest_filter = Q(template__effect_type=ItemTemplate.EffectType.LOOT_BOX) & (
        Q(template__key__startswith=MISSION_CHEST_KEY_PREFIX) | Q(template__key__in=LEGACY_MISSION_CHEST_KEYS)
    )
    if selected_category != "all":
        if selected_category == MISSION_CHEST_CATEGORY_KEY:
            items = items.filter(mission_chest_filter)
        elif selected_category in tool_effect_types:
            selected_category = tool_category_key
            items = items.filter(template__effect_type__in=tool_effect_types).exclude(mission_chest_filter)
        else:
            items = items.filter(template__effect_type=selected_category)

    # 使用 values + distinct 只查询 effect_type，避免加载全部对象
    # 并清除既有排序，避免 PostgreSQL 下 DISTINCT + 非选择列 ORDER BY 的兼容性问题。
    effect_types = _distinct_effect_types(all_items.exclude(mission_chest_filter))

    # 始终保留任务宝箱入口，空分类也能维持所选筛选项。
    categories = [{"key": MISSION_CHEST_CATEGORY_KEY, "label": get_item_effect_type_label(MISSION_CHEST_CATEGORY_KEY)}]
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
    if current_tab == "warehouse":
        _annotate_world_unique_scroll_use_status(warehouse_items)
        _annotate_player_choice_guest_cards(warehouse_items)
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

    warehouse_rows = _split_inventory_display_rows(warehouse_items, frozen_gold=frozen_gold)
    paginator = Paginator(warehouse_rows, WAREHOUSE_PAGE_SIZE)
    page_obj = paginator.get_page(page)
    items_list = list(page_obj)

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
