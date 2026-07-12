from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.exceptions import InsufficientStockError, TradeValidationError
from gameplay.services.inventory.core import consume_inventory_item_locked


def normalize_listing_inputs(quantity: int, unit_price: int, duration: int, *, safe_int) -> tuple[int, int, int]:
    return safe_int(quantity, 0), safe_int(unit_price, -1), safe_int(duration, -1)


def load_tradeable_item_template(*, item_template_model, item_key: str):
    item_template = item_template_model.objects.filter(key=item_key).first()
    if not item_template:
        raise TradeValidationError("物品不存在")
    if not item_template.tradeable:
        raise TradeValidationError("该物品不可交易")
    return item_template


def lock_listing_inventory_item(*, inventory_item_model, locked_manor, item_template):
    return (
        inventory_item_model.objects.select_for_update()
        .filter(
            manor=locked_manor,
            template=item_template,
            storage_location=inventory_item_model.StorageLocation.WAREHOUSE,
        )
        .first()
    )


def validate_listing_inventory(*, inventory_item, quantity: int) -> None:
    if not inventory_item or inventory_item.quantity < quantity:
        raise TradeValidationError("物品数量不足")


def validate_gold_bar_availability(*, inventory_item, quantity: int, frozen: int) -> None:
    available = inventory_item.quantity - frozen
    if available < quantity:
        raise TradeValidationError(f"可用金条不足（当前可用 {available} 个，{frozen} 个被拍卖冻结）")


def decrement_listing_inventory(*, inventory_item_model, inventory_item, quantity: int) -> None:
    del inventory_item_model
    try:
        consume_inventory_item_locked(inventory_item, quantity)
    except InsufficientStockError as exc:
        raise TradeValidationError("物品数量不足或已被其他操作占用") from exc


def validate_listing_total_price(*, unit_price: int, quantity: int, max_total_price: int) -> int:
    total_price = unit_price * quantity
    if total_price > max_total_price:
        raise TradeValidationError(f"总价不能超过 {max_total_price:,} 银两")
    return total_price


def create_listing_record(
    *,
    market_listing_model,
    locked_manor,
    item_template,
    quantity: int,
    unit_price: int,
    total_price: int,
    duration: int,
    listing_fee: int,
):
    expires_at = timezone.now() + timedelta(seconds=duration)
    return market_listing_model.objects.create(
        seller=locked_manor,
        item_template=item_template,
        quantity=quantity,
        unit_price=unit_price,
        total_price=total_price,
        duration=duration,
        listing_fee=listing_fee,
        expires_at=expires_at,
        status=market_listing_model.Status.ACTIVE,
    )
