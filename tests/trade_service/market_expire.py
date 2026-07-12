from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate
from trade.models import MarketListing
from trade.services import market_service

pytest_plugins = ("tests.trade_service.fixtures",)


@pytest.mark.django_db
class TestMarketExpire:
    def test_expire_listings(self, seller_manor, tradeable_item_template):
        initial_quantity = seller_manor.inventory_items.get(
            template=tradeable_item_template,
            storage_location="warehouse",
        ).quantity
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )
        listing_id = listing.id

        listing.expires_at = timezone.now() - timedelta(hours=1)
        listing.save()

        count = market_service.expire_listings()

        assert count == 1
        assert not MarketListing.objects.filter(id=listing_id).exists()

        inventory = seller_manor.inventory_items.get(
            template=tradeable_item_template,
            storage_location="warehouse",
        )
        assert inventory.quantity == initial_quantity

        from gameplay.models import Message

        message = Message.objects.filter(manor=seller_manor, kind="system", title__contains="交易过期").first()
        assert message is not None
        assert message.attachments == {}

    def test_expired_grain_listing_restores_both_balances(self, seller_manor):
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
        listing.expires_at = timezone.now() - timedelta(hours=1)
        listing.save(update_fields=["expires_at"])

        assert market_service.expire_listings() == 1

        seller_manor.refresh_from_db()
        inventory = InventoryItem.objects.get(
            manor=seller_manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        assert seller_manor.grain == 20
        assert inventory.quantity == 20

    def test_expire_listings_still_returns_inventory_when_create_message_fails(
        self, seller_manor, tradeable_item_template, monkeypatch
    ):
        initial_quantity = seller_manor.inventory_items.get(
            template=tradeable_item_template,
            storage_location="warehouse",
        ).quantity
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )
        listing_id = listing.id

        listing.expires_at = timezone.now() - timedelta(hours=1)
        listing.save()

        def _raise_message_error(*_args, **_kwargs):
            raise ConnectionError("message backend down")

        monkeypatch.setattr(market_service, "send_market_message", _raise_message_error)

        count = market_service.expire_listings()

        assert count == 1
        assert not MarketListing.objects.filter(id=listing_id).exists()
        inventory = seller_manor.inventory_items.get(
            template=tradeable_item_template,
            storage_location="warehouse",
        )
        assert inventory.quantity == initial_quantity

    def test_expire_listings_still_completes_when_notify_fails(
        self, seller_manor, tradeable_item_template, monkeypatch
    ):
        initial_quantity = seller_manor.inventory_items.get(
            template=tradeable_item_template,
            storage_location="warehouse",
        ).quantity
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )
        listing_id = listing.id

        listing.expires_at = timezone.now() - timedelta(hours=1)
        listing.save()

        def _raise_notify_error(*_args, **_kwargs):
            raise OSError("ws unavailable")

        monkeypatch.setattr(market_service, "send_market_notification", _raise_notify_error)

        count = market_service.expire_listings()
        assert count == 1
        assert not MarketListing.objects.filter(id=listing_id).exists()
        inventory = seller_manor.inventory_items.get(
            template=tradeable_item_template,
            storage_location="warehouse",
        )
        assert inventory.quantity == initial_quantity

    def test_expire_listings_programming_error_bubbles_up_during_inventory_restore(
        self, seller_manor, tradeable_item_template, monkeypatch
    ):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )
        listing.expires_at = timezone.now() - timedelta(hours=1)
        listing.save()

        monkeypatch.setattr(
            market_service,
            "grant_market_item_locked",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("inventory restore bug")),
        )

        with pytest.raises(RuntimeError, match="inventory restore bug"):
            market_service.expire_listings()
