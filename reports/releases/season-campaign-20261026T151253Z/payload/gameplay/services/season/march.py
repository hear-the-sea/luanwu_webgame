"""赛季沿用个人任务的敏捷算法，全局倍速只换算一次。"""

from battle.troops import load_troop_templates
from gameplay.utils.resource_calculator import calculate_travel_time


def travel_time_seconds(base_time: int, guests, troop_loadout: dict[str, int]) -> int:
    return calculate_travel_time(base_time, guests, troop_loadout, load_troop_templates())
