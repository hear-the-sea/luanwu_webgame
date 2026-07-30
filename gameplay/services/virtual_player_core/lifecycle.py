from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class LifecycleDates:
    key: str
    created_at: datetime
    slowing_at: datetime
    abandon_at: datetime
    retire_at: datetime


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


__all__ = ["LifecycleDates", "choose_lifecycle"]
