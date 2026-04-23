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
    participants = alive(attacker_team) + alive(defender_team)
    if not participants:
        return []
    return sorted(
        participants,
        key=lambda combatant: (
            combatant.agility,
            1 if combatant.side == "defender" else 0,
        ),
        reverse=True,
    )
