from __future__ import annotations

import pytest
from django.db import transaction
from django.test import RequestFactory

from core.exceptions import ItemInsufficientError, TradeValidationError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from trade.models import AuctionBid, AuctionRound, AuctionSlot, FrozenGoldBar, MarketListing
from trade.page_context import build_trade_request_params
from trade.selectors import get_trade_context
from trade.services import market_service
from trade.services.auction import gold_bars
from trade.services.shop_service import get_sellable_inventory, sell_item

pytest_plugins = ("tests.trade_service.fixtures",)
pytestmark = pytest.mark.django_db


def _set_mixed_stock(manor, template, *, quantity=100, bound_quantity=90):
    row, _ = InventoryItem.objects.update_or_create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": quantity, "bound_quantity": bound_quantity},
    )
    return row


def _list(manor, quantity=10):
    return market_service.create_listing(
        manor=manor, item_key="test_tradeable_item", quantity=quantity, unit_price=2000, duration=7200
    )


def test_bound_stock_cannot_back_a_listing_or_charge_a_fee(seller_manor, tradeable_item_template):
    stock = _set_mixed_stock(seller_manor, tradeable_item_template)
    silver_before = seller_manor.silver
    with pytest.raises(TradeValidationError, match="非绑定"):
        _list(seller_manor, 11)
    stock.refresh_from_db()
    seller_manor.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (100, 90)
    assert seller_manor.silver == silver_before
    assert not MarketListing.objects.filter(seller=seller_manor).exists()


@pytest.mark.parametrize("finish", ["cancel", "purchase"])
def test_market_removes_only_unbound_and_returns_unbound_despite_template_default(
    finish, seller_manor, buyer_manor, tradeable_item_template
):
    tradeable_item_template.default_bound = True
    tradeable_item_template.save(update_fields=["default_bound"])
    stock = _set_mixed_stock(seller_manor, tradeable_item_template)
    listing = _list(seller_manor)
    stock.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (90, 90)
    if finish == "cancel":
        market_service.cancel_listing(seller_manor, listing.pk)
        stock.refresh_from_db()
        assert (stock.quantity, stock.bound_quantity) == (100, 90)
    else:
        _set_mixed_stock(buyer_manor, tradeable_item_template, quantity=5, bound_quantity=5)
        market_service.purchase_listing(buyer_manor, listing.pk)
        received = InventoryItem.objects.get(manor=buyer_manor, template=tradeable_item_template)
        assert (received.quantity, received.bound_quantity) == (15, 5)
        stock.refresh_from_db()
        assert (stock.quantity, stock.bound_quantity) == (90, 90)


def test_market_sell_page_exposes_only_unbound_quantity(seller_manor, tradeable_item_template):
    _set_mixed_stock(seller_manor, tradeable_item_template)
    request = RequestFactory().get("/trade", {"tab": "market", "view": "sell"})
    context = get_trade_context(manor=seller_manor, params=build_trade_request_params(request))
    rows = list(context["tradeable_items"])
    row = next(item for item in rows if item.template.pk == tradeable_item_template.pk)
    assert row.quantity == 10
    assert row.inventory_item.quantity == 100
    InventoryItem.objects.filter(pk=row.inventory_item.pk).update(bound_quantity=100)
    assert not market_service.get_tradeable_inventory(seller_manor).filter(template=tradeable_item_template).exists()


