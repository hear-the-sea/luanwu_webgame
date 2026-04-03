from __future__ import annotations

from typing import Any

from core.config import GUEST

ARENA_COOP_BOSS_TEMPLATE_KEY = "arena_gl_top_zhang_wuji_boss"
ARENA_COOP_YANG_XIAO_TEMPLATE_KEY = "arena_gl_top_yang_xiao_guard"
ARENA_COOP_WEI_YIXIAO_TEMPLATE_KEY = "arena_gl_top_wei_yixiao_guard"
ARENA_COOP_FIVE_FLAGS_FRONT_TEMPLATE_KEY = "arena_gl_top_five_flags_elite_front"
ARENA_COOP_FIVE_FLAGS_REAR_TEMPLATE_KEY = "arena_gl_top_five_flags_elite_rear"

ARENA_COOP_FIVE_FLAGS_TEMPLATE_KEYS = {
    ARENA_COOP_FIVE_FLAGS_FRONT_TEMPLATE_KEY,
    ARENA_COOP_FIVE_FLAGS_REAR_TEMPLATE_KEY,
}
ARENA_COOP_GUARD_TEMPLATE_KEYS = {
    ARENA_COOP_YANG_XIAO_TEMPLATE_KEY,
    ARENA_COOP_WEI_YIXIAO_TEMPLATE_KEY,
    *ARENA_COOP_FIVE_FLAGS_TEMPLATE_KEYS,
}

ARENA_COOP_ENEMY_FINAL_STATS: dict[str, dict[str, int]] = {
    ARENA_COOP_BOSS_TEMPLATE_KEY: {
        "level": 90,
        "force": 1320,
        "intellect": 980,
        "defense_stat": 760,
        "agility": 320,
        "luck": 180,
        "final_hp": 300000,
    },
    ARENA_COOP_YANG_XIAO_TEMPLATE_KEY: {
        "level": 90,
        "force": 980,
        "intellect": 760,
        "defense_stat": 620,
        "agility": 260,
        "luck": 130,
        "final_hp": 200000,
    },
    ARENA_COOP_WEI_YIXIAO_TEMPLATE_KEY: {
        "level": 90,
        "force": 900,
        "intellect": 550,
        "defense_stat": 560,
        "agility": 340,
        "luck": 150,
        "final_hp": 200000,
    },
    ARENA_COOP_FIVE_FLAGS_FRONT_TEMPLATE_KEY: {
        "level": 90,
        "force": 780,
        "intellect": 420,
        "defense_stat": 520,
        "agility": 210,
        "luck": 110,
        "final_hp": 200000,
    },
    ARENA_COOP_FIVE_FLAGS_REAR_TEMPLATE_KEY: {
        "level": 90,
        "force": 720,
        "intellect": 460,
        "defense_stat": 520,
        "agility": 220,
        "luck": 110,
        "final_hp": 200000,
    },
}

ARENA_COOP_PHASE_MESSAGES = {
    2: "明教号令震荡全场，张无忌踏入二阶段",
    3: "圣火狂势彻底爆发，张无忌踏入三阶段",
}
ARENA_COOP_GUARD_STATE_KEYS = {
    "arena_coop_boss_alive",
    "arena_coop_phase_1",
    "arena_coop_phase_2_plus",
}


def _template_key(value: Any) -> str:
    return str(getattr(value, "template_key", "") or getattr(getattr(value, "template", None), "key", "") or "").strip()


def is_arena_coop_enemy_template(template_key: str) -> bool:
    return str(template_key or "").strip() in ARENA_COOP_ENEMY_FINAL_STATS


def configure_arena_coop_enemy_guest(guest: Any) -> bool:
    template_key = _template_key(guest)
    profile = ARENA_COOP_ENEMY_FINAL_STATS.get(template_key)
    if not profile:
        return False

    setattr(guest, "level", int(profile["level"]))
    setattr(guest, "force", int(profile["force"]))
    setattr(guest, "intellect", int(profile["intellect"]))
    setattr(guest, "defense_stat", int(profile["defense_stat"]))
    setattr(guest, "agility", int(profile["agility"]))
    setattr(guest, "luck", int(profile["luck"]))

    template = getattr(guest, "template", None)
    base_hp = int(getattr(template, "base_hp", 0) or 0)
    defense_stat = int(profile["defense_stat"])
    final_hp = int(profile["final_hp"])
    hp_bonus = max(0, final_hp - base_hp - defense_stat * int(GUEST.DEFENSE_TO_HP_MULTIPLIER))
    setattr(guest, "hp_bonus", hp_bonus)
    setattr(guest, "current_hp", final_hp)
    return True


def _combat_state(unit: Any) -> dict[str, Any]:
    state = getattr(unit, "battle_state", None)
    if isinstance(state, dict):
        return state
    state = {}
    setattr(unit, "battle_state", state)
    return state


