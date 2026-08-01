from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

from tests.trade_selectors.support import create_manor
from trade.page_context import build_trade_request_params
from trade.selectors import get_trade_context


@pytest.mark.django_db
def test_get_trade_context_includes_market_duration_options(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.services.market_service.load_trade_market_rules",
        lambda: {"listing_fees": {28800: 12000, 7200: 5000, 86400: 20000}},
    )

    manor = create_manor(django_user_model, username="trade_ctx_duration_options")
    request = RequestFactory().get("/trade", {"tab": "market"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))

    assert context["market_duration_options"] == [
        {"value": 7200, "label": "2小时", "fee": 5000},
        {"value": 28800, "label": "8小时", "fee": 12000},
        {"value": 86400, "label": "1天", "fee": 20000},
    ]


@pytest.mark.django_db
def test_get_trade_context_exposes_market_prestige_requirement(django_user_model):
    manor = create_manor(django_user_model, username="trade_ctx_market_prestige")
    request = RequestFactory().get("/trade", {"tab": "market"})

    manor.prestige = 299
    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["market_min_prestige"] == 300
    assert context["market_can_buy_or_list"] is False

    manor.prestige = 300
    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["market_can_buy_or_list"] is True


@pytest.mark.django_db
def test_get_trade_context_reads_latest_market_service_listing_fees(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.services.market_service.load_trade_market_rules",
        lambda: {"listing_fees": {14400: 9000, 7200: 5000}},
    )

    manor = create_manor(django_user_model, username="trade_ctx_runtime_listing_fees")
    request = RequestFactory().get("/trade", {"tab": "market"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))

    assert context["market_duration_options"] == [
        {"value": 7200, "label": "2小时", "fee": 5000},
        {"value": 14400, "label": "4小时", "fee": 9000},
    ]


@pytest.mark.django_db
def test_get_trade_context_reads_runtime_listing_fees_via_loader(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.services.market_service.LISTING_FEES", {28800: 12000})
    monkeypatch.setattr(
        "trade.services.market_service.load_trade_market_rules",
        lambda: {"listing_fees": {14400: 9000, 7200: 5000}},
    )

    manor = create_manor(django_user_model, username="trade_ctx_runtime_loader_listing_fees")
    request = RequestFactory().get("/trade", {"tab": "market"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))

    assert context["market_duration_options"] == [
        {"value": 7200, "label": "2小时", "fee": 5000},
        {"value": 14400, "label": "4小时", "fee": 9000},
    ]


@pytest.mark.django_db
def test_get_trade_context_market_buy_lists_page(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.selectors.get_active_listings", lambda **_kwargs: ["l1", "l2", "l3"])

    manor = create_manor(django_user_model, username="trade_ctx_market")
    request = RequestFactory().get("/trade", {"tab": "market", "view": "buy"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "market"
    assert context["market_view"] == "buy"
    assert list(context["listings"].object_list) == ["l1", "l2", "l3"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("requested_order", "expected_order"),
    [("unit_price", "unit_price"), ("price", "-listed_at")],
)
def test_get_trade_context_market_ordering_uses_real_listing_fields(
    monkeypatch, django_user_model, requested_order, expected_order
):
    captured: dict[str, object] = {}

    def _get_active_listings(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("trade.selectors.get_active_listings", _get_active_listings)

    manor = create_manor(django_user_model, username=f"trade_ctx_market_order_{requested_order}")
    request = RequestFactory().get(
        "/trade",
        {"tab": "market", "view": "buy", "order_by": requested_order},
    )

    get_trade_context(manor=manor, params=build_trade_request_params(request))

    assert captured["order_by"] == expected_order


@pytest.mark.django_db
def test_get_trade_context_market_buy_negative_page_clamped(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.selectors.get_active_listings", lambda **_kwargs: list(range(1, 22)))

    manor = create_manor(django_user_model, username="trade_ctx_market_page_clamp")
    request = RequestFactory().get("/trade", {"tab": "market", "view": "buy", "page": "-9"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["page_obj"].number == 1
    assert list(context["listings"].object_list) == list(range(1, 21))


@pytest.mark.django_db
def test_get_trade_context_market_my_listings_negative_page_clamped(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.selectors.get_my_listings", lambda *_args, **_kwargs: list(range(1, 22)))

    manor = create_manor(django_user_model, username="trade_ctx_market_my_page_clamp")
    request = RequestFactory().get("/trade", {"tab": "market", "view": "my_listings", "page": "-3"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["page_obj"].number == 1
    assert list(context["my_listings"].object_list) == list(range(1, 21))


@pytest.mark.django_db
def test_get_trade_context_market_sell_paginates_to_twenty_items(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.selectors.get_tradeable_inventory", lambda *_args, **_kwargs: list(range(1, 24)))

    manor = create_manor(django_user_model, username="trade_ctx_market_sell_page")
    request = RequestFactory().get("/trade", {"tab": "market", "view": "sell", "page": "2"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["page_obj"].number == 2
    assert list(context["tradeable_items"].object_list) == [21, 22, 23]


@pytest.mark.django_db
def test_get_trade_context_market_buy_tolerates_active_listings_error(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_active_listings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("listings failed")),
    )

    manor = create_manor(django_user_model, username="trade_ctx_market_buy_err")
    request = RequestFactory().get("/trade", {"tab": "market", "view": "buy"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "market"
    assert context["market_view"] == "buy"
    assert list(context["listings"].object_list) == []


@pytest.mark.django_db
def test_get_trade_context_market_sell_negative_page_clamped_and_tolerates_inventory_error(
    monkeypatch, django_user_model
):
    monkeypatch.setattr(
        "trade.selectors.get_tradeable_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("tradeable failed")),
    )

    manor = create_manor(django_user_model, username="trade_ctx_market_sell_err")
    request = RequestFactory().get("/trade", {"tab": "market", "view": "sell", "page": "-10"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "market"
    assert context["market_view"] == "sell"
    assert context["page_obj"].number == 1
    assert list(context["tradeable_items"].object_list) == []


@pytest.mark.django_db
def test_get_trade_context_market_my_listings_tolerates_loading_error(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_my_listings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("my listings failed")),
    )

    manor = create_manor(django_user_model, username="trade_ctx_market_my_err")
    request = RequestFactory().get("/trade", {"tab": "market", "view": "my_listings"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "market"
    assert context["market_view"] == "my_listings"
    assert list(context["my_listings"].object_list) == []


@pytest.mark.django_db
def test_get_trade_context_market_programming_error_bubbles_up(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_active_listings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("market bug")),
    )

    manor = create_manor(django_user_model, username="trade_ctx_market_bug")
    request = RequestFactory().get("/trade", {"tab": "market", "view": "buy"})

    with pytest.raises(RuntimeError, match="market bug"):
        get_trade_context(manor=manor, params=build_trade_request_params(request))
