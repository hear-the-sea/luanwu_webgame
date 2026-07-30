from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import Building, Manor, ResourceEvent, ResourceType
from gameplay.services.resources import spend_resources_locked

from .city_defense_rules import (  # noqa: F401
    CITY_DEFENSE_HP_RECOVERY_SECONDS,
    CITY_DEFENSE_MAX_HP,
    CITY_DEFENSE_RECOVERY_PERCENT_PER_HOUR,
    CITY_DEFENSE_REPAIR_SILVER_PER_HP,
    city_defense_max_hp,
    is_city_defense_key,
    project_city_defense_hp,
)


def _prepare_city_defense_state(building: Building, *, now: datetime) -> tuple[int, int, int]:
    state = project_city_defense_hp(
        building.building_type.key,
        building.level,
        building.current_hp,
        getattr(building, "hp_updated_at", None),
        now=now,
    )
    building.current_hp = state.current_hp
    setattr(building, "city_defense_max_hp", state.max_hp)
    setattr(
        building,
        "city_defense_repair_cost",
        max(0, state.max_hp - state.current_hp) * CITY_DEFENSE_REPAIR_SILVER_PER_HP,
    )
    setattr(building, "city_defense_recovered_hp", state.recovered_hp)
    setattr(building, "city_defense_recovery_per_hour", state.recovery_per_hour)
    setattr(building, "city_defense_recovery_percent_per_hour", CITY_DEFENSE_RECOVERY_PERCENT_PER_HOUR)
    setattr(building, "city_defense_full_at", state.full_at)
    return state.current_hp, state.max_hp, state.recovered_hp


def refresh_city_defense_hp(building: Building, *, now: datetime | None = None, persist: bool = True) -> int:
    now = now or timezone.now()
    if not is_city_defense_key(getattr(building.building_type, "key", None)):
        return 0

    persisted_hp = int(getattr(building, "current_hp", 0) or 0)
    current_hp, _max_hp, _recovered_hp = _prepare_city_defense_state(building, now=now)
    if persist and current_hp != persisted_hp and building.pk:
        building.hp_updated_at = now
        building.save(update_fields=["current_hp", "hp_updated_at"])
    return current_hp


def prepare_city_defense_display(building: Building, *, now: datetime | None = None) -> None:
    _prepare_city_defense_state(building, now=now or timezone.now())


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
    rows_by_key = {str(row.get("key")): row for row in city_defenses if is_city_defense_key(str(row.get("key") or ""))}
    if not rows_by_key:
        return

    with transaction.atomic():
        buildings = (
            Building.objects.select_for_update()
            .select_related("building_type")
            .filter(manor=manor, building_type__key__in=rows_by_key.keys())
            .order_by("pk")
        )
        for building in buildings:
            row = rows_by_key[building.building_type.key]
            max_hp = city_defense_max_hp(building.building_type.key, building.level)
            current_hp = int(building.current_hp or max_hp)
            try:
                schema_version = int(row.get("schema_version") or 1)
            except (TypeError, ValueError):
                schema_version = 1

            if schema_version >= 2 and row.get("initial_hp") is not None:
                initial_hp = max(0, min(max_hp, int(row.get("initial_hp") or 0)))
                battle_hp = max(0, min(max_hp, int(row.get("hp") or 0)))
                recovered_hp = max(0, min(initial_hp, int(row.get("recovered_before_battle") or 0)))
                expected_persisted_hp = max(1, initial_hp - recovered_hp)
                damage = max(0, initial_hp - battle_hp)
                if current_hp == expected_persisted_hp:
                    reported_settled_hp = int(row.get("settled_hp") or max(1, battle_hp))
                    settled_hp = max(1, min(max_hp, reported_settled_hp))
                else:
                    settled_hp = max(1, min(max_hp, current_hp - damage))
            else:
                reported_hp = max(1, min(max_hp, int(row.get("hp") or 0)))
                settled_hp = max(1, min(current_hp, reported_hp))

            building.current_hp = settled_hp
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
            locked_building.save(update_fields=["current_hp", "hp_updated_at"])
            building.current_hp = locked_building.current_hp
            building.hp_updated_at = locked_building.hp_updated_at
            return 0

        cost = missing_hp * CITY_DEFENSE_REPAIR_SILVER_PER_HP
        spend_resources_locked(
            manor,
            {ResourceType.SILVER: cost},
            f"{locked_building.building_type.name}修复",
            ResourceEvent.Reason.CITY_DEFENSE_REPAIR,
        )

        locked_building.current_hp = max_hp
        locked_building.hp_updated_at = now
        locked_building.save(update_fields=["current_hp", "hp_updated_at"])

    building.current_hp = locked_building.current_hp
    building.hp_updated_at = locked_building.hp_updated_at
    return cost
