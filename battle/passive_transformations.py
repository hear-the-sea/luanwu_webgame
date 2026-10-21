"""仅作用于当前战斗单位的狂暴阶段转换。"""

from __future__ import annotations

from typing import Any


def validate_enrage_effect(effect: dict[str, Any]) -> None:
    for key, minimum in (("luck_bonus", 0), ("targets", 1)):
        value = effect.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise AssertionError(f"enrage {key} must be an integer of at least {minimum}")
    if not isinstance(effect.get("skill_key"), str) or not effect["skill_key"].strip():
        raise AssertionError("enrage skill_key must be a non-empty string")


def apply_enrage_effect(unit: Any, effect: dict[str, Any], *, source_key: str) -> bool:
    validate_enrage_effect(effect)
    if unit.hp <= 0:
        return False
    state_key = f"enrage:{source_key}"
    if unit.battle_state.get(state_key):
        return False
    unit.luck += effect["luck_bonus"]
    # 技能载荷可能被其他单位共享，只替换当前单位的副本。
    unit.skills = [
        (
            {**skill, "targets": effect["targets"]}
            if skill.get("key") == effect["skill_key"] and skill.get("kind", "active") == "active"
            else skill
        )
        for skill in unit.skills
    ]
    unit.battle_state[state_key] = True
    return True
