from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import MessageError, TradeValidationError
from gameplay.models import InventoryItem, Manor
from trade.models import MarketListing, MarketTransaction
from trade.services import market_service

pytest_plugins = ("tests.trade_service.fixtures",)


@pytest.mark.django_db
class TestMarketPurchase:
    def test_purchase_listing_success(self, seller_manor, buyer_manor, tradeable_item_template):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )

        seller_initial_silver = Manor.objects.get(pk=seller_manor.pk).silver
        buyer_initial_silver = buyer_manor.silver

        transaction = market_service.purchase_listing(buyer_manor, listing.id)

        assert transaction is not None
        assert transaction.total_price == 20000
        assert transaction.tax_amount == 2000
        assert transaction.seller_received == 18000

        buyer_manor.refresh_from_db()
        assert buyer_manor.silver == buyer_initial_silver - 20000

        seller_manor.refresh_from_db()
        assert seller_manor.silver == seller_initial_silver + 18000

        buyer_inventory = InventoryItem.objects.filter(
            manor=buyer_manor, template=tradeable_item_template, storage_location="warehouse"
        ).first()
        assert buyer_inventory is not None
        assert buyer_inventory.quantity == 10

        listing.refresh_from_db()
        assert listing.status == MarketListing.Status.SOLD

    def test_purchase_listing_disables_production_sync_for_seller_income(
        self,
        seller_manor,
        buyer_manor,
        tradeable_item_template,
        monkeypatch,
    ):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )

        observed: dict[str, object] = {}
        original_settle_market_sale_proceeds = market_service.settle_market_sale_proceeds

        def _capture_settle_market_sale_proceeds(manor, *, item_name: str, silver_amount: int):
            observed["item_name"] = item_name
            observed["rewards"] = {"silver": silver_amount}
            return original_settle_market_sale_proceeds(
                manor,
                item_name=item_name,
                silver_amount=silver_amount,
            )

        monkeypatch.setattr(
            market_service,
            "settle_market_sale_proceeds",
            _capture_settle_market_sale_proceeds,
        )

        transaction = market_service.purchase_listing(buyer_manor, listing.id)

        assert transaction.seller_received == 18000
        assert observed == {"item_name": "测试可交易物品", "rewards": {"silver": 18000}}

    def test_purchase_own_listing(self, seller_manor):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )

        with pytest.raises(TradeValidationError, match="不能购买自己"):
            market_service.purchase_listing(seller_manor, listing.id)

    def test_purchase_insufficient_silver(self, seller_manor, buyer_manor):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=100000,
            duration=7200,
        )

        buyer_manor.silver = 1000
        buyer_manor.save()

        with pytest.raises(TradeValidationError, match="银两不足"):
            market_service.purchase_listing(buyer_manor, listing.id)

    def test_purchase_rejects_seller_income_overflow_and_rolls_back(
        self, seller_manor, buyer_manor, tradeable_item_template
    ):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )
        seller_manor.refresh_from_db()
        seller_manor.silver_capacity = seller_manor.silver + 5
        seller_manor.save(update_fields=["silver_capacity"])
        seller_silver_before = seller_manor.silver
        buyer_silver_before = buyer_manor.silver

        with pytest.raises(TradeValidationError, match="卖家银库空间不足"):
            market_service.purchase_listing(buyer_manor, listing.id)

        seller_manor.refresh_from_db()
        buyer_manor.refresh_from_db()
        listing.refresh_from_db()
        assert seller_manor.silver == seller_silver_before
        assert buyer_manor.silver == buyer_silver_before
        assert listing.status == MarketListing.Status.ACTIVE
        assert not MarketTransaction.objects.filter(listing=listing).exists()
        assert not InventoryItem.objects.filter(
            manor=buyer_manor,
            template=tradeable_item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).exists()

    def test_purchase_expired_listing(self, seller_manor, buyer_manor):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )

        listing.expires_at = timezone.now() - timedelta(hours=1)
        listing.save()

        with pytest.raises(TradeValidationError, match="已过期"):
            market_service.purchase_listing(buyer_manor, listing.id)

    def test_purchase_listing_succeeds_when_message_send_fails(
        self, seller_manor, buyer_manor, tradeable_item_template, monkeypatch
    ):
        listing = market_service.create_listing(
            manor=seller_manor,
            item_key="test_tradeable_item",
            quantity=10,
            unit_price=2000,
            duration=7200,
        )

        def _raise_message_error(**_kwargs):
            raise MessageError("message backend unavailable")

        monkeypatch.setattr(market_service, "send_market_message", _raise_message_error)

        transaction = market_service.purchase_listing(buyer_manor, listing.id)

        listing.refresh_from_db()
        assert listing.status == MarketListing.Status.SOLD

        buyer_inventory = InventoryItem.objects.filter(
            manor=buyer_manor, template=tradeable_item_template, storage_location="warehouse"
        ).first()
        assert buyer_inventory is not None
        assert buyer_inventory.quantity == 10

        tx = MarketTransaction.objects.get(pk=transaction.pk)
        assert tx.buyer_mail_sent is False
        assert tx.seller_mail_sent is False
