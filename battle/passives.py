from __future__ import annotations

from typing import Any

from .passive_conditions import conditions_match
from .passive_effects import apply_effect


def _trigger_chance_matches(trigger: dict[str, Any], rng: Any) -> bool:
    raw_chance = trigger.get("chance", 1.0)
    if raw_chance is None or isinstance(raw_chance, bool):
        raise AssertionError(f"invalid passive trigger chance: {raw_chance!r}")
    try:
        chance = float(raw_chance)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid passive trigger chance: {raw_chance!r}") from exc

    if chance <= 0:
        return False
    if chance >= 1:
        return True
    return bool(rng.random() < chance)


def run_passives_for_timing(
    timing: str,
    *,
    actor: Any,
    target: Any,
    attacker_team: list[Any],
    defender_team: list[Any],
    round_no: int,
    event_sink: list[dict[str, Any]],
    rng: Any,
) -> None:
    context = {
        "timing": timing,
        "actor": actor,
        "target": target,
        "attacker_team": attacker_team,
        "defender_team": defender_team,
        "round_no": round_no,
        "event_sink": event_sink,
        "rng": rng,
    }

    for skill in getattr(actor, "skills", []) or []:
        if str(skill.get("kind") or "") != "passive":
            continue
        config = skill.get("passive_config") or {}
        context["skill_key"] = str(skill.get("key") or "").strip() or "__direct__"
        for trigger in config.get("triggers") or []:
            if str(trigger.get("timing") or "") != timing:
                continue
            if not conditions_match(trigger.get("conditions") or {}, context):
                continue
            if not _trigger_chance_matches(trigger, rng):
                continue
            for effect in trigger.get("effects") or []:
                apply_effect(effect, context)
