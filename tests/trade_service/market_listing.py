from __future__ import annotations

import pytest
from django.utils import timezone

from core.exceptions import TradeValidationError
from gameplay.models import InventoryItem, ItemTemplate
from trade.models import MarketListing
from trade.services import market_service

pytest_plugins = ("tests.trade_service.fixtures",)


@pytest.mark.django_db
class TestMarketListing:
    def test_create_listing_success(self, seller_manor, tradeable_item_template):
        initial_silver = seller_manor.silver
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

        assert listing is not None
        assert listing.quantity == 10
        assert listing.unit_price == 2000
        assert listing.total_price == 20000
        assert listing.status == MarketListing.Status.ACTIVE

        seller_manor.refresh_from_db()
        assert seller_manor.silver == initial_silver - market_service.LISTING_FEES[7200]

        inventory = InventoryItem.objects.filter(
            manor=seller_manor, template=tradeable_item_template, storage_location="warehouse"
        ).first()
        assert inventory.quantity == initial_quantity - 10

    def test_grain_listing_and_cancel_keep_manor_and_inventory_balances_in_sync(self, seller_manor):
        grain_template, _ = ItemTemplate.objects.get_or_create(
            key="grain",
            defaults={
                "name": "粮食",
                "effect_type": ItemTemplate.EffectType.RESOURCE,
                "tradeable": True,
                "price": 1000,
            },
        )
        grain_template.tradeable = True
        grain_template.price = 1000
        grain_template.save(update_fields=["tradeable", "price"])
        seller_manor.grain = 20
        seller_manor.resource_updated_at = timezone.now()
        seller_manor.save(update_fields=["grain", "resource_updated_at"])
        InventoryItem.objects.update_or_create(
            manor=seller_manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": 20},
        )

        listing = market_service.create_listing(
            manor=seller_manor, item_key="grain", quantity=5, unit_price=2000, duration=7200
        )

        seller_manor.refresh_from_db()
        inventory = InventoryItem.objects.get(
            manor=seller_manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        assert seller_manor.grain == 15
        assert inventory.quantity == 15

        market_service.cancel_listing(seller_manor, listing.id)

        seller_manor.refresh_from_db()
        inventory.refresh_from_db()
        assert seller_manor.grain == 20
        assert inventory.quantity == 20

    def test_create_listing_untradeable_item(self, seller_manor, untradeable_item_template):
        InventoryItem.objects.create(
            manor=seller_manor,
            template=untradeable_item_template,
            quantity=10,
            storage_location="warehouse",
        )

        with pytest.raises(TradeValidationError, match="不可交易"):
            market_service.create_listing(
                manor=seller_manor,
                item_key="test_untradeable_item",
                quantity=5,
                unit_price=1000,
                duration=7200,
            )

    def test_create_listing_insufficient_quantity(self, seller_manor):
        with pytest.raises(TradeValidationError, match="数量不足"):
            market_service.create_listing(
                manor=seller_manor,
                item_key="test_tradeable_item",
                quantity=1000,
                unit_price=2000,
                duration=7200,
            )

    def test_create_listing_insufficient_silver(self, seller_manor):
        seller_manor.silver = 100
        seller_manor.save()

        with pytest.raises(TradeValidationError, match="银两不足"):
            market_service.create_listing(
                manor=seller_manor,
                item_key="test_tradeable_item",
                quantity=10,
                unit_price=2000,
                duration=7200,
            )

    def test_create_listing_price_too_low(self, seller_manor, tradeable_item_template):
        with pytest.raises(TradeValidationError, match="不能低于"):
            market_service.create_listing(
                manor=seller_manor,
                item_key="test_tradeable_item",
                quantity=10,
                unit_price=500,
                duration=7200,
            )
