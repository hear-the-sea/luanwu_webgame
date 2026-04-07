from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
def test_market_create_listing_view_success(monkeypatch, client, django_user_model):
    class _DummyListing:
        total_price = 100

        class _Template:
            name = "物品"

        item_template = _Template()

        def get_duration_display(self):
            return "2小时"

    monkeypatch.setattr("trade.views.create_listing", lambda *_args, **_kwargs: _DummyListing())

    user = django_user_model.objects.create_user(username="market_create", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": "k", "quantity": "1", "unit_price": "10", "duration": "7200"},
    )
    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=sell" in resp["Location"]


@pytest.mark.django_db
def test_market_create_listing_view_rejects_invalid_quantity_without_service_call(
    monkeypatch, client, django_user_model
):
    called = {"count": 0}

    class _DummyListing:
        total_price = 100

        class _Template:
            name = "物品"

        item_template = _Template()

        def get_duration_display(self):
            return "2小时"

    def _unexpected_create(*_args, **_kwargs):
        called["count"] += 1
        return _DummyListing()

    monkeypatch.setattr("trade.views.create_listing", _unexpected_create)

    user = django_user_model.objects.create_user(username="market_create_invalid_qty", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": "k", "quantity": "bad", "unit_price": "10", "duration": "7200"},
    )
    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=sell" in resp["Location"]
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("数量参数无效" in m for m in msgs)
    assert called["count"] == 0


@pytest.mark.django_db
def test_market_create_listing_view_rejects_missing_item_key_without_service_call(
    monkeypatch, client, django_user_model
):
    called = {"count": 0}

    class _DummyListing:
        total_price = 100

        class _Template:
            name = "物品"

        item_template = _Template()

        def get_duration_display(self):
            return "2小时"

    def _unexpected_create(*_args, **_kwargs):
        called["count"] += 1
        return _DummyListing()

    monkeypatch.setattr("trade.views.create_listing", _unexpected_create)

    user = django_user_model.objects.create_user(username="market_create_missing_item_key", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": " ", "quantity": "1", "unit_price": "10", "duration": "7200"},
    )
    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=sell" in resp["Location"]
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("请选择商品" in m for m in msgs)
    assert called["count"] == 0


@pytest.mark.django_db
def test_market_create_listing_view_rejects_invalid_unit_price_without_service_call(
    monkeypatch, client, django_user_model
):
    called = {"count": 0}

    class _DummyListing:
        total_price = 100

        class _Template:
            name = "物品"

        item_template = _Template()

        def get_duration_display(self):
            return "2小时"

    def _unexpected_create(*_args, **_kwargs):
        called["count"] += 1
        return _DummyListing()

    monkeypatch.setattr("trade.views.create_listing", _unexpected_create)

    user = django_user_model.objects.create_user(username="market_create_invalid_price", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": "k", "quantity": "1", "unit_price": "bad", "duration": "7200"},
    )
    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=sell" in resp["Location"]
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("单价参数无效" in m for m in msgs)
    assert called["count"] == 0


@pytest.mark.django_db
def test_market_create_listing_view_rejects_invalid_duration_without_service_call(
    monkeypatch, client, django_user_model
):
    called = {"count": 0}

    class _DummyListing:
        total_price = 100

        class _Template:
            name = "物品"

        item_template = _Template()

        def get_duration_display(self):
            return "2小时"

    def _unexpected_create(*_args, **_kwargs):
        called["count"] += 1
        return _DummyListing()

    monkeypatch.setattr("trade.views.create_listing", _unexpected_create)

    user = django_user_model.objects.create_user(username="market_create_invalid_duration", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": "k", "quantity": "1", "unit_price": "10", "duration": "bad"},
    )
    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=sell" in resp["Location"]
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("时长参数无效" in m for m in msgs)
    assert called["count"] == 0


