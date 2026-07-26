"""Shared travel-time rules for personal and guild PVP marches."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from core.utils.time_scale import scale_duration

AGILITY_BASELINE = 160.0
AGILITY_FACTOR_DIVISOR = 500.0
AGILITY_FACTOR_MIN = 0.70
AGILITY_FACTOR_MAX = 1.20
TROOPS_PER_SIZE_POINT = 200.0
SIZE_FACTOR_BONUS = 0.50
SIZE_FACTOR_SATURATION = 20.0


@dataclass(frozen=True)
class PvpTravelEstimate:
    """Calculated PVP travel time and the factors used to produce it."""

    average_agility: float
    agility_factor: float
    guest_count: int
    troop_count: int
    size_score: float
    size_factor: float
    route_seconds: float
    external_factor: float
    game_seconds: int
    scaled_seconds: int


def calculate_agility_factor(average_agility: float) -> float:
    """Return the bounded agility modifier using 160 as the neutral value."""
    factor = 1.0 - (float(average_agility) - AGILITY_BASELINE) / AGILITY_FACTOR_DIVISOR
    return min(AGILITY_FACTOR_MAX, max(AGILITY_FACTOR_MIN, factor))


def calculate_size_factor(*, guest_count: int, troop_count: int) -> tuple[float, float]:
    """Return the continuous force-size score and its travel-time modifier."""
    size_score = max(0, int(guest_count) - 1) + max(0, int(troop_count)) / TROOPS_PER_SIZE_POINT
    size_factor = 1.0 + SIZE_FACTOR_BONUS * size_score / (size_score + SIZE_FACTOR_SATURATION)
    return size_score, size_factor


def round_game_seconds_up_to_minute(seconds: float) -> int:
    """Round positive game time upward to a complete game minute."""
    if seconds <= 0:
        return 0
    return int(math.ceil(float(seconds) / 60.0) * 60)


def _average_agility(guests: list[Any]) -> float:
    if not guests:
        return AGILITY_BASELINE

    values: list[float] = []
    for guest in guests:
        try:
            value = float(getattr(guest, "agility"))
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return sum(values) / len(values) if values else AGILITY_BASELINE


def _troop_total(troop_loadout: Mapping[str, Any] | None) -> int:
    total = 0
    for raw_count in (troop_loadout or {}).values():
        if raw_count is None or isinstance(raw_count, bool):
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        total += max(0, count)
    return total


def calculate_pvp_travel_time(
    *,
    route_seconds: float,
    guests: Iterable[Any] | None,
    troop_loadout: Mapping[str, Any] | None,
    external_factor: float = 1.0,
) -> PvpTravelEstimate:
    """Calculate a one-way PVP march, rounding game time before time scaling."""
    guest_list = list(guests or ())
    guest_count = len(guest_list)
    troop_count = _troop_total(troop_loadout)
    average_agility = _average_agility(guest_list)
    agility_factor = calculate_agility_factor(average_agility)
    size_score, size_factor = calculate_size_factor(guest_count=guest_count, troop_count=troop_count)
    resolved_route_seconds = max(0.0, float(route_seconds))
    resolved_external_factor = max(0.0, float(external_factor))
    raw_game_seconds = resolved_route_seconds * agility_factor * size_factor * resolved_external_factor
    game_seconds = round_game_seconds_up_to_minute(raw_game_seconds)

    return PvpTravelEstimate(
        average_agility=average_agility,
        agility_factor=agility_factor,
        guest_count=guest_count,
        troop_count=troop_count,
        size_score=size_score,
        size_factor=size_factor,
        route_seconds=resolved_route_seconds,
        external_factor=resolved_external_factor,
        game_seconds=game_seconds,
        scaled_seconds=scale_duration(game_seconds, minimum=1),
    )
