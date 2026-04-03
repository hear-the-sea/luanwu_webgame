import random
from types import SimpleNamespace

from battle.passive_conditions import conditions_match
from battle.passive_effects import apply_effect
from battle.passives import run_passives_for_timing
from battle.simulation.attack_execution import perform_attack
from battle.simulation.battle_flow import _resolve_standard_round, simulate_battle
from battle.status_manager import prepare_combatants_for_round

__all__ = [
    "_resolve_standard_round",
    "apply_effect",
    "conditions_match",
    "make_unit",
    "perform_attack",
    "prepare_combatants_for_round",
    "random",
    "run_passives_for_timing",
    "simulate_battle",
]


def make_unit(**kwargs):
    defaults = {
        "name": "Tester",
        "side": "attacker",
        "kind": "guest",
        "hp": 1000,
        "max_hp": 1000,
        "template_key": "tester_tpl",
        "is_boss": False,
        "battle_modifiers": {},
        "battle_state": {},
        "skills": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)
