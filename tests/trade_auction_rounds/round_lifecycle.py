from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted

from tests.helpers.auction import AuctionSlotBidSpec, create_round_and_slot, create_slot_with_bids
from tests.helpers.auction import ensure_auction_item_template as _create_auction_item_template
from trade.models import AuctionBid, AuctionDelivery, AuctionRound, AuctionSlot, FrozenGoldBar
from trade.services import auction_service
from trade.services.auction.constants import AUCTION_CREATE_LOCK_KEY, AUCTION_SETTLE_LOCK_KEY
from trade.services.auction_config import AuctionItemConfig, AuctionSettings


def _install_reacquired_lock_race(monkeypatch) -> tuple[dict[str, object], str]:
    from trade.services.auction import rounds as auction_rounds
    from trade.services.auction import rounds_lifecycle_support

    state: dict[str, object] = {}
    replacement_token = "replacement-owner-token"

    def _add(key, value, timeout=None):
        state[key] = value
        return True

    def _release_if_owner(key, *, lock_token, logger, log_context):
        state[key] = replacement_token
        if state.get(key) != lock_token:
            return False
        state.pop(key, None)
        return True

    monkeypatch.setattr(auction_rounds.cache, "add", _add)
    monkeypatch.setattr(
        rounds_lifecycle_support,
        "release_cache_key_if_owner",
        _release_if_owner,
        raising=False,
    )
    return state, replacement_token


@pytest.mark.django_db
def test_create_auction_round_skips_when_settling_round_exists(monkeypatch):
    item_key = "auction_round_create_guard"
    _create_auction_item_template(item_key)

    AuctionRound.objects.create(
        round_number=10001,
        status=AuctionRound.Status.SETTLING,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(days=1),
    )

    monkeypatch.setattr(
        auction_service,
        "get_auction_settings",
        lambda: AuctionSettings(cycle_days=3, min_increment_ratio=0.1, default_min_increment=1),
    )
    monkeypatch.setattr(
        auction_service,
        "get_enabled_auction_items",
        lambda: [
            AuctionItemConfig(
                item_key=item_key,
                slots=1,
                quantity_per_slot=1,
                starting_price=10,
                min_increment=1,
                enabled=True,
            )
        ],
    )

    created = auction_service.create_auction_round()

    assert created is None
    assert AuctionRound.objects.count() == 1


@pytest.mark.django_db
def test_rounds_module_create_auction_round_can_create_slots(monkeypatch):
    from trade.services.auction.rounds import create_auction_round as create_round_impl

    item_key = "auction_rounds_impl_create"
    _create_auction_item_template(item_key)

    monkeypatch.setattr(
        "trade.services.auction.rounds.get_auction_settings",
        lambda: AuctionSettings(cycle_days=3, min_increment_ratio=0.1, default_min_increment=1),
    )
    monkeypatch.setattr(
        "trade.services.auction.rounds.get_enabled_auction_items",
        lambda: [
            AuctionItemConfig(
                item_key=item_key,
                slots=2,
                quantity_per_slot=1,
                starting_price=10,
                min_increment=1,
                enabled=True,
            )
        ],
    )

    created = create_round_impl()

    assert created is not None
    assert created.slots.count() == 2