@pytest.mark.django_db
def test_market_create_listing_view_rejects_out_of_range_duration_without_service_call(
    monkeypatch, client, django_user_model
):
    called = {"count": 0}

    class _DummyListing:
        total_price = 100

        class _Template:
            name = "物品"

        item_template = _Template()

        def get_duration_display(self):
            return "2小时"

    def _unexpected_create(*_args, **_kwargs):
        called["count"] += 1
        return _DummyListing()

    monkeypatch.setattr("trade.views.create_listing", _unexpected_create)

    user = django_user_model.objects.create_user(username="market_create_out_of_range_duration", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": "k", "quantity": "1", "unit_price": "10", "duration": "1"},
    )
    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=sell" in resp["Location"]
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("时长参数无效" in m for m in msgs)
    assert called["count"] == 0


@pytest.mark.django_db
def test_market_create_listing_view_reads_latest_market_service_listing_fees(monkeypatch, client, django_user_model):
    observed: dict[str, int] = {}

    class _DummyListing:
        total_price = 100

        class _Template:
            name = "物品"

        item_template = _Template()

        def get_duration_display(self):
            return "2小时46分钟"

    def _create_listing(_manor, item_key, quantity, unit_price, duration):
        observed.update(
            {
                "item_key": item_key,
                "quantity": quantity,
                "unit_price": unit_price,
                "duration": duration,
            }
        )
        return _DummyListing()

    monkeypatch.setattr(
        "trade.services.market_service.load_trade_market_rules",
        lambda: {"listing_fees": {9999: 7777}},
    )
    monkeypatch.setattr("trade.views.create_listing", _create_listing)

    user = django_user_model.objects.create_user(username="market_create_runtime_duration", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": "k", "quantity": "1", "unit_price": "10", "duration": "9999"},
    )

    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=sell" in resp["Location"]
    assert observed == {"item_key": "k", "quantity": 1, "unit_price": 10, "duration": 9999}


