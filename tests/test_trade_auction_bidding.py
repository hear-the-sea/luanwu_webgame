from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import MessageError, TradeValidationError
from gameplay.models import InventoryItem
from gameplay.services.manor.core import ensure_manor
from tests.helpers.auction import create_active_round_slot, ensure_gold_bar_template
from trade.models import AuctionBid
from trade.services import auction_service


def _set_gold_bars(manor, quantity: int) -> None:
    template = ensure_gold_bar_template()
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": int(quantity)},
    )


@pytest.mark.django_db
def test_validate_bid_amount_rejects_below_starting_price(django_user_model):
    user = django_user_model.objects.create_user(username="auction_validate", password="pass12345")
    _ = ensure_manor(user)

    slot = create_active_round_slot(item_key="auction_validate_item")
    slot.bid_count = 0

    with pytest.raises(TradeValidationError, match="起拍价"):
        auction_service.validate_bid_amount(slot, 1)


@pytest.mark.django_db
def test_place_bid_rejects_future_round_before_freezing_gold(django_user_model):
    user = django_user_model.objects.create_user(username="auction_future_round", password="pass12345")
    manor = ensure_manor(user)
    slot = create_active_round_slot(item_key="auction_future_round_item")
    slot.round.start_at = timezone.now() + timedelta(minutes=5)
    slot.round.save(update_fields=["start_at"])

    with pytest.raises(TradeValidationError, match="尚未开始"):
        auction_service.place_bid(manor, slot.id, 10)


@pytest.mark.django_db
def test_new_bidder_must_respect_min_increment_when_slot_is_full(monkeypatch, django_user_model):
    monkeypatch.setattr(auction_service, "_notify_outbid_vickrey", lambda *args, **kwargs: None)

    user1 = django_user_model.objects.create_user(username="auction_min_increment_first", password="pass12345")
    user2 = django_user_model.objects.create_user(username="auction_min_increment_second", password="pass12345")
    manor1 = ensure_manor(user1)
    manor2 = ensure_manor(user2)
    _set_gold_bars(manor1, 30)
    _set_gold_bars(manor2, 30)

    slot = create_active_round_slot(
        item_key="auction_min_increment_full_slot",
        starting_price=10,
        min_increment=5,
    )
    auction_service.place_bid(manor1, slot.id, 10)

    with pytest.raises(TradeValidationError, match="最小加价幅度 5"):
        auction_service.place_bid(manor2, slot.id, 14)

    bid, _ = auction_service.place_bid(manor2, slot.id, 15)
    assert bid.amount == 15


@pytest.mark.django_db
def test_place_bid_freezes_and_unfreezes_gold_bars(monkeypatch, django_user_model):
    monkeypatch.setattr(auction_service, "_notify_outbid_vickrey", lambda *args, **kwargs: None)

    user = django_user_model.objects.create_user(username="auction_freeze", password="pass12345")
    manor = ensure_manor(user)
    _set_gold_bars(manor, 10)

    slot = create_active_round_slot(item_key="auction_freeze_item")

    bid, is_first = auction_service.place_bid(manor, slot.id, 5)
    assert is_first is True

    bid.refresh_from_db()
    frozen = bid.frozen_record
    assert frozen.is_frozen is True
    assert frozen.amount == 5
    assert bid.frozen_gold_bars == 5
    assert auction_service.get_available_gold_bars(manor) == 5

    auction_service.unfreeze_gold_bars(frozen)
    frozen.refresh_from_db()
    bid.refresh_from_db()
    assert frozen.is_frozen is False
    assert bid.status == AuctionBid.Status.REFUNDED
    assert auction_service.get_available_gold_bars(manor) == 10


@pytest.mark.django_db
def test_place_bid_kicks_out_previous_winner_and_refunds(monkeypatch, django_user_model):
    monkeypatch.setattr(auction_service, "_notify_outbid_vickrey", lambda *args, **kwargs: None)

    user1 = django_user_model.objects.create_user(username="auction_kick1", password="pass12345")
    user2 = django_user_model.objects.create_user(username="auction_kick2", password="pass12345")
    manor1 = ensure_manor(user1)
    manor2 = ensure_manor(user2)
    _set_gold_bars(manor1, 10)
    _set_gold_bars(manor2, 10)

    slot = create_active_round_slot(item_key="auction_kick_item", quantity=1)

    bid1, _ = auction_service.place_bid(manor1, slot.id, 5)
    bid2, _ = auction_service.place_bid(manor2, slot.id, 6)

    bid1.refresh_from_db()
    bid2.refresh_from_db()
    slot.refresh_from_db()

    assert bid2.status == AuctionBid.Status.ACTIVE
    assert bid2.frozen_record.is_frozen is True

    # bid1 should be refunded after being kicked out.
    assert bid1.status == AuctionBid.Status.REFUNDED
    assert bid1.frozen_record.is_frozen is False
    assert auction_service.get_available_gold_bars(manor1) == 10

    assert slot.highest_bidder_id == manor2.id
    assert slot.current_price == 6

    # Winner consumption should decrease actual inventory.
    auction_service.consume_frozen_gold_bars(bid2.frozen_record, manor2)
    bid2.refresh_from_db()
    bid2.frozen_record.refresh_from_db()

    assert bid2.status == AuctionBid.Status.WON
    assert bid2.frozen_record.is_frozen is False
    assert auction_service.get_total_gold_bars(manor2) == 4