def test_auction_freeze_and_settlement_preserve_bound_gold(seller_manor, tradeable_item_template):
    template, _ = ItemTemplate.objects.get_or_create(key="gold_bar", defaults={"name": "金条"})
    stock = _set_mixed_stock(seller_manor, template, quantity=12, bound_quantity=8)
    round_obj = AuctionRound.objects.create(
        round_number=8999,
        status=AuctionRound.Status.ACTIVE,
        start_at=seller_manor.created_at,
        end_at=seller_manor.created_at,
    )
    slot = AuctionSlot.objects.create(
        round=round_obj,
        item_template=tradeable_item_template,
        quantity=1,
        starting_price=1,
        current_price=1,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key="bound_trading_test",
        slot_index=0,
    )
    bid = AuctionBid.objects.create(slot=slot, manor=seller_manor, amount=3, status=AuctionBid.Status.ACTIVE)
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=seller_manor.pk)
        frozen = gold_bars.freeze_gold_bars(locked_manor, 3, bid)
        with pytest.raises(TradeValidationError, match="可用金条不足"):
            gold_bars.freeze_gold_bars(locked_manor, 2, bid)
    assert gold_bars.get_available_gold_bars(seller_manor) == 1
    assert FrozenGoldBar.objects.filter(manor=seller_manor, is_frozen=True).count() == 1
    gold_bars.consume_frozen_gold_bars(frozen, seller_manor)
    stock.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (9, 8)
    assert gold_bars.get_available_gold_bars(seller_manor) == 1


def test_npc_gold_spending_can_use_bound_gold_but_keeps_auction_collateral(seller_manor, tradeable_item_template):
    template, _ = ItemTemplate.objects.get_or_create(key="gold_bar", defaults={"name": "金条"})
    stock = _set_mixed_stock(seller_manor, template, quantity=12, bound_quantity=8)
    FrozenGoldBar.objects.create(manor=seller_manor, amount=4, is_frozen=True)
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=seller_manor.pk)
        gold_bars.consume_available_gold_bars_locked(locked_manor, 8)
    stock.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (4, 0)
    assert gold_bars.get_available_gold_bars(seller_manor) == 0


@pytest.mark.parametrize("item_key", ["bound_sell_item", "gold_bar"])
def test_npc_selling_rejects_bound_and_preserves_it_when_selling_unbound(seller_manor, item_key):
    template, _ = ItemTemplate.objects.update_or_create(
        key=item_key,
        defaults={"name": "绑定出售检查", "price": 10},
    )
    stock = _set_mixed_stock(seller_manor, template, quantity=5, bound_quantity=3)
    silver_before = seller_manor.silver
    with pytest.raises(ItemInsufficientError):
        sell_item(seller_manor, item_key, 3)
    seller_manor.refresh_from_db()
    stock.refresh_from_db()
    assert seller_manor.silver == silver_before
    assert (stock.quantity, stock.bound_quantity) == (5, 3)
    sell_item(seller_manor, item_key, 2)
    stock.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (3, 3)


def test_npc_sell_list_exposes_only_unbound_quantity(seller_manor, tradeable_item_template):
    stock = _set_mixed_stock(seller_manor, tradeable_item_template)
    rows = get_sellable_inventory(seller_manor)
    row = next(item for item in rows if item.inventory_item.pk == stock.pk)
    assert row.quantity == 10
    assert row.inventory_item.quantity == 100
    InventoryItem.objects.filter(pk=stock.pk).update(bound_quantity=100)
    assert not any(item.inventory_item.pk == stock.pk for item in get_sellable_inventory(seller_manor))


def test_raid_preserves_bound_gold_and_auction_collateral(seller_manor):
    from gameplay.services.raid.combat.loot import _apply_loot

    template, _ = ItemTemplate.objects.get_or_create(key="gold_bar", defaults={"name": "金条"})
    stock = _set_mixed_stock(seller_manor, template, quantity=12, bound_quantity=8)
    FrozenGoldBar.objects.create(manor=seller_manor, amount=3, is_frozen=True)
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=seller_manor.pk)
        resources, items = _apply_loot(locked_manor, {}, {"gold_bar": 12}, locked_manor=locked_manor)
    stock.refresh_from_db()
    assert resources == {}
    assert items == {"gold_bar": 1}
    assert (stock.quantity, stock.bound_quantity) == (11, 8)
    assert gold_bars.get_frozen_gold_bars(seller_manor) == 3
    assert gold_bars.get_available_gold_bars(seller_manor) == 0
