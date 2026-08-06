from __future__ import annotations

import pytest
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from gameplay.services.utils.cache import CacheKeys
from trade.services import market_service

pytest_plugins = ("tests.trade_service.fixtures",)

MARKET_STATS_CACHE_KEY = CacheKeys.market_stats()


@pytest.fixture(autouse=True)
def clear_market_stats_cache():
    cache.delete(MARKET_STATS_CACHE_KEY)
    yield
    cache.delete(MARKET_STATS_CACHE_KEY)


def _seed_market_stats_cache() -> None:
    cache.set(MARKET_STATS_CACHE_KEY, {"active_count": 999, "sold_today": 999}, timeout=60)


@pytest.mark.django_db(transaction=True)
def test_market_stats_invalidation_waits_for_outer_transaction_commit():
    _seed_market_stats_cache()

    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            market_service._schedule_market_stats_cache_invalidation()
            raise RuntimeError("rollback")

    assert cache.get(MARKET_STATS_CACHE_KEY) == {"active_count": 999, "sold_today": 999}

    with transaction.atomic():
        market_service._schedule_market_stats_cache_invalidation()

    assert cache.get(MARKET_STATS_CACHE_KEY) is None


@pytest.mark.django_db(transaction=True)
def test_create_listing_invalidates_market_stats_cache(seller_manor, tradeable_item_template):
    _seed_market_stats_cache()

    market_service.create_listing(
        manor=seller_manor,
        item_key="test_tradeable_item",
        quantity=10,
        unit_price=2000,
        duration=7200,
    )

    assert cache.get(MARKET_STATS_CACHE_KEY) is None


@pytest.mark.django_db(transaction=True)
def test_cancel_listing_invalidates_market_stats_cache(seller_manor, tradeable_item_template):
    listing = market_service.create_listing(
        manor=seller_manor,
        item_key="test_tradeable_item",
        quantity=10,
        unit_price=2000,
        duration=7200,
    )
    _seed_market_stats_cache()

    market_service.cancel_listing(seller_manor, listing.id)

    assert cache.get(MARKET_STATS_CACHE_KEY) is None


@pytest.mark.django_db(transaction=True)
def test_purchase_listing_invalidates_market_stats_cache(seller_manor, buyer_manor, tradeable_item_template):
    listing = market_service.create_listing(
        manor=seller_manor,
        item_key="test_tradeable_item",
        quantity=10,
        unit_price=2000,
        duration=7200,
    )
    _seed_market_stats_cache()

    market_service.purchase_listing(buyer_manor, listing.id)

    assert cache.get(MARKET_STATS_CACHE_KEY) is None


@pytest.mark.django_db(transaction=True)
def test_expire_listings_invalidates_market_stats_cache(seller_manor, tradeable_item_template):
    listing = market_service.create_listing(
        manor=seller_manor,
        item_key="test_tradeable_item",
        quantity=10,
        unit_price=2000,
        duration=7200,
    )
    listing.expires_at = timezone.now() - timezone.timedelta(minutes=1)
    listing.save(update_fields=["expires_at"])
    _seed_market_stats_cache()

    assert market_service.expire_listings() == 1
    assert cache.get(MARKET_STATS_CACHE_KEY) is None
