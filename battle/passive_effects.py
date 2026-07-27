from __future__ import annotations

from typing import Any

from .combatants_pkg.core import BattleModifiers
from .modifier_lifecycle import (
    refresh_modifier_projections,
    register_modifier_source,
    resolve_modifier_scope,
    resolve_scoped_source_key,
)
from .simulation.report_state import snapshot_unit_state


def _modifier_source_payload(modifiers: BattleModifiers, key: str) -> dict[str, Any]:
    payload = modifiers.get(key)
    if isinstance(payload, dict):
        return payload
    payload = {}
    modifiers[key] = payload
    return payload


def _ally_units(context: dict[str, Any], actor: Any) -> list[Any]:
    if getattr(actor, "side", "") == "defender":
        units = context.get("defender_team") or []
    else:
        units = context.get("attacker_team") or []
    return [unit for unit in units if int(getattr(unit, "hp", 0) or 0) > 0]


def _state_payload(actor: Any) -> dict[str, Any]:
    state = getattr(actor, "battle_state", None)
    if isinstance(state, dict):
        return state
    state = {}
    setattr(actor, "battle_state", state)
    return state


def _modifier_payload(actor: Any) -> BattleModifiers:
    modifiers = getattr(actor, "battle_modifiers", None)
    if isinstance(modifiers, dict):
        return modifiers
    modifiers = {}
    setattr(actor, "battle_modifiers", modifiers)
    return modifiers


