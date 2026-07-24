"""Tests for auction gold bar freezing/unfreezing logic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import TradeValidationError
from trade.services.auction import gold_bars

pytestmark = pytest.mark.django_db


# ============ get_total_gold_bars tests ============


def test_get_total_gold_bars_returns_quantity():
    """Test that get_total_gold_bars returns correct quantity."""
    manor = MagicMock()

    with patch.object(gold_bars, "get_item_quantity", return_value=100):
        result = gold_bars.get_total_gold_bars(manor)

        assert result == 100


def test_get_total_gold_bars_returns_zero_when_none():
    """Test that get_total_gold_bars returns 0 when no gold bars."""
    manor = MagicMock()

    with patch.object(gold_bars, "get_item_quantity", return_value=0):
        result = gold_bars.get_total_gold_bars(manor)

        assert result == 0


# ============ get_frozen_gold_bars tests ============


def test_get_frozen_gold_bars_aggregates_correctly():
    """Test that get_frozen_gold_bars aggregates frozen amounts."""
    manor = MagicMock()

    with patch.object(gold_bars.FrozenGoldBar, "objects") as mock_qs:
        mock_qs.filter.return_value.aggregate.return_value = {"total": 50}

        result = gold_bars.get_frozen_gold_bars(manor)

        assert result == 50
        mock_qs.filter.assert_called_once_with(manor=manor, is_frozen=True)


def test_get_frozen_gold_bars_returns_zero_when_none():
    """Test that get_frozen_gold_bars returns 0 when no frozen bars."""
    manor = MagicMock()

    with patch.object(gold_bars.FrozenGoldBar, "objects") as mock_qs:
        mock_qs.filter.return_value.aggregate.return_value = {"total": None}

        result = gold_bars.get_frozen_gold_bars(manor)

        assert result == 0


# ============ get_available_gold_bars tests ============


def test_get_available_gold_bars_calculates_correctly():
    """Test that available = total - frozen."""
    manor = MagicMock()

    with patch.object(gold_bars, "get_total_gold_bars", return_value=100):
        with patch.object(gold_bars, "get_frozen_gold_bars", return_value=30):
            result = gold_bars.get_available_gold_bars(manor)

            assert result == 70


def test_get_available_gold_bars_never_negative():
    """Test that available gold bars is never negative."""
    manor = MagicMock()

    with patch.object(gold_bars, "get_total_gold_bars", return_value=10):
        with patch.object(gold_bars, "get_frozen_gold_bars", return_value=50):
            result = gold_bars.get_available_gold_bars(manor)

            assert result == 0


def test_get_available_gold_bars_all_frozen():
    """Test that available is 0 when all are frozen."""
    manor = MagicMock()

    with patch.object(gold_bars, "get_total_gold_bars", return_value=50):
        with patch.object(gold_bars, "get_frozen_gold_bars", return_value=50):
            result = gold_bars.get_available_gold_bars(manor)

            assert result == 0


# ============ freeze_gold_bars tests ============


def test_freeze_gold_bars_rejects_zero_amount():
    """Test that freezing 0 gold bars is rejected."""
    manor = MagicMock()
    bid = MagicMock()

    with pytest.raises(TradeValidationError, match="冻结数量必须大于0"):
        gold_bars.freeze_gold_bars(manor, 0, bid)


def test_freeze_gold_bars_rejects_negative_amount():
    """Test that freezing negative gold bars is rejected."""
    manor = MagicMock()
    bid = MagicMock()

    with pytest.raises(TradeValidationError, match="冻结数量必须大于0"):
        gold_bars.freeze_gold_bars(manor, -5, bid)


def test_freeze_gold_bars_rejects_insufficient():
    """Test that freezing more than available is rejected."""
    manor = MagicMock()
    bid = MagicMock()

    inventory_item = SimpleNamespace(quantity=50)

    with patch.object(gold_bars.InventoryItem, "objects") as mock_inv_qs:
        mock_inv_qs.select_for_update.return_value.filter.return_value.select_related.return_value.first.return_value = (
            inventory_item
        )

        with patch.object(gold_bars.FrozenGoldBar, "objects") as mock_frozen_qs:
            mock_frozen_qs.filter.return_value.aggregate.return_value = {"total": 30}

            # Available = 50 - 30 = 20, trying to freeze 25
            with pytest.raises(TradeValidationError, match="可用金条不足"):
                gold_bars.freeze_gold_bars(manor, 25, bid)


# ============ unfreeze_gold_bars tests ============


def test_unfreeze_gold_bars_skips_already_unfrozen():
    """Test that already unfrozen records are skipped."""
    frozen_record = MagicMock()
    frozen_record.pk = 1

    with patch.object(gold_bars.FrozenGoldBar, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = None

        # Should not raise, just return
        gold_bars.unfreeze_gold_bars(frozen_record)


def test_unfreeze_gold_bars_skips_if_not_frozen():
    """Test that records with is_frozen=False are skipped."""
    frozen_record = MagicMock()
    frozen_record.pk = 1

    locked_record = MagicMock()
    locked_record.is_frozen = False

    with patch.object(gold_bars.FrozenGoldBar, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            locked_record
        )

        # Should not raise, just return
        gold_bars.unfreeze_gold_bars(frozen_record)

        # save should not be called
        locked_record.save.assert_not_called()


# ============ consume_frozen_gold_bars tests ============


def test_consume_frozen_gold_bars_skips_not_found():
    """Test that non-existent record is skipped."""
    frozen_record = MagicMock()
    frozen_record.pk = 1
    manor = MagicMock()

    with patch.object(gold_bars.FrozenGoldBar, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = None

        # Should not raise
        gold_bars.consume_frozen_gold_bars(frozen_record, manor)


def test_consume_frozen_gold_bars_skips_already_unfrozen():
    """Test that already unfrozen record is skipped."""
    frozen_record = MagicMock()
    frozen_record.pk = 1
    manor = MagicMock()

    locked_record = MagicMock()
    locked_record.is_frozen = False

    with patch.object(gold_bars.FrozenGoldBar, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            locked_record
        )

        # Should not raise
        gold_bars.consume_frozen_gold_bars(frozen_record, manor)

        # save should not be called
        locked_record.save.assert_not_called()


# ============ try_get_frozen_record tests ============


def test_try_get_frozen_record_returns_record():
    """Test that try_get_frozen_record returns record when exists."""
    frozen_record = MagicMock()
    bid = MagicMock()
    bid.frozen_record = frozen_record

    result = gold_bars.try_get_frozen_record(bid)

    assert result == frozen_record


def test_try_get_frozen_record_returns_none_on_does_not_exist():
    """Test that try_get_frozen_record returns None when not exists."""
    bid = MagicMock()

    # Simulate DoesNotExist exception
    type(bid).frozen_record = property(lambda self: (_ for _ in ()).throw(gold_bars.FrozenGoldBar.DoesNotExist()))

    result = gold_bars.try_get_frozen_record(bid)

    assert result is None
