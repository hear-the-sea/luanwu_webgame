from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import TestCase
from django.utils import timezone

from gameplay.services.manor.core import ensure_manor
from tests.helpers.auction import ensure_auction_item_template as _create_auction_item_template
from trade.models import AuctionBid, AuctionRound, AuctionSlot, FrozenGoldBar


@pytest.mark.django_db
def test_rounds_module_settle_slot_skips_non_active_slot():
    from trade.services.auction.rounds import _settle_slot

    item_tpl = _create_auction_item_template("auction_settle_skip_non_active_item")
    auction_round = AuctionRound.objects.create(
        round_number=10012,
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
        status=AuctionSlot.Status.SOLD,
        config_key=item_tpl.key,
        slot_index=0,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        result = _settle_slot(slot)
    slot.refresh_from_db()

    assert result.get("skipped") is True
    assert slot.status == AuctionSlot.Status.SOLD


@pytest.mark.django_db
def test_rounds_module_settle_slot_marks_unsold_when_no_bids():
    from trade.services.auction.rounds import _settle_slot

    item_tpl = _create_auction_item_template("auction_settle_unsold_item")
    auction_round = AuctionRound.objects.create(
        round_number=10006,
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

    with TestCase.captureOnCommitCallbacks(execute=True):
        result = _settle_slot(slot)
    slot.refresh_from_db()

    assert result["sold"] is False
    assert slot.status == AuctionSlot.Status.UNSOLD


@pytest.mark.django_db
def test_rounds_module_settle_slot_invalid_winner_count_refunds_all_bids(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user = django_user_model.objects.create_user(username="auction_settle_invalid_winner_count", password="pass123")
    manor = ensure_manor(user)

    item_tpl = _create_auction_item_template("auction_settle_invalid_winner_item")
    auction_round = AuctionRound.objects.create(
        round_number=10009,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_tpl,
        quantity=0,
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
    frozen = FrozenGoldBar.objects.create(
        manor=manor,
        amount=20,
        reason=FrozenGoldBar.Reason.AUCTION_BID,
        auction_bid=bid,
        is_frozen=True,
    )

    monkeypatch.setattr("trade.services.auction.rounds.create_message", lambda *a, **k: None)
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *a, **k: True)

    with TestCase.captureOnCommitCallbacks(execute=True):
        result = _settle_slot(slot)

    bid.refresh_from_db()
    frozen.refresh_from_db()
    slot.refresh_from_db()

    assert result["sold"] is False
    assert result["price"] == 0
    assert slot.status == AuctionSlot.Status.UNSOLD
    assert bid.status == AuctionBid.Status.REFUNDED
    assert frozen.is_frozen is False


@pytest.mark.django_db
def test_rounds_module_settle_slot_partial_refund_flow(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user = django_user_model.objects.create_user(username="auction_rounds_settle_user", password="pass123")
    manor = ensure_manor(user)

    item_tpl = _create_auction_item_template("auction_settle_partial_refund_item")
    auction_round = AuctionRound.objects.create(
        round_number=10005,
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

    # Avoid inventory manipulation during unit test; we only need to cover control-flow.
    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_inventory_item_for_manor_locked", lambda *a, **k: None
    )
    monkeypatch.setattr("trade.services.auction.rounds.create_message", lambda *a, **k: None)
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *a, **k: True)

    with TestCase.captureOnCommitCallbacks(execute=True):
        result = _settle_slot(slot)

    bid.refresh_from_db()
    slot.refresh_from_db()

    assert result["sold"] is True
    assert slot.status == AuctionSlot.Status.SOLD
    assert bid.status == AuctionBid.Status.WON


@pytest.mark.django_db
def test_rounds_module_settle_slot_raises_when_winner_missing_frozen_record(django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user = django_user_model.objects.create_user(username="auction_settle_missing_frozen", password="pass123")
    manor = ensure_manor(user)

    item_tpl = _create_auction_item_template("auction_settle_missing_frozen_item")
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
        slot=slot,
        manor=manor,
        amount=20,
        status=AuctionBid.Status.ACTIVE,
        frozen_gold_bars=20,
    )

    with pytest.raises(RuntimeError, match="missing frozen record"):
        _settle_slot(slot)

    # 事务回滚：拍卖位与出价状态保持未结算，避免“未扣费已发货”。
    slot.refresh_from_db()
    bid.refresh_from_db()
    assert slot.status == AuctionSlot.Status.ACTIVE
    assert bid.status == AuctionBid.Status.ACTIVE


@pytest.mark.django_db
def test_rounds_module_settle_slot_raises_when_losing_bid_missing_frozen_record(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user_one = django_user_model.objects.create_user(username="auction_settle_loser_missing_1", password="pass123")
    user_two = django_user_model.objects.create_user(username="auction_settle_loser_missing_2", password="pass123")
    manor_one = ensure_manor(user_one)
    manor_two = ensure_manor(user_two)

    item_tpl = _create_auction_item_template("auction_settle_loser_missing_item")
    auction_round = AuctionRound.objects.create(
        round_number=10019,
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
    winning_bid = AuctionBid.objects.create(
        slot=slot,
        manor=manor_one,
        amount=20,
        status=AuctionBid.Status.ACTIVE,
        frozen_gold_bars=20,
    )
    losing_bid = AuctionBid.objects.create(
        slot=slot,
        manor=manor_two,
        amount=15,
        status=AuctionBid.Status.ACTIVE,
        frozen_gold_bars=15,
    )
    winning_frozen = FrozenGoldBar.objects.create(
        manor=manor_one,
        amount=20,
        reason=FrozenGoldBar.Reason.AUCTION_BID,
        auction_bid=winning_bid,
        is_frozen=True,
    )

    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_inventory_item_for_manor_locked", lambda *a, **k: None
    )
    monkeypatch.setattr("trade.services.auction.rounds.create_message", lambda *a, **k: None)
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *a, **k: True)

    with pytest.raises(RuntimeError, match="missing frozen record"):
        _settle_slot(slot)

    slot.refresh_from_db()
    winning_bid.refresh_from_db()
    losing_bid.refresh_from_db()
    winning_frozen.refresh_from_db()
    assert slot.status == AuctionSlot.Status.ACTIVE
    assert winning_bid.status == AuctionBid.Status.ACTIVE
    assert losing_bid.status == AuctionBid.Status.ACTIVE
    assert winning_frozen.is_frozen is True


@pytest.mark.django_db
def test_rounds_module_settle_slot_does_not_emit_notifications_before_commit(monkeypatch, django_user_model):
    from trade.services.auction.rounds import _settle_slot

    user_one = django_user_model.objects.create_user(username="auction_rounds_notify_a", password="pass123")
    user_two = django_user_model.objects.create_user(username="auction_rounds_notify_b", password="pass123")
    manor_one = ensure_manor(user_one)
    manor_two = ensure_manor(user_two)

    item_tpl = _create_auction_item_template("auction_settle_notify_after_commit_item")
    auction_round = AuctionRound.objects.create(
        round_number=10018,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_tpl,
        quantity=2,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=item_tpl.key,
        slot_index=0,
    )
    first_bid = AuctionBid.objects.create(
        slot=slot,
        manor=manor_one,
        amount=20,
        status=AuctionBid.Status.ACTIVE,
        frozen_gold_bars=20,
    )
    second_bid = AuctionBid.objects.create(
        slot=slot,
        manor=manor_two,
        amount=18,
        status=AuctionBid.Status.ACTIVE,
        frozen_gold_bars=18,
    )

    consume_calls = {"count": 0}
    emitted_messages: list[tuple] = []
    emitted_notifications: list[tuple] = []

    def _consume_or_fail(*_args, **_kwargs):
        consume_calls["count"] += 1
        if consume_calls["count"] == 2:
            raise RuntimeError("boom on second winner")

    monkeypatch.setattr("trade.services.auction.rounds._consume_winning_bid_frozen_gold_bars", _consume_or_fail)
    monkeypatch.setattr(
        "trade.services.auction.rounds.create_message", lambda *args, **kwargs: emitted_messages.append((args, kwargs))
    )
    monkeypatch.setattr(
        "trade.services.auction.rounds.notify_user",
        lambda *args, **kwargs: emitted_notifications.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="boom on second winner"):
        _settle_slot(slot)

    slot.refresh_from_db()
    first_bid.refresh_from_db()
    second_bid.refresh_from_db()

    assert slot.status == AuctionSlot.Status.ACTIVE
    assert first_bid.status == AuctionBid.Status.ACTIVE
    assert second_bid.status == AuctionBid.Status.ACTIVE
    assert emitted_messages == []
    assert emitted_notifications == []
