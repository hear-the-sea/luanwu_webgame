from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from trade.models import AuctionBid, AuctionRound, AuctionSlot
from trade.services.auction.bidding import get_slot_ranking
from trade.services.auction.selectors import (
    get_active_slots,
    get_auction_stats,
    get_my_bids,
    get_my_leading_bids,
    get_my_safe_slots_count,
    get_slot_bid_info,
    get_slots_bid_info_batch,
)


def _ensure_tool_item(key: str, *, effect_type: str = ItemTemplate.EffectType.TOOL) -> ItemTemplate:
    template, _ = ItemTemplate.objects.get_or_create(
        key=key,
        defaults={
            "name": key,
            "effect_type": effect_type,
            "is_usable": False,
            "tradeable": False,
            "rarity": "gray",
        },
    )
    return template


def _create_round_and_slot(*, item_key: str, status: str = AuctionSlot.Status.ACTIVE) -> AuctionSlot:
    template = _ensure_tool_item(item_key)
    round_obj = AuctionRound.objects.create(
        round_number=20001,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(hours=1),
        end_at=timezone.now() + timedelta(hours=1),
    )
    return AuctionSlot.objects.create(
        round=round_obj,
        item_template=template,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=status,
        config_key=item_key,
        slot_index=0,
    )


@pytest.mark.django_db
def test_selectors_module_active_slots_respects_order_by_whitelist():
    _create_round_and_slot(item_key="sel_item_a")

    assert get_active_slots(order_by="-current_price").count() == 1
    # Invalid field should fall back to default ordering without exploding.
    assert get_active_slots(order_by="--hax").count() == 1


@pytest.mark.django_db
def test_selectors_module_does_not_expose_expired_active_round():
    template = _ensure_tool_item("sel_expired_round_item")
    round_obj = AuctionRound.objects.create(
        round_number=20003,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(hours=2),
        end_at=timezone.now() - timedelta(minutes=1),
    )
    AuctionSlot.objects.create(
        round=round_obj,
        item_template=template,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=template.key,
        slot_index=0,
    )

    assert get_active_slots().count() == 0


@pytest.mark.django_db
def test_selectors_module_does_not_expose_future_active_round():
    template = _ensure_tool_item("sel_future_round_item")
    round_obj = AuctionRound.objects.create(
        round_number=20004,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() + timedelta(minutes=5),
        end_at=timezone.now() + timedelta(hours=1),
    )
    AuctionSlot.objects.create(
        round=round_obj,
        item_template=template,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=template.key,
        slot_index=0,
    )

    assert get_active_slots().count() == 0


@pytest.mark.django_db
def test_selectors_module_tie_ranking_uses_bid_id_as_final_tiebreaker(django_user_model):
    user1 = django_user_model.objects.create_user(username="sel_tie_u1", password="pass")
    user2 = django_user_model.objects.create_user(username="sel_tie_u2", password="pass")
    manor1 = ensure_manor(user1)
    manor2 = ensure_manor(user2)
    slot = _create_round_and_slot(item_key="sel_tie_item")
    bid1 = AuctionBid.objects.create(slot=slot, manor=manor1, amount=12, status=AuctionBid.Status.ACTIVE)
    bid2 = AuctionBid.objects.create(slot=slot, manor=manor2, amount=12, status=AuctionBid.Status.ACTIVE)
    same_created_at = timezone.now() - timedelta(minutes=1)
    AuctionBid.objects.filter(pk__in=[bid1.pk, bid2.pk]).update(created_at=same_created_at)

    assert [bid.id for bid in get_slot_ranking(slot)] == [bid1.id, bid2.id]


@pytest.mark.django_db
def test_selectors_module_my_bids_and_leading_counts(django_user_model):
    u1 = django_user_model.objects.create_user(username="sel_u1", password="pass")
    u2 = django_user_model.objects.create_user(username="sel_u2", password="pass")
    m1 = ensure_manor(u1)
    m2 = ensure_manor(u2)

    slot = _create_round_and_slot(item_key="sel_item_b")
    AuctionBid.objects.create(slot=slot, manor=m1, amount=12, status=AuctionBid.Status.ACTIVE)
    AuctionBid.objects.create(slot=slot, manor=m2, amount=11, status=AuctionBid.Status.ACTIVE)

    assert get_my_bids(m1).count() == 1
    assert len(get_my_leading_bids(m1)) == 1
    assert get_my_safe_slots_count(m1) == 1
    assert len(get_my_leading_bids(m2)) == 0


