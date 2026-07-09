"""Slot-level settlement helpers for auction rounds."""

from __future__ import annotations

import logging
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from gameplay.models import Manor
from gameplay.services.inventory.core import consume_inventory_item_for_manor_locked
from trade.models import AuctionBid, AuctionSlot, FrozenGoldBar
from trade.services.auction.constants import GOLD_BAR_ITEM_KEY
from trade.services.auction.gold_bars import (
    consume_frozen_gold_bars,
    require_frozen_record,
    try_get_frozen_record,
    unfreeze_gold_bars,
)


def refund_losing_bids_impl(losing_bids: list[AuctionBid]) -> None:
    for losing_bid in losing_bids:
        frozen_record = require_frozen_record(losing_bid, context="losing auction bid")
        if frozen_record.is_frozen:
            unfreeze_gold_bars(frozen_record)

        losing_bid.status = AuctionBid.Status.REFUNDED
        losing_bid.refunded_at = timezone.now()
        losing_bid.save(update_fields=["status", "refunded_at"])


def mark_slot_unsold_after_failure_impl(
    slot: AuctionSlot,
    *,
    refund_losing_bids_func: Callable[[list[AuctionBid]], None],
    logger: logging.Logger,
    database_exceptions: tuple[type[BaseException], ...],
) -> bool:
    try:
        with transaction.atomic():
            locked_slot = AuctionSlot.objects.select_for_update().get(pk=slot.pk)
            if locked_slot.status != AuctionSlot.Status.ACTIVE:
                return True

            active_bids = list(
                AuctionBid.objects.select_for_update().filter(slot=locked_slot, status=AuctionBid.Status.ACTIVE)
            )
            if active_bids:
                refund_losing_bids_func(active_bids)

            locked_slot.status = AuctionSlot.Status.UNSOLD
            locked_slot.save(update_fields=["status"])
            return True
    except database_exceptions as exc:
        logger.exception("failed to force slot %s unsold after settlement error: %s", slot.id, exc)
        return False


def consume_winning_bid_frozen_gold_bars_impl(
    winning_bid: AuctionBid,
    winner: Manor,
    settlement_price: int,
    *,
    safe_int_func: Callable[[object, int], int],
    partial_consume_func: Callable[[AuctionBid, Manor, int, int], None],
) -> None:
    frozen_record = try_get_frozen_record(winning_bid)
    if not frozen_record:
        raise RuntimeError(f"winning bid missing frozen record: bid_id={winning_bid.id}")
    if not frozen_record.is_frozen:
        raise RuntimeError(f"winning bid frozen record already unfrozen: bid_id={winning_bid.id}")

    frozen_amount = safe_int_func(getattr(frozen_record, "amount", 0), 0)
    if frozen_amount <= 0:
        raise RuntimeError(f"winning bid frozen amount invalid: bid_id={winning_bid.id} amount={frozen_amount}")
    if settlement_price <= 0:
        raise RuntimeError(
            f"winning bid settlement price invalid: bid_id={winning_bid.id} settlement_price={settlement_price}"
        )
    if frozen_amount < settlement_price:
        raise RuntimeError(
            f"winning bid frozen amount insufficient: bid_id={winning_bid.id} amount={frozen_amount} price={settlement_price}"
        )

    refund_amount = frozen_amount - settlement_price
    if refund_amount > 0:
        partial_consume_func(winning_bid, winner, settlement_price, refund_amount)
        return

    consume_frozen_gold_bars(frozen_record, winner)


