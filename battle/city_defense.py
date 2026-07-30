from __future__ import annotations

from typing import Any

from django.utils import timezone

from core.config import BUILDING_KEYS
from gameplay.services.city_defense_rules import (  # noqa: F401
    CITY_DEFENSE_MAX_HP,
    CITY_DEFENSE_MAX_LEVEL,
    city_defense_max_hp,
    clamp_city_defense_level,
    project_city_defense_hp,
    scale_city_defense_value,
)

from .combatants_pkg.core import Combatant

CITY_DEFENSE_REPORT_SCHEMA_VERSION = 2
WALL_MAX_HP = CITY_DEFENSE_MAX_HP[BUILDING_KEYS.WALL]
WALL_MAX_DEFENSE = 300
WALL_INTERCEPT_CHANCE = 0.80
ARROW_TOWER_MAX_HP = CITY_DEFENSE_MAX_HP[BUILDING_KEYS.ARROW_TOWER]
ARROW_TOWER_MAX_ATTACK = 1500
ARROW_TOWER_MAX_DEFENSE = 150

CITY_DEFENSE_NAMES = {
    BUILDING_KEYS.WALL: "城墙",
    BUILDING_KEYS.ARROW_TOWER: "箭塔",
}


def _clamp_level(level: Any) -> int:
    return clamp_city_defense_level(level)


def _scale(max_value: int, level: int) -> int:
    return scale_city_defense_value(max_value, level)


def city_defense_max_hp_for_key(key: str, level: int) -> int:
    return city_defense_max_hp(key, level)


def _tower_targets(level: int) -> int:
    level = _clamp_level(level)
    if level >= 10:
        return 3
    if level >= 5:
        return 2
    return 1


def _iter_city_defense_buildings(manor: Any):
    if manor is None:
        return []
    return (
        manor.buildings.select_related("building_type")
        .filter(building_type__key__in=[BUILDING_KEYS.WALL, BUILDING_KEYS.ARROW_TOWER], level__gt=0)
        .order_by("building_type__key")
    )


def build_city_defense_combatants(manor: Any, *, side: str, now=None) -> list[Combatant]:
    now = now or timezone.now()
    units: list[Combatant] = []
    for building in _iter_city_defense_buildings(manor):
        key = building.building_type.key
        level = _clamp_level(building.level)
        hp_state = project_city_defense_hp(
            key,
            level,
            building.current_hp,
            building.hp_updated_at,
            now=now,
        )
        if key == BUILDING_KEYS.WALL:
            max_hp = hp_state.max_hp
            hp = hp_state.current_hp
            units.append(
                Combatant(
                    name="城墙",
                    attack=0,
                    defense=_scale(WALL_MAX_DEFENSE, level),
                    hp=hp,
                    max_hp=max_hp,
                    side=side,
                    rarity="city_defense",
                    luck=0,
                    agility=0,
                    priority=0,
                    kind="city_defense",
                    troop_strength=1,
                    initial_troop_strength=1,
                    initial_hp=hp,
                    unit_attack=0,
                    unit_defense=_scale(WALL_MAX_DEFENSE, level),
                    unit_hp=max_hp,
                    template_key=BUILDING_KEYS.WALL,
                    level=level,
                    battle_modifiers={
                        "skip_turn": True,
                        "wall_intercept_chance": WALL_INTERCEPT_CHANCE,
                        "recovered_before_battle": hp_state.recovered_hp,
                    },
                )
            )
        elif key == BUILDING_KEYS.ARROW_TOWER:
            max_hp = hp_state.max_hp
            hp = hp_state.current_hp
            attack = _scale(ARROW_TOWER_MAX_ATTACK, level)
            defense = _scale(ARROW_TOWER_MAX_DEFENSE, level)
            units.append(
                Combatant(
                    name="箭塔",
                    attack=attack,
                    defense=defense,
                    hp=hp,
                    max_hp=max_hp,
                    side=side,
                    rarity="city_defense",
                    luck=0,
                    agility=0,
                    priority=0,
                    kind="city_defense",
                    troop_strength=1,
                    initial_troop_strength=1,
                    initial_hp=hp,
                    unit_attack=attack,
                    unit_defense=defense,
                    unit_hp=max_hp,
                    template_key=BUILDING_KEYS.ARROW_TOWER,
                    level=level,
                    battle_modifiers={
                        "city_defense_attack_targets": _tower_targets(level),
                        "fixed_first": True,
                        "recovered_before_battle": hp_state.recovered_hp,
                    },
                )
            )
    return units


def serialize_city_defenses_for_report(combatants: list[Combatant]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for unit in combatants:
        if unit.kind != "city_defense":
            continue
        initial_hp = max(0, int(unit.initial_hp or unit.max_hp))
        battle_hp = max(0, int(unit.hp))
        rows.append(
            {
                "schema_version": CITY_DEFENSE_REPORT_SCHEMA_VERSION,
                "key": unit.template_key,
                "name": unit.name,
                "level": unit.level,
                "initial_hp": initial_hp,
                "hp": battle_hp,
                "max_hp": int(unit.max_hp),
                "recovered_before_battle": max(
                    0,
                    int(unit.battle_modifiers.get("recovered_before_battle") or 0),
                ),
                "settled_hp": max(1, battle_hp),
                "destroyed": battle_hp <= 0,
                "attack": int(unit.attack),
                "defense": int(unit.defense),
            }
        )
    return rows
