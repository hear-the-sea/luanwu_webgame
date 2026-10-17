"""
回合顺序决定
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, List

from ..random_context import CURRENT_BATTLE_ENGINE_VERSION
from .utils import alive

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


def determine_turn_order(
    attacker_team: List["Combatant"],
    defender_team: List["Combatant"],
    rng: random.Random,
    *,
    round_priority: int = 0,
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION,
) -> List["Combatant"]:
    del rng
    participants = [
        unit
        for unit in alive(attacker_team) + alive(defender_team)
        if not getattr(unit, "battle_modifiers", {}).get("skip_turn")
    ]
    if not participants:
        return []
    # 按出战名单计算门客平均敏捷，不因阵亡或跳过行动而剔除门客。
    average_guest_agility = {}
    for side, team in (("attacker", attacker_team), ("defender", defender_team)):
        guests = [unit for unit in team if unit.kind == "guest"]
        average_guest_agility[side] = sum(unit.agility for unit in guests) / len(guests) if guests else 0
    return sorted(
        participants,
        key=lambda combatant: (
            1 if getattr(combatant, "battle_modifiers", {}).get("fixed_first") else 0,
            # 仅新版本普通回合按护院、门客分组；开战先攻与旧版本保留原顺序。
            1 if battle_engine_version == "4" and round_priority == 0 and combatant.kind == "troop" else 0,
            combatant.agility,
            average_guest_agility[combatant.side],
            1 if combatant.side == "defender" else 0,
        ),
        reverse=True,
    )
