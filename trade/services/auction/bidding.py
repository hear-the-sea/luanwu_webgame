"""Bidding logic (Vickrey auction) for auction service."""

from __future__ import annotations

import logging
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from core.exceptions import MessageError, TradeValidationError
from core.utils import safe_int
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.models import Manor
from gameplay.services.utils.messages import create_message
from gameplay.services.utils.notifications import notify_user
from trade.models import AuctionBid, AuctionRound, AuctionSlot
from trade.services.auction.gold_bars import freeze_gold_bars, require_frozen_record, unfreeze_gold_bars

logger = logging.getLogger(__name__)
AUCTION_OUTBID_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def _normalize_bid_amount(amount: int) -> int:
    normalized = safe_int(amount, default=0)
    if normalized <= 0:
        raise TradeValidationError("出价金额必须大于0")
    return normalized


def _safe_winner_count(slot: AuctionSlot) -> int:
    return max(1, safe_int(getattr(slot, "quantity", 1), 1))


def _require_valid_winner_count(slot: AuctionSlot) -> int:
    winner_count = safe_int(getattr(slot, "quantity", 0), 0)
    if winner_count <= 0:
        raise TradeValidationError("拍卖位配置异常，请联系管理员")
    return winner_count


def _safe_create_message(**kwargs) -> None:
    try:
        create_message(**kwargs)
    except AUCTION_OUTBID_MESSAGE_EXCEPTIONS as exc:
        logger.warning("auction outbid create_message failed: %s", exc, exc_info=True)


def _safe_notify_user(user_id: int, payload: dict, *, log_context: str) -> None:
    try:
        notify_user(user_id, payload, log_context=log_context)
    except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "auction outbid notify_user failed: user_id=%s error=%s",
            user_id,
            exc,
            exc_info=True,
        )


def _validate_bid_raise_or_increment(slot: AuctionSlot, amount: int, current_amount: int) -> None:
    if amount <= current_amount:
        raise TradeValidationError(f"加价金额必须高于您之前的出价 {current_amount} 金条")
    if amount < current_amount + slot.min_increment:
        raise TradeValidationError(f"加价幅度至少为 {slot.min_increment} 金条")


def _get_player_active_bid(ranking: list[AuctionBid], manor: Manor) -> AuctionBid | None:
    return next((bid for bid in ranking if bid.manor_id == manor.id), None)


def _load_locked_slot(slot_id: int) -> AuctionSlot:
    slot = AuctionSlot.objects.select_for_update().select_related("round", "item_template").filter(id=slot_id).first()
    if not slot:
        raise TradeValidationError("拍卖位不存在")
    return slot


def _validate_slot_active(slot: AuctionSlot) -> None:
    if slot.status != AuctionSlot.Status.ACTIVE:
        raise TradeValidationError("该拍卖位已结束")
    if slot.round.status != AuctionRound.Status.ACTIVE:
        raise TradeValidationError("该拍卖轮次已结束")
    if slot.round.end_at <= timezone.now():
        raise TradeValidationError("拍卖时间已结束")


def _load_previous_active_bid(slot: AuctionSlot, manor: Manor) -> AuctionBid | None:
    return (
        AuctionBid.objects.select_for_update().filter(slot=slot, manor=manor, status=AuctionBid.Status.ACTIVE).first()
    )


def _validate_bid_submission(
    slot: AuctionSlot,
    amount: int,
    ranking_before: list[AuctionBid],
    previous_bid: AuctionBid | None,
) -> None:
    if previous_bid:
        validate_bid_amount(slot, amount, current_bid=previous_bid)
        return
    validate_bid_amount(slot, amount, ranking=ranking_before)


def _replace_previous_bid(previous_bid: AuctionBid | None) -> None:
    if not previous_bid:
        return

    frozen_record = require_frozen_record(previous_bid, context="previous auction bid")
    if frozen_record.is_frozen:
        unfreeze_gold_bars(frozen_record)

    previous_bid.status = AuctionBid.Status.OUTBID
    previous_bid.save(update_fields=["status"])


