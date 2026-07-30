"""
Compatibility entrypoint for the auction rounds test suite.

The original file grew beyond the audit line budget. The tests now live under
`tests/trade_auction_rounds/`, while this file remains the stable pytest
entrypoint for existing commands and CI references.
"""

from __future__ import annotations

import pytest

from gameplay.services.manor.core import ensure_manor
from tests.trade_auction_rounds.delivery_notifications import *  # noqa: F401,F403
from tests.trade_auction_rounds.round_lifecycle import *  # noqa: F401,F403
from tests.trade_auction_rounds.slot_settlement import *  # noqa: F401,F403
from trade.services.bank_service import (
    GOLD_BAR_BASE_PRICE,
    GOLD_BAR_MAX_PRICE,
    GOLD_BAR_MIN_PRICE,
    calculate_next_rate,
    calculate_progressive_factor,
)


@pytest.mark.django_db
def test_calculate_next_rate_uses_next_exchange_step(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="bank_next_rate", password="pass12345")
    manor = ensure_manor(user)

    monkeypatch.setattr("trade.services.bank_service.calculate_supply_factor", lambda: 1.0)
    monkeypatch.setattr("trade.services.bank_service.get_today_exchange_count", lambda _manor: 2)

    rate = calculate_next_rate(manor)

    expected = int(GOLD_BAR_BASE_PRICE * 1.0 * calculate_progressive_factor(3))
    expected = max(GOLD_BAR_MIN_PRICE, min(GOLD_BAR_MAX_PRICE, expected))
    assert rate == expected
