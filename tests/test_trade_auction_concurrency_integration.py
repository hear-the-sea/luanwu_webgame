from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from gameplay.models import InventoryItem
from gameplay.services.manor.core import ensure_manor
from tests.helpers.auction import ensure_auction_item_template, ensure_gold_bar_template
from trade.models import AuctionBid, AuctionDelivery, AuctionRound, AuctionSlot
from trade.services.auction_service import place_bid, settle_auction_round

pytestmark = [pytest.mark.integration]


@pytest.mark.django_db(transaction=True)
def test_settle_auction_round_concurrent_requests_only_one_thread_completes(django_user_model, monkeypatch):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    bidder_user = django_user_model.objects.create_user(
        username=f"auction_settle_concurrent_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    bidder = ensure_manor(bidder_user)

    gold_bar_template = ensure_gold_bar_template()
    InventoryItem.objects.update_or_create(
        manor=bidder,
        template=gold_bar_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 10},
    )

    auction_item = ensure_auction_item_template(f"auction_concurrent_item_{uuid.uuid4().hex[:8]}")
    auction_round = AuctionRound.objects.create(
        round_number=int(timezone.now().timestamp()),
        status=AuctionRound.Status.ACTIVE,
        start_at=timezone.now() - timedelta(minutes=5),
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

    monkeypatch.setattr("trade.services.auction.delivery_outbox.notify_user", lambda *args, **kwargs: True)

    bid, _ = place_bid(bidder, slot.id, 5)
    AuctionRound.objects.filter(pk=auction_round.pk).update(end_at=timezone.now() - timedelta(seconds=1))
    auction_round.refresh_from_db()

    barrier = threading.Barrier(2)
    results: list[dict[str, int]] = []
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            barrier.wait(timeout=5)
            stats = settle_auction_round(round_id=auction_round.id)
            results.append(
                {
                    "settled": int(stats.get("settled", 0)),
                    "sold": int(stats.get("sold", 0)),
                    "unsold": int(stats.get("unsold", 0)),
                    "total_gold_bars": int(stats.get("total_gold_bars", 0)),
                }
            )
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    auction_round.refresh_from_db()
    slot.refresh_from_db()
    bid.refresh_from_db()
    bid.frozen_record.refresh_from_db()
    gold_bar_inventory = InventoryItem.objects.get(
        manor=bidder,
        template=gold_bar_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    delivery = AuctionDelivery.objects.select_related("message").get(bid=bid)

    assert errors == []
    assert len(results) == 2
    assert sum(result["settled"] for result in results) == 1
    assert sum(result["sold"] for result in results) == 1
    assert max(result["total_gold_bars"] for result in results) == 5
    assert auction_round.status == AuctionRound.Status.COMPLETED
    assert slot.status == AuctionSlot.Status.SOLD
    assert bid.status == AuctionBid.Status.WON
    assert bid.frozen_record.is_frozen is False
    assert gold_bar_inventory.quantity == 5
    assert delivery.status == AuctionDelivery.Status.DELIVERED
    assert delivery.delivery_method == AuctionDelivery.Method.MESSAGE_ATTACHMENT
    assert delivery.message is not None
    assert delivery.message.attachments.get("items", {}).get(auction_item.key) == 1
