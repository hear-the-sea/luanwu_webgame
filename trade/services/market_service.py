"""
交易行服务层
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from core.config import TRADE
from core.exceptions import InsufficientResourceError, TradeValidationError
from core.utils import safe_int
from core.utils.yaml_loader import load_yaml_data
from gameplay.models import InventoryItem, ItemTemplate, Manor, ResourceEvent
from gameplay.models.items import LEGACY_TOOL_EFFECT_TYPES
from gameplay.services.inventory.core import add_item_to_inventory_locked
from gameplay.services.resources import grant_resources_locked, spend_resources_locked
from gameplay.services.utils.messages import create_message
from gameplay.services.utils.notifications import notify_user
from trade.models import MarketListing, MarketTransaction
from trade.services import market_commands as _market_commands
from trade.services import market_notification_helpers as _market_notification_helpers
from trade.services import market_purchase_helpers as _market_purchase_helpers
from trade.services import market_queries as _market_queries
from trade.services.auction_service import get_frozen_gold_bars
from trade.services.market_expiration import expire_listings_queryset as _expire_listings_queryset_impl
from trade.services.market_listing_helpers import (
    create_listing_record,
    decrement_listing_inventory,
    load_tradeable_item_template,
    lock_listing_inventory_item,
    normalize_listing_inputs,
    validate_gold_bar_availability,
    validate_listing_inventory,
    validate_listing_total_price,
)
from trade.services.market_rules import DEFAULT_TRADE_MARKET_RULES
from trade.services.market_rules import normalize_trade_market_rules as _normalize_trade_market_rules
from trade.services.market_runtime import expire_listings_queryset_entry as _expire_listings_queryset_entry
from trade.services.market_runtime import send_purchase_notifications_entry as _send_purchase_notifications_entry

logger = logging.getLogger(__name__)

TRADE_MARKET_RULES_PATH = Path(settings.BASE_DIR) / "data" / "trade_market_rules.yaml"


@lru_cache(maxsize=1)
def load_trade_market_rules() -> dict[str, dict[int, int]]:
    raw = load_yaml_data(
        TRADE_MARKET_RULES_PATH,
        logger=logger,
        context="trade market rules",
        default=DEFAULT_TRADE_MARKET_RULES,
    )
    return _normalize_trade_market_rules(raw)


def clear_trade_market_rules_cache() -> None:
    global LISTING_FEES
    load_trade_market_rules.cache_clear()
    LISTING_FEES = get_listing_fees()


# 手续费配置
def get_listing_fees() -> dict[int, int]:
    """Return the latest normalized listing-fee rules."""
    return dict(load_trade_market_rules()["listing_fees"])


def get_allowed_listing_durations() -> frozenset[int]:
    """Return the current allowed listing durations as a stable runtime view."""
    return frozenset(get_listing_fees())


LISTING_FEES = get_listing_fees()

# 从 core.config 导入配置
TRANSACTION_TAX_RATE = TRADE.TRANSACTION_TAX_RATE
MIN_PRICE_MULTIPLIER = TRADE.MIN_PRICE_MULTIPLIER
MAX_PRICE = TRADE.MAX_PRICE
MAX_TOTAL_PRICE = TRADE.MAX_TOTAL_PRICE

ALLOWED_LISTING_ORDER_BY = {
    "listed_at",
    "-listed_at",
    "unit_price",
    "-unit_price",
    "price",
    "-price",
    "total_price",
    "-total_price",
    "quantity",
    "-quantity",
    "expires_at",
    "-expires_at",
}


def charge_listing_fee(locked_manor: Manor, silver_amount: int) -> None:
    try:
        spend_resources_locked(
            locked_manor,
            {"silver": silver_amount},
            note="交易行挂单手续费",
            reason=ResourceEvent.Reason.MARKET_LISTING_FEE,
        )
    except InsufficientResourceError as exc:
        raise TradeValidationError(str(exc)) from exc


def pay_market_purchase(locked_manor: Manor, *, item_name: str, total_price: int) -> None:
    try:
        spend_resources_locked(
            locked_manor,
            {"silver": total_price},
            note=f"购买{item_name}",
            reason=ResourceEvent.Reason.MARKET_PURCHASE,
        )
    except InsufficientResourceError as exc:
        raise TradeValidationError(str(exc)) from exc


def settle_market_sale_proceeds(locked_manor: Manor, *, item_name: str, silver_amount: int) -> None:
    _credited, overflow = grant_resources_locked(
        locked_manor,
        {"silver": silver_amount},
        note=f"出售{item_name}",
        reason=ResourceEvent.Reason.ITEM_SOLD,
        sync_production=False,
    )
    if overflow.get("silver", 0) > 0:
        raise TradeValidationError("卖家银库空间不足，暂时无法完成交易")


def send_market_message(**kwargs):
    return create_message(**kwargs)


def send_market_notification(user_id: int, payload: dict, *, log_context: str) -> None:
    notify_user(user_id, payload, log_context=log_context)


def grant_market_item_locked(locked_manor: Manor, *, item_key: str, quantity: int) -> None:
    add_item_to_inventory_locked(locked_manor, item_key, quantity)


def load_market_item_template(item_key: str):
    return load_tradeable_item_template(item_template_model=ItemTemplate, item_key=item_key)


def lock_market_listing_inventory_item(*, locked_manor: Manor, item_template: ItemTemplate):
    return lock_listing_inventory_item(
        inventory_item_model=InventoryItem,
        locked_manor=locked_manor,
        item_template=item_template,
    )


def decrement_market_listing_inventory(*, inventory_item, quantity: int) -> None:
    decrement_listing_inventory(
        inventory_item_model=InventoryItem,
        inventory_item=inventory_item,
        quantity=quantity,
    )


def get_tradeable_inventory_queryset(manor: Manor):
    return InventoryItem.objects.filter(
        manor=manor,
        template__tradeable=True,
        quantity__gt=0,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).select_related("template")


def get_listing_fee(duration: int) -> int:
    """
    获取上架手续费

    Args:
        duration: 上架时长（秒）

    Returns:
        手续费金额
    """
    return get_listing_fees().get(duration, 5000)


def validate_listing_price(item_template: ItemTemplate, unit_price: int) -> None:
    """
    验证定价是否合法

    Args:
        item_template: 物品模板
        unit_price: 单价

    Raises:
        TradeValidationError: 如果价格不合法
    """
    template_price = max(0, safe_int(getattr(item_template, "price", 0), 0))
    normalized_unit_price = safe_int(unit_price, -1)
    min_price = int(template_price * MIN_PRICE_MULTIPLIER)
    if normalized_unit_price < min_price:
        raise TradeValidationError(f"单价不能低于 {min_price} 银两")

    if normalized_unit_price > MAX_PRICE:
        raise TradeValidationError(f"单价不能超过 {MAX_PRICE:,} 银两")


def create_listing(
    manor: Manor,
    item_key: str,
    quantity: int,
    unit_price: int,
    duration: int,
) -> MarketListing:
    """
    创建交易行挂单（并发安全版本）

    使用数据库行锁和F()表达式确保并发安全：
    - 锁定Manor行防止银两透支
    - 锁定InventoryItem行防止物品超卖
    - 使用F()表达式进行原子性数值更新

    Args:
        manor: 卖家庄园
        item_key: 物品key
        quantity: 数量
        unit_price: 单价
        duration: 上架时长（秒）

    Returns:
        创建的挂单对象

    Raises:
        TradeValidationError: 验证失败时抛出异常
    """
    return _market_commands.create_market_listing(
        manor,
        item_key,
        quantity,
        unit_price,
        duration,
        normalize_listing_inputs=normalize_listing_inputs,
        listing_fees=get_listing_fees(),
        load_market_item_template=load_market_item_template,
        validate_listing_price=validate_listing_price,
        manor_model=Manor,
        get_listing_fee=get_listing_fee,
        charge_listing_fee=charge_listing_fee,
        lock_market_listing_inventory_item=lock_market_listing_inventory_item,
        get_frozen_gold_bars=get_frozen_gold_bars,
        validate_gold_bar_availability=validate_gold_bar_availability,
        validate_listing_inventory=validate_listing_inventory,
        decrement_market_listing_inventory=decrement_market_listing_inventory,
        validate_listing_total_price=validate_listing_total_price,
        create_listing_record=create_listing_record,
        market_listing_model=MarketListing,
        max_total_price=MAX_TOTAL_PRICE,
        safe_int=safe_int,
    )


def get_active_listings(
    item_template_id: int = None,
    order_by: str = "-listed_at",
    category: str = None,
    rarity: str = None,
) -> QuerySet:
    """
    获取在售挂单列表

    Args:
        item_template_id: 物品模板ID（可选）
        order_by: 排序字段
        category: 物品类别筛选
        rarity: 稀有度筛选

    Returns:
        挂单查询集
    """
    return _market_queries.get_active_listings_queryset(
        market_listing_model=MarketListing,
        now=timezone.now(),
        item_template_id=item_template_id,
        order_by=order_by,
        allowed_order_by=ALLOWED_LISTING_ORDER_BY,
        legacy_tool_effect_types=LEGACY_TOOL_EFFECT_TYPES,
        category=category,
        rarity=rarity,
    )


def _send_purchase_notifications(
    *,
    buyer: Manor,
    listing: MarketListing,
    tax_amount: int,
    seller_received: int,
) -> tuple[bool, bool]:
    return _send_purchase_notifications_entry(
        buyer=buyer,
        listing=listing,
        tax_amount=tax_amount,
        seller_received=seller_received,
        send_purchase_notifications=_market_notification_helpers.send_purchase_notifications,
        safe_send_market_message=_market_notification_helpers.safe_send_market_message,
        safe_send_market_notification=_market_notification_helpers.safe_send_market_notification,
        create_message_func=send_market_message,
        notify_user_func=send_market_notification,
        logger=logger,
    )


def purchase_listing(buyer: Manor, listing_id: int) -> MarketTransaction:
    """
    购买挂单物品（并发安全版本）

    使用数据库行锁和F()表达式确保并发安全：
    - 锁定MarketListing防止重复购买
    - 锁定买家和卖家Manor防止银两透支/覆盖
    - 锁定买家InventoryItem防止物品发放时的并发冲突
    - 使用F()表达式进行原子性数值更新

    锁定顺序：MarketListing -> 买家Manor -> 卖家Manor -> 买家InventoryItem
    这个顺序与上架路径不同，但通过Listing作为第一锁避免了循环等待

    Args:
        buyer: 买家庄园
        listing_id: 挂单ID

    Returns:
        交易记录对象

    Raises:
        ValueError: 验证失败时抛出异常
    """
    return _market_commands.purchase_market_listing(
        buyer,
        listing_id,
        market_listing_model=MarketListing,
        market_transaction_model=MarketTransaction,
        manor_model=Manor,
        get_locked_listing_for_purchase=_market_purchase_helpers.get_locked_listing_for_purchase,
        validate_listing_for_purchase=_market_purchase_helpers.validate_listing_for_purchase,
        lock_purchase_parties=_market_purchase_helpers.lock_purchase_parties,
        pay_market_purchase=pay_market_purchase,
        settle_market_sale_proceeds=settle_market_sale_proceeds,
        grant_listing_item_to_buyer_locked=_market_purchase_helpers.grant_listing_item_to_buyer_locked,
        grant_market_item_locked=grant_market_item_locked,
        transaction_tax_rate=TRANSACTION_TAX_RATE,
        send_purchase_notifications=_send_purchase_notifications,
    )


def cancel_listing(manor: Manor, listing_id: int) -> Dict:
    """
    取消挂单（仅限卖家本人）

    Args:
        manor: 卖家庄园
        listing_id: 挂单ID

    Returns:
        取消结果字典

    Raises:
        ValueError: 验证失败时抛出异常
    """
    return _market_commands.cancel_market_listing(
        manor,
        listing_id,
        manor_model=Manor,
        market_listing_model=MarketListing,
        restore_cancelled_listing_inventory=_market_notification_helpers.restore_cancelled_listing_inventory,
        build_cancel_listing_result=_market_notification_helpers.build_cancel_listing_result,
        grant_market_item_locked=grant_market_item_locked,
    )


def _expire_listings_queryset(expired_listings: QuerySet, log_label: str, limit: int | None = None) -> int:
    return _expire_listings_queryset_entry(
        expired_listings,
        log_label,
        expire_listings_queryset_impl=_expire_listings_queryset_impl,
        manor_model=Manor,
        market_listing_model=MarketListing,
        restore_cancelled_listing_inventory=_market_notification_helpers.restore_cancelled_listing_inventory,
        grant_market_item_locked=grant_market_item_locked,
        create_message_func=send_market_message,
        notify_user_func=send_market_notification,
        logger=logger,
        limit=limit,
    )


def expire_listings(limit: int = 1000) -> int:
    """
    处理过期挂单，通过邮件退回物品并删除挂单记录

    Args:
        limit: 单次最大处理数量（默认1000），防止定时任务超时

    Returns:
        处理的挂单数量
    """
    expired_listings = _market_queries.get_expired_listings_queryset(
        market_listing_model=MarketListing,
        now=timezone.now(),
    )
    return _expire_listings_queryset(expired_listings, "处理过期挂单", limit=limit)


def expire_user_listings(manor: Manor) -> int:
    """
    处理指定用户的过期挂单（用户访问交易行时主动触发）

    Args:
        manor: 庄园

    Returns:
        处理的挂单数量
    """
    expired_listings = _market_queries.get_user_expired_listings_queryset(
        market_listing_model=MarketListing,
        manor=manor,
        now=timezone.now(),
    )
    return _expire_listings_queryset(expired_listings, f"处理用户 {manor.id} 的过期挂单")


def get_my_listings(manor: Manor, status: str = None) -> QuerySet:
    """
    获取我的挂单

    Args:
        manor: 庄园
        status: 状态筛选（可选）

    Returns:
        挂单查询集
    """
    return _market_queries.get_my_listings_queryset(
        market_listing_model=MarketListing,
        manor=manor,
        status=status,
    )


def get_market_stats() -> Dict:
    """
    获取交易行统计信息

    Returns:
        统计信息字典
    """
    return _market_queries.get_market_stats_payload(
        market_listing_model=MarketListing,
        market_transaction_model=MarketTransaction,
        now=timezone.now(),
    )


def get_tradeable_inventory(manor: Manor) -> QuerySet:
    """
    获取可上架的物品列表

    IMPORTANT: Only show WAREHOUSE items since create_listing only operates on WAREHOUSE.

    Args:
        manor: 庄园

    Returns:
        可交易的库存物品查询集
    """
    return get_tradeable_inventory_queryset(manor)
