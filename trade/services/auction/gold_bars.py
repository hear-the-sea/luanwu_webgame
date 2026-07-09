"""Gold bar freezing/unfreezing helpers for auction flows."""

from __future__ import annotations

from typing import Optional

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.exceptions import ItemInsufficientError, TradeValidationError
from gameplay.models import InventoryItem, Manor
from gameplay.services.inventory.core import (
    consume_inventory_item_for_manor_locked,
    consume_inventory_item_locked,
    get_item_quantity,
)
from trade.models import AuctionBid, FrozenGoldBar
from trade.services.auction.constants import GOLD_BAR_ITEM_KEY


def get_total_gold_bars(manor: Manor) -> int:
    """获取庄园持有的总金条数量"""
    return get_item_quantity(manor, GOLD_BAR_ITEM_KEY)


def get_frozen_gold_bars(manor: Manor) -> int:
    """获取庄园被冻结的金条数量"""
    result = FrozenGoldBar.objects.filter(manor=manor, is_frozen=True).aggregate(total=Sum("amount"))
    return int(result["total"] or 0)


def get_available_gold_bars(manor: Manor) -> int:
    """获取庄园可用的金条数量（总数 - 冻结数）"""
    total = get_total_gold_bars(manor)
    frozen = get_frozen_gold_bars(manor)
    return max(0, int(total) - int(frozen))


def consume_available_gold_bars_locked(manor: Manor, amount: int) -> None:
    """消耗未被冻结的仓库金条。调用方必须已经在事务中。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("consume_available_gold_bars_locked must be called inside transaction.atomic()")
    amount = int(amount or 0)
    if amount <= 0:
        return

    inventory_item = (
        InventoryItem.objects.select_for_update()
        .filter(
            manor=manor,
            template__key=GOLD_BAR_ITEM_KEY,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
        .first()
    )
    total = int(getattr(inventory_item, "quantity", 0) or 0)
    frozen = int(
        FrozenGoldBar.objects.filter(manor=manor, is_frozen=True).aggregate(total=Sum("amount")).get("total") or 0
    )
    available = max(0, total - frozen)
    item_name = getattr(getattr(inventory_item, "template", None), "name", "金条")
    if available < amount or inventory_item is None:
        raise ItemInsufficientError(item_name, amount, available)

    consume_inventory_item_locked(inventory_item, amount)


def freeze_gold_bars(manor: Manor, amount: int, bid: AuctionBid) -> FrozenGoldBar:
    """冻结金条用于拍卖出价。"""
    if amount <= 0:
        raise TradeValidationError("冻结数量必须大于0")

    inventory_item = (
        InventoryItem.objects.select_for_update()
        .filter(
            manor=manor,
            template__key=GOLD_BAR_ITEM_KEY,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
        .first()
    )
    total = int(getattr(inventory_item, "quantity", 0) or 0)
    frozen = int(
        FrozenGoldBar.objects.filter(manor=manor, is_frozen=True).aggregate(total=Sum("amount")).get("total") or 0
    )
    available = max(0, total - frozen)
    if available < amount:
        raise TradeValidationError(f"可用金条不足，当前可用 {available} 根，需要 {amount} 根")

    frozen_record = FrozenGoldBar.objects.create(
        manor=manor,
        amount=amount,
        reason=FrozenGoldBar.Reason.AUCTION_BID,
        auction_bid=bid,
        is_frozen=True,
    )

    bid.frozen_gold_bars = amount
    bid.save(update_fields=["frozen_gold_bars"])

    return frozen_record


def unfreeze_gold_bars(frozen_record: FrozenGoldBar) -> None:
    """解冻金条（落选时调用）"""
    with transaction.atomic():
        locked = (
            FrozenGoldBar.objects.select_for_update().select_related("auction_bid").filter(pk=frozen_record.pk).first()
        )
        if not locked or not locked.is_frozen:
            return

        locked.is_frozen = False
        locked.unfrozen_at = timezone.now()
        locked.save(update_fields=["is_frozen", "unfrozen_at"])

        if locked.auction_bid_id:
            bid = locked.auction_bid
            if bid:
                bid.status = AuctionBid.Status.REFUNDED
                bid.refunded_at = timezone.now()
                bid.save(update_fields=["status", "refunded_at"])


def consume_frozen_gold_bars(frozen_record: FrozenGoldBar, manor: Manor) -> None:
    """消耗冻结的金条（中标时调用）"""
    with transaction.atomic():
        locked = (
            FrozenGoldBar.objects.select_for_update().select_related("auction_bid").filter(pk=frozen_record.pk).first()
        )
        if not locked or not locked.is_frozen:
            return

        consume_inventory_item_for_manor_locked(
            manor,
            GOLD_BAR_ITEM_KEY,
            locked.amount,
            allow_frozen_gold_bars=True,
        )

        locked.is_frozen = False
        locked.unfrozen_at = timezone.now()
        locked.save(update_fields=["is_frozen", "unfrozen_at"])

        if locked.auction_bid_id:
            bid = locked.auction_bid
            if bid:
                bid.status = AuctionBid.Status.WON
                bid.save(update_fields=["status"])


def try_get_frozen_record(bid: AuctionBid) -> Optional[FrozenGoldBar]:
    """Best-effort access to the bid's frozen record."""
    try:
        return bid.frozen_record
    except FrozenGoldBar.DoesNotExist:
        return None


def require_frozen_record(bid: AuctionBid, *, context: str) -> FrozenGoldBar:
    """Strict access to the bid's frozen record for ledger-critical flows."""
    frozen_record = try_get_frozen_record(bid)
    if not frozen_record:
        raise RuntimeError(f"{context} missing frozen record: bid_id={bid.id}")
    return frozen_record
