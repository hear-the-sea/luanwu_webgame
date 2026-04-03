from __future__ import annotations

from typing import Any


def _state_payload(actor: Any) -> dict[str, Any]:
    state = getattr(actor, "battle_state", None)
    if isinstance(state, dict):
        return state
    state = {}
    setattr(actor, "battle_state", state)
    return state


def _modifier_payload(actor: Any) -> dict[str, float]:
    modifiers = getattr(actor, "battle_modifiers", None)
    if isinstance(modifiers, dict):
        return modifiers
    modifiers = {}
    setattr(actor, "battle_modifiers", modifiers)
    return modifiers


def _append_passive_event(event_sink: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    event_sink.append({"type": "passive", **payload})


def apply_effect(effect: dict[str, Any], context: dict[str, Any]) -> None:
    actor = context["actor"]
    event_sink = context["event_sink"]
    modifiers = _modifier_payload(actor)
    state = _state_payload(actor)
    effect_type = str(effect.get("type") or "").strip()

    if effect_type == "heal_ratio":
        base_hp = (
            int(getattr(actor, "max_hp", 0) or 0) if effect.get("max_hp_based") else int(getattr(actor, "hp", 0) or 0)
        )
        healed = max(1, int(base_hp * float(effect.get("value") or 0)))
        current_hp = int(getattr(actor, "hp", 0) or 0)
        max_hp = int(getattr(actor, "max_hp", 0) or 0)
        healed = min(healed, max(0, max_hp - current_hp))
        actor.hp = current_hp + healed
        if healed > 0 and effect.get("log"):
            _append_passive_event(
                event_sink,
                {
                    "side": getattr(actor, "side", ""),
                    "unit": getattr(actor, "name", ""),
                    "effect": str(effect.get("log_name") or effect_type),
                    "healed": healed,
                },
            )
        return

    if effect_type == "modify_outgoing_damage":
        modifiers["outgoing_damage_multiplier"] = float(effect.get("value") or 0)
        return

    if effect_type == "modify_incoming_damage":
        modifiers["incoming_damage_multiplier"] = float(effect.get("value") or 0)
        return

    if effect_type == "set_softcap":
        modifiers["burst_softcap_threshold"] = float(effect.get("threshold") or 0)
        modifiers["burst_softcap_overflow_ratio"] = float(effect.get("overflow_ratio") or 1.0)
        return

    if effect_type == "set_reflect":
        modifiers["reflect_ratio"] = float(effect.get("ratio") or 0)
        modifiers["reflect_cap"] = float(effect.get("cap") or 0)
        return

    if effect_type == "set_state":
        key = str(effect.get("key") or "").strip()
        if key:
            state[key] = effect.get("value")
        return

    if effect_type == "emit_log":
        _append_passive_event(
            event_sink,
            {
                "side": getattr(actor, "side", ""),
                "unit": getattr(actor, "name", ""),
                "effect": str(effect.get("log_name") or ""),
                "message": str(effect.get("message") or ""),
            },
        )
        return

    raise AssertionError(f"unsupported passive effect: {effect_type}")
