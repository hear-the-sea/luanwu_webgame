"""Delivery helpers for auction round settlement."""

from __future__ import annotations

import logging
from typing import Callable

from django.db import transaction

from gameplay.models import ItemTemplate, Manor
from gameplay.services.inventory.core import add_item_to_inventory_locked
from trade.models import AuctionSlot


def send_winning_notification_vickrey_impl(
    slot: AuctionSlot,
    winner: Manor,
    settlement_price: int,
    total_winners: int,
    *,
    create_message_func: Callable[..., object],
    grant_item_directly_func: Callable[[Manor, ItemTemplate, int], None],
    safe_notify_user_func: Callable[[int, dict], None],
    logger: logging.Logger,
    message_delivery_exceptions: tuple[type[BaseException], ...],
) -> None:
    """Deliver auction rewards and emit realtime notifications."""
    delivery_via_message = True
    try:
        create_message_func(
            manor=winner,
            kind="reward",
            title="【拍卖行】恭喜您成功拍得物品",
            body=(
                f"恭喜！您成功拍得 {slot.item_template.name} ×1！\n\n"
                f"拍卖详情：\n"
                f"- 物品：{slot.item_template.name}\n"
                f"- 数量：1\n"
                f"- 结算价：{settlement_price} 金条（统一结算价）\n"
                f"- 中标人数：{total_winners}\n"
                f"- 拍卖轮次：第{slot.round.round_number}轮\n\n"
                f"物品已通过附件发放，请查收。"
            ),
            attachments={
                "items": {slot.item_template.key: 1},
            },
        )
    except message_delivery_exceptions as exc:
        delivery_via_message = False
        logger.exception(
            "auction winning message create failed, fallback to direct inventory grant: slot_id=%s manor_id=%s error=%s",
            slot.id,
            winner.id,
            exc,
        )
        grant_item_directly_func(winner, slot.item_template, 1)

    safe_notify_user_func(
        winner.user_id,
        {
            "kind": "auction_won",
            "title": "【拍卖行】恭喜您成功拍得物品",
            "item_name": slot.item_template.name,
            "item_key": slot.item_template.key,
            "quantity": 1,
            "price": settlement_price,
            "total_winners": total_winners,
            "delivery": "message_attachment" if delivery_via_message else "direct_inventory",
        },
    )


def grant_auction_item_directly_impl(
    manor: Manor,
    item_template: ItemTemplate,
    quantity: int,
    *,
    safe_int_func: Callable[[object, int], int],
) -> None:
    """Fallback path when reward message creation fails."""
    quantity = safe_int_func(quantity, 0)
    if quantity <= 0:
        return

    with transaction.atomic():
        add_item_to_inventory_locked(manor, item_template.key, quantity)
