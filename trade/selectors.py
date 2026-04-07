from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import trade.services.market_service as market_service
from gameplay.services.manor.troop_bank import (
    get_troop_bank_capacity,
    get_troop_bank_remaining_space,
    get_troop_bank_rows,
    get_troop_bank_used_space,
)
from trade.bank_context_builder import build_bank_trade_context
from trade.selector_builders import build_auction_trade_context, build_market_trade_context, build_shop_trade_context
from trade.services.auction_service import get_active_slots, get_auction_stats, get_my_bids, get_my_leading_bids
from trade.services.bank_service import get_bank_info
from trade.services.market_service import get_active_listings, get_my_listings, get_tradeable_inventory
from trade.services.shop_service import (
    build_sellable_inventory_display_rows,
    get_sellable_inventory,
    get_sellable_inventory_queryset,
    get_shop_items_for_display,
)


def _format_market_duration_label(duration_seconds: int) -> str:
    days, remainder = divmod(max(0, int(duration_seconds)), 86400)
    hours = remainder // 3600
    if days > 0 and hours == 0:
        return f"{days}天"
    if days > 0:
        return f"{days}天{hours}小时"
    return f"{max(1, duration_seconds // 3600)}小时"


def _build_market_duration_options() -> list[dict[str, int | str]]:
    return [
        {
            "value": duration,
            "label": _format_market_duration_label(duration),
            "fee": fee,
        }
        for duration, fee in sorted(market_service.LISTING_FEES.items())
    ]


def _base_trade_context(tab: str, manor: Any) -> dict[str, Any]:
    return {
        "current_tab": tab,
        "tabs": [
            {"key": "auction", "name": "拍卖行"},
            {"key": "bank", "name": "钱庄"},
            {"key": "shop", "name": "商店"},
            {"key": "market", "name": "集市"},
        ],
        "manor": manor,
        "market_duration_options": _build_market_duration_options(),
    }


_ORIGINAL_GET_SELLABLE_INVENTORY = get_sellable_inventory


def _update_auction_context(params: Mapping[str, str], manor: Any, context: dict[str, Any]) -> None:
    from trade.services.auction_service import get_slots_bid_info_batch

    build_auction_trade_context(
        params,
        manor,
        context,
        get_auction_stats=get_auction_stats,
        get_active_slots=get_active_slots,
        get_my_bids=get_my_bids,
        get_my_leading_bids=get_my_leading_bids,
        get_slots_bid_info_batch=get_slots_bid_info_batch,
    )


def _update_shop_context(params: Mapping[str, str], manor: Any, context: dict[str, Any]) -> None:
    build_shop_trade_context(
        params,
        manor,
        context,
        get_shop_items_for_display=get_shop_items_for_display,
        get_sellable_inventory=get_sellable_inventory,
        get_sellable_inventory_queryset=get_sellable_inventory_queryset,
        build_sellable_inventory_display_rows=build_sellable_inventory_display_rows,
        original_get_sellable_inventory=_ORIGINAL_GET_SELLABLE_INVENTORY,
    )


def _update_bank_context(params: Mapping[str, str], manor: Any, context: dict[str, Any]) -> None:
    build_bank_trade_context(
        params,
        manor,
        context,
        get_bank_info=get_bank_info,
        get_troop_bank_capacity=get_troop_bank_capacity,
        get_troop_bank_used_space=get_troop_bank_used_space,
        get_troop_bank_remaining_space=get_troop_bank_remaining_space,
        get_troop_bank_rows=get_troop_bank_rows,
    )


def _update_market_context(params: Mapping[str, str], manor: Any, context: dict[str, Any]) -> None:
    build_market_trade_context(
        params,
        manor,
        context,
        get_active_listings=get_active_listings,
        get_tradeable_inventory=get_tradeable_inventory,
        get_my_listings=get_my_listings,
    )


_TAB_CONTEXT_BUILDERS = {
    "auction": _update_auction_context,
    "bank": _update_bank_context,
    "shop": _update_shop_context,
    "market": _update_market_context,
}


def get_trade_context(*, manor: Any, params: Mapping[str, str]) -> dict[str, Any]:
    tab = params.get("tab", "shop")
    context = _base_trade_context(tab, manor)
    context["trade_alerts"] = []

    builder = _TAB_CONTEXT_BUILDERS.get(tab)
    if builder is not None:
        builder(params, manor, context)

    return context
