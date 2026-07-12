from __future__ import annotations

from typing import Callable

from django.db import DatabaseError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from core.exceptions import TradeValidationError

from . import market_notification_helpers as _market_notification_helpers


def normalize_expire_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        raise TradeValidationError("limit 必须是整数")
    if parsed <= 0:
        return 0
    return parsed


def expire_listings_queryset(
    expired_listings: QuerySet,
    log_label: str,
    *,
    manor_model,
    market_listing_model,
    return_inventory_func: Callable[..., object],
    create_message_func: Callable[..., object],
    notify_user_func: Callable[..., object],
    logger,
    limit: int | None = None,
) -> int:
    normalized_limit = normalize_expire_limit(limit)
    if normalized_limit == 0:
        return 0

    candidates = expired_listings.filter(
        status=market_listing_model.Status.ACTIVE,
        expires_at__lte=timezone.now(),
    ).order_by("expires_at")
    candidate_ids = candidates.values_list("pk", flat=True)
    if normalized_limit:
        candidate_ids = candidate_ids[:normalized_limit]

    count = 0
    for listing_id in candidate_ids:
        try:
            seller = None
            message_payload = None
            notify_payload = None
            with transaction.atomic():
                listing = (
                    market_listing_model.objects.select_for_update(skip_locked=True)
                    .filter(
                        pk=listing_id,
                        status=market_listing_model.Status.ACTIVE,
                        expires_at__lte=timezone.now(),
                    )
                    .first()
                )

                if not listing:
                    continue

                seller = manor_model.objects.select_for_update().get(pk=listing.seller_id)
                item_template = listing.item_template
                item_name = item_template.name
                item_key = item_template.key
                quantity = listing.quantity
                unit_price = listing.unit_price
                listing_fee = listing.listing_fee
                listed_at = listing.listed_at
                expires_at = listing.expires_at

                listing.status = market_listing_model.Status.EXPIRED
                listing.save(update_fields=["status"])

                return_inventory_func(manor=seller, listing=listing)

                message_payload = {
                    "manor": seller,
                    "kind": "system",
                    "title": "【交易过期】您的物品已退回",
                    "body": (
                        f"您上架的 {item_name} x{quantity} 已过期，物品已直接退回仓库。\n\n"
                        f"挂单信息：\n"
                        f"- 物品：{item_name}\n"
                        f"- 数量：{quantity}\n"
                        f"- 定价：{unit_price:,} 银两/件\n"
                        f"- 上架时间：{listed_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"- 过期时间：{expires_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"注意：手续费 {listing_fee:,} 银两不予退还。"
                    ),
                }
                notify_payload = {
                    "kind": "market_expired",
                    "title": "【交易过期】您的物品已退回",
                    "item_name": item_name,
                    "item_key": item_key,
                    "quantity": quantity,
                }

                listing.delete()
                count += 1

            _market_notification_helpers.safe_send_market_message(
                create_message_func=create_message_func,
                logger=logger,
                log_message=(
                    f"market create_message failed: listing_id={listing_id} " f"seller_id={getattr(seller, 'id', None)}"
                ),
                **message_payload,
            )

            _market_notification_helpers.safe_send_market_notification(
                notify_user_func=notify_user_func,
                logger=logger,
                user_id=seller.user_id,
                payload=notify_payload,
                log_context="market expired notification",
                log_message="market notify_user failed",
            )
        except DatabaseError as exc:
            logger.exception("%s %s 时数据库操作出错: %s", log_label, listing_id, exc)
            continue

    return count
