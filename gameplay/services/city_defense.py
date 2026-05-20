from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.config import BUILDING_KEYS
from core.exceptions import GameError
from gameplay.models import Building, Manor, ResourceEvent, ResourceType
from gameplay.services.manor.prestige import add_prestige_silver_locked
from gameplay.services.resources import spend_resources_locked

CITY_DEFENSE_HP_RECOVERY_SECONDS = 20 * 60 * 60
CITY_DEFENSE_REPAIR_SILVER_PER_HP = 1

CITY_DEFENSE_MAX_HP = {
    BUILDING_KEYS.WALL: 30000,
    BUILDING_KEYS.ARROW_TOWER: 15000,
}


def _clamp_level(level: Any) -> int:
    try:
        parsed = int(level)
    except (TypeError, ValueError):
        return 0
    return max(0, min(10, parsed))


def is_city_defense_key(key: str | None) -> bool:
    return key in CITY_DEFENSE_MAX_HP


def city_defense_max_hp(key: str, level: int) -> int:
    max_hp = CITY_DEFENSE_MAX_HP.get(key)
    if not max_hp:
        return 0
    return max(1, int(max_hp * (_clamp_level(level) / 10)))


def _current_hp_with_recovery(building: Building, *, now: datetime) -> tuple[int, int, bool]:
    key = building.building_type.key
    max_hp = city_defense_max_hp(key, building.level)
    if not max_hp:
        return 0, 0, False

    raw_hp = int(getattr(building, "current_hp", 0) or 0)
    if raw_hp <= 0:
        return max_hp, max_hp, True

    current_hp = min(raw_hp, max_hp)
    updated_at = getattr(building, "hp_updated_at", None) or now
    elapsed_seconds = max(0, int((now - updated_at).total_seconds()))
    if current_hp < max_hp and elapsed_seconds > 0:
        recovered = int(max_hp * (elapsed_seconds / CITY_DEFENSE_HP_RECOVERY_SECONDS))
        current_hp = min(max_hp, current_hp + max(0, recovered))

    changed = current_hp != raw_hp or updated_at != getattr(building, "hp_updated_at", None)
    return current_hp, max_hp, changed


def refresh_city_defense_hp(building: Building, *, now: datetime | None = None, persist: bool = True) -> int:
    now = now or timezone.now()
    if not is_city_defense_key(getattr(building.building_type, "key", None)):
        return 0

    current_hp, max_hp, changed = _current_hp_with_recovery(building, now=now)
    building.current_hp = current_hp
    setattr(building, "city_defense_max_hp", max_hp)
    setattr(building, "city_defense_repair_cost", max(0, max_hp - current_hp) * CITY_DEFENSE_REPAIR_SILVER_PER_HP)
    if persist and changed and building.pk:
        building.hp_updated_at = now
        building.save(update_fields=["current_hp", "hp_updated_at"])
    return current_hp


def prepare_city_defense_display(building: Building, *, now: datetime | None = None) -> None:
    refresh_city_defense_hp(building, now=now, persist=False)


def refresh_city_defense_buildings(manor: Manor, *, now: datetime | None = None, persist: bool = True) -> None:
    now = now or timezone.now()
    buildings = manor.buildings.select_related("building_type").filter(building_type__key__in=CITY_DEFENSE_MAX_HP)
    for building in buildings:
        refresh_city_defense_hp(building, now=now, persist=persist)


def apply_city_defense_battle_damage(
    manor: Manor | None,
    city_defenses: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> None:
    if manor is None or not city_defenses:
        return

    now = now or timezone.now()
    hp_by_key = {
        str(row.get("key")): max(1, int(row.get("hp") or 0))
        for row in city_defenses
        if is_city_defense_key(str(row.get("key") or ""))
    }
    if not hp_by_key:
        return

    with transaction.atomic():
        buildings = (
            Building.objects.select_for_update()
            .select_related("building_type")
            .filter(manor=manor, building_type__key__in=hp_by_key.keys())
            .order_by("pk")
        )
        for building in buildings:
            max_hp = city_defense_max_hp(building.building_type.key, building.level)
            reported_hp = max(1, min(max_hp, hp_by_key[building.building_type.key]))
            current_hp = int(building.current_hp or max_hp)
            building.current_hp = max(1, min(current_hp, reported_hp))
            building.hp_updated_at = now
            building.save(update_fields=["current_hp", "hp_updated_at"])


def repair_city_defense(building: Building, *, now: datetime | None = None) -> int:
    now = now or timezone.now()
    if not is_city_defense_key(getattr(building.building_type, "key", None)):
        raise GameError("该建筑不能修复城防耐久")

    with transaction.atomic():
        manor = Manor.objects.select_for_update().get(pk=building.manor_id)
        locked_building = Building.objects.select_for_update().select_related("building_type").get(pk=building.pk)
        refresh_city_defense_hp(locked_building, now=now, persist=False)

        max_hp = city_defense_max_hp(locked_building.building_type.key, locked_building.level)
        missing_hp = max(0, max_hp - int(locked_building.current_hp))
        if missing_hp <= 0:
            locked_building.hp_updated_at = now
            locked_building.save(update_fields=["hp_updated_at"])
            building.current_hp = locked_building.current_hp
            building.hp_updated_at = locked_building.hp_updated_at
            return 0

        cost = missing_hp * CITY_DEFENSE_REPAIR_SILVER_PER_HP
        spend_resources_locked(
            manor,
            {ResourceType.SILVER: cost},
            f"{locked_building.building_type.name}修复",
            ResourceEvent.Reason.UPGRADE_COST,
        )
        add_prestige_silver_locked(manor, cost)

        locked_building.current_hp = max_hp
        locked_building.hp_updated_at = now
        locked_building.save(update_fields=["current_hp", "hp_updated_at"])

    building.current_hp = locked_building.current_hp
    building.hp_updated_at = locked_building.hp_updated_at
    return cost
