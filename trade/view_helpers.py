from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse

from core.utils import safe_int, sanitize_error_message
from core.utils.view_error_mapping import (
    DEFAULT_VIEW_INFRASTRUCTURE_EXCEPTIONS,
    KNOWN_VIEW_EXCEPTIONS,
    flash_view_error,
)
from gameplay.models import Manor
from gameplay.services.manor.core import get_manor

logger = logging.getLogger(__name__)

TradeResult = TypeVar("TradeResult")


@dataclass(frozen=True)
class TradeActionOutcome(Generic[TradeResult]):
    succeeded: bool
    result: TradeResult | None = None


@dataclass(frozen=True)
class TradeItemQuantityInput:
    item_key: str
    quantity: int


@dataclass(frozen=True)
class MarketListingFormInput:
    item_key: str
    quantity: int
    unit_price: int
    duration: int


def trade_redirect(tab: str | None = None, view: str | None = None, troop_category: str | None = None, *, request=None):
    base_url = reverse("trade:trade")
    params: dict[str, str | int] = {}
    if tab:
        params["tab"] = tab
    if view:
        params["view"] = view
    if request is not None and tab in {"shop", "market"}:
        page_keys = ("buy_page", "sell_page") if tab == "shop" else ("page",)
        for key in page_keys:
            if key in request.POST:
                params[key] = safe_int(request.POST.get(key), default=1, min_val=1)
        filter_keys = ("category",) if tab == "shop" else ("category", "rarity", "order_by", "status")
        for key in filter_keys:
            value = (request.POST.get(key) or "").strip()
            if value:
                params[key] = value
    normalized_troop_category = (troop_category or "").strip()
    if normalized_troop_category and normalized_troop_category != "all":
        params["troop_category"] = normalized_troop_category
    if not params:
        return redirect(base_url)
    return redirect(f"{base_url}?{urlencode(params)}")


def trade_input_error(
    request,
    message: str,
    *,
    tab: str | None = None,
    view: str | None = None,
    troop_category: str | None = None,
):
    messages.error(request, message)
    return trade_redirect(tab=tab, view=view, troop_category=troop_category, request=request)


def parse_required_post_text(request, field: str) -> str | None:
    value = (request.POST.get(field) or "").strip()
    return value or None


def parse_positive_post_int(request, field: str) -> int | None:
    value = safe_int(request.POST.get(field), default=None)
    if value is None or value <= 0:
        return None
    return value


def parse_non_negative_post_int(request, field: str) -> int | None:
    value = safe_int(request.POST.get(field), default=None)
    if value is None or value < 0:
        return None
    return value


def parse_market_duration(request, *, allowed_durations: frozenset[int]) -> int | None:
    duration = safe_int(request.POST.get("duration"), default=None)
    if duration is None:
        return None
    if duration not in allowed_durations:
        return None
    return duration


def parse_required_post_text_or_redirect(
    request,
    field: str,
    message: str,
    *,
    tab: str | None = None,
    view: str | None = None,
    troop_category: str | None = None,
) -> str | HttpResponseRedirect:
    value = parse_required_post_text(request, field)
    if value is None:
        return trade_input_error(request, message, tab=tab, view=view, troop_category=troop_category)
    return value


def parse_positive_post_int_or_redirect(
    request,
    field: str,
    message: str,
    *,
    tab: str | None = None,
    view: str | None = None,
    troop_category: str | None = None,
) -> int | HttpResponseRedirect:
    value = parse_positive_post_int(request, field)
    if value is None:
        return trade_input_error(request, message, tab=tab, view=view, troop_category=troop_category)
    return value


def parse_non_negative_post_int_or_redirect(
    request,
    field: str,
    message: str,
    *,
    tab: str | None = None,
    view: str | None = None,
    troop_category: str | None = None,
) -> int | HttpResponseRedirect:
    value = parse_non_negative_post_int(request, field)
    if value is None:
        return trade_input_error(request, message, tab=tab, view=view, troop_category=troop_category)
    return value


def parse_market_duration_or_redirect(
    request,
    message: str,
    *,
    allowed_durations: frozenset[int],
    tab: str | None = None,
    view: str | None = None,
    troop_category: str | None = None,
) -> int | HttpResponseRedirect:
    value = parse_market_duration(request, allowed_durations=allowed_durations)
    if value is None:
        return trade_input_error(request, message, tab=tab, view=view, troop_category=troop_category)
    return value


def parse_trade_item_quantity_form(
    request,
    *,
    tab: str | None = None,
    view: str | None = None,
    troop_category: str | None = None,
) -> TradeItemQuantityInput | HttpResponseRedirect:
    item_key = parse_required_post_text_or_redirect(
        request,
        "item_key",
        "请选择商品",
        tab=tab,
        view=view,
        troop_category=troop_category,
    )
    if isinstance(item_key, HttpResponseRedirect):
        return item_key

    quantity = parse_positive_post_int_or_redirect(
        request,
        "quantity",
        "数量参数无效",
        tab=tab,
        view=view,
        troop_category=troop_category,
    )
    if isinstance(quantity, HttpResponseRedirect):
        return quantity

    return TradeItemQuantityInput(item_key=item_key, quantity=quantity)


