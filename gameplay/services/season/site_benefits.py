"""战略建筑的可见小幅优势；出征时冻结，不追溯改变在途速度。"""

from math import ceil
from typing import Any

from .playability_rules import playability_rules


def gate_march_bonus(season: Any, faction: str) -> int:
    rules = playability_rules(season)
    if not rules["enabled"] or not faction:
        return 0
    if not season.sites.filter(key="mountain:gate", owner=faction).exists():
        return 0
    return rules["site_effects"]["gate_march_reduction_percent"]


def season_travel_seconds(season: Any, faction: str, seconds: int, minimum: int) -> int:
    return max(minimum, ceil(seconds * (100 - gate_march_bonus(season, faction)) / 100))
