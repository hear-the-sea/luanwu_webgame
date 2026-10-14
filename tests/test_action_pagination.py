from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.urls import reverse

from core.exceptions import ShopValidationError


@pytest.mark.django_db
def test_decompose_returns_to_current_page_and_clamps_an_empty_last_page(manor_with_user, monkeypatch):
    _manor, client = manor_with_user
    equipment = [
        {"key": f"equip_{index}", "name": f"装备{index}", "quantity": 1, "category": "weapon"} for index in range(10)
    ]
    monkeypatch.setattr("gameplay.services.buildings.forge.get_equipment_options", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "gameplay.services.buildings.forge.get_decomposable_equipment_options", lambda *_a, **_k: equipment
    )

    def decompose(_manor, key, quantity):
        equipment.pop()
        return {"equipment_name": key, "quantity": quantity, "rewards": {}}

    monkeypatch.setattr("gameplay.services.buildings.forge.decompose_equipment", decompose)
    page = client.get(reverse("gameplay:forge"), {"mode": "decompose", "category": "weapon", "page": 2})
    assert page.context["decompose_page_obj"].number == 2
    form = re.search(r"<form[^>]*js-decompose-form[^>]*>(.*?)</form>", page.content.decode(), re.S).group(1)
    fields = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form))
    assert fields["page"] == "2"
    response = client.post(reverse("gameplay:decompose_equipment"), {**fields, "quantity": 1})
    assert parse_qs(urlsplit(response.url).query)["page"] == ["2"]
    redirected = client.get(response.url)
    assert redirected.context["decompose_page_obj"].number == 1
    assert redirected.context["current_category"] == "weapon"


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", ["success", "service_error", "invalid_input"])
def test_shop_sell_preserves_page_and_category(manor_with_user, monkeypatch, outcome):
    _manor, client = manor_with_user

    def sell(*_args, **_kwargs):
        if outcome == "service_error":
            raise ShopValidationError(action="sell", message="sell blocked")
        assert outcome != "invalid_input"
        return {"item_name": "测试物品", "quantity": 1, "total_income": 7}

    monkeypatch.setattr("trade.views.sell_item", sell)
    response = client.post(
        reverse("trade:shop_sell"),
        {
            "item_key": "k",
            "quantity": "bad" if outcome == "invalid_input" else "1",
            "sell_page": "3",
            "buy_page": "2",
            "category": "equip_weapon",
        },
    )
    assert response.status_code == 302
    assert parse_qs(urlsplit(response.url).query) == {
        "tab": ["shop"],
        "view": ["sell"],
        "sell_page": ["3"],
        "buy_page": ["2"],
        "category": ["equip_weapon"],
    }


@pytest.mark.django_db
def test_market_invalid_listing_preserves_page_and_category(manor_with_user):
    _manor, client = manor_with_user
    response = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": "k", "quantity": "bad", "page": "4", "category": "equip_weapon"},
    )
    assert parse_qs(urlsplit(response.url).query) == {
        "tab": ["market"],
        "view": ["sell"],
        "page": ["4"],
        "category": ["equip_weapon"],
    }


@pytest.mark.parametrize("tab", ["shop", "market"])
def test_trade_forms_include_current_page_and_filter(tab):
    html = render_to_string(
        "trade/partials/_action_return_state.html",
        {
            "current_tab": tab,
            "selected_category": "equip_weapon",
            "shop_buy_page_obj": Paginator(range(60), 20).page(2),
            "shop_sell_page_obj": Paginator(range(60), 20).page(3),
            "page_obj": Paginator(range(60), 20).page(3),
        },
    )
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', html))
    assert fields["category"] == "equip_weapon"
    if tab == "shop":
        assert fields["buy_page"] == "2"
        assert fields["sell_page"] == "3"
    else:
        assert fields["page"] == "3"
