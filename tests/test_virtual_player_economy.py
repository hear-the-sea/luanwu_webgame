from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gameplay.services.virtual_player_core.economy import (
    ForcedSettlementBudget,
    ForcedSettlementBudgetError,
    parse_forced_settlement_budget,
    plan_forced_settlement,
    serialize_forced_settlement_budget,
)

NOW = datetime(2026, 7, 28, 23, 30, tzinfo=UTC)


def _budget(**overrides) -> ForcedSettlementBudget:
    values = {
        "utc_date": NOW.date(),
        "silver_units": 100,
        "grain_units": 200,
        "combined_units": 300,
        "silver_capacity_snapshot": 1_000,
        "grain_capacity_snapshot": 2_000,
    }
    values.update(overrides)
    return ForcedSettlementBudget(**values)


def test_budget_payload_round_trip_is_strict_and_canonical() -> None:
    payload = _budget().to_payload()

    assert serialize_forced_settlement_budget(parse_forced_settlement_budget(payload)) == payload
    assert parse_forced_settlement_budget({}) is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "must be a mapping"),
        ({"utc_date": "2026-07-28"}, "missing combined_units"),
        ({**_budget().to_payload(), "extra": 1}, "unknown extra"),
        ({**_budget().to_payload(), "utc_date": "2026-7-28"}, "ISO-8601"),
        ({**_budget().to_payload(), "silver_units": True}, "non-negative integer"),
        ({**_budget().to_payload(), "combined_units": 301}, "must equal"),
        ({**_budget().to_payload(), "silver_units": 501, "combined_units": 701}, "daily capacity cap"),
    ],
)
def test_budget_payload_rejects_malformed_or_internally_inconsistent_values(payload, message: str) -> None:
    with pytest.raises(ForcedSettlementBudgetError, match=message):
        parse_forced_settlement_budget(payload)


def test_first_positive_settlement_freezes_capacity_and_applies_cycle_caps() -> None:
    decision = plan_forced_settlement(
        None,
        now=NOW,
        silver_capacity=1_000,
        grain_capacity=2_000,
        requested_silver=500,
        requested_grain=500,
    )

    assert (decision.silver_units, decision.grain_units) == (100, 200)
    assert decision.budget_after == _budget()


def test_same_day_capacity_growth_does_not_raise_frozen_daily_caps() -> None:
    budget = _budget(silver_units=450, grain_units=900, combined_units=1_350)

    decision = plan_forced_settlement(
        budget,
        now=NOW,
        silver_capacity=100_000,
        grain_capacity=100_000,
        requested_silver=10_000,
        requested_grain=10_000,
    )

    assert (decision.silver_units, decision.grain_units) == (50, 100)
    assert decision.budget_after is not None
    assert decision.budget_after.silver_capacity_snapshot == 1_000
    assert decision.budget_after.grain_capacity_snapshot == 2_000


def test_combined_cap_is_allocated_proportionally_without_exceeding_resource_caps() -> None:
    budget = ForcedSettlementBudget(
        utc_date=NOW.date(),
        silver_units=999_000,
        grain_units=999_000,
        combined_units=1_998_000,
        silver_capacity_snapshot=4_000_000,
        grain_capacity_snapshot=4_000_000,
    )

    decision = plan_forced_settlement(
        budget,
        now=NOW,
        silver_capacity=1_000_000,
        grain_capacity=1_000_000,
        requested_silver=3_000,
        requested_grain=1_000,
    )

    assert (decision.silver_units, decision.grain_units) == (1_500, 500)
    assert decision.budget_after is not None
    assert decision.budget_after.combined_units == 2_000_000


def test_utc_day_rollover_uses_current_capacity_only_when_positive_delta_is_applied() -> None:
    old_budget = _budget()
    after_midnight = NOW + timedelta(hours=1)

    no_op = plan_forced_settlement(
        old_budget,
        now=after_midnight,
        silver_capacity=3_000,
        grain_capacity=4_000,
        requested_silver=0,
        requested_grain=0,
    )
    applied = plan_forced_settlement(
        old_budget,
        now=after_midnight,
        silver_capacity=3_000,
        grain_capacity=4_000,
        requested_silver=300,
        requested_grain=400,
    )

    assert no_op.budget_after is old_budget
    assert applied.budget_after == ForcedSettlementBudget(
        utc_date=after_midnight.date(),
        silver_units=300,
        grain_units=400,
        combined_units=700,
        silver_capacity_snapshot=3_000,
        grain_capacity_snapshot=4_000,
    )


def test_plan_rejects_naive_time_and_negative_inputs() -> None:
    with pytest.raises(ForcedSettlementBudgetError, match="timezone-aware"):
        plan_forced_settlement(
            None,
            now=NOW.replace(tzinfo=None),
            silver_capacity=1,
            grain_capacity=1,
            requested_silver=0,
            requested_grain=0,
        )
    with pytest.raises(ForcedSettlementBudgetError, match="requested_grain"):
        plan_forced_settlement(
            None,
            now=NOW,
            silver_capacity=1,
            grain_capacity=1,
            requested_silver=0,
            requested_grain=-1,
        )
