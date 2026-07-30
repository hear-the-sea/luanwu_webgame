from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gameplay.services.virtual_player_core.contracts import (
    InvalidStrengthBudgetError,
    StrengthBudgetEntry,
    StrengthBudgetExceededError,
    calculate_positive_growth_bps,
    consume_strength_budget,
    parse_strength_budget_entries,
    prune_strength_budget_entries,
    serialize_strength_budget_entries,
    strength_budget_usage,
)

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _payload(*, hours_ago: int, growth_bps: int = 100, policy_version: int = 1):
    return {
        "applied_at": (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        "positive_growth_bps": growth_bps,
        "policy_version": policy_version,
    }


def test_strength_budget_round_trip_normalizes_offsets_to_utc() -> None:
    payload = [
        {
            "applied_at": "2026-07-28T19:00:00+08:00",
            "positive_growth_bps": 0,
            "policy_version": 2,
        }
    ]

    entries = parse_strength_budget_entries(payload, now=NOW)

    assert serialize_strength_budget_entries(entries) == [
        {
            "applied_at": "2026-07-28T11:00:00Z",
            "positive_growth_bps": 0,
            "policy_version": 2,
        }
    ]


def test_positive_growth_bps_uses_the_frozen_ceil_and_floor_formula() -> None:
    assert calculate_positive_growth_bps(pre_score=100, post_score=103) == 300
    assert calculate_positive_growth_bps(pre_score=100, post_score=103.0001) == 301
    assert calculate_positive_growth_bps(pre_score=0, post_score=0.5, score_floor=1) == 5000
    assert calculate_positive_growth_bps(pre_score=100, post_score=99) == 0


@pytest.mark.parametrize("field", ["pre_score", "post_score", "score_floor"])
def test_positive_growth_bps_rejects_invalid_numeric_inputs(field: str) -> None:
    values = {"pre_score": 1, "post_score": 2, "score_floor": 1}
    values[field] = float("nan") if field != "score_floor" else 0

    with pytest.raises(InvalidStrengthBudgetError, match=field):
        calculate_positive_growth_bps(**values)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "must be a list"),
        ([None], "must be a mapping"),
        ([{"applied_at": NOW.isoformat()}], "missing policy_version"),
        ([{**_payload(hours_ago=1), "sequence": 1}], "unknown sequence"),
        ([{**_payload(hours_ago=1), "positive_growth_bps": True}], "must be an integer"),
        ([{**_payload(hours_ago=1), "positive_growth_bps": -1}], "must be non-negative"),
        ([{**_payload(hours_ago=1), "policy_version": 0}], "must be positive"),
        ([{**_payload(hours_ago=1), "applied_at": "not-a-date"}], "ISO-8601 datetime"),
        ([{**_payload(hours_ago=1), "applied_at": "2026-07-28T11:00:00"}], "timezone-aware"),
    ],
)
def test_strength_budget_parser_rejects_malformed_payloads(payload, message: str) -> None:
    with pytest.raises(InvalidStrengthBudgetError, match=message):
        parse_strength_budget_entries(payload, now=NOW)


def test_strength_budget_parser_rejects_more_than_four_unsorted_and_far_future_entries() -> None:
    with pytest.raises(InvalidStrengthBudgetError, match="at most 4"):
        parse_strength_budget_entries([_payload(hours_ago=index) for index in range(5)], now=NOW)
    with pytest.raises(InvalidStrengthBudgetError, match="sorted"):
        parse_strength_budget_entries([_payload(hours_ago=1), _payload(hours_ago=2)], now=NOW)
    with pytest.raises(InvalidStrengthBudgetError, match="clock skew"):
        parse_strength_budget_entries(
            [{**_payload(hours_ago=0), "applied_at": (NOW + timedelta(minutes=6)).isoformat()}],
            now=NOW,
        )


def test_pruning_excludes_entries_at_the_exact_trailing_window_boundary() -> None:
    entries = parse_strength_budget_entries(
        [_payload(hours_ago=24), _payload(hours_ago=23), _payload(hours_ago=0)],
        now=NOW,
    )

    active = prune_strength_budget_entries(entries, now=NOW)

    assert [entry.applied_at for entry in active] == [NOW - timedelta(hours=23), NOW]
    assert strength_budget_usage(active).action_count == 2
    assert strength_budget_usage(active).positive_growth_bps == 200


def test_consumption_allows_exact_caps_and_zero_growth_still_consumes_an_action() -> None:
    entries = (
        StrengthBudgetEntry(applied_at=NOW - timedelta(hours=2), positive_growth_bps=200, policy_version=1),
        StrengthBudgetEntry(applied_at=NOW - timedelta(hours=1), positive_growth_bps=300, policy_version=1),
    )

    consumed = consume_strength_budget(
        entries,
        now=NOW,
        positive_growth_bps=0,
        policy_version=2,
        max_actions=3,
        max_positive_growth_bps=500,
    )

    assert strength_budget_usage(consumed).action_count == 3
    assert strength_budget_usage(consumed).positive_growth_bps == 500
    assert consumed[-1].policy_version == 2


def test_consumption_rejects_action_and_growth_overruns_independently() -> None:
    entries = (StrengthBudgetEntry(applied_at=NOW - timedelta(hours=1), positive_growth_bps=200, policy_version=1),)

    with pytest.raises(StrengthBudgetExceededError, match="action count"):
        consume_strength_budget(
            entries,
            now=NOW,
            positive_growth_bps=0,
            policy_version=1,
            max_actions=1,
            max_positive_growth_bps=1000,
        )
    with pytest.raises(StrengthBudgetExceededError, match="positive strength growth"):
        consume_strength_budget(
            entries,
            now=NOW,
            positive_growth_bps=101,
            policy_version=1,
            max_actions=4,
            max_positive_growth_bps=300,
        )


def test_consumption_prunes_expired_entries_before_applying_limits() -> None:
    entries = (StrengthBudgetEntry(applied_at=NOW - timedelta(hours=25), positive_growth_bps=1000, policy_version=1),)

    consumed = consume_strength_budget(
        entries,
        now=NOW,
        positive_growth_bps=100,
        policy_version=1,
        max_actions=1,
        max_positive_growth_bps=100,
    )

    assert consumed == (StrengthBudgetEntry(applied_at=NOW, positive_growth_bps=100, policy_version=1),)
