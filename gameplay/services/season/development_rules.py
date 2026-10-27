"""人口、活动和贡献规则；旧赛季缺少对应配置时保持兼容。"""

from copy import deepcopy
from typing import Any

from django.core.exceptions import ImproperlyConfigured

POPULATION = {
    "enabled": True,
    "active_days": 7,
    "refresh_seconds": 86400,
    "victory_min_days": 3,
    "hotspot_multiplier": 2,
    "recommendation_max_active": 0,
    "tiers": [
        {"key": "solo", "name": "单人开拓", "maximum": 1, "rate": 0.1, "capacity": 0.25, "threshold": 0.05},
        {"key": "small", "name": "小队发展", "maximum": 4, "rate": 0.25, "capacity": 0.5, "threshold": 0.15},
        {"key": "medium", "name": "协同发展", "maximum": 19, "rate": 1, "capacity": 1, "threshold": 0.5},
        {"key": "large", "name": "军团发展", "maximum": 99, "rate": 2, "capacity": 2, "threshold": 2},
        {"key": "mass", "name": "大规模发展", "maximum": 1000000, "rate": 4, "capacity": 4, "threshold": 5},
    ],
}
ACTIVITIES: dict[str, Any] = {
    "enabled": True,
    "explore": {
        "nodes": ["mountain:ruins", "forest:lodge"],
        "intel_seconds": 7200,
        "bonus_percent": 20,
        "daily_limit": 2,
        "merit": 20,
        "construction": 10,
        "resources": {"grain": 50, "wood": 30, "iron": 20},
    },
    "transport": {
        "daily_npc_limit": 3,
        "merit": 45,
        "construction": 30,
        "supply_capacity": 5000,
        "capture_grain": 300,
        "garrison_grain_per_100": 2,
        "supply_tick_seconds": 300,
        "battle_grain_per_100": 5,
        "frontline_merit_percent": 200,
        "npc_reserve_percent": 50,
        "npc_resources": {"grain": 300, "wood": 150, "iron": 100},
    },
    "intercept": {
        "loot_percent": 30,
        "npc_merit": 25,
        "npc_failure_merit": 5,
        "npc_construction": 20,
        "npc_failure_construction": 5,
        "daily_npc_limit": 3,
        "merit": 30,
        "npc_resources": {"grain": 180, "wood": 100, "iron": 80},
    },
}
CONTRIBUTION = {
    "enabled": True,
    "daily_cap": 300,
    "battle_pool_per_player": 60,
    "npc_pool_per_player": 25,
    "participation_percent": 30,
    "damage_weight": 50,
    "tank_weight": 30,
    "support_weight": 20,
    "loss_percent": 35,
    "demolition_daily_budget": 100,
    "demolition_points_per_100": 10,
    "capture_bonus_pool": 100,
    "minimum_battle_value": 100,
}


def feature_rules(season: Any, key: str) -> dict[str, Any]:
    catalog: dict[str, dict[str, Any]] = {
        "population": POPULATION,
        "activities": ACTIVITIES,
        "contribution": CONTRIBUTION,
    }
    defaults = catalog[key]
    snapshot = season.rules.get("campaign", {}) if season is not None else {}
    result = deepcopy(defaults)
    if key not in snapshot:
        result["enabled"] = False
    else:
        result.update(deepcopy(snapshot[key]))
    return result


def validate_development_rules(rules: dict[str, Any]) -> None:
    import math

    from .rules import NODES

    def integer(value: Any, minimum: int = 1, maximum: int = 1000000) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum

    for key in ("population", "activities", "contribution"):
        if key not in rules:
            continue
        value = rules[key]
        if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
            raise ImproperlyConfigured(f"{key}必须包含布尔开关")
        if not value["enabled"]:
            continue
        try:
            if key == "population":
                if not all(integer(value[k]) for k in ("active_days", "refresh_seconds", "victory_min_days")):
                    raise ValueError
                if not integer(value["hotspot_multiplier"], 1, 10):
                    raise ValueError
                if "recommendation_max_active" in value and not integer(value["recommendation_max_active"], 0):
                    raise ValueError
                last = 0
                for tier in value["tiers"]:
                    if not integer(tier["maximum"]) or tier["maximum"] <= last or not tier["key"] or not tier["name"]:
                        raise ValueError
                    last = tier["maximum"]
                    for multiplier in ("rate", "capacity", "threshold"):
                        number = tier[multiplier]
                        if (
                            isinstance(number, bool)
                            or not isinstance(number, (int, float))
                            or not math.isfinite(number)
                            or not 0 < number <= 100
                        ):
                            raise ValueError
                if last < 1000000:
                    raise ValueError
            elif key == "contribution":
                for field, number in CONTRIBUTION.items():
                    if field != "enabled" and not integer(value[field], 0):
                        raise ValueError
                if value["daily_cap"] < 1 or any(value[k] > 100 for k in ("participation_percent", "loss_percent")):
                    raise ValueError
                if sum(value[k] for k in ("damage_weight", "tank_weight", "support_weight")) != 100:
                    raise ValueError
            else:
                if not set(value["explore"]["nodes"]) <= set(NODES):
                    raise ValueError
                for section in ("explore", "transport", "intercept"):
                    if set(value[section]) != set(ACTIVITIES[section]):
                        raise ValueError
                    for field, entry in value[section].items():
                        if field == "nodes":
                            continue
                        if isinstance(entry, dict):
                            if not set(entry) <= {"grain", "wood", "iron"} or not all(
                                integer(n, 0) for n in entry.values()
                            ):
                                raise ValueError
                        elif not integer(entry):
                            raise ValueError
                if value["intercept"]["loot_percent"] > 100 or value["transport"]["npc_reserve_percent"] > 100:
                    raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ImproperlyConfigured(f"{key}配置不完整或包含无效数值") from exc
