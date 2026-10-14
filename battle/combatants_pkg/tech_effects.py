"""
Tech effects builder for combat units.
"""

from __future__ import annotations

from typing import Callable, Dict

from core.utils import safe_int


def _add_effect_if_positive(effects: Dict[str, float], key: str, value: float) -> bool:
    if value <= 0:
        return False
    effects[key] = value
    return True


def _build_dao_effects(bonus: Callable[[str], float], effects: Dict[str, float]) -> None:
    _add_effect_if_positive(effects, "double_strike_chance", bonus("double_strike_chance"))


def _build_qiang_effects(bonus: Callable[[str], float], effects: Dict[str, float]) -> None:
    _add_effect_if_positive(effects, "siege_damage", bonus("siege_damage"))
    if _add_effect_if_positive(effects, "counter_attack_chance", bonus("counter_attack_chance")):
        effects["counter_attack_damage"] = 0.30


def _build_jian_effects(bonus: Callable[[str], float], effects: Dict[str, float]) -> None:
    reflect = bonus("damage_reflect")
    if reflect > 0:
        effects["damage_reflect"] = 0.10 + reflect

    preempt = bonus("preemptive_strike")
    if preempt > 0:
        effects["preemptive_damage"] = 0.50 + preempt


def _build_quan_effects(bonus: Callable[[str], float], effects: Dict[str, float]) -> None:
    _add_effect_if_positive(effects, "ranged_defense", bonus("ranged_defense"))
    if _add_effect_if_positive(effects, "battle_heal_chance", bonus("battle_heal_chance")):
        effects["battle_heal_amount"] = 0.10


def _build_gong_effects(bonus: Callable[[str], float], effects: Dict[str, float]) -> None:
    extra_range = bonus("extra_range")
    if extra_range > 0:
        effects["extra_range_damage"] = 0.35 + extra_range

    _add_effect_if_positive(effects, "melee_attack_bonus", bonus("melee_attack"))


def build_tech_effects(
    troop_class: str,
    tech_levels: Dict[str, int],
) -> Dict[str, float]:
    """
    Build tech effects for a troop class.

    Args:
        troop_class: Troop class (dao/qiang/jian/quan/gong)
        tech_levels: Technology levels dict

    Returns:
        Special effect parameters dict
    """
    from core.game_data.technology import get_tech_bonus_from_levels

    effects: Dict[str, float] = {}

    def _bonus(effect: str) -> float:
        return get_tech_bonus_from_levels(tech_levels, effect, troop_class)

    builders: Dict[str, Callable[[Callable[[str], float], Dict[str, float]], None]] = {
        "dao": _build_dao_effects,
        "qiang": _build_qiang_effects,
        "jian": _build_jian_effects,
        "quan": _build_quan_effects,
        "gong": _build_gong_effects,
    }
    builder = builders.get(troop_class)
    if builder is not None:
        builder(_bonus, effects)

    if troop_class == "quan":
        ranged_level = safe_int(tech_levels.get("quan_vs_ranged", 0), default=0)
        if ranged_level >= 10:
            effects["ranged_damage_reduction"] = 0.30
        elif ranged_level >= 5:
            effects["ranged_damage_reduction"] = 0.20
        else:
            effects["ranged_damage_reduction"] = 0.10

    return effects
