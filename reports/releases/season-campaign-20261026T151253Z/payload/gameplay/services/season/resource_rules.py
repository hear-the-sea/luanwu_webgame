"""赛季三资源经济配置、只读预算与兼容入口。"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from django.core.exceptions import ImproperlyConfigured

RESOURCES = ("grain", "wood", "iron")
DEFAULT_RESOURCE_ECONOMY: dict[str, Any] = {
    "resources": ["grain", "wood", "iron"],
    "settlement_seconds": 300,
    "population": {
        "smoothing_previous_weight": 0.75,
        "qualification": "completed_effective_activity",
        "empty_faction_production": False,
    },
    "initial_grant": {
        "once_per_faction": True,
        "fixed": {"grain": 800, "wood": 500, "iron": 300},
        "per_active": {"grain": 1600, "wood": 900, "iron": 700},
    },
    "production": {
        "daily_fixed": {"grain": 500, "wood": 300, "iron": 200},
        "daily_per_active": {"grain": 600, "wood": 350, "iron": 250},
        "logistics_multipliers": [1, 1.12, 1.24, 1.36, 1.48, 1.6],
        "carry_fractional_remainders": True,
    },
    "pve": {
        "material_reward_multipliers": [1, 1, 1, 1, 0.5, 0.5],
        "reward_after_budget": 0,
        "failure_resource_multiplier": 0.25,
        "failure_consumes_reward_tier": True,
        "grain_run": {"grain": 6000, "wood": 600, "iron": 300},
        "wood_run": {"grain": 1200, "wood": 3000, "iron": 600},
        "iron_run": {"grain": 1200, "wood": 600, "iron": 3000},
        "mixed_run": {"grain": 4000, "wood": 1500, "iron": 1500},
        "exploration_daily_limit": 2,
        "exploration_uses_material_tier": False,
        "exploration_resources": {"grain": 50, "wood": 30, "iron": 20},
        "intelligence_bonus_percent": 20,
    },
    "training": {
        "normal_cost_per_10": {"grain": 60, "wood": 15, "iron": 15},
        "cavalry_cost_per_10": {"grain": 100, "wood": 40, "iron": 60},
        "normal_daily_capacity_per_active": 1000,
        "training_multipliers": [1, 1.15, 1.3, 1.45, 1.6, 1.75],
        "normal_target_per_active": 3000,
        "normal_initial_per_active": 400,
        "cavalry_daily_capacity_per_active": 100,
        "cavalry_target_per_active": 500,
        "cavalry_initial": 0,
        "cavalry_requires_site": "forest:camp",
        "cavalry_full_technology": True,
    },
    "operations": {
        "march_grain_per_100_per_10_minutes": 4,
        "garrison_normal_grain_per_100_per_hour": 8,
        "cavalry_garrison_cost_multiplier": 3,
        "battle_normal_grain_per_100_per_wave": 3,
        "cavalry_battle_cost_multiplier": 3,
        "courier_grain_per_order": 5,
        "guest_only_pve_and_explore_grain": 0,
        "siege_cost_per_100_actual_wall": {"wood": 80, "iron": 50},
        "repair_cost_per_100_actual_wall": {"wood": 120, "iron": 60},
    },
    "transport": {
        "cargo_capacity": 1200,
        "resource_value_weights": {"grain": 1, "wood": 2, "iron": 3},
        "target_food_hours": 6,
        "reserve_inflight_site_demand": True,
        "reward_for_unneeded_delivery": False,
        "pure_consumption_npc_orders": False,
        "target_repair_demand_uses_actual_missing_wall": True,
    },
    "capture": {"created_resources": {"grain": 0, "wood": 0, "iron": 0}, "keep_existing_local_resources": True},
    "storage": {
        "reference_income_days": 3,
        "reference_pve_daily": {"grain": 8000, "wood": 3000, "iron": 3000},
        "preserve_existing_overflow": True,
        "reserve_existing_convoy_cargo": True,
    },
    "site_effects": {
        "forest:granary": {"passive_grain_bonus_percent": 10},
        "river:docks": {"cargo_capacity_bonus_percent": 20},
        "forest:camp": {"enables_cavalry": True},
        "river:town": {"resource_bonus_percent": 0},
        "mountain:gate": {"resource_bonus_percent": 0},
        "total_resource_production_bonus_cap_percent": 15,
    },
    "enabled": True,
}


def resource_rules(season: Any) -> dict[str, Any]:
    snapshot = season.rules.get("campaign", {}).get("resource_economy") if season is not None else None
    result = deepcopy(DEFAULT_RESOURCE_ECONOMY)
    if snapshot is None:
        result["enabled"] = False
    else:
        result.update(deepcopy(snapshot))
    return result


def validate_resource_rules(campaign: dict[str, Any]) -> None:
    value = campaign.get("resource_economy")
    if value is None:
        return
    if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
        raise ImproperlyConfigured("resource_economy必须包含布尔开关")
    if not value["enabled"]:
        return

    def check(candidate: Any, reference: Any, path: str) -> None:
        if isinstance(reference, bool):
            if not isinstance(candidate, bool):
                raise ValueError(path)
        elif isinstance(reference, (int, float)):
            if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
                raise ValueError(path)
            if not math.isfinite(candidate) or candidate < 0:
                raise ValueError(path)
        elif isinstance(reference, dict):
            if not isinstance(candidate, dict) or not set(reference) <= set(candidate):
                raise ValueError(path)
            for key, item in reference.items():
                check(candidate[key], item, f"{path}.{key}")
        elif isinstance(reference, list):
            if not isinstance(candidate, list) or len(candidate) != len(reference):
                raise ValueError(path)
            for index, item in enumerate(reference):
                check(candidate[index], item, f"{path}.{index}")
        elif not isinstance(candidate, str) or not candidate:
            raise ValueError(path)

    try:
        check(value, DEFAULT_RESOURCE_ECONOMY, "resource_economy")
        if "action_merit" in value["pve"]:
            if type(value["pve"]["action_merit"]) is not int or not 0 <= value["pve"]["action_merit"] <= 300:
                raise ValueError("物资行动贡献")
        if not isinstance(value["pve"].get("effective_material_merit", False), bool):
            raise ValueError("有效物资贡献开关")
        if not 0 <= value["population"]["smoothing_previous_weight"] < 1:
            raise ValueError("人口平滑系数")
        if (
            not isinstance(value["settlement_seconds"], int)
            or isinstance(value["settlement_seconds"], bool)
            or value["settlement_seconds"] < 1
            or value["storage"]["reference_income_days"] < 1
        ):
            raise ValueError("结算/仓库周期")
        if value["resources"] != list(RESOURCES):
            raise ValueError("资源列表")
        for key in (
            "normal_daily_capacity_per_active",
            "normal_target_per_active",
            "cavalry_daily_capacity_per_active",
            "cavalry_target_per_active",
        ):
            if value["training"][key] <= 0:
                raise ValueError("训练产能/储备")
        if value["transport"]["cargo_capacity"] <= 0 or value["transport"]["target_food_hours"] <= 0:
            raise ValueError("运输载重/口粮周期")
        for section, key in (("production", "logistics_multipliers"), ("training", "training_multipliers")):
            curve = value[section][key]
            if any(multiplier <= 0 or multiplier > 10 for multiplier in curve) or curve != sorted(curve):
                raise ValueError("设施倍率")
        for key in ("normal_cost_per_10", "cavalry_cost_per_10"):
            if set(value["training"][key]) != set(RESOURCES) or not all(
                isinstance(amount, int) and not isinstance(amount, bool) and amount > 0
                for amount in value["training"][key].values()
            ):
                raise ValueError("训练成本")
        weights = value["transport"]["resource_value_weights"]
        if set(weights) != set(RESOURCES) or not all(amount > 0 for amount in weights.values()):
            raise ValueError("运输价值权重")
        if any(multiplier > 1 for multiplier in value["pve"]["material_reward_multipliers"]):
            raise ValueError("物资收益档")
        if value["training"]["normal_initial_per_active"] > value["training"]["normal_target_per_active"]:
            raise ValueError("初始兵力")
    except (KeyError, TypeError, ValueError) as exc:
        raise ImproperlyConfigured(f"resource_economy配置无效：{exc}") from exc


def economic_population(season: Any, faction: str) -> float:
    profile = season.population.get("factions", {}).get(faction, {})
    count = profile.get("economic_count", profile.get("count", 0))
    return max(0.0, float(count))


def level_multiplier(curve: list[float], level: int) -> float:
    return float(curve[min(len(curve), max(1, level)) - 1])


def daily_resource_output(season: Any, faction: str, levels: dict[str, int] | None = None) -> dict[str, float]:
    config = resource_rules(season)
    population = economic_population(season, faction)
    if not population:
        return dict.fromkeys(RESOURCES, 0.0)
    if levels is None:
        state = season.faction_states.filter(faction=faction).only("levels").first()
        levels = state.levels if state else {}
    multiplier = level_multiplier(config["production"]["logistics_multipliers"], levels.get("logistics", 1))
    result = {
        key: (config["production"]["daily_fixed"][key] + config["production"]["daily_per_active"][key] * population)
        * multiplier
        for key in RESOURCES
    }
    effects = config["site_effects"]
    if season.sites.filter(key="forest:granary", owner=faction).exists():
        bonus = min(
            effects["total_resource_production_bonus_cap_percent"],
            effects["forest:granary"]["passive_grain_bonus_percent"],
        )
        result["grain"] *= 1 + bonus / 100
    return result


def resource_caps(season: Any, faction: str, levels: dict[str, int] | None = None) -> dict[str, int]:
    config = resource_rules(season)
    population = economic_population(season, faction)
    output = daily_resource_output(season, faction, levels)
    # 首次有效PVE还未刷新活跃人数时也要能接收奖励；库容不等于自动产出。
    storage_population = max(1, population)
    return {
        key: max(
            1,
            int(
                (output[key] + storage_population * config["storage"]["reference_pve_daily"][key])
                * config["storage"]["reference_income_days"]
            ),
        )
        for key in RESOURCES
    }


def training_targets(season: Any, faction: str) -> dict[str, int]:
    config = resource_rules(season)["training"]
    population = economic_population(season, faction)
    return {
        "normal": int(population * config["normal_target_per_active"]),
        "cavalry": int(population * config["cavalry_target_per_active"]),
    }


def training_summary(season: Any, faction: str, levels: dict[str, int]) -> dict[str, Any]:
    rules = resource_rules(season)
    training = rules["training"]
    count = economic_population(season, faction)
    level = levels.get("training", 1)
    return {
        "level": level,
        "normal_daily": round(
            count
            * training["normal_daily_capacity_per_active"]
            * level_multiplier(training["training_multipliers"], level),
            2,
        ),
        "cavalry_daily": round(count * training["cavalry_daily_capacity_per_active"], 2),
        "settlement_minutes": rules["settlement_seconds"] / 60,
        "normal_target": int(count * training["normal_target_per_active"]),
        "cavalry_target": int(count * training["cavalry_target_per_active"]),
    }


def normal_training_potential(resources: dict[str, int], cost_per_10: dict[str, int]) -> int:
    """仅按收到的物资估算完整训练人数，不发兵、不跳过产能与训练时间。"""
    batches = [max(0, resources.get(key, 0)) // cost for key, cost in cost_per_10.items() if cost > 0]
    return min(batches) * 10 if batches else 0
