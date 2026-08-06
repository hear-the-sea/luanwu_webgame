"""Tests for market service (交易行/集市) logic."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from core.exceptions import TradeValidationError
from gameplay.models import ItemTemplate
from gameplay.services.manor.core import ensure_manor
from trade.models import MarketListing
from trade.services import market_service

pytestmark = pytest.mark.django_db


# ============ Constants tests ============


def test_price_limits():
    """Test that price limits are correctly defined."""
    assert market_service.MARKET_MIN_PRESTIGE == 300
    assert market_service.MIN_PRICE_MULTIPLIER == 1.0
    assert market_service.MAX_PRICE == 10000000
    assert market_service.MAX_TOTAL_PRICE == 2000000000


def test_market_prestige_requirement_includes_exact_threshold():
    below_threshold = SimpleNamespace(prestige=market_service.MARKET_MIN_PRESTIGE - 1)
    at_threshold = SimpleNamespace(prestige=market_service.MARKET_MIN_PRESTIGE)

    assert market_service.can_buy_or_list_on_market(below_threshold) is False
    with pytest.raises(TradeValidationError, match="声望达到 300"):
        market_service.validate_market_buy_or_list_access(below_threshold)

    assert market_service.can_buy_or_list_on_market(at_threshold) is True
    market_service.validate_market_buy_or_list_access(at_threshold)


def test_allowed_listing_order_by_fields():
    """Test that allowed order by fields are defined."""
    expected_fields = {
        "listed_at",
        "-listed_at",
        "unit_price",
        "-unit_price",
        "total_price",
        "-total_price",
        "quantity",
        "-quantity",
        "expires_at",
        "-expires_at",
    }
    assert market_service.ALLOWED_LISTING_ORDER_BY == expected_fields


# ============ get_listing_fee tests ============


def test_get_listing_fee_returns_correct_fee():
    """Test that get_listing_fee returns correct fee for known durations."""
    assert market_service.get_listing_fee(7200) == 5000
    assert market_service.get_listing_fee(28800) == 10000
    assert market_service.get_listing_fee(86400) == 20000


def test_get_listing_fee_returns_default_for_unknown():
    """Test that get_listing_fee returns default for unknown duration."""
    assert market_service.get_listing_fee(9999) == 5000


# ============ validate_listing_price tests ============


def test_validate_listing_price_rejects_below_minimum():
    """Test that price below minimum is rejected."""
    item_template = SimpleNamespace(price=1000)

    with pytest.raises(TradeValidationError, match="单价不能低于"):
        market_service.validate_listing_price(item_template, 500)


def test_validate_listing_price_rejects_zero_price_for_zero_reference_items():
    item_template = SimpleNamespace(price=0)

    with pytest.raises(TradeValidationError, match="单价必须大于 0 银两"):
        market_service.validate_listing_price(item_template, 0)


def test_validate_listing_price_rejects_above_maximum():
    """Test that price above maximum is rejected."""
    item_template = SimpleNamespace(price=100)

    with pytest.raises(TradeValidationError, match="单价不能超过"):
        market_service.validate_listing_price(item_template, 20000000)


def test_validate_listing_price_accepts_valid_price():
    """Test that valid price is accepted."""
    item_template = SimpleNamespace(price=1000)

    # Should not raise
    market_service.validate_listing_price(item_template, 1500)


def test_validate_listing_price_accepts_exact_minimum():
    """Test that exact minimum price is accepted."""
    item_template = SimpleNamespace(price=1000)

    # Should not raise (1000 * 1.0 = 1000)
    market_service.validate_listing_price(item_template, 1000)


def test_validate_listing_price_accepts_exact_maximum():
    """Test that exact maximum price is accepted."""
    item_template = SimpleNamespace(price=100)

    # Should not raise
    market_service.validate_listing_price(item_template, 10000000)


def test_validate_listing_price_handles_non_integer_template_price():
    """Test that malformed template price does not crash validation."""
    item_template = SimpleNamespace(price="bad")

    # min price should be treated as 0
    market_service.validate_listing_price(item_template, 10)


# ============ create_listing validation tests ============


def test_create_listing_rejects_invalid_duration():
    """Test that invalid duration is rejected."""
    manor = MagicMock()

    with pytest.raises(TradeValidationError, match="无效的上架时长"):
        market_service.create_listing(manor, "item_key", 1, 1000, 9999)


def test_create_listing_rejects_nonexistent_item():
    """Test that nonexistent item is rejected."""
    manor = MagicMock()

    with patch.object(market_service, "load_market_item_template", side_effect=TradeValidationError("物品不存在")):
        with pytest.raises(TradeValidationError, match="物品不存在"):
            market_service.create_listing(manor, "fake_item", 1, 1000, 7200)


def test_create_listing_rejects_non_tradeable_item():
    """Test that non-tradeable item is rejected."""
    manor = MagicMock()

    with patch.object(market_service, "load_market_item_template", side_effect=TradeValidationError("该物品不可交易")):
        with pytest.raises(TradeValidationError, match="该物品不可交易"):
            market_service.create_listing(manor, "item", 1, 1000, 7200)


def test_create_listing_rejects_zero_quantity():
    """Test that zero quantity is rejected."""
    manor = MagicMock()
    item_template = SimpleNamespace(key="item", tradeable=True, price=100)

    with patch.object(market_service, "load_market_item_template", return_value=item_template):
        with pytest.raises(TradeValidationError, match="数量必须大于0"):
            market_service.create_listing(manor, "item", 0, 1000, 7200)


def test_create_listing_rejects_negative_quantity():
    """Test that negative quantity is rejected."""
    manor = MagicMock()
    item_template = SimpleNamespace(key="item", tradeable=True, price=100)

    with patch.object(market_service, "load_market_item_template", return_value=item_template):
        with pytest.raises(TradeValidationError, match="数量必须大于0"):
            market_service.create_listing(manor, "item", -1, 1000, 7200)


def test_create_listing_rejects_non_integer_quantity():
    """Test that non-integer quantity is rejected safely."""
    manor = MagicMock()
    item_template = SimpleNamespace(key="item", tradeable=True, price=100)

    with patch.object(market_service, "load_market_item_template", return_value=item_template):
        with pytest.raises(TradeValidationError, match="数量必须大于0"):
            market_service.create_listing(manor, "item", cast(int, "abc"), 1000, 7200)


def test_create_listing_rejects_non_integer_unit_price():
    """Test that non-integer price input is rejected safely."""
    manor = MagicMock()
    item_template = SimpleNamespace(key="item", tradeable=True, price=100)

    with patch.object(market_service, "load_market_item_template", return_value=item_template):
        with pytest.raises(TradeValidationError, match="单价不能低于"):
            market_service.create_listing(manor, "item", 1, cast(int, "abc"), 7200)


def test_create_listing_rejects_non_integer_duration():
    """Test that non-integer duration is rejected safely."""
    manor = MagicMock()

    with pytest.raises(TradeValidationError, match="无效的上架时长"):
        market_service.create_listing(manor, "item_key", 1, 1000, cast(int, "abc"))


# ============ get_active_listings tests ============


def test_get_active_listings_uses_safe_order_by():
    """Test that get_active_listings uses safe order by field."""
    with patch.object(market_service.MarketListing, "objects") as mock_qs:
        mock_filter = MagicMock()
        mock_qs.filter.return_value.select_related.return_value = mock_filter
        mock_filter.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_filter

        # Valid order by
        market_service.get_active_listings(order_by="-listed_at")
        mock_filter.order_by.assert_called_with("-listed_at", "-id")


def test_get_active_listings_rejects_unsafe_order_by():
    """Test that get_active_listings falls back for unsafe order by."""
    with patch.object(market_service.MarketListing, "objects") as mock_qs:
        mock_filter = MagicMock()
        mock_qs.filter.return_value.select_related.return_value = mock_filter
        mock_filter.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_filter

        # Invalid order by should fall back to default
        market_service.get_active_listings(order_by="sql_injection_attempt")
        mock_filter.order_by.assert_called_with("-listed_at", "-id")


@pytest.mark.django_db
def test_get_active_listings_executes_real_ordering_fields_without_field_error(django_user_model):
    user = django_user_model.objects.create_user(username="market_ordering_real_query", password="pass12345")
    manor = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key="market_ordering_real_query_item",
        name="交易排序测试物品",
        tradeable=True,
        price=100,
    )
    MarketListing.objects.create(
        seller=manor,
        item_template=template,
        quantity=1,
        unit_price=200,
        total_price=200,
        duration=MarketListing.Duration.SHORT,
        listing_fee=5000,
        expires_at=timezone.now() + timedelta(hours=1),
        status=MarketListing.Status.ACTIVE,
    )

    for order_by in market_service.ALLOWED_LISTING_ORDER_BY:
        assert list(market_service.get_active_listings(order_by=order_by))

    # `price` belongs to ItemTemplate, not MarketListing; it must safely fall back.
    assert list(market_service.get_active_listings(order_by="price"))


def test_get_active_listings_treats_loot_box_as_tool_category():
    """Tool-category filtering should include loot_box templates."""
    with patch.object(market_service.MarketListing, "objects") as mock_qs:
        mock_filter = MagicMock()
        mock_qs.filter.return_value.select_related.return_value = mock_filter
        mock_filter.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_filter

        market_service.get_active_listings(category="tool")

        mock_filter.filter.assert_called_once_with(
            item_template__effect_type__in=market_service.LEGACY_TOOL_EFFECT_TYPES
        )
        assert "loot_box" in market_service.LEGACY_TOOL_EFFECT_TYPES


# ============ purchase_listing validation tests ============


def test_purchase_listing_rejects_nonexistent():
    """Test that nonexistent listing is rejected."""
    buyer = MagicMock()

    with patch.object(market_service.MarketListing, "objects") as mock_qs:
        locked_chain = mock_qs.select_for_update.return_value
        locked_chain.filter.return_value.first.return_value = None

        with pytest.raises(TradeValidationError, match="挂单不存在"):
            market_service.purchase_listing(buyer, 999)

        locked_chain.select_related.assert_not_called()


# ============ cancel_listing tests ============


def test_cancel_listing_rejects_nonexistent():
    """Test that nonexistent listing cannot be cancelled."""
    manor = MagicMock()

    with patch.object(market_service.MarketListing, "objects") as mock_qs:
        locked_chain = mock_qs.select_for_update.return_value
        locked_chain.filter.return_value.first.return_value = None

        with pytest.raises(TradeValidationError, match="挂单不存在或无权取消"):
            market_service.cancel_listing(manor, 999)

        locked_chain.select_related.assert_not_called()


# ============ get_my_listings tests ============


def test_get_my_listings_filters_by_manor():
    """Test that get_my_listings filters by manor."""
    manor = MagicMock()
    manor.pk = 1

    with patch.object(market_service.MarketListing, "objects") as mock_qs:
        mock_filter = MagicMock()
        mock_qs.filter.return_value.select_related.return_value = mock_filter
        mock_filter.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_filter

        market_service.get_my_listings(manor)

        mock_qs.filter.assert_called_once_with(seller=manor)


def test_get_my_listings_filters_by_status():
    """Test that get_my_listings can filter by status."""
    manor = MagicMock()

    with patch.object(market_service.MarketListing, "objects") as mock_qs:
        mock_filter = MagicMock()
        mock_qs.filter.return_value.select_related.return_value = mock_filter
        mock_filter.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_filter

        market_service.get_my_listings(manor, status="active")

        mock_filter.filter.assert_called_once_with(status="active")


def test_get_my_listings_ignores_all_status():
    """Test that get_my_listings ignores 'all' status filter."""
    manor = MagicMock()

    with patch.object(market_service.MarketListing, "objects") as mock_qs:
        mock_filter = MagicMock()
        mock_qs.filter.return_value.select_related.return_value = mock_filter
        mock_filter.order_by.return_value = mock_filter

        market_service.get_my_listings(manor, status="all")

        # filter should not be called with status when status is "all"
        mock_filter.filter.assert_not_called()


# ============ get_market_stats tests ============


def test_get_market_stats_returns_expected_keys(monkeypatch):
    """Test that get_market_stats returns expected keys."""
    monkeypatch.setattr(market_service, "get_or_set", lambda _key, default_func, timeout: default_func())
    with patch.object(market_service.MarketListing, "objects") as mock_listing_qs:
        with patch.object(market_service.MarketTransaction, "objects") as mock_tx_qs:
            mock_listing_qs.filter.return_value.count.return_value = 10
            mock_tx_qs.filter.return_value.count.return_value = 5

            stats = market_service.get_market_stats()

            assert "active_count" in stats
            assert "sold_today" in stats
            assert stats["active_count"] == 10
            assert stats["sold_today"] == 5


def test_get_market_stats_uses_local_day_range_for_transaction_index():
    now = timezone.make_aware(
        timezone.datetime(2026, 8, 5, 12, 30),
        timezone.get_current_timezone(),
    )
    expected_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    expected_end = expected_start + timedelta(days=1)

    with patch.object(market_service, "get_or_set", lambda _key, default_func, timeout: default_func()):
        with patch.object(market_service.timezone, "now", return_value=now):
            with patch.object(market_service.MarketListing, "objects") as mock_listing_qs:
                with patch.object(market_service.MarketTransaction, "objects") as mock_tx_qs:
                    mock_listing_qs.filter.return_value.count.return_value = 0
                    mock_tx_qs.filter.return_value.count.return_value = 0

                    market_service.get_market_stats()

    mock_tx_qs.filter.assert_called_once_with(
        transaction_at__gte=expected_start,
        transaction_at__lt=expected_end,
    )


# ============ get_tradeable_inventory tests ============


def test_get_tradeable_inventory_filters_correctly():
    """Test that get_tradeable_inventory applies correct filters."""
    manor = MagicMock()
    expected_queryset = MagicMock()

    with patch.object(market_service, "get_tradeable_inventory_queryset", return_value=expected_queryset) as mock_get:
        result = market_service.get_tradeable_inventory(manor)

    mock_get.assert_called_once_with(manor)
    assert result is expected_queryset


# ============ expire listings limit handling tests ============


def test_expire_listings_queryset_returns_zero_when_limit_zero():
    queryset = MagicMock()

    result = market_service._expire_listings_queryset(queryset, "test", limit=0)

    assert result == 0
    queryset.order_by.assert_not_called()
    queryset.select_related.assert_not_called()


def test_expire_listings_queryset_returns_zero_when_limit_negative():
    queryset = MagicMock()

    result = market_service._expire_listings_queryset(queryset, "test", limit=-10)

    assert result == 0
    queryset.order_by.assert_not_called()
    queryset.select_related.assert_not_called()


def test_expire_listings_queryset_rejects_non_integer_limit():
    queryset = MagicMock()

    with pytest.raises(TradeValidationError, match="数量上限必须是整数"):
        market_service._expire_listings_queryset(queryset, "test", limit=cast(int, "abc"))


def test_expire_listings_queryset_skips_when_row_no_longer_active(monkeypatch):
    queryset = MagicMock()
    queryset.filter.return_value.order_by.return_value.values_list.return_value = [1]

    locked_chain = MagicMock()
    locked_chain.filter.return_value.first.return_value = None
    objects_mock = MagicMock()
    objects_mock.select_for_update.return_value = locked_chain

    with patch.object(market_service.MarketListing, "objects", objects_mock):
        create_message_mock = MagicMock()
        monkeypatch.setattr(market_service, "send_market_message", create_message_mock)

        result = market_service._expire_listings_queryset(queryset, "test", limit=None)

    assert result == 0
    create_message_mock.assert_not_called()

    prefilter_kwargs = queryset.filter.call_args.kwargs
    assert prefilter_kwargs["status"] == market_service.MarketListing.Status.ACTIVE
    assert "expires_at__lte" in prefilter_kwargs

    locked_chain.select_related.assert_not_called()
    filter_kwargs = locked_chain.filter.call_args.kwargs
    assert filter_kwargs["pk"] == 1
    assert filter_kwargs["status"] == market_service.MarketListing.Status.ACTIVE
    assert "expires_at__lte" in filter_kwargs
