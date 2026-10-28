"""城墙、箭塔及车轮战疲敝的赛季配置与独立建筑状态。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.core.exceptions import ImproperlyConfigured

DEFAULT_FORTIFICATIONS = {
    "enabled": True,
    "capture_on_breach": True,
    "sites": {
        "mountain:gate": {"wall_multiplier": 2, "tower_hp": 300},
        "river:town": {"wall_multiplier": 2, "tower_hp": 600},
    },
    "tower": {
        "damage_percent": 3,
        "max_damage_percent": 6,
        "wear_per_battle": 25,
        "repair_cost_per_100": {"wood": 80, "iron": 100},
    },
    "fatigue": {"per_stack_percent": 5, "max_stacks": 2},
}


def validate_fortifications(value: Any, sites: dict[str, Any]) -> None:
    if value is None:
        return
    try:
        if not isinstance(value["enabled"], bool) or not isinstance(value["capture_on_breach"], bool):
            raise ValueError
        for key, spec in value["sites"].items():
            if key not in sites:
                raise ValueError
            for field, maximum in (("wall_multiplier", 10), ("tower_hp", 1000000)):
                if type(spec[field]) is not int or not 1 <= spec[field] <= maximum:
                    raise ValueError
        hp = value.get("hitpoints")
        if hp is not None:
            if not isinstance(hp["enabled"], bool):
                raise ValueError
            for field in ("gate_hp", "tower_hp", "hp_per_work", "repair_amount", "repair_interval_seconds"):
                if type(hp[field]) is not int or not 1 <= hp[field] <= 100000000:
                    raise ValueError
        if hp is not None:
            normal_rounds = hp.get("gate_normal_rounds", 8)
            if type(normal_rounds) is not int or not 1 <= normal_rounds <= 48:
                raise ValueError
        tower, fatigue = value["tower"], value["fatigue"]
        for number, maximum in (
            (tower["damage_percent"], 20),
            (tower["max_damage_percent"], 20),
            (tower["wear_per_battle"], 100000),
            (fatigue["per_stack_percent"], 20),
            (fatigue["max_stacks"], 10),
        ):
            if type(number) is not int or not 0 <= number <= maximum:
                raise ValueError
        if (
            tower["damage_percent"] > tower["max_damage_percent"]
            or fatigue["per_stack_percent"] * fatigue["max_stacks"] >= 100
        ):
            raise ValueError
        if set(tower["repair_cost_per_100"]) != {"wood", "iron"}:
            raise ValueError
        if any(type(n) is not int or not 1 <= n <= 100000 for n in tower["repair_cost_per_100"].values()):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ImproperlyConfigured("城防、箭塔或疲敝配置无效") from exc


def fortification_rules(season: Any) -> dict[str, Any]:
    if season is None:
        return deepcopy(DEFAULT_FORTIFICATIONS)
    value = season.rules.get("campaign", {}).get("fortifications")
    return deepcopy(value) if value is not None else {**deepcopy(DEFAULT_FORTIFICATIONS), "enabled": False}


def breach_capture(season: Any) -> bool:
    rules = fortification_rules(season)
    return rules["enabled"] and rules["capture_on_breach"]


def tower_state(site: Any) -> dict[str, int]:
    rules = fortification_rules(site.season)
    spec = rules["sites"].get(site.key, {}) if rules["enabled"] else {}
    from .hitpoints import hitpoint_rules

    hp = hitpoint_rules(site.season)
    maximum = int(hp["tower_hp"] if hp["enabled"] and spec else spec.get("tower_hp", 0))
    stored = site.defenses.get("arrow_tower", {})
    return {"max_hp": maximum, "hp": min(maximum, max(0, int(stored.get("hp", maximum))))}


def reset_defenses(site: Any) -> None:
    state = tower_state(site)
    site.defenses = {"arrow_tower": {"hp": state["max_hp"], "max_hp": state["max_hp"]}} if state["max_hp"] else {}


def needs_repair(site: Any) -> bool:
    from .campaign_rules import campaign_rules

    maximum = (site.npc_profile or campaign_rules(site.season)["sites"][site.key])["durability"]
    tower = tower_state(site)
    return site.durability < maximum or tower["hp"] < tower["max_hp"]