def _combat_modifiers(unit: Any) -> dict[str, float]:
    modifiers = getattr(unit, "battle_modifiers", None)
    if isinstance(modifiers, dict):
        return modifiers
    modifiers = {}
    setattr(unit, "battle_modifiers", modifiers)
    return modifiers


def _clear_arena_coop_guard_state(unit: Any) -> None:
    state = _combat_state(unit)
    for key in ARENA_COOP_GUARD_STATE_KEYS:
        state.pop(key, None)


def _has_action_before_heal_passive(unit: Any) -> bool:
    for skill in getattr(unit, "skills", []) or []:
        if str(skill.get("kind") or "") != "passive":
            continue
        config = skill.get("passive_config") or {}
        for trigger in config.get("triggers") or []:
            if str(trigger.get("timing") or "") != "action_before":
                continue
            for effect in trigger.get("effects") or []:
                if str(effect.get("type") or "") == "heal_ratio":
                    return True
    return False


def _resolve_boss_phase(boss: Any) -> int:
    if getattr(boss, "max_hp", 0) <= 0:
        return 1
    ratio = float(getattr(boss, "hp", 0) or 0) / float(getattr(boss, "max_hp", 1) or 1)
    if ratio <= 0.40:
        return 3
    if ratio <= 0.70:
        return 2
    return 1


def sync_arena_coop_combat_state(
    attacker_team: list[Any], defender_team: list[Any], round_no: int
) -> list[dict[str, Any]]:
    del attacker_team, round_no

    defenders = [unit for unit in defender_team if is_arena_coop_enemy_template(_template_key(unit))]
    if not defenders:
        return []

    for unit in defenders:
        _combat_modifiers(unit).clear()
        _clear_arena_coop_guard_state(unit)

    boss = next((unit for unit in defenders if _template_key(unit) == ARENA_COOP_BOSS_TEMPLATE_KEY), None)
    if boss is None or getattr(boss, "hp", 0) <= 0:
        return []

    alive_guards = [unit for unit in defenders if unit is not boss and getattr(unit, "hp", 0) > 0]
    phase = _resolve_boss_phase(boss)

    boss_state = _combat_state(boss)
    previous_phase = boss_state.get("arena_coop_phase")
    boss_state["arena_coop_phase"] = phase

    for guard in alive_guards:
        guard_state = _combat_state(guard)
        guard_state["arena_coop_boss_alive"] = True
        if phase == 1:
            guard_state["arena_coop_phase_1"] = True
        else:
            guard_state["arena_coop_phase_2_plus"] = True

    if previous_phase is None or int(previous_phase) >= phase:
        return []

    return [
        {
            "actor": getattr(boss, "name", "张无忌"),
            "side": getattr(boss, "side", "defender"),
            "status": "phase_shift",
            "message": ARENA_COOP_PHASE_MESSAGES[phase],
        }
    ]


def try_trigger_arena_coop_pre_action_heal(actor: Any) -> dict[str, Any] | None:
    if getattr(actor, "hp", 0) <= 0:
        return None
    if _has_action_before_heal_passive(actor):
        return None

    modifiers = _combat_modifiers(actor)
    heal_ratio = float(modifiers.get("self_heal_ratio_on_action", 0.0) or 0.0)
    if heal_ratio <= 0:
        return None

    max_hp = int(getattr(actor, "max_hp", 0) or 0)
    current_hp = int(getattr(actor, "hp", 0) or 0)
    missing_hp = max(0, max_hp - current_hp)
    if missing_hp <= 0:
        return None

    healed = max(1, int(max_hp * heal_ratio))
    healed = min(healed, missing_hp)
    actor.hp = current_hp + healed
    return {
        "unit": getattr(actor, "name", "张无忌"),
        "side": getattr(actor, "side", "defender"),
        "healed": healed,
        "new_hp": actor.hp,
        "effect": "九阳护体",
    }


def adjust_arena_coop_damage(actor: Any, target: Any, damage: int) -> int:
    if damage <= 0:
        return 0

    actor_modifiers = _combat_modifiers(actor)
    target_modifiers = _combat_modifiers(target)

    adjusted = int(damage * float(actor_modifiers.get("outgoing_damage_multiplier", 1.0) or 1.0))
    softcap_threshold = int(target_modifiers.get("burst_softcap_threshold", 0) or 0)
    overflow_ratio = float(target_modifiers.get("burst_softcap_overflow_ratio", 1.0) or 1.0)
    if softcap_threshold > 0 and adjusted > softcap_threshold:
        adjusted = softcap_threshold + int((adjusted - softcap_threshold) * overflow_ratio)

    adjusted = int(adjusted * float(target_modifiers.get("incoming_damage_multiplier", 1.0) or 1.0))
    return max(1, adjusted)


def get_arena_coop_reflect_values(target: Any) -> tuple[float, int]:
    modifiers = _combat_modifiers(target)
    reflect_ratio = float(modifiers.get("reflect_ratio", 0.0) or 0.0)
    reflect_cap = int(modifiers.get("reflect_cap", 0) or 0)
    return reflect_ratio, reflect_cap