@pytest.mark.django_db
def test_place_bid_allows_same_player_to_raise_bid_and_unfreezes_previous(monkeypatch, django_user_model):
    monkeypatch.setattr(auction_service, "_notify_outbid_vickrey", lambda *args, **kwargs: None)

    user = django_user_model.objects.create_user(username="auction_raise", password="pass12345")
    manor = ensure_manor(user)
    _set_gold_bars(manor, 12)

    slot = create_active_round_slot(item_key="auction_raise_item", quantity=2)

    bid1, is_first = auction_service.place_bid(manor, slot.id, 5)
    assert is_first is True
    assert auction_service.get_available_gold_bars(manor) == 7

    bid2, is_first = auction_service.place_bid(manor, slot.id, 7)
    assert is_first is False

    bid1.refresh_from_db()
    bid1.frozen_record.refresh_from_db()
    bid2.refresh_from_db()
    bid2.frozen_record.refresh_from_db()

    # Old bid is replaced by the player's new bid and marked OUTBID.
    # The refund status is tracked on the FrozenGoldBar record.
    assert bid1.status == AuctionBid.Status.OUTBID
    assert bid1.frozen_record.is_frozen is False
    assert bid2.status == AuctionBid.Status.ACTIVE
    assert bid2.frozen_record.is_frozen is True
    assert auction_service.get_available_gold_bars(manor) == 5


@pytest.mark.django_db
def test_place_bid_raises_when_previous_bid_frozen_record_is_missing(monkeypatch, django_user_model):
    monkeypatch.setattr(auction_service, "_notify_outbid_vickrey", lambda *args, **kwargs: None)

    user = django_user_model.objects.create_user(username="auction_missing_previous_frozen", password="pass12345")
    manor = ensure_manor(user)
    _set_gold_bars(manor, 12)

    slot = create_active_round_slot(item_key="auction_missing_previous_frozen_item", quantity=2)

    bid1, _ = auction_service.place_bid(manor, slot.id, 5)
    bid1.frozen_record.delete()

    with pytest.raises(RuntimeError, match="missing frozen record"):
        auction_service.place_bid(manor, slot.id, 7)

    bid1.refresh_from_db()
    slot.refresh_from_db()
    assert bid1.status == AuctionBid.Status.ACTIVE
    assert AuctionBid.objects.filter(slot=slot).count() == 1


@pytest.mark.django_db
def test_place_bid_raises_when_outbid_player_frozen_record_is_missing(monkeypatch, django_user_model):
    monkeypatch.setattr(auction_service, "_notify_outbid_vickrey", lambda *args, **kwargs: None)

    user1 = django_user_model.objects.create_user(username="auction_missing_kicked_frozen_1", password="pass12345")
    user2 = django_user_model.objects.create_user(username="auction_missing_kicked_frozen_2", password="pass12345")
    manor1 = ensure_manor(user1)
    manor2 = ensure_manor(user2)
    _set_gold_bars(manor1, 10)
    _set_gold_bars(manor2, 10)

    slot = create_active_round_slot(item_key="auction_missing_kicked_frozen_item", quantity=1)

    bid1, _ = auction_service.place_bid(manor1, slot.id, 5)
    bid1.frozen_record.delete()

    with pytest.raises(RuntimeError, match="missing frozen record"):
        auction_service.place_bid(manor2, slot.id, 6)

    bid1.refresh_from_db()
    slot.refresh_from_db()
    assert bid1.status == AuctionBid.Status.ACTIVE
    assert AuctionBid.objects.filter(slot=slot).count() == 1