def _append_passive_event(event_sink: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    event_sink.append({"type": "passive", **payload})


def _effect_source_key(context: dict[str, Any]) -> str:
    raw = str(context.get("skill_key") or "").strip()
    return raw or "__direct__"


def _matches_effect_target(unit: Any, effect: dict[str, Any]) -> bool:
    if "target_template_in" in effect:
        allowed = {
            str(item or "").strip() for item in effect.get("target_template_in") or [] if str(item or "").strip()
        }
        template_key = str(getattr(unit, "template_key", "") or "").strip()
        if template_key not in allowed:
            return False

    if "target_kind_is" in effect and getattr(unit, "kind", None) != effect.get("target_kind_is"):
        return False

    return True


def _resolve_effect_targets(effect: dict[str, Any], context: dict[str, Any]) -> list[Any]:
    actor = context["actor"]
    target_scope = str(effect.get("target_scope") or "self").strip()

    if target_scope == "self":
        candidates = [actor]
    elif target_scope == "allies":
        candidates = _ally_units(context, actor)
    else:
        raise AssertionError(f"unsupported passive target_scope: {target_scope}")

    return [unit for unit in candidates if _matches_effect_target(unit, effect)]


def _record_multiplier_source(
    target_unit: Any,
    *,
    flat_key: str,
    sources_key: str,
    source_key: str,
    value: float,
    scope: str,
) -> None:
    modifiers = _modifier_payload(target_unit)
    sources = _modifier_source_payload(modifiers, sources_key)
    scoped_source_key = resolve_scoped_source_key(
        target_unit,
        scope=scope,
        container_key=sources_key,
        source_key=source_key,
    )
    sources[scoped_source_key] = float(value)
    register_modifier_source(target_unit, scope=scope, container_key=sources_key, source_key=scoped_source_key)
    refresh_modifier_projections(modifiers)


def _record_softcap_source(
    target_unit: Any,
    *,
    source_key: str,
    threshold: float,
    overflow_ratio: float,
    scope: str,
) -> None:
    modifiers = _modifier_payload(target_unit)
    sources = _modifier_source_payload(modifiers, "burst_softcap_sources")
    scoped_source_key = resolve_scoped_source_key(
        target_unit,
        scope=scope,
        container_key="burst_softcap_sources",
        source_key=source_key,
    )
    sources[scoped_source_key] = {
        "threshold": float(threshold),
        "overflow_ratio": float(overflow_ratio),
    }
    register_modifier_source(
        target_unit,
        scope=scope,
        container_key="burst_softcap_sources",
        source_key=scoped_source_key,
    )


def _record_true_damage_source(
    target_unit: Any,
    *,
    source_key: str,
    value: float,
    troop_value_multiplier: float,
    scope: str,
) -> None:
    modifiers = _modifier_payload(target_unit)
    sources = _modifier_source_payload(modifiers, "true_damage_ratio_sources")
    scoped_source_key = resolve_scoped_source_key(
        target_unit,
        scope=scope,
        container_key="true_damage_ratio_sources",
        source_key=source_key,
    )
    sources[scoped_source_key] = {
        "value": float(value),
        "troop_value_multiplier": float(troop_value_multiplier),
    }
    register_modifier_source(
        target_unit,
        scope=scope,
        container_key="true_damage_ratio_sources",
        source_key=scoped_source_key,
    )


def apply_effect(effect: dict[str, Any], context: dict[str, Any]) -> None:
    actor = context["actor"]
    event_sink = context["event_sink"]
    effect_type = str(effect.get("type") or "").strip()
    targets = _resolve_effect_targets(effect, context)
    source_key = _effect_source_key(context)
    modifier_scope = resolve_modifier_scope(
        timing=str(context.get("timing") or ""),
        effect_type=effect_type,
        explicit_scope=effect.get("scope"),
    )

    if effect_type == "heal_ratio":
        for target_unit in targets:
            base_hp = (
                int(getattr(target_unit, "max_hp", 0) or 0)
                if effect.get("max_hp_based")
                else int(getattr(target_unit, "hp", 0) or 0)
            )
            healed = max(1, int(base_hp * float(effect.get("value") or 0)))
            current_hp = int(getattr(target_unit, "hp", 0) or 0)
            max_hp = int(getattr(target_unit, "max_hp", 0) or 0)
            healed = min(healed, max(0, max_hp - current_hp))
            target_unit.hp = current_hp + healed
            if healed > 0 and effect.get("log"):
                _append_passive_event(
                    event_sink,
                    {
                        "side": getattr(target_unit, "side", ""),
                        "unit": getattr(target_unit, "name", ""),
                        "effect": str(effect.get("log_name") or effect_type),
                        "healed": healed,
                        "unit_state": snapshot_unit_state(target_unit),
                    },
                )
        return

    if effect_type == "lose_hp_ratio":
        for target_unit in targets:
            base_hp = (
                int(getattr(target_unit, "hp", 0) or 0)
                if effect.get("current_hp_based")
                else int(getattr(target_unit, "max_hp", 0) or 0)
            )
            current_hp = int(getattr(target_unit, "hp", 0) or 0)
            lost = max(1, int(base_hp * float(effect.get("value") or 0)))
            if effect.get("nonlethal"):
                lost = min(lost, max(0, current_hp - 1))
            else:
                lost = min(lost, max(0, current_hp))
            target_unit.hp = current_hp - lost
            if lost > 0 and effect.get("log"):
                _append_passive_event(
                    event_sink,
                    {
                        "side": getattr(target_unit, "side", ""),
                        "unit": getattr(target_unit, "name", ""),
                        "effect": str(effect.get("log_name") or effect_type),
                        "lost": lost,
                        "unit_state": snapshot_unit_state(target_unit),
                    },
                )
        return

    if effect_type == "modify_outgoing_damage":
        for target_unit in targets:
            _record_multiplier_source(
                target_unit,
                flat_key="outgoing_damage_multiplier",
                sources_key="outgoing_damage_multiplier_sources",
                source_key=source_key,
                value=float(effect.get("value") or 0),
                scope=str(modifier_scope),
            )
        return

    if effect_type == "modify_incoming_damage":
        for target_unit in targets:
            _record_multiplier_source(
                target_unit,
                flat_key="incoming_damage_multiplier",
                sources_key="incoming_damage_multiplier_sources",
                source_key=source_key,
                value=float(effect.get("value") or 0),
                scope=str(modifier_scope),
            )
        return

    if effect_type == "modify_target_weight":
        for target_unit in targets:
            _record_multiplier_source(
                target_unit,
                flat_key="target_weight_multiplier",
                sources_key="target_weight_multiplier_sources",
                source_key=source_key,
                value=float(effect.get("value") or 0),
                scope=str(modifier_scope),
            )
        return

    if effect_type == "add_true_damage":
        for target_unit in targets:
            _record_true_damage_source(
                target_unit,
                source_key=source_key,
                value=float(effect.get("value") or 0),
                troop_value_multiplier=float(effect.get("troop_value_multiplier") or 1.0),
                scope=str(modifier_scope),
            )
        return

    if effect_type == "set_softcap":
        for target_unit in targets:
            _record_softcap_source(
                target_unit,
                source_key=source_key,
                threshold=float(effect.get("threshold") or 0),
                overflow_ratio=float(effect.get("overflow_ratio") or 1.0),
                scope=str(modifier_scope),
            )
        return

    if effect_type == "set_reflect":
        for target_unit in targets:
            modifiers = _modifier_payload(target_unit)
            sources = _modifier_source_payload(modifiers, "reflect_sources")
            scoped_source_key = resolve_scoped_source_key(
                target_unit,
                scope=str(modifier_scope),
                container_key="reflect_sources",
                source_key=source_key,
            )
            sources.pop(scoped_source_key, None)
            sources[scoped_source_key] = {
                "ratio": float(effect.get("ratio") or 0),
                "cap": float(effect.get("cap") or 0),
            }
            register_modifier_source(
                target_unit,
                scope=str(modifier_scope),
                container_key="reflect_sources",
                source_key=scoped_source_key,
            )
            refresh_modifier_projections(modifiers)
        return

    if effect_type == "set_state":
        key = str(effect.get("key") or "").strip()
        if key:
            for target_unit in targets:
                state = _state_payload(target_unit)
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
                "unit_state": snapshot_unit_state(actor),
            },
        )
        return

    raise AssertionError(f"unsupported passive effect: {effect_type}")
