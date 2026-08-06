from __future__ import annotations

from typing import Any, Callable


def send_purchase_notifications_entry(
    *,
    buyer: Any,
    listing: Any,
    tax_amount: int,
    seller_received: int,
    send_purchase_notifications: Callable[..., tuple[bool, bool]],
    safe_send_market_message: Callable[..., bool],
    safe_send_market_notification: Callable[..., None],
    create_message_func: Callable[..., Any],
    notify_user_func: Callable[..., Any],
    logger: Any,
) -> tuple[bool, bool]:
    return send_purchase_notifications(
        buyer=buyer,
        listing=listing,
        tax_amount=tax_amount,
        seller_received=seller_received,
        safe_create_message=lambda **kwargs: safe_send_market_message(
            create_message_func=create_message_func,
            logger=logger,
            log_message="market create_message failed",
            **kwargs,
        ),
        safe_notify_user=lambda user_id, payload, *, log_context: safe_send_market_notification(
            notify_user_func=notify_user_func,
            logger=logger,
            user_id=user_id,
            payload=payload,
            log_context=log_context,
            log_message="market notify_user failed",
        ),
    )


def expire_listings_queryset_entry(
    expired_listings,
    log_label: str,
    *,
    expire_listings_queryset_impl: Callable[..., int],
    manor_model: Any,
    market_listing_model: Any,
    restore_cancelled_listing_inventory: Callable[..., None],
    grant_market_item_locked: Callable[..., None],
    create_message_func: Callable[..., Any],
    notify_user_func: Callable[..., Any],
    logger: Any,
    schedule_market_stats_cache_invalidation: Callable[[], None],
    limit: int | None = None,
) -> int:
    return expire_listings_queryset_impl(
        expired_listings,
        log_label,
        manor_model=manor_model,
        market_listing_model=market_listing_model,
        return_inventory_func=lambda *, manor, listing: restore_cancelled_listing_inventory(
            manor=manor,
            listing=listing,
            grant_item_locked=grant_market_item_locked,
        ),
        create_message_func=create_message_func,
        notify_user_func=notify_user_func,
        logger=logger,
        limit=limit,
        schedule_market_stats_cache_invalidation=schedule_market_stats_cache_invalidation,
    )