@pytest.mark.django_db
def test_selectors_module_bid_info_and_batch_info(django_user_model):
    user = django_user_model.objects.create_user(username="sel_u3", password="pass")
    manor = ensure_manor(user)

    slot = _create_round_and_slot(item_key="sel_item_c")
    AuctionBid.objects.create(slot=slot, manor=manor, amount=15, status=AuctionBid.Status.ACTIVE)

    info = get_slot_bid_info(slot, manor=manor)
    assert info["winner_count"] == 1
    assert info["bidder_count"] == 1
    assert info["my_bid_amount"] == 15
    assert info["is_safe"] is True

    batch = get_slots_bid_info_batch([slot], manor=manor)
    assert batch[slot.id]["my_bid_amount"] == 15


@pytest.mark.django_db
def test_selectors_module_batch_cutoff_price_matches_single_slot_logic(django_user_model):
    u1 = django_user_model.objects.create_user(username="sel_cutoff_u1", password="pass")
    u2 = django_user_model.objects.create_user(username="sel_cutoff_u2", password="pass")
    m1 = ensure_manor(u1)
    m2 = ensure_manor(u2)

    slot = _create_round_and_slot(item_key="sel_item_cutoff")
    slot.quantity = 3
    slot.starting_price = 10
    slot.save(update_fields=["quantity", "starting_price"])

    AuctionBid.objects.create(slot=slot, manor=m1, amount=15, status=AuctionBid.Status.ACTIVE)
    AuctionBid.objects.create(slot=slot, manor=m2, amount=11, status=AuctionBid.Status.ACTIVE)

    single_info = get_slot_bid_info(slot, manor=m1)
    batch_info = get_slots_bid_info_batch([slot], manor=m1)[slot.id]

    assert single_info["cutoff_price"] == 11
    assert batch_info["cutoff_price"] == single_info["cutoff_price"]


@pytest.mark.django_db
def test_selectors_module_batch_info_handles_invalid_zero_winner_count():
    slot = _create_round_and_slot(item_key="sel_invalid_zero_winner_count")
    slot.quantity = 0
    slot.save(update_fields=["quantity"])

    info = get_slots_bid_info_batch([slot])[slot.id]

    assert info["winner_count"] == 1
    assert info["cutoff_price"] == slot.starting_price


@pytest.mark.django_db
def test_selectors_module_auction_stats_includes_gold_bar_numbers(django_user_model):
    user = django_user_model.objects.create_user(username="sel_u4", password="pass")
    manor = ensure_manor(user)

    gold_tpl = _ensure_tool_item("gold_bar")
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=gold_tpl,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 3},
    )
    _create_round_and_slot(item_key="sel_item_stats")

    stats = get_auction_stats(manor=manor)
    assert stats["available_gold_bars"] >= 0


@pytest.mark.django_db
def test_selectors_module_active_slots_category_and_rarity_filters():
    # TOOL effect type
    tool_tpl = _ensure_tool_item("sel_filter_tool", effect_type=ItemTemplate.EffectType.TOOL)
    round_obj = AuctionRound.objects.create(
        round_number=20002,
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(hours=1),
        end_at=timezone.now() + timedelta(hours=1),
    )
    AuctionSlot.objects.create(
        round=round_obj,
        item_template=tool_tpl,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=tool_tpl.key,
        slot_index=0,
    )

    res_tpl = _ensure_tool_item("sel_filter_res", effect_type=ItemTemplate.EffectType.RESOURCE)
    res_tpl.rarity = "purple"
    res_tpl.save(update_fields=["rarity"])
    AuctionSlot.objects.create(
        round=round_obj,
        item_template=res_tpl,
        quantity=1,
        starting_price=10,
        current_price=10,
        min_increment=1,
        status=AuctionSlot.Status.ACTIVE,
        config_key=res_tpl.key,
        slot_index=1,
    )

    assert get_active_slots(category="tool").count() == 1
    assert get_active_slots(rarity="purple").count() == 1
