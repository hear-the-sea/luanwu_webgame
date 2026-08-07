from __future__ import annotations

import pytest

from gameplay.services.arena.virtual_reserve_policy import reserve_target_plan, virtual_roster_target_count


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
