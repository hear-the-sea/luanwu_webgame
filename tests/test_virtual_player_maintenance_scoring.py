from __future__ import annotations

import pytest

from gameplay.services.virtual_player_core.maintenance_scoring import candidate_efficiency_score


def test_candidate_scoring_keeps_grain_and_silver_prices_distinct() -> None:
    silver_heavy = candidate_efficiency_score(
        base_utility_score=1.0,
        expected_strength_gain=100,
        resource_costs={"silver": 100_000},
        completion_seconds=0,
    )
    grain_heavy = candidate_efficiency_score(
        base_utility_score=1.0,
        expected_strength_gain=100,
        resource_costs={"grain": 100_000},
        completion_seconds=0,
    )

    assert grain_heavy > silver_heavy


def test_candidate_scoring_penalizes_long_domain_completion() -> None:
    immediate = candidate_efficiency_score(
        base_utility_score=1.0,
        expected_strength_gain=100,
        resource_costs={},
        completion_seconds=0,
    )
    timed = candidate_efficiency_score(
        base_utility_score=1.0,
        expected_strength_gain=100,
        resource_costs={},
        completion_seconds=48 * 60 * 60,
    )

    assert timed < immediate


@pytest.mark.parametrize("duration", [-1, True])
def test_candidate_scoring_rejects_invalid_duration(duration) -> None:
    with pytest.raises(ValueError, match="completion_seconds"):
        candidate_efficiency_score(
            base_utility_score=1.0,
            expected_strength_gain=1,
            resource_costs={},
            completion_seconds=duration,
        )
