from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from common.constants.virtual_players import VIRTUAL_PLAYER_ARCHETYPES

STRENGTH_QUANTILES = ("p25", "p50", "p75")

DEFAULT_COMBAT_PERSONAS: dict[str, dict[str, float]] = {
    "balanced": {
        "guest_level_multiplier": 1.0,
        "guest_count_multiplier": 1.0,
        "troop_multiplier": 1.0,
    },
    "rich": {
        "guest_level_multiplier": 0.85,
        "guest_count_multiplier": 0.85,
        "troop_multiplier": 0.8,
    },
    "dojo": {
        "guest_level_multiplier": 1.15,
        "guest_count_multiplier": 1.0,
        "troop_multiplier": 0.75,
    },
    "guard": {
        "guest_level_multiplier": 0.85,
        "guest_count_multiplier": 0.85,
        "troop_multiplier": 1.35,
    },
    "abandoned": {
        "guest_level_multiplier": 0.75,
        "guest_count_multiplier": 0.75,
        "troop_multiplier": 0.6,
    },
}


@dataclass(frozen=True)
class LifecycleDates:
    key: str
    created_at: datetime
    slowing_at: datetime
    abandon_at: datetime
    retire_at: datetime


def nearest_rank_quantile(values: Sequence[int], quantile: float) -> int:
    if not values:
        raise ValueError("quantile requires at least one value")
    normalized = min(1.0, max(0.0, float(quantile)))
    ordered = sorted(int(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(normalized * len(ordered)) - 1))
    return ordered[index]


def bounded_approach(current: int, target: int, *, ratio: float, min_step: int, max_step: int) -> int:
    current = int(current)
    target = int(target)
    delta = target - current
    if delta == 0:
        return current

    normalized_min = max(0, int(min_step))
    normalized_max = max(normalized_min, int(max_step))
    step = max(normalized_min, math.ceil(abs(delta) * max(0.0, float(ratio))))
    step = min(normalized_max, step)
    movement = min(abs(delta), step)
    return current + movement if delta > 0 else current - movement


def apply_combat_persona(
    targets: Mapping[str, int],
    persona: str,
    *,
    config: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    persona_key = str(persona or "balanced")
    if persona_key not in VIRTUAL_PLAYER_ARCHETYPES:
        persona_key = "balanced"
    defaults = DEFAULT_COMBAT_PERSONAS.get(persona_key, DEFAULT_COMBAT_PERSONAS["balanced"])
    configured = (config or {}).get(persona_key) or {}
    multipliers = {**defaults, **configured}

    return {
        "guest_level": max(
            0,
            round(int(targets.get("guest_level", 0)) * float(multipliers["guest_level_multiplier"])),
        ),
        "guest_count": max(
            0,
            round(int(targets.get("guest_count", 0)) * float(multipliers["guest_count_multiplier"])),
        ),
        "troop_count": max(
            0,
            round(int(targets.get("troop_count", 0)) * float(multipliers["troop_multiplier"])),
        ),
    }


def apply_stable_troop_variation(troop_count: int, growth_seed: int, *, max_delta_bps: int = 1000) -> int:
    """Apply a stable per-Bot army variance without consuming runtime RNG state."""
    maximum = max(0, min(10_000, int(max_delta_bps)))
    delta_bps = random.Random(f"troop-variation:{int(growth_seed)}").randint(-maximum, maximum)
    multiplier_bps = 10_000 + delta_bps
    return max(0, round(int(troop_count) * multiplier_bps / 10_000))


def _weighted_key(rng: random.Random, weights: Mapping[str, int | float]) -> str:
    normalized = [(str(key), max(0.0, float(weight))) for key, weight in weights.items()]
    total = sum(weight for _key, weight in normalized)
    if total <= 0:
        raise ValueError("weighted distribution requires at least one positive weight")

    target = rng.random() * total
    cumulative = 0.0
    for key, weight in normalized:
        cumulative += weight
        if target < cumulative:
            return key
    return normalized[-1][0]


def choose_strength_quantile(growth_seed: int, weights: Mapping[str, int | float]) -> str:
    ordered_weights = {key: weights.get(key, 0) for key in STRENGTH_QUANTILES}
    return _weighted_key(random.Random(f"strength:{int(growth_seed)}"), ordered_weights)


def _day_range(rng: random.Random, raw: Any, *, default: tuple[int, int]) -> int:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        low, high = default
    else:
        low, high = int(raw[0]), int(raw[1])
    low = max(0, low)
    high = max(low, high)
    return rng.randint(low, high)


def choose_lifecycle(
    rng: random.Random,
    now: datetime,
    personas: Mapping[str, Mapping[str, Any]],
) -> LifecycleDates:
    lifecycle_key = _weighted_key(
        rng,
        {key: float(values.get("weight", 0)) for key, values in personas.items()},
    )
    lifecycle = personas[lifecycle_key]
    active_days = _day_range(rng, lifecycle.get("active_days"), default=(30, 90))
    abandoned_days = _day_range(rng, lifecycle.get("abandoned_days"), default=(14, 45))
    active_duration = timedelta(days=active_days)
    abandon_at = now + active_duration
    return LifecycleDates(
        key=lifecycle_key,
        created_at=now,
        slowing_at=now + (active_duration * 0.8),
        abandon_at=abandon_at,
        retire_at=abandon_at + timedelta(days=abandoned_days),
    )
