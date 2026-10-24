from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone

from core.exceptions import TradeValidationError
from gameplay.models import InventoryItem
from trade.models import MarketListing, MarketTransaction
from trade.services import market_service

pytest_plugins = ("tests.trade_service.fixtures",)
pytestmark = pytest.mark.django_db


@pytest.fixture
def partial_listing(seller_manor):
    return market_service.create_listing(seller_manor, "test_tradeable_item", 10, 2000, 7200)


def test_partial_then_all_keeps_independent_records_and_correct_notifications(
    partial_listing, buyer_manor, seller_manor, monkeypatch
):
    messages, notifications = [], []
    monkeypatch.setattr(market_service, "send_market_message", lambda **kwargs: messages.append(kwargs))
    monkeypatch.setattr(
        market_service, "send_market_notification", lambda user_id, payload, **kwargs: notifications.append(payload)
    )
    seller_manor.refresh_from_db()
    seller_before, buyer_before = seller_manor.silver, buyer_manor.silver
    first = market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=3)
    partial_listing.refresh_from_db()
    assert (partial_listing.quantity, partial_listing.total_price) == (7, 14000)
    assert partial_listing.status == MarketListing.Status.ACTIVE
    assert partial_listing.buyer_id is None and partial_listing.sold_at is None
    assert all("×3" in message["body"] and "6,000" in message["body"] for message in messages)
    assert notifications[0]["quantity"] == 3
    second = market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=4)
    last = market_service.purchase_listing(buyer_manor, partial_listing.pk)
    partial_listing.refresh_from_db()
    assert partial_listing.status == MarketListing.Status.SOLD
    assert partial_listing.buyer_id == buyer_manor.pk
    first.refresh_from_db()
    assert [(record.quantity, record.total_price) for record in (first, second, last)] == [
        (3, 6000),
        (4, 8000),
        (3, 6000),
    ]
    assert partial_listing.transactions.count() == 3
    buyer_manor.refresh_from_db()
    seller_manor.refresh_from_db()
    assert buyer_manor.silver == buyer_before - 20000
    assert seller_manor.silver == seller_before + 18000
    assert InventoryItem.objects.get(manor=buyer_manor, template=partial_listing.item_template).quantity == 10
    with pytest.raises(TradeValidationError, match="已下架"):
        market_service.purchase_listing(buyer_manor, partial_listing.pk)


@pytest.mark.parametrize("quantity", [0, -1, True, False, 1.5, "2", 11])
def test_partial_rejects_invalid_or_stale_quantity_without_side_effects(partial_listing, buyer_manor, quantity):
    balance = buyer_manor.silver
    with pytest.raises(TradeValidationError):
        market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=quantity)
    partial_listing.refresh_from_db()
    buyer_manor.refresh_from_db()
    assert (partial_listing.quantity, partial_listing.total_price) == (10, 20000)
    assert buyer_manor.silver == balance
    assert not partial_listing.transactions.exists()


def test_partial_can_buy_with_insufficient_balance_for_all(partial_listing, buyer_manor):
    buyer_manor.silver = 5000
    buyer_manor.save(update_fields=["silver"])
    with pytest.raises(TradeValidationError, match="银两不足"):
        market_service.purchase_listing(buyer_manor, partial_listing.pk)
    record = market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=2)
    assert record.total_price == 4000
    with pytest.raises(TradeValidationError, match="银两不足"):
        market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=1)
    partial_listing.refresh_from_db()
    buyer_manor.refresh_from_db()
    assert partial_listing.quantity == 8 and buyer_manor.silver == 1000


@pytest.mark.parametrize("finish", ["cancel", "expire"])
def test_partial_unsold_remainder_returns_once_and_keeps_history(partial_listing, buyer_manor, seller_manor, finish):
    record = market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=3)
    if finish == "cancel":
        assert market_service.cancel_listing(seller_manor, partial_listing.pk)["quantity"] == 7
    else:
        MarketListing.objects.filter(pk=partial_listing.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        assert market_service.expire_listings() == 1
        assert market_service.expire_listings() == 0
    partial_listing.refresh_from_db()
    assert partial_listing.status == (
        MarketListing.Status.CANCELLED if finish == "cancel" else MarketListing.Status.EXPIRED
    )
    assert InventoryItem.objects.get(manor=seller_manor, template=partial_listing.item_template).quantity == 97
    record.refresh_from_db()
    assert record.quantity == 3
    with pytest.raises(TradeValidationError, match="已下架"):
        market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=1)


def test_partial_item_failure_rolls_back_stock_and_both_balances(
    partial_listing, buyer_manor, seller_manor, monkeypatch
):
    seller_manor.refresh_from_db()
    seller_before, buyer_before = seller_manor.silver, buyer_manor.silver

    def fail_grant(*args, **kwargs):
        raise TradeValidationError("仓库无法收取物品")

    monkeypatch.setattr(market_service, "grant_market_item_locked", fail_grant)
    with pytest.raises(TradeValidationError, match="仓库无法收取物品"):
        market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=3)
    partial_listing.refresh_from_db()
    seller_manor.refresh_from_db()
    buyer_manor.refresh_from_db()
    assert (partial_listing.quantity, partial_listing.total_price) == (10, 20000)
    assert (seller_manor.silver, buyer_manor.silver) == (seller_before, buyer_before)
    assert not partial_listing.transactions.exists()


def test_partial_uses_latest_stock_when_other_purchase_has_reduced_listing(partial_listing, buyer_manor):
    market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=7)
    with pytest.raises(TradeValidationError, match="当前仅剩 3 件"):
        market_service.purchase_listing(buyer_manor, partial_listing.pk, quantity=5)
    assert market_service.purchase_listing(buyer_manor, partial_listing.pk).quantity == 3


def test_legacy_transaction_quantity_backfill(partial_listing, buyer_manor):
    record = market_service.purchase_listing(buyer_manor, partial_listing.pk)
    MarketTransaction.objects.filter(pk=record.pk).update(quantity=1)
    migration = import_module("trade.migrations.0012_market_partial_purchases")
    migration.backfill_transaction_quantities(apps, SimpleNamespace(connection=connection))
    record.refresh_from_db()
    assert record.quantity == 10


@pytest.mark.parametrize("enabled", [True, False])
def test_partial_controls_only_render_for_backend_supporting_quantity(partial_listing, buyer_manor, enabled):
    from django.template.loader import render_to_string

    html = render_to_string(
        "trade/partials/_market.html",
        {
            "listings": [partial_listing],
            "manor": buyer_manor,
            "market_view": "buy",
            "market_can_buy_or_list": True,
            "market_partial_purchase_enabled": enabled,
        },
    )
    assert ('value="partial"' in html) is enabled
    assert ('id="market-purchase-quantity"' in html) is enabled
    assert ("data-market-purchase-all" in html) is enabled
    assert "js-open-market-purchase" in html
    mode = "partial" if enabled else "all"
    assert f'name="purchase_mode" value="{mode}"' in html
