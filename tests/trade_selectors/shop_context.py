from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

import trade.selectors as trade_selectors
from tests.trade_selectors.support import DummySellable, create_manor, make_sellable_items
from trade.page_context import build_trade_request_params
from trade.selectors import get_trade_context
from trade.services.shop_service import ShopItemDisplay


def test_trade_selectors_no_longer_exposes_sync_resource_production_shim():
    assert not hasattr(trade_selectors, "sync_resource_production")


@pytest.mark.django_db
def test_get_trade_context_shop_builds_categories_and_filters(monkeypatch, django_user_model):
    manor = create_manor(django_user_model, username="trade_ctx_shop")

    shop_items = [
        ShopItemDisplay(
            key="tool_item",
            name="工具",
            description="",
            price=1,
            stock=-1,
            stock_display="无限",
            available=True,
            icon="",
            image_url="",
            effect_type="tool",
            category="道具",
            rarity="black",
            effect_payload={},
        ),
        ShopItemDisplay(
            key="med_item",
            name="药品",
            description="",
            price=1,
            stock=-1,
            stock_display="无限",
            available=True,
            icon="",
            image_url="",
            effect_type="medicine",
            category="药品",
            rarity="black",
            effect_payload={},
        ),
    ]

    captured = {"category": "unset"}

    def _sellable_inventory(_manor, category=None):
        captured["category"] = category
        return [DummySellable(inventory_item=object(), sell_price=1)]

    monkeypatch.setattr("trade.selectors.get_shop_items_for_display", lambda: list(shop_items))
    monkeypatch.setattr("trade.selectors.get_sellable_inventory", _sellable_inventory)

    request = RequestFactory().get("/trade", {"tab": "shop", "category": "magnifying_glass"})
    context = get_trade_context(manor=manor, params=build_trade_request_params(request))

    assert context["current_tab"] == "shop"
    assert context["selected_category"] == "tool"
    assert [item.key for item in context["shop_items"]] == ["tool_item"]
    assert captured["category"] is None

    category_keys = [category["key"] for category in context["categories"]]
    assert "all" in category_keys
    assert "tool" in category_keys
    assert "medicine" in category_keys
    assert len(context["inventory"]) == 1


@pytest.mark.django_db
def test_get_trade_context_shop_treats_loot_box_as_tool_category(monkeypatch, django_user_model):
    manor = create_manor(django_user_model, username="trade_ctx_shop_loot_box")

    shop_items = [
        ShopItemDisplay(
            key="work_chest_small",
            name="打工宝箱（小）",
            description="",
            price=1,
            stock=-1,
            stock_display="无限",
            available=True,
            icon="",
            image_url="",
            effect_type="loot_box",
            category="道具",
            rarity="green",
            effect_payload={},
        ),
        ShopItemDisplay(
            key="med_item",
            name="药品",
            description="",
            price=1,
            stock=-1,
            stock_display="无限",
            available=True,
            icon="",
            image_url="",
            effect_type="medicine",
            category="药品",
            rarity="black",
            effect_payload={},
        ),
    ]

    monkeypatch.setattr("trade.selectors.get_shop_items_for_display", lambda: list(shop_items))
    monkeypatch.setattr("trade.selectors.get_sellable_inventory", lambda *_args, **_kwargs: [])

    request = RequestFactory().get("/trade", {"tab": "shop", "category": "tool"})
    context = get_trade_context(manor=manor, params=build_trade_request_params(request))

    assert context["selected_category"] == "tool"
    assert [item.key for item in context["shop_items"]] == ["work_chest_small"]
    assert context["shop_items"][0].category == "道具"


@pytest.mark.django_db
def test_get_trade_context_shop_paginates_buy_and_sell_lists_independently(monkeypatch, django_user_model):
    manor = create_manor(django_user_model, username="trade_ctx_shop_paging")

    shop_items = [
        ShopItemDisplay(
            key=f"shop_item_{idx}",
            name=f"商品{idx}",
            description="",
            price=idx + 1,
            stock=-1,
            stock_display="无限",
            available=True,
            icon="",
            image_url="",
            effect_type="tool",
            category="道具",
            rarity="black",
            effect_payload={},
        )
        for idx in range(25)
    ]

    monkeypatch.setattr("trade.selectors.get_shop_items_for_display", lambda: list(shop_items))
    monkeypatch.setattr("trade.selectors.get_sellable_inventory", lambda *_args, **_kwargs: make_sellable_items(23))

    request = RequestFactory().get("/trade", {"tab": "shop", "view": "sell", "buy_page": "-8", "sell_page": "2"})
    context = get_trade_context(manor=manor, params=build_trade_request_params(request))

    assert context["current_tab"] == "shop"
    assert context["shop_view"] == "sell"
    assert context["shop_buy_page_obj"].number == 1
    assert context["shop_sell_page_obj"].number == 2
    assert len(context["shop_items"]) == 20
    assert len(context["inventory"]) == 3


@pytest.mark.django_db
def test_get_trade_context_shop_tolerates_data_loading_errors(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_shop_items_for_display",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("shop items failed")),
    )
    monkeypatch.setattr(
        "trade.selectors.get_sellable_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("sellable failed")),
    )

    manor = create_manor(django_user_model, username="trade_ctx_shop_load_err")
    request = RequestFactory().get("/trade", {"tab": "shop"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "shop"
    assert context["shop_items"] == []
    assert context["inventory"] == []
    assert context["categories"][0]["key"] == "all"


@pytest.mark.django_db
def test_get_trade_context_shop_programming_error_bubbles_up(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_shop_items_for_display",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("shop bug")),
    )

    manor = create_manor(django_user_model, username="trade_ctx_shop_bug")
    request = RequestFactory().get("/trade", {"tab": "shop"})

    with pytest.raises(RuntimeError, match="shop bug"):
        get_trade_context(manor=manor, params=build_trade_request_params(request))


@pytest.mark.django_db
def test_get_trade_context_shop_runtime_marker_bubbles_up(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_shop_items_for_display",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database backend unavailable")),
    )

    manor = create_manor(django_user_model, username="trade_ctx_shop_runtime_backend")
    request = RequestFactory().get("/trade", {"tab": "shop"})

    with pytest.raises(RuntimeError, match="database backend unavailable"):
        get_trade_context(manor=manor, params=build_trade_request_params(request))
