"""Deterministic resource/time efficiency scoring for maintenance candidates."""

from __future__ import annotations

import math
from collections.abc import Mapping

# These are deliberately normalized prices rather than an assertion that one
# unit of grain equals one unit of silver.  The scales keep the existing
# utility values comparable while making long queues and expensive upgrades
# visible to selection.
SILVER_SHADOW_PRICE_PER_UNIT = 1 / 100_000
GRAIN_SHADOW_PRICE_PER_UNIT = 0.35 / 100_000
TIME_SHADOW_PRICE_PER_HOUR = 0.05
MIN_CANDIDATE_SCORE = 0.000001


def candidate_efficiency_score(
    *,
    base_utility_score: float,
    expected_strength_gain: int,
    resource_costs: Mapping[str, int],
    completion_seconds: int,
) -> float:
    """Return a stable score that combines return, resources, and queue time."""

    if not math.isfinite(float(base_utility_score)):
        raise ValueError("base_utility_score must be finite")
    if isinstance(expected_strength_gain, bool) or expected_strength_gain < 0:
        raise ValueError("expected_strength_gain must be non-negative")
    if isinstance(completion_seconds, bool) or completion_seconds < 0:
        raise ValueError("completion_seconds must be non-negative")
    resource_penalty = 0.0
    for resource, amount in resource_costs.items():
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("resource costs must be non-negative integers")
        if resource == "silver":
            resource_penalty += amount * SILVER_SHADOW_PRICE_PER_UNIT
        elif resource == "grain":
            resource_penalty += amount * GRAIN_SHADOW_PRICE_PER_UNIT
        else:
            # Unknown resources remain visible but do not silently become free.
            resource_penalty += amount * SILVER_SHADOW_PRICE_PER_UNIT
    time_penalty = completion_seconds / 3600 * TIME_SHADOW_PRICE_PER_HOUR
    expected_return = max(
        MIN_CANDIDATE_SCORE,
        float(base_utility_score),
        float(expected_strength_gain) / 100.0,
    )
    return max(MIN_CANDIDATE_SCORE, expected_return / (1.0 + resource_penalty + time_penalty))


__all__ = [
    "GRAIN_SHADOW_PRICE_PER_UNIT",
    "MIN_CANDIDATE_SCORE",
    "SILVER_SHADOW_PRICE_PER_UNIT",
    "TIME_SHADOW_PRICE_PER_HOUR",
    "candidate_efficiency_score",
]
