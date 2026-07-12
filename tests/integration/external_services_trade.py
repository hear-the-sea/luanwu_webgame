from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from trade.models import AuctionBid, AuctionRound, AuctionSlot, MarketListing
from trade.services.auction_service import place_bid, settle_auction_round
from trade.services.market_service import cancel_listing, create_listing, purchase_listing
from trade.services.shop_service import sell_item

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("load_guest_data", "load_troop_data")]


@pytest.mark.django_db(transaction=True)
def test_integration_market_purchase_flow(require_env_services, django_user_model):
    seller_user = django_user_model.objects.create_user(
        username=f"intg_seller_{uuid.uuid4().hex[:8]}", password="pass123"
    )
    buyer_user = django_user_model.objects.create_user(
        username=f"intg_buyer_{uuid.uuid4().hex[:8]}", password="pass123"
    )
    seller = ensure_manor(seller_user)
    buyer = ensure_manor(buyer_user)

    seller.silver = 100000
    seller.silver_capacity = 300000
    buyer.silver = 200000
    buyer.silver_capacity = 300000
    seller.save(update_fields=["silver", "silver_capacity"])
    buyer.save(update_fields=["silver", "silver_capacity"])

    item_key = f"intg_market_item_{uuid.uuid4().hex[:8]}"
    template = ItemTemplate.objects.create(
        key=item_key,
        name="集成测试交易物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=True,
        price=1000,
    )
    InventoryItem.objects.create(
        manor=seller,
        template=template,
        quantity=5,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    listing = create_listing(seller, item_key, quantity=2, unit_price=3000, duration=7200)
    transaction_record = purchase_listing(buyer, listing.id)

    listing.refresh_from_db()
    buyer_item = InventoryItem.objects.get(
        manor=buyer,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert listing.status == MarketListing.Status.SOLD
    assert transaction_record.total_price == 6000
    assert buyer_item.quantity == 2


@pytest.mark.django_db(transaction=True)
def test_integration_grain_cancel_and_shop_sale_keep_lock_order_and_balances(require_env_services, django_user_model):
    user = django_user_model.objects.create_user(
        username=f"intg_grain_lock_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    manor = ensure_manor(user)
    manor.grain = 20
    manor.silver = 100000
    manor.silver_capacity = 300000
    manor.resource_updated_at = timezone.now()
    manor.save(update_fields=["grain", "silver", "silver_capacity", "resource_updated_at"])
    grain_template, _ = ItemTemplate.objects.get_or_create(
        key="grain",
        defaults={
            "name": "粮食",
            "effect_type": ItemTemplate.EffectType.RESOURCE,
            "tradeable": True,
            "price": 1,
        },
    )
    grain_template.tradeable = True
    grain_template.price = 1
    grain_template.save(update_fields=["tradeable", "price"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 20},
    )
    listing = create_listing(manor, "grain", quantity=5, unit_price=1000, duration=7200)
    start = Barrier(2)

    def _cancel() -> None:
        close_old_connections()
        try:
            local_manor = type(manor).objects.get(pk=manor.pk)
            start.wait(timeout=10)
            cancel_listing(local_manor, listing.pk)
        finally:
            close_old_connections()

    def _sell() -> None:
        close_old_connections()
        try:
            local_manor = type(manor).objects.get(pk=manor.pk)
            start.wait(timeout=10)
            sell_item(local_manor, "grain", 1)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_cancel), executor.submit(_sell)]
        for future in futures:
            future.result(timeout=20)

    manor.refresh_from_db()
    inventory = InventoryItem.objects.get(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    listing.refresh_from_db()
    assert listing.status == MarketListing.Status.CANCELLED
    assert manor.grain == 19
    assert inventory.quantity == 19


@pytest.mark.django_db(transaction=True)
def test_integration_auction_bid_and_settlement_flow(require_env_services, django_user_model):
    bidder_user = django_user_model.objects.create_user(
        username=f"intg_bidder_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    bidder = ensure_manor(bidder_user)

    gold_bar_tpl, _ = ItemTemplate.objects.get_or_create(
        key="gold_bar",
        defaults={
            "name": "金条",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": False,
            "tradeable": False,
        },
    )
    InventoryItem.objects.update_or_create(
        manor=bidder,
        template=gold_bar_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 10},
    )

    auction_item = ItemTemplate.objects.create(
        key=f"intg_auction_item_{uuid.uuid4().hex[:8]}",
        name="集成测试拍卖物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=5000,
    )
    round_number = int(time.time())
    auction_round = AuctionRound.objects.create(
        round_number=round_number,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(minutes=1),
        end_at=timezone.now() + timedelta(minutes=5),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=auction_item,
        quantity=1,
        starting_price=2,
        current_price=2,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=auction_item.key,
        slot_index=0,
    )

    bid, _is_first = place_bid(bidder, slot.id, 5)
    stats = settle_auction_round(round_id=auction_round.id)

    bid.refresh_from_db()
    bid_item = InventoryItem.objects.get(
        manor=bidder,
        template=gold_bar_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert bid.status == AuctionBid.Status.WON
    assert stats["settled"] == 1
    assert stats["sold"] == 1
    assert bid_item.quantity == 5
