"""
商铺交易服务
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from django.db import transaction
from django.db.models import F

from core.exceptions import (
    InsufficientResourceError,
    InsufficientSilverError,
    InsufficientStockError,
    ItemInsufficientError,
    ItemNotConfiguredError,
    ItemNotFoundError,
    ShopValidationError,
)
from core.utils import safe_int, safe_non_negative_int
from gameplay.models import InventoryItem, ItemTemplate, Manor, ResourceEvent
from gameplay.models.items import (
    ITEM_EFFECT_TYPE_LABELS,
    LEGACY_TOOL_EFFECT_TYPES,
    get_item_effect_type_label,
    normalize_item_effect_type,
)
from gameplay.services.inventory.core import add_item_to_inventory_locked, consume_inventory_item_locked
from gameplay.services.resources import grant_resources_locked, spend_resources_locked
from gameplay.utils.template_loader import get_item_templates_by_keys
from trade.models import ShopPurchaseLog, ShopSellLog, ShopStock
from trade.services.auction.constants import GOLD_BAR_ITEM_KEY
from trade.services.auction.gold_bars import consume_available_gold_bars_locked
from trade.services.shop_config import (
    calculate_buy_price,
    get_sell_price_by_template,
    get_shop_config,
    get_shop_item_config,
)


@dataclass
class ShopItemDisplay:
    """商铺商品展示数据"""

    key: str
    name: str
    description: str
    price: int
    stock: int  # -1 表示无限
    stock_display: str
    available: bool  # 是否可购买
    icon: str  # 图标
    image_url: str  # 图片 URL
    effect_type: str  # 效果类型
    category: str  # 种类显示名称
    rarity: str  # 稀有度
    effect_payload: dict  # 效果数据（装备属性、套装等）


@dataclass
class SellableItemDisplay:
    """可出售物品展示数据"""

    inventory_item: InventoryItem
    sell_price: int


# 效果类型到种类名称映射（单一来源，来自 gameplay.models.items）
EFFECT_TYPE_CATEGORY = ITEM_EFFECT_TYPE_LABELS


def _normalize_mapping(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    return {}


def _normalize_effect_type(effect_type: Any) -> str:
    """统一工具类 effect_type，兼容旧数据。"""
    return normalize_item_effect_type(str(effect_type or "").strip())


def _get_category(effect_type: Any) -> str:
    """获取种类显示名称"""
    return get_item_effect_type_label(str(effect_type or "").strip())


def _get_buy_price_from_config(config: Any, template: ItemTemplate) -> int | None:
    config_buy_price = getattr(config, "buy_price", None)
    if config_buy_price is not None:
        return safe_non_negative_int(config_buy_price, 0)

    base_price = config.price if config.price is not None else template.price
    return calculate_buy_price(base_price)


def get_shop_items_for_display() -> List[ShopItemDisplay]:
    """
    获取商铺商品列表（用于页面展示）
    """
    config_list = get_shop_config()
    if not config_list:
        return []

    # 批量获取 ItemTemplate
    item_keys = [c.item_key for c in config_list]
    templates = get_item_templates_by_keys(item_keys)

    # 批量获取库存
    stocks = {s.item_key: s.current_stock for s in ShopStock.objects.filter(item_key__in=item_keys)}

    result = []
    for config in config_list:
        template = templates.get(config.item_key)
        if not template:
            continue

        price = safe_non_negative_int(_get_buy_price_from_config(config, template), 0)

        # 确定库存
        if config.is_unlimited:
            stock = -1
            stock_display = "无限"
            available = True
        else:
            # 有限库存：从数据库获取当前库存，如果不存在则使用配置的初始值
            current_stock = stocks.get(config.item_key, config.stock)
            current_stock = safe_non_negative_int(current_stock, 0)
            stock = current_stock
            stock_display = str(current_stock)
            available = current_stock > 0

        result.append(
            ShopItemDisplay(
                key=template.key,
                name=template.name,
                description=template.description,
                price=price,
                stock=stock,
                stock_display=stock_display,
                available=available,
                icon=template.icon or "",
                image_url=template.image.url if template.image else "",
                effect_type=_normalize_effect_type(template.effect_type or ""),
                category=_get_category(template.effect_type or ""),
                rarity=template.rarity or "black",
                effect_payload=_normalize_mapping(template.effect_payload),
            )
        )

    return result


def _build_sell_price_overrides() -> dict[str, int]:
    """
    构建回收价覆盖表，避免在遍历库存时重复线性扫描商店配置。
    """
    overrides: dict[str, int] = {}
    for config in get_shop_config():
        if config.price is None:
            continue
        overrides[config.item_key] = safe_non_negative_int(config.price, 0)
    return overrides


def get_sellable_inventory_queryset(manor: Manor, category: str = None):
    """
    获取玩家可出售物品的查询集。

    IMPORTANT: Only show WAREHOUSE items since sell_item only operates on WAREHOUSE.

    Args:
        manor: 庄园对象
        category: 分类筛选（可选），使用 normalized effect_type
    """
    items = manor.inventory_items.select_related("template").filter(
        quantity__gt=0, storage_location=InventoryItem.StorageLocation.WAREHOUSE
    )

    # 数据库层面的分类筛选
    normalized_category = _normalize_effect_type(category or "all")
    if normalized_category and normalized_category != "all":
        tool_effect_types = LEGACY_TOOL_EFFECT_TYPES
        if normalized_category in tool_effect_types:
            items = items.filter(template__effect_type__in=tool_effect_types)
        else:
            items = items.filter(template__effect_type=normalized_category)

    return items


def build_sellable_inventory_display_rows(items: Iterable[InventoryItem]) -> List[SellableItemDisplay]:
    """将库存行转换为可出售展示行。"""
    sell_price_overrides = _build_sell_price_overrides()
    result = []

    for item in items:
        sell_price = sell_price_overrides.get(item.template.key)
        if sell_price is None:
            sell_price = safe_non_negative_int(item.template.price, 0)
        if sell_price > 0:
            result.append(SellableItemDisplay(inventory_item=item, sell_price=sell_price))

    return result


def get_sellable_inventory(manor: Manor, category: str = None) -> List[SellableItemDisplay]:
    """
    获取玩家可出售的物品列表

    IMPORTANT: Only show WAREHOUSE items since sell_item only operates on WAREHOUSE.

    Args:
        manor: 庄园对象
        category: 分类筛选（可选），使用 normalized effect_type
    """
    items = get_sellable_inventory_queryset(manor, category=category)

    return build_sellable_inventory_display_rows(items)


@transaction.atomic
def buy_item(manor: Manor, item_key: str, quantity: int) -> Dict:
    """
    购买商品

    Lock order: ShopStock -> Manor (via spend_resources) -> InventoryItem
    All code paths must acquire locks in this order to prevent deadlocks.

    Args:
        manor: 庄园对象
        item_key: 商品 key
        quantity: 购买数量

    Returns:
        购买结果字典

    Raises:
        GameError: 购买失败时抛出显式领域异常
    """
    quantity = safe_int(quantity, 0)
    if quantity <= 0:
        raise ShopValidationError(action="buy")

    # 检查商品配置
    config = get_shop_item_config(item_key)
    if config is None:
        raise ItemNotFoundError("商品不存在")

    # 获取物品模板
    try:
        template = ItemTemplate.objects.get(key=item_key)
    except ItemTemplate.DoesNotExist:
        raise ItemNotFoundError("商品不存在")

    unit_price = safe_int(_get_buy_price_from_config(config, template), -1)
    if unit_price < 0:
        raise ItemNotConfiguredError("商品价格配置异常")
    total_cost = unit_price * quantity

    # 检查库存（使用行级锁防止超卖）
    if not config.is_unlimited:
        stock, created = ShopStock.objects.select_for_update().get_or_create(
            item_key=item_key,
            defaults={"current_stock": safe_non_negative_int(config.stock, 0)},
        )
        if stock.current_stock < quantity:
            raise InsufficientStockError(template.name, quantity, int(stock.current_stock), message="库存不足")

    # 锁定庄园并检查扣除银两（并发安全）
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    try:
        spend_resources_locked(
            locked_manor,
            {"silver": total_cost},
            f"购买 {template.name} ×{quantity}",
            ResourceEvent.Reason.SHOP_PURCHASE,
        )
    except InsufficientResourceError as exc:
        raise InsufficientSilverError(total_cost, int(locked_manor.silver), message="银两不足") from exc

    # 扣除库存（原子操作，防止并发超卖）
    if not config.is_unlimited:
        # Use atomic update with validation to prevent race conditions
        updated = ShopStock.objects.filter(pk=stock.pk, current_stock__gte=quantity).update(
            current_stock=F("current_stock") - quantity
        )
        if not updated:
            refreshed_stock = ShopStock.objects.filter(pk=stock.pk).values_list("current_stock", flat=True).first() or 0
            raise InsufficientStockError(template.name, quantity, int(refreshed_stock), message="库存不足")

    add_item_to_inventory_locked(locked_manor, template.key, quantity)

    # 记录购买日志
    ShopPurchaseLog.objects.create(
        manor=locked_manor,
        item_key=item_key,
        quantity=quantity,
        unit_price=unit_price,
        total_cost=total_cost,
    )

    return {
        "item_name": template.name,
        "quantity": quantity,
        "total_cost": total_cost,
    }


@transaction.atomic
def sell_item(manor: Manor, item_key: str, quantity: int) -> Dict:
    """
    出售物品

    Lock order: Manor (via grant_resources) -> InventoryItem
    Matches buy_item lock order to prevent deadlocks.

    Args:
        manor: 庄园对象
        item_key: 物品 key
        quantity: 出售数量

    Returns:
        出售结果字典

    Raises:
        GameError: 出售失败时抛出显式领域异常
    """
    quantity = safe_int(quantity, 0)
    if quantity <= 0:
        raise ShopValidationError(action="sell")

    # 获取物品模板
    try:
        template = ItemTemplate.objects.only("id", "key", "name", "price").get(key=item_key)
    except ItemTemplate.DoesNotExist:
        raise ItemNotFoundError("物品不存在")

    # 计算回收价
    unit_price = get_sell_price_by_template(template)
    if unit_price <= 0:
        raise ItemNotConfiguredError("该物品无法出售")

    total_income = unit_price * quantity

    # 锁定庄园并发放银两（并发安全）
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    if template.key == GOLD_BAR_ITEM_KEY:
        consume_available_gold_bars_locked(locked_manor, quantity)
    else:
        inventory_item = (
            InventoryItem.objects.select_for_update()
            .filter(
                manor=locked_manor,
                template=template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .first()
        )
        if inventory_item is None:
            raise ItemInsufficientError(template.name, quantity, 0, message="您没有该物品")
        if inventory_item.quantity < quantity:
            raise ItemInsufficientError(
                template.name,
                quantity,
                int(inventory_item.quantity),
                message="物品数量不足",
            )
        inventory_item.template = template
        consume_inventory_item_locked(inventory_item, quantity)

    _credited, overflow = grant_resources_locked(
        locked_manor,
        {"silver": total_income},
        f"出售 {template.name} ×{quantity}",
        ResourceEvent.Reason.SHOP_SELL,
        sync_production=False,
    )
    if overflow.get("silver", 0) > 0:
        raise ShopValidationError(action="sell", message="银库空间不足，无法收取全部出售收入")

    # 记录出售日志
    ShopSellLog.objects.create(
        manor=locked_manor,
        item_key=item_key,
        quantity=quantity,
        unit_price=unit_price,
        total_income=total_income,
    )

    return {
        "item_name": template.name,
        "quantity": quantity,
        "total_income": total_income,
    }
