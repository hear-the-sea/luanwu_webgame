"""
Core combatant data classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, TypeAlias, TypedDict


class SoftcapSource(TypedDict):
    threshold: float
    overflow_ratio: float


BattleModifiers: TypeAlias = dict[str, Any]
CombatValue: TypeAlias = int | float


@dataclass(slots=True)
class BattleSimulationResult:
    rounds: List[Dict[str, Any]]
    winner: str
    losses: Dict[str, dict]
    drops: Dict[str, int]
    seed: int
    starts_at: datetime
    completed_at: datetime


@dataclass(slots=True)
class Combatant:
    name: str
    attack: CombatValue
    defense: CombatValue
    hp: int
    max_hp: int
    side: str
    rarity: str
    luck: int
    agility: CombatValue
    priority: int
    kind: str
    troop_strength: int
    initial_troop_strength: int = 0
    initial_hp: int = 0
    unit_attack: CombatValue = 0
    unit_defense: CombatValue = 0
    unit_hp: CombatValue = 0
    skills: list = field(default_factory=list)
    template_key: str | None = None
    force_attr: int = 0
    intellect_attr: int = 0
    defense_attr: int = 0
    guest_id: int | None = None
    owner_entry_id: int | None = None
    combatant_slot: int | None = None
    is_boss: bool = False
    level: int = 1
    battle_modifiers: BattleModifiers = field(default_factory=dict)
    battle_state: Dict[str, Any] = field(default_factory=dict)
    status_effects: Dict[str, Dict[str, int]] = field(default_factory=dict)
    has_acted_this_round: bool = False
    current_round: int = 0
    last_round_acted: int = 0
    troop_class: str = ""
    tech_effects: Dict[str, float] = field(default_factory=dict)
