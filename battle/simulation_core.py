"""
战斗模拟核心模块 (兼容性 shim)

此文件保持向后兼容性，所有实现已移至 battle/simulation/ 包。
"""

# Re-export all public APIs for backward compatibility
from .simulation import build_rng, simulate_battle
from .simulation.attack_execution import perform_attack
from .simulation.battle_flow import resolve_priority_phases
from .simulation.constants import (
    BASE_CRIT_CHANCE,
    COUNTER_DAMAGE_MULTIPLIER,
    CRIT_DAMAGE_MULTIPLIER,
    DAMAGE_VARIANCE_MAX,
    DAMAGE_VARIANCE_MIN,
    DEFAULT_DEFENSE_CONSTANT,
    GUEST_SKILL_VS_GUEST_MULTIPLIER,
    GUEST_SKILL_VS_TROOP_MULTIPLIER,
    GUEST_VS_GUEST_DAMAGE_MULTIPLIER,
    GUEST_VS_GUEST_DEFENSE_CONSTANT,
    GUEST_VS_TROOP_DEFENSE_CONSTANT,
    HARDCAP,
    MAX_ALLOWED_PRIORITY,
    MIN_ALLOWED_PRIORITY,
    PREEMPTIVE_DAMAGE_REDUCTION,
    PRIORITY_TARGET_WEIGHT,
    SOFTCAP_THRESHOLD,
    TROOP_COUNTERS,
    TROOP_VS_GUEST_DEFENSE_CONSTANT,
)
from .simulation.damage_application import apply_damage_results
from .simulation.damage_calculation import calculate_attack_damage, process_status_effects
from .simulation.target_selection import is_ranged_attack, select_target_with_priority
from .simulation.turn_order import determine_turn_order
from .simulation.types import (
    AttackLogEntry,
    AttackSkill,
    AttackType,
    _DamageApplication,
    _DamageCalculation,
    _SelectedAttackTargets,
)
from .simulation.utils import alive, calculate_crit_chance, calculate_dodge_chance, roll_loot, summarize_losses

__all__ = [
    # Main functions
    "simulate_battle",
    "build_rng",
    "perform_attack",
    "resolve_priority_phases",
    # Constants
    "PRIORITY_TARGET_WEIGHT",
    "TROOP_COUNTERS",
    "COUNTER_DAMAGE_MULTIPLIER",
    "DEFAULT_DEFENSE_CONSTANT",
    "GUEST_VS_GUEST_DEFENSE_CONSTANT",
    "TROOP_VS_GUEST_DEFENSE_CONSTANT",
    "SOFTCAP_THRESHOLD",
    "HARDCAP",
    "GUEST_VS_GUEST_DAMAGE_MULTIPLIER",
    "GUEST_SKILL_VS_GUEST_MULTIPLIER",
    "GUEST_SKILL_VS_TROOP_MULTIPLIER",
    "BASE_CRIT_CHANCE",
    "CRIT_DAMAGE_MULTIPLIER",
    "PREEMPTIVE_DAMAGE_REDUCTION",
    "DAMAGE_VARIANCE_MIN",
    "DAMAGE_VARIANCE_MAX",
    "GUEST_VS_TROOP_DEFENSE_CONSTANT",
    "MIN_ALLOWED_PRIORITY",
    "MAX_ALLOWED_PRIORITY",
    # Types
    "AttackSkill",
    "AttackType",
    "AttackLogEntry",
    "_SelectedAttackTargets",
    "_DamageCalculation",
    "_DamageApplication",
    # Utils
    "calculate_crit_chance",
    "calculate_dodge_chance",
    "alive",
    "determine_turn_order",
    "is_ranged_attack",
    "select_target_with_priority",
    "calculate_attack_damage",
    "process_status_effects",
    "apply_damage_results",
    "roll_loot",
    "summarize_losses",
]
