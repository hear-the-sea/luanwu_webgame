"""Persistent auction reward delivery outbox."""

from __future__ import annotations

import logging
from typing import Any, Callable

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.exceptions import MessageError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS, combine_infrastructure_exceptions
from gameplay.models import ItemTemplate, Manor, Message
from gameplay.services.utils.messages import create_message
from gameplay.services.utils.notifications import notify_user
from trade.models import AuctionBid, AuctionDelivery, AuctionSlot
from trade.services.auction.rounds_delivery_support import grant_auction_item_directly_impl

logger = logging.getLogger(__name__)

AUCTION_MESSAGE_DELIVERY_EXCEPTIONS = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def create_auction_delivery(
    *,
    slot: AuctionSlot,
    bid: AuctionBid,
    manor: Manor,
    settlement_price: int,
    total_winners: int,
    quantity: int = 1,
) -> AuctionDelivery:
    """Create or return the idempotent delivery record for a winning auction bid."""
    delivery, _created = AuctionDelivery.objects.get_or_create(
        bid=bid,
        defaults={
            "slot": slot,
            "manor": manor,
            "item_template": slot.item_template,
            "quantity": int(quantity),
            "settlement_price": int(settlement_price),
            "total_winners": int(total_winners),
        },
    )
    return delivery


def _safe_notify_user(user_id: int, payload: dict) -> None:
    notify_user(user_id, payload, log_context="auction won notification")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return int(default)


def _grant_item_directly(manor: Manor, item_template: ItemTemplate, quantity: int) -> None:
    grant_auction_item_directly_impl(manor, item_template, quantity, safe_int_func=_safe_int)


def process_auction_delivery(
    delivery_id: int,
    *,
    create_message_func: Callable[..., Message] = create_message,
    grant_item_directly_func: Callable[[Manor, ItemTemplate, int], None] = _grant_item_directly,
    safe_notify_user_func: Callable[[int, dict], None] = _safe_notify_user,
    message_delivery_exceptions: tuple[type[BaseException], ...] = AUCTION_MESSAGE_DELIVERY_EXCEPTIONS,
) -> bool:
    """Process one pending delivery. Returns True only when this call delivered it."""
    with transaction.atomic():
        delivery = (
            AuctionDelivery.objects.select_for_update()
            .select_related("slot__round", "item_template", "manor")
            .filter(pk=delivery_id)
            .first()
        )
        if not delivery or delivery.status == AuctionDelivery.Status.DELIVERED:
            return False

        AuctionDelivery.objects.filter(pk=delivery.pk).update(attempts=F("attempts") + 1, updated_at=timezone.now())
        delivery.attempts += 1

        delivery_method = AuctionDelivery.Method.MESSAGE_ATTACHMENT
        message: Message | None = None
        try:
            message = create_message_func(
                manor=delivery.manor,
                kind="reward",
                title="【拍卖行】恭喜您成功拍得物品",
                body=(
                    f"恭喜！您成功拍得 {delivery.item_template.name} x{delivery.quantity}！\n\n"
                    f"拍卖详情：\n"
                    f"- 物品：{delivery.item_template.name}\n"
                    f"- 数量：{delivery.quantity}\n"
                    f"- 结算价：{delivery.settlement_price} 金条（统一结算价）\n"
                    f"- 中标人数：{delivery.total_winners}\n"
                    f"- 拍卖轮次：第{delivery.slot.round.round_number}轮\n\n"
                    f"物品已通过附件发放，请查收。"
                ),
                attachments={
                    "items": {delivery.item_template.key: delivery.quantity},
                },
            )
        except message_delivery_exceptions as exc:
            delivery_method = AuctionDelivery.Method.DIRECT_INVENTORY
            logger.exception(
                "auction winning message create failed, fallback to direct inventory grant: "
                "delivery_id=%s slot_id=%s manor_id=%s error=%s",
                delivery.id,
                delivery.slot_id,
                delivery.manor_id,
                exc,
            )
            grant_item_directly_func(delivery.manor, delivery.item_template, delivery.quantity)

        safe_notify_user_func(
            delivery.manor.user_id,
            {
                "kind": "auction_won",
                "title": "【拍卖行】恭喜您成功拍得物品",
                "item_name": delivery.item_template.name,
                "item_key": delivery.item_template.key,
                "quantity": delivery.quantity,
                "price": delivery.settlement_price,
                "total_winners": delivery.total_winners,
                "delivery": delivery_method,
            },
        )

        delivery.status = AuctionDelivery.Status.DELIVERED
        delivery.delivery_method = delivery_method
        delivery.message = message
        delivery.last_error = ""
        delivery.delivered_at = timezone.now()
        delivery.save(
            update_fields=[
                "status",
                "delivery_method",
                "message",
                "last_error",
                "delivered_at",
                "updated_at",
            ]
        )
        return True


def process_pending_auction_deliveries(*, limit: int = 100) -> int:
    """Process pending auction deliveries for recovery jobs or tests."""
    delivery_ids = list(
        AuctionDelivery.objects.filter(status=AuctionDelivery.Status.PENDING)
        .order_by("created_at")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    delivered_count = 0
    for delivery_id in delivery_ids:
        if process_auction_delivery(delivery_id):
            delivered_count += 1
    return delivered_count
