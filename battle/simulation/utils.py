"""
战斗模拟工具函数
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Dict, List, Tuple

from .constants import BASE_CRIT_CHANCE

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


def build_rng(seed: int | None = None) -> Tuple[int, random.Random]:
    from ..random_context import RNG_STREAM_COMBAT, BattleRandomContext

    context = BattleRandomContext.create(seed)
    return context.base_seed, context.rng(RNG_STREAM_COMBAT)


def calculate_crit_chance(actor: "Combatant") -> float:
    """
    计算暴击率（敏捷不再影响暴击）
    固定基础暴击率
    """
    return BASE_CRIT_CHANCE


def calculate_dodge_chance(target: "Combatant") -> float:
    """
    闪避率已移除（敏捷不再影响闪避）
    """
    return 0.0


def alive(team: List["Combatant"]) -> List["Combatant"]:
    """
    判断战斗单位是否存活

    规则：
    - 门客：hp > 0
    - 护院：hp > 0 且 troop_strength > 0
    """
    result = []
    for c in team:
        if c.hp <= 0:
            continue
        # 护院需要额外检查兵力
        if c.kind == "troop" and c.troop_strength <= 0:
            continue
        result.append(c)
    return result


def roll_loot(config: dict, rng: random.Random) -> Dict[str, int]:
    loot_pool = config.get("loot_pool") or {}
    if not loot_pool:
        return {}
    resources = list(loot_pool.items())
    rng.shuffle(resources)
    take_count = rng.randint(1, len(resources))
    drops: Dict[str, int] = {}
    for resource, base_amount in resources[:take_count]:
        portion = rng.uniform(0.4, 0.85)
        amount = int(base_amount * portion)
        if amount > 0:
            drops[resource] = amount
    return drops


def summarize_losses(
    attacker_team: List["Combatant"],
    defender_team: List["Combatant"],
    winner: str,
    rng: random.Random,
) -> Dict[str, dict]:
    from ..utils.battle_calculator import calculate_team_losses

    return {
        "attacker": calculate_team_losses(attacker_team, winner == "attacker", rng, side="attacker"),
        "defender": calculate_team_losses(defender_team, winner == "defender", rng, side="defender"),
    }
