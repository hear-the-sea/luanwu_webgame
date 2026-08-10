from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gameplay.services.virtual_player_core.contracts import (
    AcceleratedGrowthOutcome,
    ArenaGrowthObjective,
    MaintenanceOutcome,
    MaintenanceResult,
    MaintenanceScheduleDisposition,
    MaintenanceTrigger,
    MaintenanceTriggerPolicy,
    maintenance_trigger_policy,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_arena_growth_objective_separates_hard_count_from_soft_preference() -> None:
    objective = ArenaGrowthObjective(
        critical_guest_count=3,
        preferred_guest_count=7,
        selected_power_lower_bound=800,
        selected_power_upper_bound=1200,
        selected_power_before=650,
        target_team_power=1000,
        lineup_mode="tournament",
        lineup_event_id=7,
        lineup_max_size=10,
        minimum_guest_level=20,
        recruitment_rarity_cap="blue",
        max_guest_level_step=6,
    )

    assert objective.critical_guest_count == 3
    assert objective.preferred_guest_count == 7
    assert objective.selected_lineup_gap == 150
    assert objective.to_payload()["recruitment_rarity_cap"] == "blue"


def test_scheduled_trigger_requires_due_and_advances_the_normal_schedule() -> None:
    policy = maintenance_trigger_policy(MaintenanceTrigger.SCHEDULED)

    assert policy.requires_due is True
    assert policy.schedule_disposition is MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE
    assert policy.is_due(next_growth_at=NOW, now=NOW) is True
    assert policy.is_due(next_growth_at=NOW - timedelta(seconds=1), now=NOW) is True
    assert policy.is_due(next_growth_at=NOW + timedelta(seconds=1), now=NOW) is False
    assert policy.is_due(next_growth_at=None, now=NOW) is False


def test_arena_trigger_ignores_due_time_and_preserves_the_normal_schedule() -> None:
    policy = maintenance_trigger_policy(MaintenanceTrigger.ARENA_ACCELERATION)

    assert policy.requires_due is False
    assert policy.schedule_disposition is MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE
    assert policy.is_due(next_growth_at=NOW + timedelta(days=30), now=NOW) is True
    assert policy.is_due(next_growth_at=None, now=NOW) is True
    assert (
        policy.is_due(
            next_growth_at=NOW + timedelta(days=30),
            now=NOW,
            arena_bypass_due=False,
        )
        is False
    )
    assert (
        policy.is_due(
            next_growth_at=NOW,
            now=NOW,
            arena_bypass_due=False,
        )
        is True
    )


@pytest.mark.parametrize(
    "requires_due, disposition",
    [
        (None, None),
        (True, None),
        (None, MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE),
    ],
)
def test_admin_trigger_fails_closed_without_both_explicit_semantics(requires_due, disposition) -> None:
    with pytest.raises(ValueError, match="requires explicit"):
        maintenance_trigger_policy(
            MaintenanceTrigger.ADMIN,
            admin_requires_due=requires_due,
            admin_schedule_disposition=disposition,
        )


@pytest.mark.parametrize("requires_due", [0, 1, "false", object()])
def test_admin_trigger_rejects_truthy_and_falsy_non_booleans(requires_due) -> None:
    with pytest.raises(ValueError, match="must be a boolean"):
        maintenance_trigger_policy(
            MaintenanceTrigger.ADMIN,
            admin_requires_due=requires_due,
            admin_schedule_disposition=MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
        )


@pytest.mark.parametrize("requires_due", [False, True])
@pytest.mark.parametrize(
    "disposition",
    [
        MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE,
        MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
    ],
)
def test_admin_trigger_uses_each_explicit_due_and_schedule_combination(requires_due, disposition) -> None:
    policy = maintenance_trigger_policy(
        MaintenanceTrigger.ADMIN,
        admin_requires_due=requires_due,
        admin_schedule_disposition=disposition,
    )

    assert policy.requires_due is requires_due
    assert policy.schedule_disposition is disposition
    assert policy.is_due(next_growth_at=NOW + timedelta(seconds=1), now=NOW) is (not requires_due)


@pytest.mark.parametrize("outcome", [MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION])
def test_committed_applied_and_no_action_cycles_advance_sequence(
    outcome: MaintenanceOutcome,
) -> None:
    assert maintenance_trigger_policy(MaintenanceTrigger.SCHEDULED).advances_sequence(outcome) is True


@pytest.mark.parametrize(
    "outcome",
    [MaintenanceOutcome.BUSY, MaintenanceOutcome.PAUSED, MaintenanceOutcome.INELIGIBLE],
)
def test_non_committed_or_ineligible_cycles_do_not_advance_sequence(
    outcome: MaintenanceOutcome,
) -> None:
    assert maintenance_trigger_policy(MaintenanceTrigger.SCHEDULED).advances_sequence(outcome) is False


def _result_fields(
    *,
    outcome: MaintenanceOutcome,
    trigger: MaintenanceTrigger,
) -> dict:
    committed = outcome in {MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION}
    if trigger is MaintenanceTrigger.ARENA_ACCELERATION:
        disposition = MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE
        after = NOW if committed or outcome is MaintenanceOutcome.BUSY else None
    else:
        disposition = MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE
        after = NOW + timedelta(hours=1) if committed else NOW if outcome is MaintenanceOutcome.BUSY else None
    return {
        "outcome": outcome,
        "trigger": trigger,
        "profile_id": 7,
        "sequence_before": 10,
        "sequence_after": 10 + int(committed),
        "schedule_disposition": disposition,
        "next_growth_at_before": NOW,
        "next_growth_at_after": after,
        "action_kind": "training" if outcome is MaintenanceOutcome.APPLIED else "",
        "reason": "strength_cap" if outcome is not MaintenanceOutcome.APPLIED else "",
    }


@pytest.mark.parametrize("trigger", list(MaintenanceTrigger))
@pytest.mark.parametrize("outcome", list(MaintenanceOutcome))
def test_maintenance_result_accepts_the_complete_outcome_trigger_matrix(
    outcome: MaintenanceOutcome,
    trigger: MaintenanceTrigger,
) -> None:
    result = MaintenanceResult(**_result_fields(outcome=outcome, trigger=trigger))

    assert result.sequence_after == 10 + int(outcome in {MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION})
    if outcome is MaintenanceOutcome.BUSY:
        assert result.next_growth_at_after == result.next_growth_at_before
    elif outcome in {MaintenanceOutcome.PAUSED, MaintenanceOutcome.INELIGIBLE}:
        assert result.next_growth_at_after is None


@pytest.mark.parametrize("outcome", [MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION])
def test_admin_committed_advance_may_replace_a_far_future_deadline_with_an_earlier_one(
    outcome: MaintenanceOutcome,
) -> None:
    fields = _result_fields(outcome=outcome, trigger=MaintenanceTrigger.ADMIN)
    fields["next_growth_at_before"] = NOW + timedelta(days=30)
    fields["next_growth_at_after"] = NOW + timedelta(hours=1)

    result = MaintenanceResult(**fields)

    assert result.next_growth_at_after < result.next_growth_at_before


@pytest.mark.parametrize("outcome", [MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION])
def test_admin_committed_preserve_retains_the_existing_deadline(
    outcome: MaintenanceOutcome,
) -> None:
    fields = _result_fields(outcome=outcome, trigger=MaintenanceTrigger.ADMIN)
    fields["schedule_disposition"] = MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE
    fields["next_growth_at_after"] = fields["next_growth_at_before"]

    result = MaintenanceResult(**fields)

    assert result.next_growth_at_after == result.next_growth_at_before


@pytest.mark.parametrize(
    ("outcome", "sequence_after"),
    [
        (MaintenanceOutcome.APPLIED, 10),
        (MaintenanceOutcome.NO_ACTION, 10),
        (MaintenanceOutcome.BUSY, 11),
        (MaintenanceOutcome.PAUSED, 11),
        (MaintenanceOutcome.INELIGIBLE, 11),
    ],
)
def test_maintenance_result_rejects_invalid_sequence_transitions(
    outcome: MaintenanceOutcome,
    sequence_after: int,
) -> None:
    with pytest.raises(ValueError, match="advance exactly once"):
        MaintenanceResult(
            **(
                _result_fields(
                    outcome=outcome,
                    trigger=MaintenanceTrigger.ARENA_ACCELERATION,
                )
                | {"sequence_after": sequence_after}
            )
        )


def test_maintenance_result_rejects_schedule_semantic_drift() -> None:
    arena_fields = _result_fields(
        outcome=MaintenanceOutcome.NO_ACTION,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
    )
    arena_fields["next_growth_at_after"] = NOW + timedelta(hours=1)
    with pytest.raises(ValueError, match="retain next_growth_at exactly"):
        MaintenanceResult(**arena_fields)

    busy_fields = _result_fields(
        outcome=MaintenanceOutcome.BUSY,
        trigger=MaintenanceTrigger.SCHEDULED,
    )
    busy_fields["next_growth_at_after"] = NOW + timedelta(hours=1)
    with pytest.raises(ValueError, match="BUSY"):
        MaintenanceResult(**busy_fields)

    scheduled_fields = _result_fields(
        outcome=MaintenanceOutcome.APPLIED,
        trigger=MaintenanceTrigger.SCHEDULED,
    )
    scheduled_fields["next_growth_at_after"] = NOW
    with pytest.raises(ValueError, match="move next_growth_at forward"):
        MaintenanceResult(**scheduled_fields)


@pytest.mark.parametrize(
    ("trigger", "disposition", "message"),
    [
        (
            MaintenanceTrigger.SCHEDULED,
            MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
            "scheduled committed maintenance",
        ),
        (
            MaintenanceTrigger.ARENA_ACCELERATION,
            MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE,
            "arena committed maintenance",
        ),
    ],
)
def test_committed_results_reject_trigger_disposition_mismatches(
    trigger: MaintenanceTrigger,
    disposition: MaintenanceScheduleDisposition,
    message: str,
) -> None:
    fields = _result_fields(outcome=MaintenanceOutcome.NO_ACTION, trigger=trigger)
    fields["schedule_disposition"] = disposition

    with pytest.raises(ValueError, match=message):
        MaintenanceResult(**fields)


def test_scheduled_committed_result_requires_a_non_null_due_deadline() -> None:
    fields = _result_fields(
        outcome=MaintenanceOutcome.APPLIED,
        trigger=MaintenanceTrigger.SCHEDULED,
    )
    fields["next_growth_at_before"] = None

    with pytest.raises(ValueError, match="non-null due deadline"):
        MaintenanceResult(**fields)


@pytest.mark.parametrize("next_growth_at_after", [None, NOW])
def test_admin_committed_advance_requires_a_non_null_different_deadline(
    next_growth_at_after,
) -> None:
    fields = _result_fields(
        outcome=MaintenanceOutcome.NO_ACTION,
        trigger=MaintenanceTrigger.ADMIN,
    )
    fields["next_growth_at_after"] = next_growth_at_after

    with pytest.raises(ValueError, match="non-null different value"):
        MaintenanceResult(**fields)


@pytest.mark.parametrize("outcome", list(MaintenanceOutcome))
def test_maintenance_result_enforces_action_and_reason_exclusivity(
    outcome: MaintenanceOutcome,
) -> None:
    fields = _result_fields(outcome=outcome, trigger=MaintenanceTrigger.SCHEDULED)
    if outcome is MaintenanceOutcome.APPLIED:
        fields["action_kind"] = ""
        with pytest.raises(ValueError, match="non-empty action_kind"):
            MaintenanceResult(**fields)
        fields["action_kind"] = "training"
        fields["reason"] = "unexpected"
        with pytest.raises(ValueError, match="must not include a reason"):
            MaintenanceResult(**fields)
    else:
        fields["reason"] = ""
        with pytest.raises(ValueError, match="non-empty reason"):
            MaintenanceResult(**fields)
        fields["reason"] = "expected_reason"
        fields["action_kind"] = "unexpected"
        if outcome is MaintenanceOutcome.NO_ACTION:
            result = MaintenanceResult(**fields)
            assert result.action_kind == "unexpected"
        else:
            with pytest.raises(ValueError, match="must not include an action_kind"):
                MaintenanceResult(**fields)


@pytest.mark.parametrize(
    ("outcome", "field", "value"),
    [
        (MaintenanceOutcome.APPLIED, "action_kind", 1),
        (MaintenanceOutcome.NO_ACTION, "reason", None),
    ],
)
def test_maintenance_result_rejects_non_string_payload_fields(
    outcome: MaintenanceOutcome,
    field: str,
    value: object,
) -> None:
    fields = _result_fields(outcome=outcome, trigger=MaintenanceTrigger.SCHEDULED)
    fields[field] = value

    with pytest.raises(ValueError, match="action_kind and reason must be strings"):
        MaintenanceResult(**fields)


@pytest.mark.parametrize(
    ("outcome", "trigger", "disposition"),
    [
        (
            MaintenanceOutcome.PAUSED,
            MaintenanceTrigger.ARENA_ACCELERATION,
            MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE,
        ),
        (
            MaintenanceOutcome.INELIGIBLE,
            MaintenanceTrigger.SCHEDULED,
            MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
        ),
    ],
)
def test_lifecycle_results_may_override_the_nominal_trigger_schedule(
    outcome: MaintenanceOutcome,
    trigger: MaintenanceTrigger,
    disposition: MaintenanceScheduleDisposition,
) -> None:
    fields = _result_fields(outcome=outcome, trigger=trigger)
    fields["schedule_disposition"] = disposition
    fields["next_growth_at_before"] = NOW
    fields["next_growth_at_after"] = NOW + timedelta(days=1)

    result = MaintenanceResult(**fields)

    assert result.next_growth_at_after == NOW + timedelta(days=1)


def test_trigger_policy_cannot_be_constructed_with_an_invalid_frozen_matrix() -> None:
    with pytest.raises(ValueError, match="scheduled maintenance"):
        MaintenanceTriggerPolicy(
            trigger=MaintenanceTrigger.SCHEDULED,
            requires_due=False,
            schedule_disposition=MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE,
        )
    with pytest.raises(ValueError, match="only APPLIED and NO_ACTION"):
        MaintenanceTriggerPolicy(
            trigger=MaintenanceTrigger.ADMIN,
            requires_due=False,
            schedule_disposition=MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
            sequence_advancing_outcomes=frozenset({MaintenanceOutcome.APPLIED}),
        )


def test_legacy_acceleration_enum_keeps_existing_values_and_adds_explicit_v2_results() -> None:
    assert [outcome.value for outcome in AcceleratedGrowthOutcome] == [
        "grown",
        "busy",
        "ineligible",
        "no_action",
        "paused",
    ]
