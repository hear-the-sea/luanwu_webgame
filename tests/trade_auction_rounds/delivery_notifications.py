from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.db import DatabaseError, IntegrityError, OperationalError, ProgrammingError
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone

from core.exceptions import MessageError
from gameplay.models import InventoryItem, Message
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.messages import claim_message_attachments
from tests.helpers.auction import AuctionSlotBidSpec, create_slot_with_bids
from tests.helpers.auction import ensure_auction_item_template as _create_auction_item_template
from tests.helpers.auction import ensure_gold_bar_template as _ensure_gold_bar_template
from trade.models import AuctionBid, AuctionDelivery, AuctionRound, AuctionSlot, FrozenGoldBar


def _create_pending_delivery_pair(django_user_model, *, suffix: str) -> tuple[AuctionDelivery, AuctionDelivery]:
    setup = create_slot_with_bids(
        django_user_model=django_user_model,
        bid_specs=[
            AuctionSlotBidSpec(username=f"auction_delivery_{suffix}_first", amount=20),
            AuctionSlotBidSpec(username=f"auction_delivery_{suffix}_second", amount=15),
        ],
        item_key=f"auction_delivery_{suffix}_item",
        round_number=11000,
        quantity=2,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    deliveries = [
        AuctionDelivery.objects.create(
            slot=setup.slot,
            bid=bid,
            manor=bid.manor,
            item_template=setup.slot.item_template,
            quantity=1,
            settlement_price=15,
            total_winners=2,
        )
        for bid in setup.bids
    ]
    return deliveries[0], deliveries[1]


def _create_message_with_integrity_error(**kwargs):
    return Message.objects.create(**{**kwargs, "manor": None})


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
def test_pending_delivery_scan_records_integrity_error_and_continues(monkeypatch, django_user_model, caplog):
    from trade.services.auction import delivery_outbox

    first, second = _create_pending_delivery_pair(django_user_model, suffix="poison")
    processed_ids = []
    poison_error = IntegrityError("invalid delivery payload")

    def _process(delivery_id):
        processed_ids.append(delivery_id)
        if delivery_id == first.id:
            raise poison_error
        AuctionDelivery.objects.filter(pk=delivery_id).update(
            status=AuctionDelivery.Status.DELIVERED,
            delivered_at=timezone.now(),
        )
        return True

    monkeypatch.setattr(delivery_outbox, "process_auction_delivery", _process)

    with caplog.at_level(logging.ERROR):
        delivered_count = delivery_outbox.process_pending_auction_deliveries()

    first.refresh_from_db()
    second.refresh_from_db()
    assert processed_ids == [first.id, second.id]
    assert delivered_count == 1
    assert first.status == AuctionDelivery.Status.PENDING
    assert first.attempts == 1
    assert "IntegrityError: invalid delivery payload" in first.last_error
    assert second.status == AuctionDelivery.Status.DELIVERED
    assert "deterministic auction delivery failure" in caplog.text


@pytest.mark.django_db
def test_pending_delivery_scan_records_operational_error_and_reraises_same_exception(monkeypatch, django_user_model):
    from trade.services.auction import delivery_outbox

    first, _second = _create_pending_delivery_pair(django_user_model, suffix="transient")
    transient_error = OperationalError("database connection lost")
    monkeypatch.setattr(
        delivery_outbox,
        "process_auction_delivery",
        lambda _delivery_id: (_ for _ in ()).throw(transient_error),
    )

    with pytest.raises(OperationalError) as exc_info:
        delivery_outbox.process_pending_auction_deliveries()

    first.refresh_from_db()
    assert exc_info.value is transient_error
    assert first.status == AuctionDelivery.Status.PENDING
    assert first.attempts == 1
    assert "OperationalError: database connection lost" in first.last_error


@pytest.mark.django_db
def test_pending_delivery_scan_record_failure_does_not_replace_transient_error(monkeypatch, django_user_model):
    from trade.services.auction import delivery_outbox

    _first, _second = _create_pending_delivery_pair(django_user_model, suffix="record_failure")
    transient_error = OperationalError("database connection lost")
    record_attempted = False
    original_update = QuerySet.update

    def _update(queryset, **kwargs):
        nonlocal record_attempted
        if queryset.model is AuctionDelivery and "last_error" in kwargs:
            record_attempted = True
            raise DatabaseError("failure record unavailable")
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(
        delivery_outbox, "process_auction_delivery", lambda _delivery_id: (_ for _ in ()).throw(transient_error)
    )
    monkeypatch.setattr(QuerySet, "update", _update)

    with pytest.raises(OperationalError) as exc_info:
        delivery_outbox.process_pending_auction_deliveries()

    assert record_attempted is True
    assert exc_info.value is transient_error


@pytest.mark.django_db
def test_pending_delivery_scan_record_programming_error_bubbles(monkeypatch, django_user_model):
    from trade.services.auction import delivery_outbox

    _first, _second = _create_pending_delivery_pair(django_user_model, suffix="record_contract_bug")
    transient_error = OperationalError("database connection lost")
    original_update = QuerySet.update

    def _update(queryset, **kwargs):
        if queryset.model is AuctionDelivery and "last_error" in kwargs:
            raise ProgrammingError("record contract bug")
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(
        delivery_outbox,
        "process_auction_delivery",
        lambda _delivery_id: (_ for _ in ()).throw(transient_error),
    )
    monkeypatch.setattr(QuerySet, "update", _update)

    with pytest.raises(ProgrammingError, match="record contract bug"):
        delivery_outbox.process_pending_auction_deliveries()


@pytest.mark.django_db
def test_pending_delivery_scan_programming_error_bubbles_without_recording(monkeypatch, django_user_model):
    from trade.services.auction import delivery_outbox

    delivery, _other_delivery = _create_pending_delivery_pair(django_user_model, suffix="scan_contract_bug")
    programming_error = ProgrammingError("delivery contract bug")
    monkeypatch.setattr(
        delivery_outbox,
        "process_auction_delivery",
        lambda _delivery_id: (_ for _ in ()).throw(programming_error),
    )

    with pytest.raises(ProgrammingError) as exc_info:
        delivery_outbox.process_pending_auction_deliveries()

    delivery.refresh_from_db()
    assert exc_info.value is programming_error
    assert delivery.attempts == 0
    assert delivery.last_error == ""


@pytest.mark.django_db
def test_process_auction_delivery_integrity_error_rolls_back_without_fallback(django_user_model):
    from trade.services.auction.delivery_outbox import process_auction_delivery

    delivery, _other_delivery = _create_pending_delivery_pair(django_user_model, suffix="direct_integrity")
    direct_grants = []
    notifications = []

    with pytest.raises(IntegrityError):
        process_auction_delivery(
            delivery.id,
            create_message_func=_create_message_with_integrity_error,
            grant_item_directly_func=lambda *args: direct_grants.append(args),
            safe_notify_user_func=lambda *args: notifications.append(args),
        )

    delivery.refresh_from_db()
    assert direct_grants == []
    assert notifications == []
    assert delivery.status == AuctionDelivery.Status.PENDING
    assert delivery.attempts == 0
    assert delivery.last_error == ""
    assert delivery.message_id is None
    assert delivery.delivery_method == ""
    assert delivery.delivered_at is None
    assert AuctionDelivery.objects.filter(pk=delivery.pk).exists()


@pytest.mark.django_db
def test_process_auction_delivery_notifies_only_after_commit(django_user_model):
    from trade.services.auction.delivery_outbox import process_auction_delivery

    delivery, _other_delivery = _create_pending_delivery_pair(django_user_model, suffix="notify_after_commit")
    notifications = []

    with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
        assert (
            process_auction_delivery(
                delivery.id,
                safe_notify_user_func=lambda *args: notifications.append(args),
            )
            is True
        )

        delivery.refresh_from_db()
        assert delivery.status == AuctionDelivery.Status.DELIVERED
        assert notifications == []

    assert len(callbacks) == 1
    callbacks[0]()
    assert len(notifications) == 1
    assert notifications[0][0] == delivery.manor.user_id
    assert notifications[0][1]["kind"] == "auction_won"


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

    delivery = AuctionDelivery.objects.get(bid=bid)
    assert delivery.status == AuctionDelivery.Status.DELIVERED
    assert delivery.delivery_method == AuctionDelivery.Method.DIRECT_INVENTORY
    assert delivery.message_id is None

    granted_item = InventoryItem.objects.get(
        manor=manor,
        template=item_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert granted_item.quantity == 1


@pytest.mark.django_db
def test_rounds_module_settle_slot_database_error_leaves_delivery_pending(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    setup = create_slot_with_bids(
        django_user_model=django_user_model,
        bid_specs=[AuctionSlotBidSpec(username="auction_rounds_delivery_db_failure", amount=20)],
        item_key="auction_settle_delivery_db_failure_item",
        round_number=10021,
        starting_price=10,
        min_increment=1,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = setup.slot
    bid = setup.bids[0]
    manor = setup.manors_by_username["auction_rounds_delivery_db_failure"]
    item_tpl = slot.item_template
    gold_tpl = _ensure_gold_bar_template()
    InventoryItem.objects.create(
        manor=manor,
        template=gold_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=30,
    )
    direct_grants = []

    monkeypatch.setattr("trade.services.auction.rounds.create_message", _create_message_with_integrity_error)
    monkeypatch.setattr(
        "trade.services.auction.rounds._grant_auction_item_directly",
        lambda *args: direct_grants.append(args),
    )
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *a, **k: True)

    with pytest.raises(IntegrityError):
        with TestCase.captureOnCommitCallbacks(execute=True):
            _settle_slot(slot)

    delivery = AuctionDelivery.objects.get(bid=bid)
    assert direct_grants == []
    assert delivery.status == AuctionDelivery.Status.PENDING
    assert delivery.attempts == 0
    assert delivery.delivery_method == ""
    assert delivery.message_id is None
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=item_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()


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
