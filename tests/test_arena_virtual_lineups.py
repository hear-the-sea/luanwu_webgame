from __future__ import annotations

from copy import deepcopy

import pytest

from gameplay.services.arena.virtual_lineups import (
    InvalidVirtualLineupSnapshot,
    LineupSelectionContext,
    evaluate_lineup_snapshots,
    normalize_virtual_lineup_snapshots,
    validate_full_health_virtual_lineup_snapshots,
)


def _snapshot(*, current_hp: int = 250, max_hp: int = 1_000) -> dict:
    return {
        "display_name": "virtual guest",
        "attack": 100,
        "defense": 100,
        "current_hp": current_hp,
        "max_hp": max_hp,
    }


def test_virtual_lineup_normalization_only_changes_output_health() -> None:
    source = [_snapshot()]
    before = deepcopy(source)

    normalized = normalize_virtual_lineup_snapshots(source)

    assert source == before
    assert normalized[0] is not source[0]
    assert normalized[0] | {"current_hp": 250} == {
        **source[0],
        "arena_power_snapshot_semantics": "legacy_missing_agility",
    }
    assert normalized[0]["current_hp"] == normalized[0]["max_hp"] == 1_000


@pytest.mark.parametrize("max_hp", (None, True, 0, -1, 1.5, "100"))
def test_virtual_lineup_normalization_rejects_invalid_max_hp(max_hp) -> None:
    with pytest.raises(
        InvalidVirtualLineupSnapshot,
        match="max_hp must be a positive integer",
    ):
        normalize_virtual_lineup_snapshots([_snapshot(max_hp=max_hp)])


def test_lineup_selection_normalizes_health_without_changing_power_or_input() -> None:
    snapshots = [_snapshot(current_hp=100), _snapshot(current_hp=200)]
    before = deepcopy(snapshots)

    result = evaluate_lineup_snapshots(
        snapshots,
        context=LineupSelectionContext(mode="tournament", event_id=1, profile_id=2),
        target_guest_count=1,
        target_team_power=300,
    )

    assert snapshots == before
    assert result.is_ready is True
    assert result.selected_power == 300
    assert result.snapshots[0]["current_hp"] == result.snapshots[0]["max_hp"]


def test_lineup_selection_can_use_more_guests_when_reference_power_needs_them() -> None:
    snapshots = [_snapshot(), _snapshot(), _snapshot()]

    result = evaluate_lineup_snapshots(
        snapshots,
        context=LineupSelectionContext(
            mode="tournament",
            event_id=1,
            profile_id=2,
            max_lineup_size=10,
        ),
        target_guest_count=1,
        target_team_power=600,
    )

    assert result.is_ready is True
    assert len(result.snapshots) == 2
    assert result.selected_power == 600


def test_lineup_selection_varies_above_real_reference_until_arena_cap() -> None:
    snapshots = [_snapshot() for _ in range(10)]

    result = evaluate_lineup_snapshots(
        snapshots,
        context=LineupSelectionContext(
            mode="tournament",
            event_id=3,
            profile_id=4,
            max_lineup_size=10,
        ),
        target_guest_count=3,
        target_team_power=1_200,
    )

    assert result.is_ready is True
    assert len(result.snapshots) == 4
    assert len(result.snapshots) <= 10
    assert result.selected_power == 1_200


def test_lineup_selection_prefers_persisted_virtual_roster_target() -> None:
    snapshots = [_snapshot() for _ in range(6)]

    result = evaluate_lineup_snapshots(
        snapshots,
        context=LineupSelectionContext(
            mode="tournament",
            event_id=8,
            profile_id=9,
            max_lineup_size=10,
        ),
        target_guest_count=3,
        target_team_power=1_200,
        preferred_guest_count=4,
    )

    assert result.is_ready is True
    assert len(result.snapshots) == 4
    assert result.selected_power == 1_200


def test_lineup_selection_respects_arena_maximum_lineup_size() -> None:
    snapshots = [_snapshot(), _snapshot(), _snapshot()]

    result = evaluate_lineup_snapshots(
        snapshots,
        context=LineupSelectionContext(
            mode="tournament",
            event_id=1,
            profile_id=2,
            max_lineup_size=1,
        ),
        target_guest_count=1,
        target_team_power=500,
    )

    assert result.is_ready is False
    assert len(result.snapshots) == 1
    assert result.selected_power == 300


def test_lineup_selection_returns_non_ready_partial_evidence_when_hard_count_is_missing() -> None:
    snapshots = [_snapshot(), _snapshot()]

    result = evaluate_lineup_snapshots(
        snapshots,
        context=LineupSelectionContext(
            mode="tournament",
            event_id=11,
            profile_id=12,
            max_lineup_size=10,
        ),
        target_guest_count=3,
        target_team_power=600,
    )

    assert result.is_ready is False
    assert len(result.snapshots) == 2
    assert result.selected_power == 600


@pytest.mark.parametrize("current_hp", (True, 1.0, "1", 0, 2))
def test_locked_write_validation_rejects_noncanonical_health(current_hp) -> None:
    with pytest.raises(
        InvalidVirtualLineupSnapshot,
        match="must be normalized to full health",
    ):
        validate_full_health_virtual_lineup_snapshots([_snapshot(current_hp=current_hp, max_hp=1)])
