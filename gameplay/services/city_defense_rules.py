from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from core.config import BUILDING_KEYS

CITY_DEFENSE_MAX_LEVEL = 10
CITY_DEFENSE_HP_RECOVERY_SECONDS = 20 * 60 * 60
CITY_DEFENSE_REPAIR_SILVER_PER_HP = 1
CITY_DEFENSE_RECOVERY_PERCENT_PER_HOUR = 5

CITY_DEFENSE_MAX_HP = {
    BUILDING_KEYS.WALL: 30_000,
    BUILDING_KEYS.ARROW_TOWER: 15_000,
}


@dataclass(frozen=True, slots=True)
class CityDefenseHpState:
    current_hp: int
    max_hp: int
    recovered_hp: int
    recovery_per_hour: int
    full_at: datetime | None


@dataclass(frozen=True, slots=True)
class CityDefenseUpgradeState:
    current_hp: int
    max_hp: int
    recovered_before_upgrade: int


def clamp_city_defense_level(level: Any) -> int:
    try:
        parsed = int(level)
    except (TypeError, ValueError):
        return 0
    return max(0, min(CITY_DEFENSE_MAX_LEVEL, parsed))


def scale_city_defense_value(max_value: int, level: int) -> int:
    return max(1, max_value * clamp_city_defense_level(level) // CITY_DEFENSE_MAX_LEVEL)


def is_city_defense_key(key: str | None) -> bool:
    return key in CITY_DEFENSE_MAX_HP


def city_defense_max_hp(key: str, level: int) -> int:
    maximum = CITY_DEFENSE_MAX_HP.get(key)
    if maximum is None:
        return 0
    return scale_city_defense_value(maximum, level)


def city_defense_recovery_per_hour(key: str, level: int) -> int:
    maximum = city_defense_max_hp(key, level)
    if maximum <= 0:
        return 0
    return maximum * 60 * 60 // CITY_DEFENSE_HP_RECOVERY_SECONDS


def project_city_defense_hp(
    key: str,
    level: int,
    current_hp: Any,
    hp_updated_at: datetime | None,
    *,
    now: datetime,
) -> CityDefenseHpState:
    maximum = city_defense_max_hp(key, level)
    if maximum <= 0:
        return CityDefenseHpState(0, 0, 0, 0, None)

    try:
        persisted_hp = int(current_hp or 0)
    except (TypeError, ValueError):
        persisted_hp = 0

    # Zero is the legacy sentinel for a newly-created, fully repaired building.
    if persisted_hp <= 0:
        projected_hp = maximum
        recovered_hp = 0
    else:
        persisted_hp = min(maximum, max(1, persisted_hp))
        elapsed_seconds = 0
        if hp_updated_at is not None:
            elapsed_seconds = max(0, int((now - hp_updated_at).total_seconds()))
        recovered_hp = min(
            maximum - persisted_hp,
            maximum * elapsed_seconds // CITY_DEFENSE_HP_RECOVERY_SECONDS,
        )
        projected_hp = persisted_hp + recovered_hp

    recovery_per_hour = city_defense_recovery_per_hour(key, level)
    full_at = None
    if projected_hp < maximum:
        missing_hp = maximum - projected_hp
        seconds_until_full = (missing_hp * CITY_DEFENSE_HP_RECOVERY_SECONDS + maximum - 1) // maximum
        full_at = now + timedelta(seconds=seconds_until_full)

    return CityDefenseHpState(
        current_hp=projected_hp,
        max_hp=maximum,
        recovered_hp=recovered_hp,
        recovery_per_hour=recovery_per_hour,
        full_at=full_at,
    )


def project_city_defense_upgrade(
    key: str,
    previous_level: int,
    current_hp: Any,
    hp_updated_at: datetime | None,
    *,
    completed_at: datetime,
) -> CityDefenseUpgradeState:
    previous = project_city_defense_hp(
        key,
        previous_level,
        current_hp,
        hp_updated_at,
        now=completed_at,
    )
    upgraded_max_hp = city_defense_max_hp(key, previous_level + 1)
    if upgraded_max_hp <= 0:
        return CityDefenseUpgradeState(0, 0, previous.recovered_hp)

    missing_hp = max(0, previous.max_hp - previous.current_hp)
    upgraded_hp = max(1, min(upgraded_max_hp, upgraded_max_hp - missing_hp))
    return CityDefenseUpgradeState(
        current_hp=upgraded_hp,
        max_hp=upgraded_max_hp,
        recovered_before_upgrade=previous.recovered_hp,
    )
