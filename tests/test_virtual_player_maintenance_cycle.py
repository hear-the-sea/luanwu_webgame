from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gameplay.services.virtual_player_core.maintenance_cycle import (
    CycleTrigger,
    MaintenanceCycleError,
    MaintenanceCycleState,
    MaintenanceReasonCategory,
    allocate_cycle_action,
    classify_maintenance_reason,
    cycle_retry_due_at,
    next_ordinary_slot_due_at,
    ordinary_slot_interval_minutes,
)


def test_ordinary_slot_intervals_are_deterministic_and_bounded() -> None:
    cycle_id = "vp-cycle-42-3-seed"

    first = tuple(ordinary_slot_interval_minutes(cycle_id, ordinal) for ordinal in range(1, 17))
    second = tuple(ordinary_slot_interval_minutes(cycle_id, ordinal) for ordinal in range(1, 17))

    assert first == second
    assert all(10 <= interval <= 15 for interval in first)


def test_next_ordinary_slot_due_at_uses_the_persisted_seed() -> None:
    completed_at = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    interval = ordinary_slot_interval_minutes("seed-a", 2)

    assert next_ordinary_slot_due_at(
        "seed-a",
        completed_at=completed_at,
        next_slot_ordinal=2,
    ) == completed_at + timedelta(minutes=interval)


@pytest.mark.parametrize(
    ("cycle_id", "slot_ordinal"),
    (("", 1), ("seed", 0), ("seed", 17)),
)
def test_ordinary_slot_interval_rejects_invalid_identity_or_ordinal(
    cycle_id: str,
    slot_ordinal: int,
) -> None:
    with pytest.raises(MaintenanceCycleError):
        ordinary_slot_interval_minutes(cycle_id, slot_ordinal)


def test_next_ordinary_slot_due_at_rejects_naive_timestamp() -> None:
    with pytest.raises(MaintenanceCycleError, match="timezone-aware"):
        next_ordinary_slot_due_at(
            "seed-a",
            completed_at=datetime(2026, 8, 10, 8, 0),
            next_slot_ordinal=2,
        )


def test_cycle_retry_due_at_is_a_stable_short_backoff() -> None:
    now = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)

    retry_at = cycle_retry_due_at("seed-a", now=now, reason="profile_busy")

    assert retry_at == cycle_retry_due_at("seed-a", now=now, reason="profile_busy")
    assert timedelta(minutes=1) <= retry_at - now <= timedelta(minutes=3)


def test_repeated_high_cost_actions_consume_distinct_cycle_budget() -> None:
    state = MaintenanceCycleState(
        cycle_id="vp-cycle-high-cost",
        cycle_ordinal=1,
        trigger=CycleTrigger.SCHEDULED,
        max_actions=16,
    )

    first, first_ordinal = allocate_cycle_action(
        state,
        action_kind="training",
        business_key="training:guest:1",
    )
    second, second_ordinal = allocate_cycle_action(
        first,
        action_kind="training",
        business_key="training:guest:2",
    )

    assert (first_ordinal, second_ordinal) == (1, 2)
    assert second.covered_action_kinds == ("training",)
    assert second.high_cost_actions_used == 2


@pytest.mark.parametrize(
    ("reason", "category"),
    (
        ("candidate_domain_constraint", MaintenanceReasonCategory.DOMAIN_CONSTRAINT),
        ("insufficient_resource", MaintenanceReasonCategory.RESOURCE),
        ("salary_runway_protected", MaintenanceReasonCategory.SALARY),
        ("profile_busy", MaintenanceReasonCategory.LOCK_CONFLICT),
        ("no_eligible_candidate", MaintenanceReasonCategory.NO_CANDIDATE),
        ("strength_cap", MaintenanceReasonCategory.POLICY_GUARD),
        ("unclassified_reason", MaintenanceReasonCategory.OTHER),
    ),
)
def test_maintenance_reason_categories_are_stable(
    reason: str,
    category: MaintenanceReasonCategory,
) -> None:
    assert classify_maintenance_reason(reason) is category
