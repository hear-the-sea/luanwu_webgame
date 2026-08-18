from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gameplay.services.virtual_player_core.maintenance_cycle import (
    CycleTrigger,
    MaintenanceCycleError,
    MaintenanceCycleState,
    MaintenanceProgressCategory,
    MaintenanceReasonCategory,
    allocate_cycle_action,
    candidate_pool_cooldown_until,
    classify_maintenance_progress,
    classify_maintenance_reason,
    cycle_retry_due_at,
    merge_candidate_pool_cooldowns,
    next_ordinary_slot_due_at,
    ordinary_slot_interval_minutes,
    wake_candidate_pool_cooldowns,
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


def test_empty_candidate_pool_uses_a_bounded_cooldown_and_domain_wake() -> None:
    now = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    gaps = (
        {
            "action_kind": "building_upgrade",
            "reason": "no_candidate",
            "reason_source": "empty_candidate_pool",
            "candidate_count": 0,
        },
    )

    cooldowns = merge_candidate_pool_cooldowns({}, gaps=gaps, now=now)

    assert candidate_pool_cooldown_until({"candidate_pool_cooldowns": cooldowns}, now=now) == now + timedelta(hours=6)
    remaining, woken = wake_candidate_pool_cooldowns(
        {"candidate_pool_cooldowns": cooldowns},
        domain_event_kind="building_upgrade",
        now=now + timedelta(minutes=1),
    )
    assert remaining == []
    assert woken[0]["action_kind"] == "building_upgrade"


def test_candidate_pool_cooldown_ignores_expired_and_malformed_entries() -> None:
    now = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)

    assert (
        candidate_pool_cooldown_until(
            {
                "candidate_pool_cooldowns": [
                    None,
                    {"action_kind": "building_upgrade", "retry_at": "not-a-timestamp"},
                    {
                        "action_kind": "technology_upgrade",
                        "reason": "no_candidate",
                        "reason_source": "empty_candidate_pool",
                        "retry_at": (now - timedelta(seconds=1)).isoformat(),
                    },
                ]
            },
            now=now,
        )
        is None
    )


def test_candidate_pool_cooldown_merge_ignores_malformed_candidate_count() -> None:
    now = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    gaps = (
        {
            "action_kind": "building_upgrade",
            "reason": "no_candidate",
            "reason_source": "empty_candidate_pool",
            "candidate_count": "not-an-integer",
        },
        {
            "action_kind": "technology_upgrade",
            "reason": "no_candidate",
            "reason_source": "empty_candidate_pool",
            "candidate_count": False,
        },
    )

    assert merge_candidate_pool_cooldowns({}, gaps=gaps, now=now) == []


def test_repeated_costly_actions_are_retained_as_audit_telemetry() -> None:
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
        ("salary_already_paid", MaintenanceReasonCategory.HOUSEKEEPING),
        ("no_guests_to_heal", MaintenanceReasonCategory.HOUSEKEEPING),
        ("salary_runway_protected", MaintenanceReasonCategory.SALARY),
        ("profile_busy", MaintenanceReasonCategory.LOCK_CONFLICT),
        ("no_eligible_candidate", MaintenanceReasonCategory.NO_CANDIDATE),
        ("multi_band_transition", MaintenanceReasonCategory.POLICY_GUARD),
        ("unclassified_reason", MaintenanceReasonCategory.OTHER),
    ),
)
def test_maintenance_reason_categories_are_stable(
    reason: str,
    category: MaintenanceReasonCategory,
) -> None:
    assert classify_maintenance_reason(reason) is category


@pytest.mark.parametrize(
    ("outcome", "reason", "stage", "category"),
    (
        ("applied", "", "slot", MaintenanceProgressCategory.PROGRESS_APPLIED),
        ("no_action", "insufficient_resource", "slot", MaintenanceProgressCategory.RESOURCE_WAIT),
        ("no_action", "candidate_domain_constraint", "slot", MaintenanceProgressCategory.DOMAIN_WAIT),
        ("busy", "profile_busy", "slot", MaintenanceProgressCategory.LOCK_RETRY),
        ("no_action", "salary_already_paid", "preamble", MaintenanceProgressCategory.HOUSEKEEPING),
        ("ineligible", "scheduled_cycle_slot_not_due", "slot", MaintenanceProgressCategory.SCHEDULER_WAIT),
        ("no_action", "candidate_exhausted", "slot", MaintenanceProgressCategory.CANDIDATE_EXHAUSTED),
        ("no_action", "multi_band_transition", "slot", MaintenanceProgressCategory.PROGRESS_BLOCKED),
    ),
)
def test_maintenance_progress_categories_separate_housekeeping_and_waits(
    outcome: str,
    reason: str,
    stage: str,
    category: MaintenanceProgressCategory,
) -> None:
    assert classify_maintenance_progress(outcome=outcome, reason=reason, stage=stage) is category