def _pick_candidate_to_kick(ranking_before: list[AuctionBid], winner_count: int) -> AuctionBid | None:
    if len(ranking_before) < winner_count:
        return None
    return ranking_before[winner_count - 1]


def _is_bid_in_top_n(bid_id: int, ranking_after: list[AuctionBid], winner_count: int) -> bool:
    return any(bid.id == bid_id for bid in ranking_after[:winner_count])


def _kick_out_player_if_needed(
    player_to_kick: AuctionBid | None,
    bidder_manor: Manor,
    ranking_after: list[AuctionBid],
    winner_count: int,
) -> Manor | None:
    if not player_to_kick or player_to_kick.manor_id == bidder_manor.id:
        return None

    if _is_bid_in_top_n(player_to_kick.id, ranking_after, winner_count):
        return None

    outbid_player = player_to_kick.manor
    player_to_kick.status = AuctionBid.Status.OUTBID
    player_to_kick.save(update_fields=["status"])
    frozen_record = require_frozen_record(player_to_kick, context="outbid auction bid")
    if frozen_record.is_frozen:
        unfreeze_gold_bars(frozen_record)
    return outbid_player


def _update_slot_snapshot(
    slot: AuctionSlot, ranking_after: list[AuctionBid], winner_count: int, bidder_manor: Manor
) -> None:
    if len(ranking_after) >= winner_count:
        slot.current_price = ranking_after[winner_count - 1].amount
    elif ranking_after:
        slot.current_price = ranking_after[-1].amount
    else:
        slot.current_price = slot.starting_price

    slot.highest_bidder = ranking_after[0].manor if ranking_after else bidder_manor
    slot.save(update_fields=["current_price", "highest_bidder", "bid_count"])


def get_slot_ranking(slot: AuctionSlot) -> List[AuctionBid]:
    """获取拍卖位的出价排名（按金额从高到低）"""
    return list(
        AuctionBid.objects.filter(
            slot=slot,
            status=AuctionBid.Status.ACTIVE,
        )
        .select_related("manor")
        .order_by("-amount", "created_at")  # 金额相同时，先出价者排前面
    )


def get_cutoff_price(slot: AuctionSlot, ranking: Optional[List[AuctionBid]] = None) -> int:
    """获取当前最低中标价（第N名的出价）。"""
    if ranking is None:
        ranking = get_slot_ranking(slot)
    winner_count = _safe_winner_count(slot)

    if len(ranking) >= winner_count:
        return ranking[winner_count - 1].amount
    if ranking:
        return ranking[-1].amount
    return slot.starting_price


def get_my_rank(slot: AuctionSlot, manor: Manor) -> Optional[int]:
    """获取我在某拍卖位的当前排名（1-based）。"""
    ranking = get_slot_ranking(slot)
    for i, bid in enumerate(ranking, start=1):
        if bid.manor_id == manor.id:
            return i
    return None


def is_in_winning_range(slot: AuctionSlot, manor: Manor) -> bool:
    """判断某庄园是否在中标范围内（前N名）。"""
    rank = get_my_rank(slot, manor)
    if rank is None:
        return False
    return rank <= slot.quantity


