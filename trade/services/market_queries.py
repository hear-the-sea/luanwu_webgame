from __future__ import annotations

from typing import Any, Collection


def get_active_listings_queryset(
    *,
    market_listing_model: Any,
    now: Any,
    order_by: str,
    allowed_order_by: set[str],
    legacy_tool_effect_types: Collection[str],
    item_template_id: int | None = None,
    category: str | None = None,
    rarity: str | None = None,
):
    queryset = market_listing_model.objects.filter(
        status=market_listing_model.Status.ACTIVE,
        expires_at__gt=now,
    ).select_related("seller__user", "item_template")

    if item_template_id:
        queryset = queryset.filter(item_template_id=item_template_id)

    if category and category != "all":
        if category in legacy_tool_effect_types:
            queryset = queryset.filter(item_template__effect_type__in=legacy_tool_effect_types)
        else:
            queryset = queryset.filter(item_template__effect_type=category)

    if rarity and rarity != "all":
        queryset = queryset.filter(item_template__rarity=rarity)

    safe_order_by = order_by if order_by in allowed_order_by else "-listed_at"
    # Keep pagination deterministic when multiple listings share the same sort timestamp/value.
    return queryset.order_by(safe_order_by, "-id")


def get_user_expired_listings_queryset(*, market_listing_model: Any, manor: Any, now: Any):
    return market_listing_model.objects.filter(
        seller=manor,
        status=market_listing_model.Status.ACTIVE,
        expires_at__lte=now,
    )


def get_expired_listings_queryset(*, market_listing_model: Any, now: Any):
    return market_listing_model.objects.filter(
        status=market_listing_model.Status.ACTIVE,
        expires_at__lte=now,
    )


def get_my_listings_queryset(*, market_listing_model: Any, manor: Any, status: str | None = None):
    queryset = market_listing_model.objects.filter(seller=manor).select_related("item_template", "buyer__user")

    if status and status != "all":
        queryset = queryset.filter(status=status)

    return queryset.order_by("-listed_at", "-id")


def get_market_stats_payload(*, market_listing_model: Any, market_transaction_model: Any, now: Any) -> dict[str, int]:
    active_count = market_listing_model.objects.filter(status=market_listing_model.Status.ACTIVE).count()
    sold_today = market_transaction_model.objects.filter(transaction_at__date=now.date()).count()
    return {
        "active_count": active_count,
        "sold_today": sold_today,
    }
