"""
回合顺序决定
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, List

from .utils import alive

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


def determine_turn_order(
    attacker_team: List["Combatant"],
    defender_team: List["Combatant"],
    rng: random.Random,
) -> List["Combatant"]:
    del rng
    participants = [
        unit
        for unit in alive(attacker_team) + alive(defender_team)
        if not getattr(unit, "battle_modifiers", {}).get("skip_turn")
    ]
    if not participants:
        return []
    return sorted(
        participants,
        key=lambda combatant: (
            1 if getattr(combatant, "battle_modifiers", {}).get("fixed_first") else 0,
            combatant.agility,
            1 if combatant.side == "defender" else 0,
        ),
        reverse=True,
    )