def validate_bid_amount(
    slot: AuctionSlot,
    amount: int,
    manor: Manor = None,
    ranking: Optional[List[AuctionBid]] = None,
    current_bid: Optional[AuctionBid] = None,
) -> None:
    """验证出价金额是否合法。"""
    amount = _normalize_bid_amount(amount)

    # When there are no active bids, treat it as the first bid regardless of
    # `slot.bid_count` (which is a historical counter).
    if ranking is None:
        ranking = get_slot_ranking(slot)
    if not ranking and not current_bid:
        min_bid = slot.starting_price
        if amount < min_bid:
            raise TradeValidationError(f"出价金额不得低于起拍价 {min_bid} 金条")
        return

    # 如果已有出价，检查是否为加价
    if current_bid:
        _validate_bid_raise_or_increment(slot, amount, current_bid.amount)
        return

    if manor:
        my_bid = _get_player_active_bid(ranking, manor)
        if my_bid is not None:
            _validate_bid_raise_or_increment(slot, amount, my_bid.amount)
            return

    # 新出价者，需要高于当前最低中标价才有意义
    cutoff = get_cutoff_price(slot, ranking=ranking)
    winner_count = _safe_winner_count(slot)

    if len(ranking) >= winner_count and amount <= cutoff:
        raise TradeValidationError(f"出价金额需要高于当前最低中标价 {cutoff} 金条才能进入前 {winner_count} 名")


def place_bid(
    manor: Manor,
    slot_id: int,
    amount: int,
    *,
    notify_outbid_func=None,
) -> tuple[AuctionBid, bool]:
    """玩家出价（维克里拍卖）。

    Args:
        notify_outbid_func: Optional callback for outbid notifications.
            Defaults to internal `_notify_outbid_vickrey`.
    """
    outbid_player = None  # 被挤出前N名的玩家
    outbid_cutoff_price: int | None = None
    winner_count = 0
    slot: AuctionSlot | None = None
    amount = _normalize_bid_amount(amount)

    with transaction.atomic():
        slot = _load_locked_slot(slot_id)
        _validate_slot_active(slot)

        previous_bid = _load_previous_active_bid(slot, manor)

        # 安全修复：统一在事务开始时获取一次排名，避免 TOCTOU 问题和变量未定义
        ranking_before = get_slot_ranking(slot)

        _validate_bid_submission(slot, amount, ranking_before, previous_bid)
        _replace_previous_bid(previous_bid)

        is_first_bid = previous_bid is None

        winner_count = _require_valid_winner_count(slot)

        player_to_kick = _pick_candidate_to_kick(ranking_before, winner_count)

        new_bid = AuctionBid.objects.create(
            slot=slot,
            manor=manor,
            amount=amount,
            status=AuctionBid.Status.ACTIVE,
        )

        freeze_gold_bars(manor, amount, new_bid)

        slot.bid_count += 1

        ranking_after = get_slot_ranking(slot)
        outbid_player = _kick_out_player_if_needed(player_to_kick, manor, ranking_after, winner_count)
        if outbid_player is not None:
            outbid_cutoff_price = get_cutoff_price(slot, ranking=ranking_after)
        _update_slot_snapshot(slot, ranking_after, winner_count, manor)

    if outbid_player and slot is not None and outbid_cutoff_price is not None:
        (notify_outbid_func or _notify_outbid_vickrey)(outbid_player, slot, outbid_cutoff_price, manor, winner_count)

    return new_bid, is_first_bid


def _notify_outbid_vickrey(
    manor: Manor, slot: AuctionSlot, cutoff_price: int, new_bidder: Manor, winner_count: int
) -> None:
    """通知玩家被挤出中标范围（维克里拍卖）。"""
    _safe_create_message(
        manor=manor,
        kind="system",
        title="【拍卖行】您已被挤出中标范围",
        body=(
            f"在 {slot.item_template.name} 的拍卖中，您已被挤出前 {winner_count} 名！\n\n"
            f"当前最低中标价：{cutoff_price} 金条\n\n"
            f"您冻结的金条已自动退还，如需继续竞拍请尽快加价。"
        ),
    )

    _safe_notify_user(
        manor.user_id,
        {
            "kind": "auction_outbid",
            "title": "【拍卖行】您已被挤出中标范围",
            "item_name": slot.item_template.name,
            "item_key": slot.item_template.key,
            "new_price": cutoff_price,
            "winner_count": winner_count,
        },
        log_context="auction outbid notification",
    )
