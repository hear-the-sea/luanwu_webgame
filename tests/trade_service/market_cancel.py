from __future__ import annotations

import pytest
from django.db import IntegrityError

from core.exceptions import TradeValidationError
from gameplay.models import InventoryItem
from trade.services import market_service

pytest_plugins = ("tests.trade_service.fixtures",)


@pytest.mark.django_db
class TestMarketCancel:
    def test_cancel_listing_remains_available_below_prestige_requirement(self, seller_manor):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )
        seller_manor.prestige = market_service.MARKET_MIN_PRESTIGE - 1
        seller_manor.save(update_fields=["prestige"])

        result = market_service.cancel_listing(seller_manor, listing.id)

        listing.refresh_from_db()
        assert result["quantity"] == 10
        assert listing.status == listing.Status.CANCELLED

    def test_cancel_listing_success(self, seller_manor, tradeable_item_template):
        initial_quantity = InventoryItem.objects.get(
            manor=seller_manor, template=tradeable_item_template, storage_location="warehouse"
        ).quantity

        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )

        result = market_service.cancel_listing(seller_manor, listing.id)

        assert result["quantity"] == 10

        inventory = InventoryItem.objects.get(
            manor=seller_manor, template=tradeable_item_template, storage_location="warehouse"
        )
        assert inventory.quantity == initial_quantity

        listing.refresh_from_db()
        assert listing.status == listing.Status.CANCELLED

    def test_cancel_others_listing(self, seller_manor, buyer_manor):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )

        with pytest.raises(TradeValidationError, match="无权取消"):
            market_service.cancel_listing(buyer_manor, listing.id)

    def test_cancel_listing_restores_inventory_when_create_races(
        self, seller_manor, tradeable_item_template, monkeypatch
    ):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )
        InventoryItem.objects.filter(
            manor=seller_manor,
            template=tradeable_item_template,
            storage_location="warehouse",
        ).delete()

        original_create = InventoryItem.objects.create

        def _race_create(**kwargs):
            original_create(**{**kwargs, "quantity": 0})
            raise IntegrityError("duplicate key value violates unique constraint")

        monkeypatch.setattr(InventoryItem.objects, "create", _race_create)

        result = market_service.cancel_listing(seller_manor, listing.id)

        assert result["quantity"] == 10
        inventory = InventoryItem.objects.get(
            manor=seller_manor,
            template=tradeable_item_template,
            storage_location="warehouse",
        )
        assert inventory.quantity == 10
