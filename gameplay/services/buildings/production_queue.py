"""生产建筑并行队列规则。"""

from __future__ import annotations

from typing import Any

from gameplay.constants import BUILDING_MAX_LEVELS, BuildingKeys

BASE_PRODUCTION_QUEUE_CAPACITY = 1
MAX_LEVEL_PRODUCTION_QUEUE_CAPACITY = 2

_MAX_LEVEL_QUEUE_BUILDING_KEYS = frozenset(
    {
        BuildingKeys.SMITHY,
        BuildingKeys.FORGE,
        BuildingKeys.STABLE,
    }
)


def get_production_queue_capacity(manor: Any, building_key: str) -> int:
    """返回指定生产建筑当前可同时进行的生产队列数。"""
    if building_key not in _MAX_LEVEL_QUEUE_BUILDING_KEYS:
        return BASE_PRODUCTION_QUEUE_CAPACITY

    max_level = BUILDING_MAX_LEVELS.get(building_key)
    if max_level is None:
        return BASE_PRODUCTION_QUEUE_CAPACITY

    current_level = int(manor.get_building_level(building_key))
    if current_level >= int(max_level):
        return MAX_LEVEL_PRODUCTION_QUEUE_CAPACITY
    return BASE_PRODUCTION_QUEUE_CAPACITY