@pytest.mark.django_db
def test_rounds_module_create_auction_round_skips_when_cache_add_fails(monkeypatch):
    from trade.services.auction.rounds import create_auction_round as create_round_impl

    item_key = "auction_rounds_cache_add_fail_create"
    _create_auction_item_template(item_key)

    monkeypatch.setattr(
        "trade.services.auction.rounds.cache.add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )
    monkeypatch.setattr(
        "trade.services.auction.rounds.get_auction_settings",
        lambda: AuctionSettings(cycle_days=3, min_increment_ratio=0.1, default_min_increment=1),
    )
    monkeypatch.setattr(
        "trade.services.auction.rounds.get_enabled_auction_items",
        lambda: [
            AuctionItemConfig(
                item_key=item_key,
                slots=1,
                quantity_per_slot=1,
                starting_price=10,
                min_increment=1,
                enabled=True,
            )
        ],
    )

    created = create_round_impl()

    assert created is None
    assert AuctionRound.objects.count() == 0


@pytest.mark.django_db
def test_auction_round_db_constraint_allows_only_one_active_round():
    now = timezone.now()
    AuctionRound.objects.create(
        round_number=11001,
        status=AuctionRound.Status.ACTIVE,
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=1),
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AuctionRound.objects.create(
                round_number=11002,
                status=AuctionRound.Status.ACTIVE,
                start_at=now - timedelta(hours=1),
                end_at=now + timedelta(days=2),
            )

    first = AuctionRound.objects.get(round_number=11001)
    assert first.status_singleton == AuctionRound.Status.ACTIVE


@pytest.mark.django_db
def test_auction_round_db_constraint_allows_only_one_settling_round():
    now = timezone.now()
    AuctionRound.objects.create(
        round_number=12001,
        status=AuctionRound.Status.SETTLING,
        start_at=now - timedelta(days=3),
        end_at=now - timedelta(hours=1),
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AuctionRound.objects.create(
                round_number=12002,
                status=AuctionRound.Status.SETTLING,
                start_at=now - timedelta(days=2),
                end_at=now - timedelta(minutes=30),
            )

    first = AuctionRound.objects.get(round_number=12001)
    assert first.status_singleton == AuctionRound.Status.SETTLING


@pytest.mark.django_db
def test_auction_round_completed_status_clears_singleton_value():
    now = timezone.now()
    round_obj = AuctionRound.objects.create(
        round_number=13001,
        status=AuctionRound.Status.ACTIVE,
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=1),
    )

    round_obj.status = AuctionRound.Status.COMPLETED
    round_obj.save(update_fields=["status"])
    round_obj.refresh_from_db()

    assert round_obj.status_singleton is None


@pytest.mark.django_db
def test_settle_auction_round_database_error_preserves_retryable_state(monkeypatch, django_user_model):
    setup = create_slot_with_bids(
        django_user_model=django_user_model,
        bid_specs=[
            AuctionSlotBidSpec(username="auction_settle_db_failure_high", amount=20),
            AuctionSlotBidSpec(username="auction_settle_db_failure_low", amount=15),
        ],
        item_key="auction_settle_db_failure_item",
        round_number=10002,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    error = DatabaseError("boom")

    monkeypatch.setattr(auction_service, "_settle_slot", lambda _slot: (_ for _ in ()).throw(error))

    with pytest.raises(DatabaseError) as exc_info:
        auction_service.settle_auction_round(round_id=setup.auction_round.id)

    assert exc_info.value is error
    setup.auction_round.refresh_from_db()
    setup.slot.refresh_from_db()
    assert setup.auction_round.status in (AuctionRound.Status.ACTIVE, AuctionRound.Status.SETTLING)
    assert setup.auction_round.settled_at is None
    assert setup.slot.status == AuctionSlot.Status.ACTIVE

    for bid in setup.bids:
        bid.refresh_from_db()
        frozen = FrozenGoldBar.objects.get(auction_bid=bid)
        assert bid.status == AuctionBid.Status.ACTIVE
        assert bid.refunded_at is None
        assert frozen.amount == bid.amount
        assert frozen.is_frozen is True
        assert frozen.unfrozen_at is None


@pytest.mark.django_db
def test_settle_auction_round_invalid_winner_count_preserves_retryable_state(django_user_model):
    setup = create_slot_with_bids(
        django_user_model=django_user_model,
        bid_specs=[AuctionSlotBidSpec(username="auction_invalid_winner_count", amount=20)],
        item_key="auction_invalid_winner_count_item",
        round_number=10017,
        quantity=0,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="winner_count must be positive"):
        auction_service.settle_auction_round(round_id=setup.auction_round.id)

    setup.auction_round.refresh_from_db()
    setup.slot.refresh_from_db()
    setup.bids[0].refresh_from_db()
    frozen = FrozenGoldBar.objects.get(auction_bid=setup.bids[0])

    assert setup.auction_round.status == AuctionRound.Status.SETTLING
    assert setup.auction_round.settled_at is None
    assert setup.slot.status == AuctionSlot.Status.ACTIVE
    assert setup.bids[0].status == AuctionBid.Status.ACTIVE
    assert setup.bids[0].refunded_at is None
    assert frozen.is_frozen is True
    assert frozen.unfrozen_at is None


@pytest.mark.django_db
def test_rounds_module_settle_auction_round_marks_completed_when_no_slots():
    from trade.services.auction.rounds import settle_auction_round as settle_round_impl

    auction_round = AuctionRound.objects.create(
        round_number=10004,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )

    stats = settle_round_impl(round_id=auction_round.id)

    auction_round.refresh_from_db()
    assert stats["settled"] == 1
    assert auction_round.status == AuctionRound.Status.COMPLETED
    assert auction_round.settled_at is not None


@pytest.mark.django_db
def test_settle_auction_round_without_round_id_resumes_settling_round():
    auction_round = AuctionRound.objects.create(
        round_number=10011,
        status=AuctionRound.Status.SETTLING,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )

    stats = auction_service.settle_auction_round()

    auction_round.refresh_from_db()
    assert stats["settled"] == 1
    assert stats["sold"] == 0
    assert stats["unsold"] == 0
    assert auction_round.status == AuctionRound.Status.COMPLETED
    assert auction_round.settled_at is not None


@pytest.mark.django_db
def test_settle_auction_round_without_round_id_resumes_settling_active_slots(monkeypatch):
    auction_round, slot = create_round_and_slot(
        item_key="auction_settling_resume_active_slot",
        round_number=10014,
        round_status=AuctionRound.Status.SETTLING,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )

    def _settle_unsold(slot_to_settle):
        AuctionSlot.objects.filter(pk=slot_to_settle.pk).update(status=AuctionSlot.Status.UNSOLD)
        return {"sold": False, "price": 0}

    monkeypatch.setattr(auction_service, "_settle_slot", _settle_unsold)

    stats = auction_service.settle_auction_round()

    auction_round.refresh_from_db()
    slot.refresh_from_db()
    assert stats["settled"] == 1
    assert stats["unsold"] == 1
    assert auction_round.status == AuctionRound.Status.COMPLETED
    assert slot.status == AuctionSlot.Status.UNSOLD


@pytest.mark.django_db
def test_settle_auction_round_completion_reports_full_persisted_round_totals(django_user_model):
    from trade.services.auction.rounds import settle_auction_round as settle_round_impl

    setup = create_slot_with_bids(
        django_user_model=django_user_model,
        bid_specs=[
            AuctionSlotBidSpec(username="auction_round_totals_first", amount=30),
            AuctionSlotBidSpec(username="auction_round_totals_second", amount=25),
        ],
        item_key="auction_round_persisted_totals",
        round_number=10022,
        round_status=AuctionRound.Status.SETTLING,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    sold_slot = AuctionSlot.objects.create(
        round=setup.auction_round,
        item_template=setup.slot.item_template,
        quantity=2,
        starting_price=10,
        current_price=17,
        min_increment=1,
        status=AuctionSlot.Status.SOLD,
        config_key=setup.slot.item_template.key,
        slot_index=1,
    )
    unsold_slot = AuctionSlot.objects.create(
        round=setup.auction_round,
        item_template=setup.slot.item_template,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.UNSOLD,
        config_key=setup.slot.item_template.key,
        slot_index=2,
    )
    for index, manor in enumerate(setup.manors_by_username.values()):
        historical_bid = AuctionBid.objects.create(
            slot=sold_slot,
            manor=manor,
            amount=20 + index,
            status=AuctionBid.Status.WON,
            frozen_gold_bars=0,
        )
        AuctionDelivery.objects.create(
            slot=sold_slot,
            bid=historical_bid,
            manor=manor,
            item_template=sold_slot.item_template,
            quantity=1,
            settlement_price=17,
            total_winners=2,
        )
    stale_unsold_bid = AuctionBid.objects.create(
        slot=unsold_slot,
        manor=setup.bids[1].manor,
        amount=999,
        status=AuctionBid.Status.WON,
        frozen_gold_bars=0,
    )
    AuctionDelivery.objects.create(
        slot=unsold_slot,
        bid=stale_unsold_bid,
        manor=stale_unsold_bid.manor,
        item_template=unsold_slot.item_template,
        quantity=1,
        settlement_price=999,
        total_winners=1,
    )

    def _settle_active_slot(slot):
        AuctionSlot.objects.filter(pk=slot.pk).update(status=AuctionSlot.Status.SOLD)
        AuctionDelivery.objects.create(
            slot=slot,
            bid=setup.bids[0],
            manor=setup.bids[0].manor,
            item_template=slot.item_template,
            quantity=1,
            settlement_price=23,
            total_winners=1,
        )
        return {"sold": True, "price": 23}

    stats = settle_round_impl(round_id=setup.auction_round.id, settle_slot_func=_settle_active_slot)

    setup.auction_round.refresh_from_db()
    setup.slot.refresh_from_db()
    unsold_slot.refresh_from_db()
    assert stats == {"settled": 1, "sold": 2, "unsold": 1, "total_gold_bars": 57}
    assert setup.auction_round.status == AuctionRound.Status.COMPLETED
    assert setup.slot.status == AuctionSlot.Status.SOLD
    assert unsold_slot.status == AuctionSlot.Status.UNSOLD


@pytest.mark.django_db
def test_rounds_module_settle_auction_round_keeps_settling_when_active_slots_remain():
    from trade.services.auction.rounds import settle_auction_round as settle_round_impl

    item_template = _create_auction_item_template("auction_rounds_keep_settling_when_active")
    auction_round = AuctionRound.objects.create(
        round_number=10013,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_template,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=item_template.key,
        slot_index=0,
    )

    stats = settle_round_impl(round_id=auction_round.id, settle_slot_func=lambda _slot: {"skipped": True})

    auction_round.refresh_from_db()
    assert stats["settled"] == 0
    assert auction_round.status == AuctionRound.Status.SETTLING
    assert auction_round.settled_at is None


@pytest.mark.django_db
def test_rounds_module_settle_auction_round_runtime_error_bubbles_up():
    from trade.services.auction.rounds import settle_auction_round as settle_round_impl

    item_template = _create_auction_item_template("auction_rounds_runtime_error")
    auction_round = AuctionRound.objects.create(
        round_number=10013,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_template,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=item_template.key,
        slot_index=0,
    )

    with pytest.raises(RuntimeError, match="boom"):
        settle_round_impl(
            round_id=auction_round.id, settle_slot_func=lambda _slot: (_ for _ in ()).throw(RuntimeError("boom"))
        )

    auction_round.refresh_from_db()
    slot.refresh_from_db()
    assert auction_round.status == AuctionRound.Status.SETTLING
    assert auction_round.settled_at is None
    assert slot.status == AuctionSlot.Status.ACTIVE


@pytest.mark.django_db
def test_rounds_module_settle_auction_round_invalid_settle_result_bubbles_contract_error():
    from trade.services.auction.rounds import settle_auction_round as settle_round_impl

    item_template = _create_auction_item_template("auction_rounds_invalid_settle_result")
    auction_round = AuctionRound.objects.create(
        round_number=10014,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    slot = AuctionSlot.objects.create(
        round=auction_round,
        item_template=item_template,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=item_template.key,
        slot_index=0,
    )

    with pytest.raises(AssertionError, match="invalid settle slot result"):
        settle_round_impl(round_id=auction_round.id, settle_slot_func=lambda _slot: None)

    auction_round.refresh_from_db()
    slot.refresh_from_db()
    assert auction_round.status == AuctionRound.Status.SETTLING
    assert auction_round.settled_at is None
    assert slot.status == AuctionSlot.Status.ACTIVE


@pytest.mark.django_db
def test_settle_auction_round_can_resume_from_settling_status():
    auction_round = AuctionRound.objects.create(
        round_number=10003,
        status=AuctionRound.Status.SETTLING,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )

    stats = auction_service.settle_auction_round(round_id=auction_round.id)

    auction_round.refresh_from_db()
    assert stats["settled"] == 1
    assert auction_round.status == AuctionRound.Status.COMPLETED
    assert auction_round.settled_at is not None


@pytest.mark.django_db
def test_rounds_module_settle_auction_round_propagates_cache_add_failure(monkeypatch):
    from trade.services.auction.rounds import settle_auction_round as settle_round_impl

    auction_round = AuctionRound.objects.create(
        round_number=10010,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )

    error = ConnectionInterrupted("cache down")
    monkeypatch.setattr(
        "trade.services.auction.rounds.cache.add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(ConnectionInterrupted) as exc_info:
        settle_round_impl(round_id=auction_round.id)

    assert exc_info.value is error
    auction_round.refresh_from_db()
    assert auction_round.status == AuctionRound.Status.ACTIVE


@pytest.mark.django_db
def test_rounds_module_settle_auction_round_skips_when_settlement_lock_is_held(monkeypatch):
    from trade.services.auction.rounds import settle_auction_round as settle_round_impl

    auction_round = AuctionRound.objects.create(
        round_number=10016,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )

    monkeypatch.setattr("trade.services.auction.rounds.cache.add", lambda *_args, **_kwargs: False)

    stats = settle_round_impl(round_id=auction_round.id)

    auction_round.refresh_from_db()
    assert stats["settled"] == 0
    assert auction_round.status == AuctionRound.Status.ACTIVE


@pytest.mark.django_db
def test_rounds_module_settlement_release_preserves_reacquired_lock(monkeypatch):
    from trade.services.auction import rounds as auction_rounds

    state, replacement_token = _install_reacquired_lock_race(monkeypatch)
    auction_round = AuctionRound.objects.create(
        round_number=10018,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )

    auction_rounds.settle_auction_round(round_id=auction_round.id)

    assert state[AUCTION_SETTLE_LOCK_KEY] == replacement_token


@pytest.mark.django_db
def test_rounds_module_create_release_preserves_reacquired_lock(monkeypatch):
    from trade.services.auction import rounds as auction_rounds

    state, replacement_token = _install_reacquired_lock_race(monkeypatch)

    created = auction_rounds.create_auction_round(
        get_settings_func=lambda: AuctionSettings(
            cycle_days=3,
            min_increment_ratio=0.1,
            default_min_increment=1,
        ),
        get_enabled_items_func=lambda: [],
    )

    assert created is None
    assert state[AUCTION_CREATE_LOCK_KEY] == replacement_token


@pytest.mark.django_db
def test_rounds_module_settlement_release_failure_preserves_database_error(monkeypatch, caplog):
    from trade.services.auction import rounds as auction_rounds
    from trade.services.auction import rounds_lifecycle_support

    auction_round, _slot = create_round_and_slot(
        item_key="auction_settlement_release_primary_error",
        round_number=10019,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    primary_error = DatabaseError("settlement failed")
    cleanup_error = AssertionError("settlement lock release failed")
    monkeypatch.setattr(auction_rounds.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        rounds_lifecycle_support,
        "release_cache_key_if_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(cleanup_error),
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(DatabaseError) as exc_info:
            auction_rounds.settle_auction_round(
                round_id=auction_round.id,
                settle_slot_func=lambda _slot: (_ for _ in ()).throw(primary_error),
            )

    assert exc_info.value is primary_error
    assert "lock release failed while preserving active exception" in caplog.text


@pytest.mark.django_db
def test_rounds_module_settlement_release_failure_bubbles_without_primary_error(monkeypatch):
    from trade.services.auction import rounds as auction_rounds
    from trade.services.auction import rounds_lifecycle_support

    auction_round = AuctionRound.objects.create(
        round_number=10020,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    cleanup_error = AssertionError("settlement lock release failed")
    monkeypatch.setattr(auction_rounds.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        rounds_lifecycle_support,
        "release_cache_key_if_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(cleanup_error),
    )

    with pytest.raises(AssertionError) as exc_info:
        auction_rounds.settle_auction_round(round_id=auction_round.id)

    assert exc_info.value is cleanup_error


@pytest.mark.django_db
def test_rounds_module_create_release_failure_preserves_database_error(monkeypatch, caplog):
    from trade.services.auction import rounds as auction_rounds
    from trade.services.auction import rounds_lifecycle_support

    primary_error = DatabaseError("create round failed")
    cleanup_error = AssertionError("create lock release failed")
    monkeypatch.setattr(auction_rounds.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        rounds_lifecycle_support,
        "release_cache_key_if_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(cleanup_error),
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(DatabaseError) as exc_info:
            auction_rounds.create_auction_round(
                get_settings_func=lambda: (_ for _ in ()).throw(primary_error),
                get_enabled_items_func=lambda: [],
            )

    assert exc_info.value is primary_error
    assert "lock release failed while preserving active exception" in caplog.text


@pytest.mark.django_db
def test_rounds_module_create_release_failure_bubbles_without_primary_error(monkeypatch):
    from trade.services.auction import rounds as auction_rounds
    from trade.services.auction import rounds_lifecycle_support

    cleanup_error = AssertionError("create lock release failed")
    monkeypatch.setattr(auction_rounds.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        rounds_lifecycle_support,
        "release_cache_key_if_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(cleanup_error),
    )

    with pytest.raises(AssertionError) as exc_info:
        auction_rounds.create_auction_round(
            get_settings_func=lambda: AuctionSettings(
                cycle_days=3,
                min_increment_ratio=0.1,
                default_min_increment=1,
            ),
            get_enabled_items_func=lambda: [],
        )

    assert exc_info.value is cleanup_error


@pytest.mark.django_db
def test_rounds_module_create_auction_round_runtime_marker_cache_add_bubbles_up(monkeypatch):
    from trade.services.auction.rounds import create_auction_round as create_round_impl

    monkeypatch.setattr(
        "trade.services.auction.rounds.cache.add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        create_round_impl()