@pytest.mark.django_db
def test_market_create_listing_view_reads_runtime_listing_fees_via_loader(monkeypatch, client, django_user_model):
    observed: dict[str, int] = {}

    class _DummyListing:
        total_price = 100

        class _Template:
            name = "物品"

        item_template = _Template()

        def get_duration_display(self):
            return "2小时46分钟"

    def _create_listing(_manor, item_key, quantity, unit_price, duration):
        observed.update(
            {
                "item_key": item_key,
                "quantity": quantity,
                "unit_price": unit_price,
                "duration": duration,
            }
        )
        return _DummyListing()

    monkeypatch.setattr("trade.services.market_service.LISTING_FEES", {28800: 12000})
    monkeypatch.setattr(
        "trade.services.market_service.load_trade_market_rules",
        lambda: {"listing_fees": {9999: 7777}},
    )
    monkeypatch.setattr("trade.views.create_listing", _create_listing)

    user = django_user_model.objects.create_user(username="market_create_runtime_loader_duration", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(
        reverse("trade:market_create_listing"),
        {"item_key": "k", "quantity": "1", "unit_price": "10", "duration": "9999"},
    )

    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=sell" in resp["Location"]
    assert observed == {"item_key": "k", "quantity": 1, "unit_price": 10, "duration": 9999}


@pytest.mark.django_db
def test_market_purchase_view_success(monkeypatch, client, django_user_model):
    class _DummyListing:
        quantity = 2

        class _Template:
            name = "物品"

        item_template = _Template()

    class _DummyTransaction:
        total_price = 100
        listing = _DummyListing()

    monkeypatch.setattr("trade.views.purchase_listing", lambda *_args, **_kwargs: _DummyTransaction())

    user = django_user_model.objects.create_user(username="market_buy", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(reverse("trade:market_purchase", args=[1]))
    assert resp.status_code == 302
    assert "tab=market" in resp["Location"]
    assert "view=buy" in resp["Location"]


@pytest.mark.django_db
def test_market_purchase_view_database_error_uses_generic_message(monkeypatch, client, django_user_model):
    monkeypatch.setattr(
        "trade.views.purchase_listing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    user = django_user_model.objects.create_user(username="market_buy_unexpected", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(reverse("trade:market_purchase", args=[1]))
    assert resp.status_code == 302
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("操作失败，请稍后重试" in m for m in msgs)


@pytest.mark.django_db
def test_market_purchase_view_programming_error_bubbles_up(monkeypatch, client, django_user_model):
    monkeypatch.setattr(
        "trade.views.purchase_listing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    user = django_user_model.objects.create_user(username="market_buy_runtime", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    with pytest.raises(RuntimeError, match="boom"):
        client.post(reverse("trade:market_purchase", args=[1]))


@pytest.mark.django_db
def test_market_purchase_view_tolerates_missing_threshold_setting(monkeypatch, client, django_user_model):
    class _DummyListing:
        quantity = 2

        class _Template:
            name = "物品"

        item_template = _Template()

    class _DummyTransaction:
        total_price = 100
        listing = _DummyListing()

    monkeypatch.setattr("trade.views.purchase_listing", lambda *_args, **_kwargs: _DummyTransaction())
    monkeypatch.setattr("trade.view_helpers.settings.TRADE_HIGH_VALUE_SILVER_THRESHOLD", None, raising=False)

    user = django_user_model.objects.create_user(username="market_buy_missing_threshold", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(reverse("trade:market_purchase", args=[1]))
    assert resp.status_code == 302
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("成功购买" in m for m in msgs)


@pytest.mark.django_db
def test_market_purchase_view_tolerates_invalid_threshold_setting(monkeypatch, client, django_user_model):
    class _DummyListing:
        quantity = 2

        class _Template:
            name = "物品"

        item_template = _Template()

    class _DummyTransaction:
        total_price = 100
        listing = _DummyListing()

    monkeypatch.setattr("trade.views.purchase_listing", lambda *_args, **_kwargs: _DummyTransaction())
    monkeypatch.setattr("trade.view_helpers.settings.TRADE_HIGH_VALUE_SILVER_THRESHOLD", "invalid", raising=False)

    user = django_user_model.objects.create_user(username="market_buy_invalid_threshold", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(reverse("trade:market_purchase", args=[1]))
    assert resp.status_code == 302
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("成功购买" in m for m in msgs)


@pytest.mark.django_db
def test_market_purchase_view_uses_shared_threshold_warning_helper(monkeypatch, client, django_user_model):
    class _DummyListing:
        quantity = 2

        class _Template:
            name = "物品"

        item_template = _Template()

    class _DummyTransaction:
        total_price = 123
        listing = _DummyListing()

    calls: list[dict[str, object]] = []

    monkeypatch.setattr("trade.views.purchase_listing", lambda *_args, **_kwargs: _DummyTransaction())
    monkeypatch.setattr(
        "trade.view_helpers.warn_if_threshold_exceeded",
        lambda **kwargs: calls.append(kwargs),
    )

    user = django_user_model.objects.create_user(username="market_buy_shared_threshold", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(reverse("trade:market_purchase", args=[7]))
    assert resp.status_code == 302
    assert calls == [
        {
            "setting_name": "TRADE_HIGH_VALUE_SILVER_THRESHOLD",
            "default": 1_000_000,
            "value": 123,
            "log_message": "High-value market purchase: user_id=%s listing_id=%s total_price=%s",
            "log_args": (user.id, 7, 123),
        }
    ]


@pytest.mark.django_db
def test_market_cancel_view_success(monkeypatch, client, django_user_model):
    monkeypatch.setattr(
        "trade.views.cancel_listing",
        lambda *_args, **_kwargs: {"item_name": "物品", "quantity": 1},
    )

    user = django_user_model.objects.create_user(username="market_cancel", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(reverse("trade:market_cancel", args=[1]))
    assert resp.status_code == 302
    assert "view=my_listings" in resp["Location"]
