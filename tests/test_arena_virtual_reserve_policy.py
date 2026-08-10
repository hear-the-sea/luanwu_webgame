from __future__ import annotations

import pytest

from gameplay.services.arena.virtual_reserve_policy import (
    assess_reserve_admission,
    reserve_materialization_needed,
    reserve_target_plan,
    virtual_roster_target_count,
)


@pytest.mark.parametrize(
    ("missing", "replacement", "warm"),
    [
        (0, 0, 0),
        (1, 6, 6),
        (3, 9, 6),
        (5, 15, 8),
    ],
)
def test_reserve_target_plan_separates_replacement_budget_from_warm_target(
    missing: int,
    replacement: int,
    warm: int,
) -> None:
    plan = reserve_target_plan(missing)

    assert plan.replacement_target_count == replacement
    assert plan.warm_target_count == warm


def test_reserve_target_plan_clamps_negative_shortage() -> None:
    assert reserve_target_plan(-1).replacement_target_count == 0
    assert reserve_target_plan(-1).warm_target_count == 0


def test_materialization_need_uses_warm_slots_and_replacement_attempt_budget() -> None:
    assert (
        reserve_materialization_needed(
            warm_target=14,
            ready_count=2,
            training_count=12,
            attempt_count=50,
            replacement_target=27,
        )
        == 0
    )
    assert (
        reserve_materialization_needed(
            warm_target=6,
            ready_count=1,
            training_count=2,
            attempt_count=4,
            replacement_target=9,
        )
        == 3
    )
    assert (
        reserve_materialization_needed(
            warm_target=6,
            ready_count=1,
            training_count=2,
            attempt_count=8,
            replacement_target=9,
        )
        == 1
    )


def test_admission_assessment_suppresses_only_new_materialization_when_stalled() -> None:
    assessment = assess_reserve_admission(
        warm_target=6,
        ready_count=1,
        training_count=2,
        leased_attempts=4,
        admission_attempt_high_water=4,
        replacement_target=9,
        stalled_without_explained_constraint=True,
    )

    assert assessment.raw_materialization_needed == 3
    assert assessment.admitted_materialization_needed == 0
    assert assessment.suppressed_materialization_needed == 3
    assert assessment.guard_reasons == ("no_effective_progress",)


def test_admission_assessment_preserves_a_persisted_pause_until_real_progress() -> None:
    assessment = assess_reserve_admission(
        warm_target=6,
        ready_count=2,
        training_count=0,
        leased_attempts=2,
        admission_attempt_high_water=2,
        replacement_target=6,
        active_pause_reason="no_effective_progress",
    )

    assert assessment.raw_materialization_needed == 4
    assert assessment.admitted_materialization_needed == 0
    assert assessment.admission_guard_active is True


def test_admission_assessment_opens_only_one_reserved_probe_ordinal() -> None:
    common = {
        "warm_target": 6,
        "ready_count": 0,
        "training_count": 0,
        "leased_attempts": 1,
        "admission_attempt_high_water": 1,
        "replacement_target": 6,
        "active_pause_reason": "no_effective_progress",
    }

    cooling_down = assess_reserve_admission(**common)
    probe = assess_reserve_admission(**common, admission_probe_target_ordinal=2)
    invalid_probe_gap = assess_reserve_admission(**common, admission_probe_target_ordinal=3)

    assert cooling_down.raw_materialization_needed == 5
    assert cooling_down.admitted_materialization_needed == 0
    assert cooling_down.admission_probe_allowed is False
    assert probe.raw_materialization_needed == 5
    assert probe.admitted_materialization_needed == 1
    assert probe.suppressed_materialization_needed == 4
    assert probe.admission_guard_active is True
    assert probe.admission_probe_allowed is True
    assert invalid_probe_gap.admitted_materialization_needed == 0
    assert invalid_probe_gap.admission_probe_allowed is False


def test_admission_probe_never_bypasses_the_replacement_attempt_budget() -> None:
    assessment = assess_reserve_admission(
        warm_target=6,
        ready_count=0,
        training_count=0,
        leased_attempts=6,
        admission_attempt_high_water=6,
        replacement_target=6,
        active_pause_reason="no_effective_progress",
        admission_probe_target_ordinal=7,
    )

    assert assessment.raw_materialization_needed == 0
    assert assessment.admitted_materialization_needed == 0
    assert assessment.admission_probe_allowed is False


def test_virtual_roster_target_is_stable_and_varies_above_human_reference() -> None:
    kwargs = {
        "reference_guest_count": 3,
        "max_lineup_size": 10,
        "mode": "tournament",
        "event_id": 42,
        "profile_id": 17,
    }

    first = virtual_roster_target_count(**kwargs)
    second = virtual_roster_target_count(**kwargs)

    assert first == second
    assert 4 <= first <= 10
    targets = {virtual_roster_target_count(**{**kwargs, "profile_id": profile_id}) for profile_id in range(1, 51)}
    assert targets <= set(range(4, 11))
    assert len(targets) > 1


def test_virtual_roster_target_never_exceeds_the_arena_lineup_limit() -> None:
    assert (
        virtual_roster_target_count(
            reference_guest_count=8,
            max_lineup_size=5,
            mode="coop",
            event_id=7,
            profile_id=9,
        )
        == 5
    )
    assert (
        virtual_roster_target_count(
            reference_guest_count=3,
            max_lineup_size=0,
            mode="coop",
            event_id=7,
            profile_id=9,
        )
        == 1
    )
