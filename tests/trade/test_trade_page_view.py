from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from gameplay.models import Manor
from gameplay.services.manor.core import ensure_manor
from trade.page_context import build_trade_page_context


@pytest.mark.django_db
def test_trade_view_renders(monkeypatch, client, django_user_model):
    monkeypatch.setattr("trade.views.build_trade_page_context", lambda *_args, **_kwargs: {"current_tab": "shop"})

    user = django_user_model.objects.create_user(username="trade_view", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.get(reverse("trade:trade"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_market_page_disables_listing_for_low_prestige_user(client, django_user_model):
    user = django_user_model.objects.create_user(username="trade_market_low_prestige", password="pass12345")
    manor = ensure_manor(user)
    manor.prestige = 299
    manor.save(update_fields=["prestige"])
    client.force_login(user)

    resp = client.get(reverse("trade:trade"), {"tab": "market", "view": "buy"})

    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "购买与上架未解锁" in content
    assert "当前声望 299，达到 300" in content
    assert 'disabled title="声望达到 300 后可上架"' in content


@pytest.mark.django_db
def test_trade_view_creates_manor_when_missing(monkeypatch, client, django_user_model):
    monkeypatch.setattr("trade.views.build_trade_page_context", lambda *_args, **_kwargs: {"current_tab": "shop"})

    user = django_user_model.objects.create_user(username="trade_view_create_manor", password="pass12345")
    client.force_login(user)

    resp = client.get(reverse("trade:trade"))
    assert resp.status_code == 200
    assert Manor.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_trade_view_tolerates_resource_sync_error(monkeypatch, client, django_user_model):
    monkeypatch.setattr("trade.page_context.get_trade_context", lambda *_args, **_kwargs: {"current_tab": "shop"})
    monkeypatch.setattr(
        "trade.page_context.project_resource_production_for_read",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("sync failed")),
    )

    user = django_user_model.objects.create_user(username="trade_view_sync_err", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.get(reverse("trade:trade"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_trade_view_renders_bank_degraded_banner_and_disables_exchange(monkeypatch, client, django_user_model):
    user = django_user_model.objects.create_user(username="trade_view_bank_degraded", password="pass12345")
    manor = ensure_manor(user)
    monkeypatch.setattr(
        "trade.views.build_trade_page_context",
        lambda *_args, **_kwargs: {
            "current_tab": "bank",
            "tabs": [{"key": "bank", "name": "钱庄"}],
            "manor": manor,
            "trade_alerts": [{"section": "bank", "message": "钱庄汇率数据暂时不可用，已暂时关闭兑换。"}],
            "bank_info": {
                "current_rate": 0,
                "next_rate": 0,
                "total_cost_per_bar": 0,
                "gold_bar_fee_rate": 0,
                "today_count": 0,
                "manor_silver": manor.silver,
                "effective_supply": 0,
                "supply_factor": 0,
                "progressive_factor": 0,
                "gold_bar_base_price": 0,
                "gold_bar_min_price": 0,
                "gold_bar_max_price": 0,
                "exchange_available": False,
            },
            "troop_bank_capacity": 5000,
            "troop_bank_used": 0,
            "troop_bank_remaining": 5000,
            "troop_bank_rows": [],
            "troop_bank_categories": [{"key": "all", "name": "全部"}],
            "troop_bank_current_category": "all",
        },
    )
    client.force_login(user)

    resp = client.get(reverse("trade:trade"))
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "钱庄汇率数据暂时不可用，已暂时关闭兑换。" in content
    assert "兑换暂不可用" in content


@pytest.mark.django_db
def test_trade_page_context_passes_normalized_params_to_selector(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="trade_ctx_normalized_params", password="pass12345")
    manor = ensure_manor(user)
    request = RequestFactory().get("/trade", {"tab": "market", "view": "sell", "page": "3"})
    request.user = user
    captured: dict[str, object] = {}

    monkeypatch.setattr("trade.page_context.get_prepared_manor_for_read", lambda *_args, **_kwargs: manor)
    monkeypatch.setattr(
        "trade.page_context.build_trade_request_params",
        lambda _request: {"tab": "market", "view": "sell", "page": "3"},
    )

    def _fake_get_trade_context(*, manor, params):
        captured["manor"] = manor
        captured["params"] = params
        return {"current_tab": params["tab"]}

    monkeypatch.setattr("trade.page_context.get_trade_context", _fake_get_trade_context)

    context = build_trade_page_context(request)

    assert context == {"current_tab": "market"}
    assert captured == {
        "manor": manor,
        "params": {"tab": "market", "view": "sell", "page": "3"},
    }


@pytest.mark.django_db
def test_shop_tooltips_render_direct_stats_and_multi_tier_set_bonuses():
    buy_payload = {
        "attack": 7,
        "troop_capacity": 11,
        "set_key": "buy_set",
        "set_description": "买入套装",
        "set_bonus": [
            {"pieces": 2, "bonus": {"attack": 30, "troop_capacity": 40}},
            {"pieces": 4, "bonus": {"attack": 50, "troop_capacity": 80}},
        ],
    }
    sell_payload = {
        "attack": 9,
        "troop_capacity": 13,
        "set_key": "sell_set",
        "set_description": "回收套装",
        "set_bonus": [
            {"pieces": 2, "bonus": {"attack": 33, "troop_capacity": 44}},
            {"pieces": 4, "bonus": {"attack": 55, "troop_capacity": 88}},
        ],
    }
    shop_item = SimpleNamespace(
        key="buy_gear",
        name="买入装备",
        description="买入描述",
        price=100,
        stock_display="无限",
        available=True,
        image_url="",
        category="装备",
        rarity="blue",
        effect_payload=buy_payload,
    )
    sell_template = SimpleNamespace(
        key="sell_gear",
        name="回收装备",
        description="回收描述",
        image=None,
        rarity="blue",
        effect_payload=sell_payload,
    )
    sell_item = SimpleNamespace(
        inventory_item=SimpleNamespace(template=sell_template, quantity=1),
        sell_price=50,
    )
    request = RequestFactory().get("/trade")
    request.user = AnonymousUser()
    html = render_to_string(
        "trade/partials/_shop.html",
        {
            "shop_view": "buy",
            "shop_items": [shop_item],
            "inventory": [sell_item],
            "categories": [],
            "selected_category": "all",
            "shop_buy_page_obj": Paginator([shop_item], 20).page(1),
            "shop_sell_page_obj": Paginator([sell_item], 20).page(1),
            "manor": SimpleNamespace(silver=1000),
        },
        request=request,
    )

    assert '<span class="tw-attr-label">攻击</span><span class="tw-attr-value">+7</span>' in html
    assert '<span class="tw-attr-label">可携带护院人数</span><span class="tw-attr-value">+11</span>' in html
    assert "2 件套" in html
    assert "4 件套" in html
    assert "攻击+30" in html
    assert "可携带护院人数+40" in html
    assert "攻击+50" in html
    assert "可携带护院人数+80" in html
    assert '<span class="tw-attr-label">攻击</span><span class="tw-attr-value">+9</span>' in html
    assert '<span class="tw-attr-label">可携带护院人数</span><span class="tw-attr-value">+13</span>' in html
    assert "攻击+33" in html
    assert "可携带护院人数+44" in html
    assert "攻击+55" in html
    assert "可携带护院人数+88" in html
