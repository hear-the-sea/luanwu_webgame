from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _hp_ratio(unit: Any) -> float:
    max_hp = float(getattr(unit, "max_hp", 0) or 0)
    if max_hp <= 0:
        return 0.0
    return float(getattr(unit, "hp", 0) or 0) / max_hp


def _state_payload(actor: Any) -> dict[str, Any]:
    state = getattr(actor, "battle_state", None)
    if isinstance(state, dict):
        return state
    state = {}
    setattr(actor, "battle_state", state)
    return state


def _ally_units(context: dict[str, Any], actor: Any) -> list[Any]:
    if getattr(actor, "side", "") == "defender":
        units = context.get("defender_team") or []
    else:
        units = context.get("attacker_team") or []
    return list(units)


def _alive_template_counts(units: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for unit in units:
        if int(getattr(unit, "hp", 0) or 0) <= 0:
            continue
        key = str(getattr(unit, "template_key", "") or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _matches_template_count_rule(raw_rule: Any, counts: Mapping[str, int], *, op: str) -> bool:
    if isinstance(raw_rule, Mapping):
        for raw_key, raw_expected in raw_rule.items():
            key = str(raw_key or "").strip()
            expected = int(raw_expected or 0)
            actual = counts.get(key, 0)
            if op == "gte" and actual < expected:
                return False
            if op == "lte" and actual > expected:
                return False
        return True

    expected = int(raw_rule or 0)
    total_alive = sum(counts.values())
    if op == "gte":
        return total_alive >= expected
    return total_alive <= expected


def conditions_match(conditions: dict[str, Any], context: dict[str, Any]) -> bool:
    actor = context["actor"]
    target = context.get("target")
    actor_ratio = _hp_ratio(actor)
    state = _state_payload(actor)

    if "hp_ratio_lte" in conditions and actor_ratio > float(conditions["hp_ratio_lte"]):
        return False
    if "hp_ratio_gte" in conditions and actor_ratio < float(conditions["hp_ratio_gte"]):
        return False
    if "self_is_boss" in conditions and bool(getattr(actor, "is_boss", False)) is not bool(conditions["self_is_boss"]):
        return False

    if "self_template_in" in conditions:
        template_key = str(getattr(actor, "template_key", "") or "").strip()
        allowed = {str(item or "").strip() for item in conditions["self_template_in"]}
        if template_key not in allowed:
            return False

    if "target_kind_is" in conditions and getattr(target, "kind", None) != conditions["target_kind_is"]:
        return False

    if "state_present" in conditions:
        required_keys = conditions["state_present"]
        if isinstance(required_keys, str):
            required_keys = [required_keys]
        for key in required_keys:
            if str(key) not in state:
                return False

    if "state_absent" in conditions:
        absent_keys = conditions["state_absent"]
        if isinstance(absent_keys, str):
            absent_keys = [absent_keys]
        for key in absent_keys:
            if str(key) in state:
                return False

    ally_counts = _alive_template_counts(_ally_units(context, actor))
    if "ally_alive_template_count_gte" in conditions and not _matches_template_count_rule(
        conditions["ally_alive_template_count_gte"], ally_counts, op="gte"
    ):
        return False
    if "ally_alive_template_count_lte" in conditions and not _matches_template_count_rule(
        conditions["ally_alive_template_count_lte"], ally_counts, op="lte"
    ):
        return False

    return True