def parse_market_listing_form(
    request,
    *,
    allowed_durations: frozenset[int],
) -> MarketListingFormInput | HttpResponseRedirect:
    parsed = parse_trade_item_quantity_form(request, tab="market", view="sell")
    if isinstance(parsed, HttpResponseRedirect):
        return parsed

    unit_price = parse_non_negative_post_int_or_redirect(
        request, "unit_price", "单价参数无效", tab="market", view="sell"
    )
    if isinstance(unit_price, HttpResponseRedirect):
        return unit_price

    duration = parse_market_duration_or_redirect(
        request,
        "时长参数无效",
        allowed_durations=allowed_durations,
        tab="market",
        view="sell",
    )
    if isinstance(duration, HttpResponseRedirect):
        return duration

    return MarketListingFormInput(
        item_key=parsed.item_key,
        quantity=parsed.quantity,
        unit_price=unit_price,
        duration=duration,
    )


def handle_trade_error(request, exc: Exception) -> None:
    messages.error(request, sanitize_error_message(exc))


def handle_unexpected_trade_error(request, exc: Exception, *, op: str) -> None:
    flash_view_error(
        request,
        exc,
        log_message="trade view action failed: op=%s user_id=%s",
        log_args=(
            getattr(request, "op", op),
            getattr(request.user, "id", None),
        ),
        logger_instance=logger,
    )


def execute_trade_action(
    request,
    *,
    op: str,
    action: Callable[[], TradeResult],
    success_message: Callable[[TradeResult], str],
) -> TradeActionOutcome[TradeResult]:
    try:
        result = action()
        messages.success(request, success_message(result))
        return TradeActionOutcome(succeeded=True, result=result)
    except KNOWN_VIEW_EXCEPTIONS as exc:
        handle_trade_error(request, exc)
        return TradeActionOutcome(succeeded=False)
    except DEFAULT_VIEW_INFRASTRUCTURE_EXCEPTIONS as exc:
        handle_unexpected_trade_error(request, exc, op=op)
        return TradeActionOutcome(succeeded=False)


def execute_trade_action_and_redirect(
    request,
    *,
    op: str,
    action: Callable[[], TradeResult],
    success_message: Callable[[TradeResult], str],
    tab: str | None = None,
    view: str | None = None,
    troop_category: str | None = None,
):
    execute_trade_action(
        request,
        op=op,
        action=action,
        success_message=success_message,
    )
    return trade_redirect(tab=tab, view=view, troop_category=troop_category, request=request)


def execute_manor_trade_action_and_redirect(
    request,
    *,
    op: str,
    action_factory: Callable[[Manor], TradeResult],
    success_message: Callable[[TradeResult], str],
    tab: str | None = None,
    view: str | None = None,
    troop_category: str | None = None,
):
    manor = get_manor(request.user)
    return execute_trade_action_and_redirect(
        request,
        op=op,
        action=lambda: action_factory(manor),
        success_message=success_message,
        tab=tab,
        view=view,
        troop_category=troop_category,
    )


def handle_troop_bank_transfer(
    request,
    *,
    op: str,
    transfer_action: Callable[[Manor, str, int], TradeResult],
    success_message: Callable[[TradeResult], str],
):
    troop_category = parse_required_post_text(request, "troop_category") or ""
    troop_key = parse_required_post_text_or_redirect(
        request,
        "troop_key",
        "请选择护院类型",
        tab="bank",
        troop_category=troop_category,
    )
    if isinstance(troop_key, HttpResponseRedirect):
        return troop_key

    quantity = parse_positive_post_int_or_redirect(
        request,
        "quantity",
        "数量参数无效",
        tab="bank",
        troop_category=troop_category,
    )
    if isinstance(quantity, HttpResponseRedirect):
        return quantity

    return execute_manor_trade_action_and_redirect(
        request,
        op=op,
        action_factory=lambda manor: transfer_action(manor, troop_key, quantity),
        success_message=success_message,
        tab="bank",
        troop_category=troop_category,
    )


def get_positive_int_setting(name: str, default: int) -> int:
    value = safe_int(getattr(settings, name, default), default=default)
    if value is None or value <= 0:
        return default
    return value


def warn_if_threshold_exceeded(
    *,
    setting_name: str,
    default: int,
    value: int,
    log_message: str,
    log_args: tuple[object, ...],
):
    threshold = get_positive_int_setting(setting_name, default)
    if value >= threshold:
        logger.warning(log_message, *log_args)
