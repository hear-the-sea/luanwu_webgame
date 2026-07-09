from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import TestCase
from django.utils import timezone

from core.exceptions import MessageError
from gameplay.models import InventoryItem, Message
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.messages import claim_message_attachments
from tests.helpers.auction import AuctionSlotBidSpec, create_slot_with_bids
from tests.helpers.auction import ensure_auction_item_template as _create_auction_item_template
from tests.helpers.auction import ensure_gold_bar_template as _ensure_gold_bar_template
from trade.models import AuctionBid, AuctionRound, AuctionSlot, FrozenGoldBar


@pytest.mark.django_db
def test_rounds_module_settle_slot_delivers_item_via_message_attachment(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    slot_with_bids = create_slot_with_bids(
        django_user_model=django_user_model,
        bid_specs=[AuctionSlotBidSpec(username="auction_rounds_attachment_flow", amount=20)],
        item_key="auction_settle_attachment_item",
        round_number=10014,
        starting_price=10,
        min_increment=1,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = slot_with_bids.slot
    bid = slot_with_bids.bids[0]
    manor = slot_with_bids.manors_by_username["auction_rounds_attachment_flow"]
    item_tpl = slot.item_template
    gold_tpl = _ensure_gold_bar_template()
    InventoryItem.objects.create(
        manor=manor,
        template=gold_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=30,
    )
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *a, **k: True)

    with TestCase.captureOnCommitCallbacks(execute=True):
        result = _settle_slot(slot)

    bid.refresh_from_db()
    slot.refresh_from_db()

    assert result["sold"] is True
    assert slot.status == AuctionSlot.Status.SOLD
    assert bid.status == AuctionBid.Status.WON
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=item_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()

    message = Message.objects.filter(manor=manor, title__contains="拍卖行").order_by("-id").first()
    assert message is not None
    assert message.is_claimed is False
    assert message.attachments.get("items", {}).get(item_tpl.key) == 1

    claim_message_attachments(message)
    item_after_claim = InventoryItem.objects.get(
        manor=manor,
        template=item_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert item_after_claim.quantity == 1


@pytest.mark.django_db
def test_rounds_module_settle_slot_persists_pending_delivery_when_on_commit_is_lost(monkeypatch, django_user_model):
    from trade.models import AuctionDelivery
    from trade.services.auction.delivery_outbox import process_pending_auction_deliveries
    from trade.services.auction.rounds import _settle_slot

    slot_with_bids = create_slot_with_bids(
        django_user_model=django_user_model,
        bid_specs=[AuctionSlotBidSpec(username="auction_rounds_outbox_flow", amount=20)],
        item_key="auction_settle_outbox_item",
        round_number=10020,
        starting_price=10,
        min_increment=1,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = slot_with_bids.slot
    bid = slot_with_bids.bids[0]
    manor = slot_with_bids.manors_by_username["auction_rounds_outbox_flow"]
    item_tpl = slot.item_template
    gold_tpl = _ensure_gold_bar_template()
    InventoryItem.objects.create(
        manor=manor,
        template=gold_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=30,
    )
    monkeypatch.setattr(
        "trade.services.auction.rounds_settlement_support.transaction.on_commit",
        lambda callback: None,
    )
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *a, **k: True)

    result = _settle_slot(slot)

    bid.refresh_from_db()
    slot.refresh_from_db()
    assert result["sold"] is True
    assert slot.status == AuctionSlot.Status.SOLD
    assert bid.status == AuctionBid.Status.WON
    assert not Message.objects.filter(manor=manor, title__contains="拍卖行").exists()
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=item_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()

    delivery = AuctionDelivery.objects.get(bid=bid)
    assert delivery.status == AuctionDelivery.Status.PENDING
    assert delivery.manor == manor
    assert delivery.slot == slot
    assert delivery.item_template == item_tpl
    assert delivery.quantity == 1
    assert delivery.settlement_price == 20

    assert process_pending_auction_deliveries() == 1

    delivery.refresh_from_db()
    assert delivery.status == AuctionDelivery.Status.DELIVERED
    message = Message.objects.get(pk=delivery.message_id)
    assert message.manor == manor
    assert message.attachments.get("items", {}).get(item_tpl.key) == 1

    claim_message_attachments(message)
    item_after_claim = InventoryItem.objects.get(
        manor=manor,
        template=item_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert item_after_claim.quantity == 1


@pytest.mark.django_db
def test_rounds_module_settle_slot_falls_back_to_direct_grant_when_message_create_fails(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user = django_user_model.objects.create_user(username="auction_rounds_message_fallback", password="pass123")
    manor = ensure_manor(user)

    item_tpl = _create_auction_item_template("auction_settle_direct_grant_item")
    gold_tpl = _ensure_gold_bar_template()
    InventoryItem.objects.create(
        manor=manor,
        template=gold_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=30,
    )
    auction_round = AuctionRound.objects.create(
        round_number=10015,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_tpl,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=item_tpl.key,
        slot_index=0,
    )
    bid = AuctionBid.objects.create(
        slot=slot,
        manor=manor,
        amount=20,
        status=AuctionBid.Status.ACTIVE,
        frozen_gold_bars=20,
    )
    FrozenGoldBar.objects.create(
        manor=manor,
        amount=20,
        reason=FrozenGoldBar.Reason.AUCTION_BID,
        auction_bid=bid,
        is_frozen=True,
    )

    monkeypatch.setattr(
        "trade.services.auction.rounds.create_message",
        lambda *a, **k: (_ for _ in ()).throw(MessageError("message unavailable")),
    )
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *a, **k: True)

    with TestCase.captureOnCommitCallbacks(execute=True):
        result = _settle_slot(slot)

    bid.refresh_from_db()
    slot.refresh_from_db()

    assert result["sold"] is True
    assert slot.status == AuctionSlot.Status.SOLD
    assert bid.status == AuctionBid.Status.WON
    assert not Message.objects.filter(manor=manor, title__contains="拍卖行").exists()

    granted_item = InventoryItem.objects.get(
        manor=manor,
        template=item_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert granted_item.quantity == 1


@pytest.mark.django_db
def test_rounds_module_settle_slot_runtime_marker_message_error_bubbles_up(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user = django_user_model.objects.create_user(username="auction_rounds_message_runtime", password="pass123")
    manor = ensure_manor(user)

    item_tpl = _create_auction_item_template("auction_settle_direct_runtime_item")
    gold_tpl = _ensure_gold_bar_template()
    InventoryItem.objects.create(
        manor=manor,
        template=gold_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=30,
    )
    auction_round = AuctionRound.objects.create(
        round_number=10016,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_tpl,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=item_tpl.key,
        slot_index=0,
    )
    bid = AuctionBid.objects.create(
        slot=slot,
        manor=manor,
        amount=20,
        status=AuctionBid.Status.ACTIVE,
        frozen_gold_bars=20,
    )
    FrozenGoldBar.objects.create(
        manor=manor,
        amount=20,
        reason=FrozenGoldBar.Reason.AUCTION_BID,
        auction_bid=bid,
        is_frozen=True,
    )

    monkeypatch.setattr(
        "trade.services.auction.rounds.create_message",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *a, **k: True)

    with pytest.raises(RuntimeError, match="message backend down"):
        with TestCase.captureOnCommitCallbacks(execute=True):
            _settle_slot(slot)

    assert not Message.objects.filter(manor=manor, title__contains="拍卖行").exists()
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=item_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()


@pytest.mark.django_db
def test_rounds_module_settle_slot_ignores_notify_failure(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user = django_user_model.objects.create_user(username="auction_rounds_notify_fail", password="pass123")
    manor = ensure_manor(user)

    item_tpl = _create_auction_item_template("auction_settle_notify_fail_item")
    auction_round = AuctionRound.objects.create(
        round_number=10008,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_tpl,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=item_tpl.key,
        slot_index=0,
    )
    bid = AuctionBid.objects.create(
        slot=slot, manor=manor, amount=20, status=AuctionBid.Status.ACTIVE, frozen_gold_bars=20
    )

    FrozenGoldBar.objects.create(
        manor=manor,
        amount=20,
        reason=FrozenGoldBar.Reason.AUCTION_BID,
        auction_bid=bid,
        is_frozen=True,
    )

    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_inventory_item_for_manor_locked", lambda *a, **k: None
    )
    monkeypatch.setattr("trade.services.auction.rounds.create_message", lambda *a, **k: None)
    monkeypatch.setattr(
        "trade.services.auction.rounds.notify_user",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("ws unavailable")),
    )

    result = _settle_slot(slot)

    bid.refresh_from_db()
    slot.refresh_from_db()
    assert result["sold"] is True
    assert slot.status == AuctionSlot.Status.SOLD
    assert bid.status == AuctionBid.Status.WON


@pytest.mark.django_db
def test_rounds_module_settle_slot_runtime_marker_notify_error_bubbles_up(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user = django_user_model.objects.create_user(username="auction_rounds_notify_runtime", password="pass123")
    manor = ensure_manor(user)

    item_tpl = _create_auction_item_template("auction_settle_notify_runtime_item")
    auction_round = AuctionRound.objects.create(
        round_number=10017,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_tpl,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=item_tpl.key,
        slot_index=0,
    )
    bid = AuctionBid.objects.create(
        slot=slot, manor=manor, amount=20, status=AuctionBid.Status.ACTIVE, frozen_gold_bars=20
    )
    FrozenGoldBar.objects.create(
        manor=manor,
        amount=20,
        reason=FrozenGoldBar.Reason.AUCTION_BID,
        auction_bid=bid,
        is_frozen=True,
    )

    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_inventory_item_for_manor_locked", lambda *a, **k: None
    )
    monkeypatch.setattr("trade.services.auction.rounds.create_message", lambda *a, **k: None)
    monkeypatch.setattr(
        "trade.services.auction.rounds.notify_user",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ws backend down")),
    )

    with pytest.raises(RuntimeError, match="ws backend down"):
        with TestCase.captureOnCommitCallbacks(execute=True):
            _settle_slot(slot)
