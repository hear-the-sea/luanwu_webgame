from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
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


def range_value(rng: random.Random, values: Any, *, default: tuple[int, int]) -> int:
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        low, high = default
    else:
        low, high = int(values[0]), int(values[1])
    if high < low:
        low, high = high, low
    return rng.randint(low, high)


def range_float(rng: random.Random, values: Any, *, default: tuple[float, float]) -> float:
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        low, high = default
    else:
        low, high = float(values[0]), float(values[1])
    if high < low:
        low, high = high, low
    return rng.uniform(low, high)


def chance_value(value: Any, *, default: float = 0.0) -> float:
    try:
        chance = float(value)
    except (TypeError, ValueError):
        chance = default
    return max(0.0, min(1.0, chance))


def weighted_archetype(rng: random.Random) -> str:
    weighted = [
        ("balanced", 35),
        ("rich", 25),
        ("dojo", 15),
        ("guard", 15),
        ("abandoned", 10),
    ]
    total = sum(weight for _key, weight in weighted)
    roll = rng.randint(1, total)
    current = 0
    for key, weight in weighted:
        current += weight
        if roll <= current:
            return key
    return "balanced"


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


__all__ = [
    "DEFAULT_COMBAT_PERSONAS",
    "STRENGTH_QUANTILES",
    "apply_combat_persona",
    "apply_stable_troop_variation",
    "bounded_approach",
    "chance_value",
    "choose_strength_quantile",
    "nearest_rank_quantile",
    "range_float",
    "range_value",
    "weighted_archetype",
]
