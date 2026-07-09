"""Auction round creation and settlement logic."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from django.core.cache import cache

from core.exceptions import MessageError
from core.utils import safe_int
from core.utils.infrastructure import (
    CACHE_INFRASTRUCTURE_EXCEPTIONS,
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.models import ItemTemplate, Manor
from gameplay.services.utils.messages import create_message
from gameplay.services.utils.notifications import notify_user
from trade.models import AuctionBid, AuctionDelivery, AuctionRound, AuctionSlot
from trade.services.auction.bidding import get_slot_ranking
from trade.services.auction.delivery_outbox import create_auction_delivery, process_auction_delivery
from trade.services.auction.rounds_delivery_support import grant_auction_item_directly_impl
from trade.services.auction.rounds_lifecycle_support import create_auction_round_impl, settle_auction_round_impl
from trade.services.auction.rounds_settlement_support import (
    consume_winning_bid_frozen_gold_bars_impl,
    mark_slot_unsold_after_failure_impl,
    partial_consume_frozen_gold_bars_impl,
    refund_losing_bids_impl,
    settle_slot_impl,
)
from trade.services.auction_config import (
    AuctionItemConfig,
    AuctionSettings,
    get_auction_settings,
    get_enabled_auction_items,
)
from trade.services.cache_resilience import best_effort_cache_add, best_effort_cache_delete, best_effort_cache_get

logger = logging.getLogger(__name__)


AUCTION_INFRASTRUCTURE_EXCEPTIONS: InfrastructureExceptions = CACHE_INFRASTRUCTURE_EXCEPTIONS
AUCTION_CACHE_COMPONENT = "auction_cache"
AUCTION_MESSAGE_DELIVERY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def _safe_cache_add(key: str, value: Any, timeout: int) -> bool:
    return best_effort_cache_add(
        cache,
        key,
        value,
        timeout,
        logger=logger,
        component=AUCTION_CACHE_COMPONENT,
        infrastructure_exceptions=AUCTION_INFRASTRUCTURE_EXCEPTIONS,
    )


def _safe_cache_get(key: str, default: Any = None) -> Any:
    return best_effort_cache_get(
        cache,
        key,
        default,
        logger=logger,
        component=AUCTION_CACHE_COMPONENT,
        infrastructure_exceptions=AUCTION_INFRASTRUCTURE_EXCEPTIONS,
    )


def _safe_cache_delete(key: str) -> None:
    best_effort_cache_delete(
        cache,
        key,
        logger=logger,
        component=AUCTION_CACHE_COMPONENT,
        infrastructure_exceptions=AUCTION_INFRASTRUCTURE_EXCEPTIONS,
    )


def _safe_notify_user(user_id: int, payload: dict, *, log_context: str) -> None:
    try:
        notify_user(user_id, payload, log_context=log_context)
    except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "auction winning notify_user failed: user_id=%s error=%s",
            user_id,
            exc,
            exc_info=True,
        )


def get_current_round() -> Optional[AuctionRound]:
    """获取当前进行中的拍卖轮次。"""
    return AuctionRound.objects.filter(status=AuctionRound.Status.ACTIVE).first()


def get_next_round_number() -> int:
    """获取下一轮次编号。"""
    last_round = AuctionRound.objects.order_by("-round_number").first()
    return (last_round.round_number + 1) if last_round else 1


def create_auction_round(
    *,
    get_settings_func: Callable[[], AuctionSettings] | None = None,
    get_enabled_items_func: Callable[[], List[AuctionItemConfig]] | None = None,
) -> Optional[AuctionRound]:
    """创建新的拍卖轮次（若已有进行中则跳过）。"""
    return create_auction_round_impl(
        safe_cache_add_func=_safe_cache_add,
        safe_cache_get_func=_safe_cache_get,
        safe_cache_delete_func=_safe_cache_delete,
        logger=logger,
        get_settings_func=get_settings_func or get_auction_settings,
        get_enabled_items_func=get_enabled_items_func or get_enabled_auction_items,
    )


def settle_auction_round(
    round_id: int = None,
    *,
    settle_slot_func: Callable[[AuctionSlot], Dict] | None = None,
) -> Dict[str, Any]:
    """结算拍卖轮次。"""
    return settle_auction_round_impl(
        round_id=round_id,
        settle_slot_func=settle_slot_func or _settle_slot,
        mark_slot_unsold_after_failure_func=_mark_slot_unsold_after_failure,
        safe_cache_add_func=_safe_cache_add,
        safe_cache_get_func=_safe_cache_get,
        safe_cache_delete_func=_safe_cache_delete,
        safe_int_func=safe_int,
        logger=logger,
        database_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    )


def _refund_losing_bids(losing_bids: list[AuctionBid]) -> None:
    refund_losing_bids_impl(losing_bids)


def _mark_slot_unsold_after_failure(slot: AuctionSlot) -> bool:
    return mark_slot_unsold_after_failure_impl(
        slot,
        refund_losing_bids_func=_refund_losing_bids,
        logger=logger,
        database_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    )


def _consume_winning_bid_frozen_gold_bars(winning_bid: AuctionBid, winner: Manor, settlement_price: int) -> None:
    consume_winning_bid_frozen_gold_bars_impl(
        winning_bid,
        winner,
        settlement_price,
        safe_int_func=safe_int,
        partial_consume_func=_partial_consume_frozen_gold_bars,
    )


def _settle_slot(slot: AuctionSlot) -> Dict:
    """结算单个拍卖位（维克里拍卖）。"""
    return settle_slot_impl(
        slot,
        safe_int_func=safe_int,
        refund_losing_bids_func=_refund_losing_bids,
        consume_winning_bid_frozen_gold_bars_func=_consume_winning_bid_frozen_gold_bars,
        create_winning_delivery_func=_create_winning_delivery,
        process_winning_delivery_func=_process_winning_delivery,
        get_slot_ranking_func=get_slot_ranking,
        logger=logger,
    )


def _partial_consume_frozen_gold_bars(bid: AuctionBid, manor: Manor, consume_amount: int, refund_amount: int) -> None:
    """部分消耗冻结金条（用于维克里拍卖，出价高于结算价的情况）。"""
    partial_consume_frozen_gold_bars_impl(
        bid,
        manor,
        consume_amount,
        refund_amount,
        safe_int_func=safe_int,
        logger=logger,
    )


def _create_winning_delivery(
    slot: AuctionSlot,
    winning_bid: AuctionBid,
    winner: Manor,
    settlement_price: int,
    total_winners: int,
) -> AuctionDelivery:
    return create_auction_delivery(
        slot=slot,
        bid=winning_bid,
        manor=winner,
        settlement_price=settlement_price,
        total_winners=total_winners,
    )


def _process_winning_delivery(delivery_id: int) -> None:
    """发送中标通知并发放物品（维克里拍卖，每人1个）。"""
    process_auction_delivery(
        delivery_id,
        create_message_func=create_message,
        grant_item_directly_func=_grant_auction_item_directly,
        safe_notify_user_func=lambda user_id, payload: _safe_notify_user(
            user_id,
            payload,
            log_context="auction won notification",
        ),
        message_delivery_exceptions=AUCTION_MESSAGE_DELIVERY_EXCEPTIONS,
    )


def _grant_auction_item_directly(manor: Manor, item_template: ItemTemplate, quantity: int) -> None:
    """Fallback path when reward message creation fails."""
    grant_auction_item_directly_impl(manor, item_template, quantity, safe_int_func=safe_int)