@pytest.mark.django_db
def test_place_bid_succeeds_when_outbid_notification_fails(monkeypatch, django_user_model):
    user1 = django_user_model.objects.create_user(username="auction_notify_fail_1", password="pass12345")
    user2 = django_user_model.objects.create_user(username="auction_notify_fail_2", password="pass12345")
    manor1 = ensure_manor(user1)
    manor2 = ensure_manor(user2)
    _set_gold_bars(manor1, 10)
    _set_gold_bars(manor2, 10)

    slot = create_active_round_slot(item_key="auction_notify_fail_item", quantity=1)

    auction_service.place_bid(manor1, slot.id, 5)
    monkeypatch.setattr(
        "trade.services.auction.bidding.create_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(MessageError("msg down")),
    )
    monkeypatch.setattr(
        "trade.services.auction.bidding.notify_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("ws down")),
    )

    bid2, _ = auction_service.place_bid(manor2, slot.id, 6)
    bid2.refresh_from_db()

    assert bid2.status == AuctionBid.Status.ACTIVE
    assert bid2.frozen_record.is_frozen is True


@pytest.mark.django_db
def test_place_bid_runtime_marker_notification_error_bubbles_up(monkeypatch, django_user_model):
    user1 = django_user_model.objects.create_user(username="auction_notify_runtime_1", password="pass12345")
    user2 = django_user_model.objects.create_user(username="auction_notify_runtime_2", password="pass12345")
    manor1 = ensure_manor(user1)
    manor2 = ensure_manor(user2)
    _set_gold_bars(manor1, 10)
    _set_gold_bars(manor2, 10)

    slot = create_active_round_slot(item_key="auction_notify_runtime_item", quantity=1)

    auction_service.place_bid(manor1, slot.id, 5)
    monkeypatch.setattr(
        "trade.services.auction.bidding.create_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        auction_service.place_bid(manor2, slot.id, 6)


@pytest.mark.django_db
def test_place_bid_notifies_outbid_player_with_cutoff_price_in_multi_winner_slot(monkeypatch, django_user_model):
    user1 = django_user_model.objects.create_user(username="auction_cutoff_1", password="pass12345")
    user2 = django_user_model.objects.create_user(username="auction_cutoff_2", password="pass12345")
    user3 = django_user_model.objects.create_user(username="auction_cutoff_3", password="pass12345")
    manor1 = ensure_manor(user1)
    manor2 = ensure_manor(user2)
    manor3 = ensure_manor(user3)
    _set_gold_bars(manor1, 20)
    _set_gold_bars(manor2, 20)
    _set_gold_bars(manor3, 20)

    slot = create_active_round_slot(item_key="auction_cutoff_item", quantity=2)

    auction_service.place_bid(manor1, slot.id, 10)
    auction_service.place_bid(manor2, slot.id, 9)

    notifications: list[dict] = []

    def _capture_outbid(manor, slot, new_price, new_bidder, winner_count):
        notifications.append(
            {
                "manor_id": manor.id,
                "slot_id": slot.id,
                "new_price": new_price,
                "new_bidder_id": new_bidder.id,
                "winner_count": winner_count,
            }
        )

    monkeypatch.setattr(auction_service, "_notify_outbid_vickrey", _capture_outbid)
    auction_service.place_bid(manor3, slot.id, 11)

    assert notifications == [
        {
            "manor_id": manor2.id,
            "slot_id": slot.id,
            "new_price": 10,
            "new_bidder_id": manor3.id,
            "winner_count": 2,
        }
    ]


@pytest.mark.django_db
def test_validate_bid_amount_rejects_non_positive_amount(django_user_model):
    user = django_user_model.objects.create_user(username="auction_validate_non_positive", password="pass12345")
    _ = ensure_manor(user)

    slot = create_active_round_slot(item_key="auction_validate_non_positive_item")
    slot.bid_count = 0

    with pytest.raises(TradeValidationError, match="出价金额必须大于0"):
        auction_service.validate_bid_amount(slot, 0)


@pytest.mark.django_db
def test_place_bid_rejects_invalid_amount_type(django_user_model):
    user = django_user_model.objects.create_user(username="auction_invalid_amount_type", password="pass12345")
    manor = ensure_manor(user)
    _set_gold_bars(manor, 10)
    slot = create_active_round_slot(item_key="auction_invalid_amount_type_item")

    with pytest.raises(TradeValidationError, match="出价金额必须大于0"):
        auction_service.place_bid(manor, slot.id, "invalid")


@pytest.mark.django_db
def test_place_bid_rejects_invalid_winner_count_configuration(django_user_model):
    user = django_user_model.objects.create_user(username="auction_invalid_winner_count", password="pass12345")
    manor = ensure_manor(user)
    _set_gold_bars(manor, 10)
    slot = create_active_round_slot(item_key="auction_invalid_winner_count_item", quantity=1)
    slot.quantity = 0
    slot.save(update_fields=["quantity"])

    with pytest.raises(TradeValidationError, match="拍卖位配置异常"):
        auction_service.place_bid(manor, slot.id, 5)
