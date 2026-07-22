from __future__ import annotations

import pytest

from gameplay.services.jail_persuasion.effects import difficulty_factor, normalize_speaker_ratio, resolve_effect
from gameplay.services.jail_persuasion.profiles import METHOD_MIGHT, METHOD_REASON

BASE_ARGS = {
    "base_score": 50,
    "stance_method": "",
    "taboo_method": "",
    "rarity_difficulty_value": 0,
    "original_level": 1,
    "same_method_streak": 1,
    "speaker_archetype": "",
    "heart_variation": 0,
    "affinity_variation": 0,
}


@pytest.mark.parametrize(
    ("raw_ratio", "expected"),
    [
        (-1.0, 0.0),
        (0.8, 0.8),
        (1.5, 1.5),
        (3.0, 1.5),
    ],
)
def test_speaker_ratio_is_clamped_to_design_range(raw_ratio, expected):
    assert normalize_speaker_ratio(raw_ratio) == expected


@pytest.mark.parametrize(
    ("ratio", "outcome"),
    [
        (0.0, "backfire"),
        (0.6999, "backfire"),
        (0.70, "failed"),
        (0.8499, "failed"),
        (0.85, "neutral"),
        (1.1499, "neutral"),
        (1.15, "neutral"),
        (1.4999, "neutral"),
        (1.50, "neutral"),
        (3.0, "neutral"),
    ],
)
def test_reason_ratio_boundaries_are_gap_free(ratio, outcome):
    result = resolve_effect(method=METHOD_REASON, speaker_ratio=ratio, **BASE_ARGS)
    assert result.outcome == outcome


def test_reason_backfire_has_fixed_deltas():
    result = resolve_effect(method=METHOD_REASON, speaker_ratio=0.5, **BASE_ARGS)
    assert (result.heart_delta, result.affinity_delta, result.speaker_loyalty_delta) == (2, -4, -1)


def test_might_backfire_has_fixed_deltas():
    result = resolve_effect(method=METHOD_MIGHT, speaker_ratio=0.5, **BASE_ARGS)
    assert (result.heart_delta, result.affinity_delta, result.speaker_loyalty_delta) == (3, -5, -1)


def test_failed_speaker_attempt_has_no_state_effect():
    result = resolve_effect(method=METHOD_REASON, speaker_ratio=0.8, **BASE_ARGS)
    assert (result.heart_delta, result.affinity_delta, result.speaker_loyalty_delta) == (0, 0, 0)


def test_taboo_precedes_backfire_without_speaker_loyalty_loss():
    result = resolve_effect(
        method=METHOD_MIGHT,
        speaker_ratio=0.2,
        **{**BASE_ARGS, "taboo_method": METHOD_MIGHT},
    )
    assert (result.outcome, result.heart_delta, result.affinity_delta, result.speaker_loyalty_delta) == (
        "taboo",
        3,
        -8,
        0,
    )


def test_reason_speaker_applies_ratio_and_matching_archetype_bonuses():
    result = resolve_effect(
        method=METHOD_REASON,
        speaker_ratio=1.2,
        **{**BASE_ARGS, "stance_method": METHOD_REASON, "speaker_archetype": "civil"},
    )
    assert result.outcome == "matched"
    assert (result.heart_delta, result.affinity_delta) == (-11, 16)


def test_might_speaker_applies_ratio_and_matching_archetype_bonuses():
    result = resolve_effect(
        method=METHOD_MIGHT,
        speaker_ratio=1.2,
        **{**BASE_ARGS, "speaker_archetype": "military"},
    )
    assert (result.heart_delta, result.affinity_delta) == (-16, 10)


def test_difficulty_factor_is_bounded_at_point_sixty_eight():
    assert difficulty_factor(0, 1) == 1.0
    assert difficulty_factor(5, 60) == 0.68
    assert difficulty_factor(20, 999) == 0.68


def test_third_repeated_method_reduces_complete_positive_effect():
    normal = resolve_effect(method="kindness", **BASE_ARGS)
    repeated = resolve_effect(method="kindness", **{**BASE_ARGS, "same_method_streak": 3})

    assert (normal.heart_delta, normal.affinity_delta) == (-4, 12)
    assert (repeated.heart_delta, repeated.affinity_delta) == (-2, 7)


def test_repeated_effect_rounds_only_after_all_multipliers():
    result = resolve_effect(
        method="kindness",
        **{
            **BASE_ARGS,
            "base_score": 80,
            "rarity_difficulty_value": 4,
            "original_level": 20,
            "same_method_streak": 3,
        },
    )

    assert result.affinity_delta == 9


def test_effect_clamps_random_variation_to_design_ranges():
    result = resolve_effect(
        method="bribe",
        **{**BASE_ARGS, "heart_variation": 1, "affinity_variation": -2},
    )
    assert (result.heart_delta, result.affinity_delta) == (-9, 3)

    with pytest.raises(ValueError, match="心防随机浮动"):
        resolve_effect(method="bribe", **{**BASE_ARGS, "heart_variation": 2})
    with pytest.raises(ValueError, match="归心随机浮动"):
        resolve_effect(method="bribe", **{**BASE_ARGS, "affinity_variation": 3})
