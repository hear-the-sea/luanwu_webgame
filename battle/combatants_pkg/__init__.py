"""
Combatants package - battle unit construction.

This package contains modules for building combat units:
- core: Core data classes (Combatant, BattleSimulationResult)
- cache: Template cache management
- tech_effects: Tech effect builders
- guest_builder: Guest combatant construction
- troop_builder: Troop combatant construction
- ai_generator: AI guest/loadout generation
"""

from __future__ import annotations

# AI generator
from .ai_generator import allocate_ai_attribute_points, build_ai_guests, build_named_ai_guests, generate_ai_loadout

# Cache management
from .cache import clear_guest_template_cache, get_all_guest_templates

# Core data classes
from .core import BattleSimulationResult, Combatant

# Guest builder
from .guest_builder import (
    DEFAULT_GUEST_AGILITY,
    DEFAULT_LUCK,
    VANGUARD_RATIO,
    assign_agility_based_priorities,
    build_guest_combatants,
    serialize_guest_for_report,
    serialize_skills,
)

# Tech effects
from .tech_effects import build_tech_effects

# Troop builder
from .troop_builder import build_troop_combatants, normalize_troop_loadout

__all__ = [
    # Core
    "BattleSimulationResult",
    "Combatant",
    # Cache
    "clear_guest_template_cache",
    "get_all_guest_templates",
    # Tech effects
    "build_tech_effects",
    # Guest builder
    "DEFAULT_GUEST_AGILITY",
    "DEFAULT_LUCK",
    "VANGUARD_RATIO",
    "assign_agility_based_priorities",
    "build_guest_combatants",
    "serialize_guest_for_report",
    "serialize_skills",
    # Troop builder
    "build_troop_combatants",
    "normalize_troop_loadout",
    # AI generator
    "allocate_ai_attribute_points",
    "build_ai_guests",
    "build_named_ai_guests",
    "generate_ai_loadout",
]
