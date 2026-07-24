"""Tests for recruitment variance logic."""

from __future__ import annotations

import random

from guests.utils.recruitment_variance import (
    MAX_GROWABLE_ATTRIBUTE,
    MAX_RATIO,
    MIN_RATIO,
    apply_recruitment_variance,
    calculate_talent_grade,
)

# ============ apply_recruitment_variance tests ============


def test_apply_recruitment_variance_preserves_total_points():
    """Test that four-dimensional total points are preserved after variance."""
    template_attrs = {
        "force": 52,
        "intellect": 21,
        "defense": 59,
        "agility": 43,
        "luck": 45,
    }
    growable = ["force", "intellect", "defense", "agility"]
    original_total = sum(template_attrs[k] for k in growable)

    rng = random.Random(42)
    result = apply_recruitment_variance(template_attrs, "green", "military", rng=rng)

    result_total = sum(result[k] for k in growable)
    assert result_total == original_total


def test_apply_recruitment_variance_respects_min_max_ratio():
    """Test that each attribute stays within 88%-112% of template."""
    template_attrs = {
        "force": 50,
        "intellect": 50,
        "defense": 50,
        "agility": 50,
        "luck": 50,
    }

    for seed in range(100):
        rng = random.Random(seed)
        result = apply_recruitment_variance(template_attrs, "blue", "balanced", rng=rng)

        for attr in ["force", "intellect", "defense", "agility"]:
            base = template_attrs[attr]
            min_val = max(1, int(base * MIN_RATIO))
            max_val = min(int(base * MAX_RATIO), MAX_GROWABLE_ATTRIBUTE)
            assert (
                min_val <= result[attr] <= max_val
            ), f"Seed {seed}: {attr}={result[attr]} not in [{min_val}, {max_val}]"


def test_apply_recruitment_variance_luck_independent():
    """Test that luck varies independently within +-5 range."""
    template_attrs = {
        "force": 50,
        "intellect": 50,
        "defense": 50,
        "agility": 50,
        "luck": 50,
    }

    luck_values = set()
    for seed in range(100):
        rng = random.Random(seed)
        result = apply_recruitment_variance(template_attrs, "gray", "scholar", rng=rng)
        luck_values.add(result["luck"])

    # Should see variation in luck
    assert len(luck_values) > 1
    # All luck values should be in valid range
    for luck in luck_values:
        assert 45 <= luck <= 55  # 50 +- 5


def test_apply_recruitment_variance_luck_at_least_one():
    """Test that luck is at least 1 even with low template value."""
    template_attrs = {
        "force": 50,
        "intellect": 50,
        "defense": 50,
        "agility": 50,
        "luck": 2,  # Very low luck
    }

    for seed in range(50):
        rng = random.Random(seed)
        result = apply_recruitment_variance(template_attrs, "gray", "military", rng=rng)
        assert result["luck"] >= 1


def test_apply_recruitment_variance_respects_max_growable():
    """Test that attributes don't exceed MAX_GROWABLE_ATTRIBUTE (99)."""
    template_attrs = {
        "force": 95,
        "intellect": 95,
        "defense": 95,
        "agility": 95,
        "luck": 50,
    }

    for seed in range(50):
        rng = random.Random(seed)
        result = apply_recruitment_variance(template_attrs, "orange", "military", rng=rng)

        for attr in ["force", "intellect", "defense", "agility"]:
            assert result[attr] <= MAX_GROWABLE_ATTRIBUTE


def test_apply_recruitment_variance_deterministic_with_seed():
    """Test that same seed produces same result."""
    template_attrs = {
        "force": 60,
        "intellect": 40,
        "defense": 55,
        "agility": 45,
        "luck": 50,
    }

    rng1 = random.Random(12345)
    result1 = apply_recruitment_variance(template_attrs, "purple", "balanced", rng=rng1)

    rng2 = random.Random(12345)
    result2 = apply_recruitment_variance(template_attrs, "purple", "balanced", rng=rng2)

    assert result1 == result2


def test_apply_recruitment_variance_different_seeds_different_results():
    """Test that different seeds can produce different results."""
    template_attrs = {
        "force": 50,
        "intellect": 50,
        "defense": 50,
        "agility": 50,
        "luck": 50,
    }

    results = set()
    for seed in range(20):
        rng = random.Random(seed)
        result = apply_recruitment_variance(template_attrs, "green", "military", rng=rng)
        results.add(tuple(result.items()))

    # Should have some variation
    assert len(results) > 1


def test_apply_recruitment_variance_handles_low_attributes():
    """Test that low attribute values don't break the variance."""
    template_attrs = {
        "force": 5,
        "intellect": 5,
        "defense": 5,
        "agility": 5,
        "luck": 5,
    }

    rng = random.Random(42)
    result = apply_recruitment_variance(template_attrs, "gray", "scholar", rng=rng)

    # All attributes should be at least 1
    for attr in ["force", "intellect", "defense", "agility", "luck"]:
        assert result[attr] >= 1


def test_apply_recruitment_variance_uses_default_rng():
    """Test that function works without explicit RNG."""
    template_attrs = {
        "force": 50,
        "intellect": 50,
        "defense": 50,
        "agility": 50,
        "luck": 50,
    }

    # Should not raise
    result = apply_recruitment_variance(template_attrs, "blue", "military")

    # Should have all expected keys
    assert set(result.keys()) == {"force", "intellect", "defense", "agility", "luck"}


# ============ calculate_talent_grade tests ============


def test_calculate_talent_grade_normal_when_equal():
    """Test that grade is 'normal' when total equals base."""
    guest_attrs = {
        "force": 50,
        "intellect": 50,
        "defense": 50,
        "agility": 50,
    }
    base_total = 200

    grade = calculate_talent_grade(guest_attrs, base_total)

    assert grade == "normal"


def test_calculate_talent_grade_superior_when_higher():
    """Test that grade is 'superior' when total is significantly higher."""
    guest_attrs = {
        "force": 55,
        "intellect": 55,
        "defense": 55,
        "agility": 55,
    }
    base_total = 200  # Guest total is 220

    grade = calculate_talent_grade(guest_attrs, base_total)

    assert grade == "superior"


def test_calculate_talent_grade_inferior_when_lower():
    """Test that grade is 'inferior' when total is significantly lower."""
    guest_attrs = {
        "force": 45,
        "intellect": 45,
        "defense": 45,
        "agility": 45,
    }
    base_total = 200  # Guest total is 180

    grade = calculate_talent_grade(guest_attrs, base_total)

    assert grade == "inferior"


def test_calculate_talent_grade_normal_within_tolerance():
    """Test that grade is 'normal' when difference is within +-2."""
    guest_attrs = {
        "force": 51,
        "intellect": 50,
        "defense": 50,
        "agility": 50,
    }
    base_total = 200  # Guest total is 201, diff is 1

    grade = calculate_talent_grade(guest_attrs, base_total)

    assert grade == "normal"
