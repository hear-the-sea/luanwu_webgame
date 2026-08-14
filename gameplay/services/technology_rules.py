from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, Optional


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def allocate_upgrade_budget(
    total_budget: int,
    floor_amount: int,
    curve_growth: float,
    step_count: int,
) -> list[int]:
    """
    将全等级预算平滑分配到每一次升级。

    预算决定累计消耗，曲线只决定各级之间的倾斜程度；保留基础消耗
    作为每级底线，并把整数舍入误差放到最后一级，确保累计值精确等于预算。
    """
    if step_count <= 0:
        return []

    floor_amount = max(0, int(floor_amount))
    total_budget = max(int(total_budget), floor_amount * step_count)
    curve_growth = max(1.0, float(curve_growth))
    if step_count == 1:
        return [total_budget]

    weights = [curve_growth**index for index in range(step_count)]
    remaining_budget = total_budget - floor_amount * step_count
    weight_total = sum(weights)
    allocations = [math.floor(remaining_budget * weight / weight_total) for weight in weights]
    allocations[-1] += remaining_budget - sum(allocations)
    return [floor_amount + allocation for allocation in allocations]


def calculate_upgrade_duration(
    template: Dict[str, Any] | None,
    current_level: int,
    *,
    scale_duration_func: Callable[..., int],
) -> int:
    """统一使用全等级预算曲线计算个人科技升级时长。"""
    base_time = coerce_float((template or {}).get("base_time", 60), 60.0)
    if base_time <= 0:
        base_time = 60.0

    max_level = max(1, coerce_int((template or {}).get("max_level", 10), 10))
    floor_time = math.ceil(base_time)
    total_budget = coerce_int((template or {}).get("upgrade_time_budget", 0), 0)
    if total_budget <= 0:
        total_budget = floor_time * max_level
    schedule = allocate_upgrade_budget(
        total_budget=total_budget,
        floor_amount=floor_time,
        curve_growth=coerce_float((template or {}).get("time_curve", 1.08), 1.08),
        step_count=max_level,
    )
    level = max(0, coerce_int(current_level, 0))
    return scale_duration_func(schedule[min(level, len(schedule) - 1)], minimum=1)


def calculate_upgrade_cost(
    template: Dict[str, Any] | None,
    current_level: int,
    *,
    coerce_int_func: Callable[[Any, int], int],
    coerce_float_func: Callable[[Any, float], float],
) -> int:
    if not template:
        return 0
    base_cost = max(0, coerce_int_func(template.get("base_cost", 8000), 8000))
    max_level = max(1, coerce_int_func(template.get("max_level", 10), 10))
    total_budget = coerce_int_func(template.get("upgrade_cost_budget", 0), 0)
    if total_budget <= 0:
        total_budget = base_cost * max_level
    schedule = allocate_upgrade_budget(
        total_budget=total_budget,
        floor_amount=base_cost,
        curve_growth=coerce_float_func(template.get("cost_curve", 1.15), 1.15),
        step_count=max_level,
    )
    level = max(0, coerce_int_func(current_level, 0))
    return schedule[min(level, len(schedule) - 1)]


def get_tech_bonus_from_templates(
    technologies: Iterable[Any],
    levels: Dict[str, int],
    effect_type: str,
    *,
    troop_class: Optional[str],
    coerce_int_func: Callable[[Any, int], int],
    coerce_float_func: Callable[[Any, float], float],
) -> float:
    total = 0.0
    for tech in technologies:
        if not isinstance(tech, dict):
            continue
        if tech.get("effect_type") != effect_type:
            continue
        tech_key = str(tech.get("key") or "").strip()
        if not tech_key:
            continue
        tech_troop_class = tech.get("troop_class")
        if tech_troop_class and troop_class and tech_troop_class != troop_class:
            continue
        level = coerce_int_func(levels.get(tech_key, 0), 0)
        if level <= 0:
            continue
        effect_per_level = coerce_float_func(tech.get("effect_per_level", 0.10), 0.10)
        total += level * effect_per_level
    return total


def build_uniform_tech_levels(
    technologies: Iterable[Any],
    level: int,
    *,
    coerce_int_func: Callable[[Any, int], int],
) -> Dict[str, int]:
    base_level = max(0, coerce_int_func(level, 0))
    resolved: Dict[str, int] = {}
    for tech in technologies:
        if not isinstance(tech, dict):
            continue
        tech_key = str(tech.get("key") or "").strip()
        if not tech_key:
            continue
        max_level = max(0, coerce_int_func(tech.get("max_level", base_level), base_level))
        resolved[tech_key] = max(0, min(base_level, max_level))
    return resolved


def resolve_enemy_tech_levels(
    config: Dict[str, Any],
    *,
    build_uniform_tech_levels_func: Callable[[int], Dict[str, int]],
    coerce_int_func: Callable[[Any, int], int],
) -> Dict[str, int]:
    if not config or not isinstance(config, dict):
        return {}
    levels = {}
    if config.get("level") is not None:
        levels = build_uniform_tech_levels_func(coerce_int_func(config.get("level", 0), 0))
    for key, val in (config.get("levels") or {}).items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        levels[normalized_key] = max(0, coerce_int_func(val, 0))
    return levels


def get_guest_stat_bonuses(
    config: Dict[str, Any],
    *,
    coerce_float_func: Callable[[Any, float], float],
) -> Dict[str, float]:
    if not config:
        return {}

    bonuses = {}
    if "guest_bonus" in config:
        bonus_percent = coerce_float_func(config.get("guest_bonus", 0), 0.0)
        bonuses["attack"] = bonus_percent
        bonuses["defense"] = bonus_percent
        bonuses["hp"] = bonus_percent
        bonuses["agility"] = bonus_percent * 0.5

    return bonuses


def get_resource_production_bonus_from_templates(
    technologies: Iterable[Any],
    levels: Dict[str, int],
    resource_type: str,
    *,
    building_key: Optional[str],
    coerce_int_func: Callable[[Any, int], int],
    coerce_float_func: Callable[[Any, float], float],
) -> float:
    total_bonus = 0.0
    for tech in technologies:
        if not isinstance(tech, dict):
            continue
        if tech.get("effect_type") != "resource_production":
            continue
        if tech.get("resource_type") != resource_type:
            continue

        required_building_key = tech.get("building_key")
        required_building_keys = tech.get("building_keys")
        if required_building_key or required_building_keys:
            if not building_key:
                continue
            if required_building_key and building_key != required_building_key:
                continue
            if required_building_keys and building_key not in required_building_keys:
                continue

        tech_key = str(tech.get("key") or "").strip()
        if not tech_key:
            continue
        level = coerce_int_func(levels.get(tech_key, 0), 0)
        if level <= 0:
            continue
        effect_per_level = coerce_float_func(tech.get("effect_per_level", 0.05), 0.05)
        total_bonus += level * effect_per_level

    return total_bonus


__all__ = [
    "allocate_upgrade_budget",
    "build_uniform_tech_levels",
    "calculate_upgrade_cost",
    "calculate_upgrade_duration",
    "coerce_float",
    "coerce_int",
    "get_guest_stat_bonuses",
    "get_resource_production_bonus_from_templates",
    "get_tech_bonus_from_templates",
    "resolve_enemy_tech_levels",
]
