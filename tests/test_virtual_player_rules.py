from __future__ import annotations

from datetime import datetime, timezone
from random import Random

import pytest

from gameplay.services.virtual_player_rules import (
    apply_combat_persona,
    bounded_approach,
    choose_lifecycle,
    choose_strength_quantile,
    nearest_rank_quantile,
)


def test_nearest_rank_quantile_is_deterministic_for_small_samples():
    values = [9, 1, 5, 3]

    assert nearest_rank_quantile(values, 0.25) == 1
    assert nearest_rank_quantile(values, 0.50) == 3
    assert nearest_rank_quantile(values, 0.75) == 5


def test_nearest_rank_quantile_rejects_empty_samples():
    with pytest.raises(ValueError, match="at least one value"):
        nearest_rank_quantile([], 0.50)


def test_bounded_approach_moves_toward_target_without_overshoot():
    assert bounded_approach(4, 12, ratio=0.25, min_step=1, max_step=3) == 6
    assert bounded_approach(10, 3, ratio=0.50, min_step=1, max_step=2) == 8
    assert bounded_approach(4, 5, ratio=0.50, min_step=1, max_step=3) == 5
    assert bounded_approach(5, 5, ratio=0.50, min_step=1, max_step=3) == 5


def test_personas_create_distinct_guest_and_troop_targets():
    base = {"guest_level": 10, "guest_count": 6, "troop_count": 1000}

    balanced = apply_combat_persona(base, "balanced")
    rich = apply_combat_persona(base, "rich")
    dojo = apply_combat_persona(base, "dojo")
    guard = apply_combat_persona(base, "guard")
    abandoned = apply_combat_persona(base, "abandoned")

    assert dojo["guest_level"] > balanced["guest_level"] > rich["guest_level"]
    assert guard["troop_count"] > balanced["troop_count"] > dojo["troop_count"]
    assert abandoned["guest_level"] <= rich["guest_level"]
    assert abandoned["troop_count"] < dojo["troop_count"]


def test_combat_persona_can_be_configured_without_dropping_defaults():
    base = {"guest_level": 10, "guest_count": 6, "troop_count": 1000}

    result = apply_combat_persona(
        base,
        "guard",
        config={"guard": {"troop_multiplier": 1.5}},
    )

    assert result == {"guest_level": 8, "guest_count": 5, "troop_count": 1500}


def test_strength_quantile_is_seed_stable_and_uses_only_known_buckets():
    weights = {"p25": 25, "p50": 50, "p75": 25}

    first = choose_strength_quantile(9123, weights)

    assert first == choose_strength_quantile(9123, weights)
    assert first in {"p25", "p50", "p75"}


def test_weighted_choice_rejects_an_empty_strength_distribution():
    with pytest.raises(ValueError, match="positive weight"):
        choose_strength_quantile(1, {"p25": 0, "p50": 0, "p75": 0})


def test_lifecycle_choice_is_seed_stable_and_in_configured_ranges():
    config = {
        "tourist": {"weight": 1, "active_days": [7, 7], "abandoned_days": [10, 10]},
        "veteran": {"weight": 0, "active_days": [180, 180], "abandoned_days": [60, 60]},
    }
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)

    result = choose_lifecycle(Random(8), now, config)

    assert result.key == "tourist"
    assert result.created_at == now
    assert (result.slowing_at - result.created_at).days == 5
    assert (result.abandon_at - result.created_at).days == 7
    assert (result.retire_at - result.abandon_at).days == 10


def test_lifecycle_choice_rejects_an_all_zero_distribution():
    config = {
        "tourist": {"weight": 0, "active_days": [7, 7], "abandoned_days": [10, 10]},
    }

    with pytest.raises(ValueError, match="positive weight"):
        choose_lifecycle(Random(8), datetime(2026, 7, 12, tzinfo=timezone.utc), config)