def settle_slot_impl(
    slot: AuctionSlot,
    *,
    safe_int_func: Callable[[object, int], int],
    refund_losing_bids_func: Callable[[list[AuctionBid]], None],
    consume_winning_bid_frozen_gold_bars_func: Callable[[AuctionBid, Manor, int], None],
    create_winning_delivery_func: Callable[[AuctionSlot, AuctionBid, Manor, int, int], Any],
    process_winning_delivery_func: Callable[[int], None],
    get_slot_ranking_func: Callable[[AuctionSlot], list[AuctionBid]],
    logger: logging.Logger,
) -> dict[str, object]:
    """Settle a single auction slot under Vickrey pricing."""
    result: dict[str, object] = {"sold": False, "price": 0, "winner_count": 0}

    with transaction.atomic():
        slot = AuctionSlot.objects.select_for_update().get(pk=slot.pk)

        if slot.status != AuctionSlot.Status.ACTIVE:
            return {**result, "skipped": True}

        ranking = get_slot_ranking_func(slot)
        winner_count = safe_int_func(getattr(slot, "quantity", 0), 0)

        if not ranking:
            slot.status = AuctionSlot.Status.UNSOLD
            slot.save(update_fields=["status"])
            return result

        if winner_count <= 0:
            logger.error("拍卖位配置异常: slot_id=%s quantity=%s", slot.id, slot.quantity)
            refund_losing_bids_func(ranking)
            slot.status = AuctionSlot.Status.UNSOLD
            slot.save(update_fields=["status"])
            return result

        actual_winners = ranking[:winner_count]
        actual_winner_count = len(actual_winners)
        settlement_price = actual_winners[-1].amount

        for winning_bid in actual_winners:
            winner = winning_bid.manor

            consume_winning_bid_frozen_gold_bars_func(winning_bid, winner, settlement_price)

            winning_bid.status = AuctionBid.Status.WON
            winning_bid.save(update_fields=["status"])
            delivery = create_winning_delivery_func(
                slot,
                winning_bid,
                winner,
                settlement_price,
                actual_winner_count,
            )

            def _notify_winner(
                delivery_id: int = delivery.id,
            ) -> None:
                process_winning_delivery_func(delivery_id)

            transaction.on_commit(_notify_winner)

            current_price = safe_int_func(result["price"], 0)
            result["price"] = current_price + settlement_price

        result["sold"] = True
        result["winner_count"] = actual_winner_count

        refund_losing_bids_func(ranking[winner_count:])

        slot.status = AuctionSlot.Status.SOLD
        slot.save(update_fields=["status"])

    return result


def partial_consume_frozen_gold_bars_impl(
    bid: AuctionBid,
    manor: Manor,
    consume_amount: int,
    refund_amount: int,
    *,
    safe_int_func: Callable[[object, int], int],
    logger: logging.Logger,
) -> None:
    """Partially consume frozen gold bars for Vickrey settlement."""
    frozen_record = try_get_frozen_record(bid)
    if not frozen_record:
        raise RuntimeError(f"partial consume missing frozen record: bid_id={bid.id}")
    if not frozen_record.is_frozen:
        raise RuntimeError(f"partial consume frozen record already unfrozen: bid_id={bid.id}")

    with transaction.atomic():
        locked_record = FrozenGoldBar.objects.select_for_update().filter(pk=frozen_record.pk, is_frozen=True).first()
        if not locked_record:
            raise RuntimeError(f"partial consume lock failed for frozen record: bid_id={bid.id}")

        locked_amount = safe_int_func(getattr(locked_record, "amount", 0), 0)
        if locked_amount < consume_amount:
            raise RuntimeError(
                f"partial consume amount exceeds frozen amount: bid_id={bid.id} consume={consume_amount} amount={locked_amount}"
            )

        consume_inventory_item_for_manor_locked(
            manor,
            GOLD_BAR_ITEM_KEY,
            consume_amount,
            allow_frozen_gold_bars=True,
        )

        locked_record.is_frozen = False
        locked_record.unfrozen_at = timezone.now()
        locked_record.save(update_fields=["is_frozen", "unfrozen_at"])

    logger.info("维克里拍卖结算: 庄园 %s 实际扣除 %s 金条，退还 %s 金条", manor.id, consume_amount, refund_amount)
