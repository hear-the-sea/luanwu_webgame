from __future__ import annotations

from collections.abc import Iterable
from typing import Any

MODIFIER_SCOPE_BATTLE = "battle"
MODIFIER_SCOPE_ROUND = "round"
MODIFIER_SCOPE_ACTION = "action"
VALID_MODIFIER_SCOPES = {
    MODIFIER_SCOPE_BATTLE,
    MODIFIER_SCOPE_ROUND,
    MODIFIER_SCOPE_ACTION,
}

MODIFIER_EFFECT_TYPES = {
    "add_true_damage",
    "modify_outgoing_damage",
    "modify_incoming_damage",
    "modify_target_weight",
    "set_softcap",
    "set_reflect",
}

DEFAULT_SCOPE_BY_TIMING = {
    "battle_start": MODIFIER_SCOPE_BATTLE,
    "round_start": MODIFIER_SCOPE_ROUND,
    "action_before": MODIFIER_SCOPE_ACTION,
    "action_end": MODIFIER_SCOPE_ACTION,
    "attack_before": MODIFIER_SCOPE_ACTION,
    "hit_taken": MODIFIER_SCOPE_ACTION,
    "attack_after": MODIFIER_SCOPE_ACTION,
}
ALLOWED_SCOPES_BY_TIMING = {
    "battle_start": {MODIFIER_SCOPE_BATTLE},
    "round_start": {MODIFIER_SCOPE_BATTLE, MODIFIER_SCOPE_ROUND},
    "action_before": VALID_MODIFIER_SCOPES,
    "action_end": VALID_MODIFIER_SCOPES,
    "attack_before": VALID_MODIFIER_SCOPES,
    "hit_taken": VALID_MODIFIER_SCOPES,
    "attack_after": VALID_MODIFIER_SCOPES,
}

_REGISTRY_STATE_KEY = "_modifier_scope_registry"
_MULTIPLIER_PROJECTIONS = {
    "outgoing_damage_multiplier_sources": "outgoing_damage_multiplier",
    "incoming_damage_multiplier_sources": "incoming_damage_multiplier",
    "target_weight_multiplier_sources": "target_weight_multiplier",
}


def resolve_modifier_scope(*, timing: str, effect_type: str, explicit_scope: object = None) -> str | None:
    if effect_type not in MODIFIER_EFFECT_TYPES:
        if explicit_scope is not None:
            raise AssertionError(f"passive effect {effect_type!r} does not support modifier scope")
        return None

    if explicit_scope is None:
        scope = DEFAULT_SCOPE_BY_TIMING.get(timing) or MODIFIER_SCOPE_ROUND
    elif not isinstance(explicit_scope, str) or not explicit_scope.strip():
        raise AssertionError(f"invalid passive modifier scope: timing={timing!r} scope={explicit_scope!r}")
    else:
        scope = explicit_scope.strip()
    if scope not in VALID_MODIFIER_SCOPES:
        raise AssertionError(f"invalid passive modifier scope: timing={timing!r} scope={scope!r}")
    allowed_scopes = ALLOWED_SCOPES_BY_TIMING.get(timing)
    if allowed_scopes is not None and scope not in allowed_scopes:
        raise AssertionError(f"unsupported passive modifier scope: timing={timing!r} scope={scope!r}")
    return scope


def _modifier_registry(unit: Any) -> dict[str, dict[str, set[str]]]:
    state = getattr(unit, "battle_state", None)
    if not isinstance(state, dict):
        state = {}
        setattr(unit, "battle_state", state)
    registry = state.get(_REGISTRY_STATE_KEY)
    if not isinstance(registry, dict):
        registry = {}
        state[_REGISTRY_STATE_KEY] = registry
    return registry


def register_modifier_source(unit: Any, *, scope: str, container_key: str, source_key: str) -> None:
    if scope not in VALID_MODIFIER_SCOPES:
        raise AssertionError(f"invalid modifier scope: {scope!r}")
    registry = _modifier_registry(unit)
    scoped = registry.setdefault(scope, {})
    scoped.setdefault(container_key, set()).add(source_key)


def resolve_scoped_source_key(unit: Any, *, scope: str, container_key: str, source_key: str) -> str:
    """Keep legacy source names unless the same source is owned by another scope."""

    registry = _modifier_registry(unit)
    for registered_scope, scoped in registry.items():
        registered_sources = scoped.get(container_key, set())
        if source_key in registered_sources:
            if registered_scope == scope:
                return source_key
            return f"{source_key}@{scope}"
    return source_key


def refresh_modifier_projections(modifiers: dict[str, Any]) -> None:
    for sources_key, flat_key in _MULTIPLIER_PROJECTIONS.items():
        sources = modifiers.get(sources_key)
        if not isinstance(sources, dict):
            continue
        if not sources:
            modifiers.pop(sources_key, None)
            modifiers.pop(flat_key, None)
            continue
        total = 1.0
        for value in sources.values():
            total *= float(value or 1.0)
        modifiers[flat_key] = total

    reflect_sources = modifiers.get("reflect_sources")
    if isinstance(reflect_sources, dict) and not reflect_sources:
        modifiers.pop("reflect_sources", None)
        modifiers.pop("reflect_ratio", None)
        modifiers.pop("reflect_cap", None)
    elif isinstance(reflect_sources, dict):
        latest = next(reversed(reflect_sources.values()))
        payload = latest if isinstance(latest, dict) else {}
        modifiers["reflect_ratio"] = float(payload.get("ratio", 0.0) or 0.0)
        modifiers["reflect_cap"] = float(payload.get("cap", 0.0) or 0.0)

    for sources_key in ("burst_softcap_sources", "true_damage_ratio_sources"):
        sources = modifiers.get(sources_key)
        if isinstance(sources, dict) and not sources:
            modifiers.pop(sources_key, None)


def clear_modifier_scope(unit: Any, scope: str) -> None:
    registry = _modifier_registry(unit)
    scoped = registry.pop(scope, {})
    modifiers = getattr(unit, "battle_modifiers", None)
    if not isinstance(modifiers, dict):
        return

    for container_key, source_keys in scoped.items():
        container = modifiers.get(container_key)
        if not isinstance(container, dict):
            continue
        for source_key in source_keys:
            container.pop(source_key, None)
    refresh_modifier_projections(modifiers)


def clear_round_and_action_modifiers(unit: Any) -> None:
    clear_modifier_scope(unit, MODIFIER_SCOPE_ROUND)
    clear_modifier_scope(unit, MODIFIER_SCOPE_ACTION)


def clear_action_modifiers(units: Iterable[Any]) -> None:
    for unit in units:
        clear_modifier_scope(unit, MODIFIER_SCOPE_ACTION)
