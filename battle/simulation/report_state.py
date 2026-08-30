from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _status_for_percent(percent: int) -> tuple[str, str]:
    if percent <= 0:
        return "empty", "状态耗尽"
    if percent <= 25:
        return "danger", "状态濒危"
    if percent < 50:
        return "warning", "状态偏低"
    return "healthy", "状态充足"


def snapshot_unit_state(unit: Any) -> dict[str, Any]:
    kind = str(getattr(unit, "kind", "guest") or "guest")
    if kind == "troop":
        current = max(0, int(getattr(unit, "troop_strength", 0) or 0))
        maximum = max(0, int(getattr(unit, "initial_troop_strength", 0) or 0))
    else:
        current = max(0, int(getattr(unit, "hp", 0) or 0))
        maximum = max(0, int(getattr(unit, "max_hp", 0) or 0))

    if maximum > 0:
        current = min(current, maximum)
        percent = max(0, min(100, round(current * 100 / maximum)))
    else:
        percent = 0

    status, status_label = _status_for_percent(percent)
    return {
        "kind": kind,
        "side": str(getattr(unit, "side", "") or ""),
        "current": current,
        "maximum": maximum,
        "percent": percent,
        "status": status,
        "status_label": status_label,
    }


def _snapshot_lineup_side(units: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    guests: list[dict[str, Any]] = []
    troops: list[dict[str, Any]] = []
    city_defenses: list[dict[str, Any]] = []

    for unit in units:
        if int(getattr(unit, "hp", 0) or 0) <= 0:
            continue

        kind = str(getattr(unit, "kind", "") or "")
        if kind == "guest":
            guest = {
                "name": str(getattr(unit, "name", "") or "门客"),
                "template_key": str(getattr(unit, "template_key", "") or ""),
                "guest_id": getattr(unit, "guest_id", None),
                "current_hp": max(0, int(getattr(unit, "hp", 0) or 0)),
                "max_hp": max(0, int(getattr(unit, "max_hp", 0) or 0)),
            }
            agility = getattr(unit, "agility", None)
            if agility is not None:
                guest["agility"] = agility
            guests.append(guest)
            continue

        if kind == "city_defense":
            city_defenses.append(
                {
                    "name": str(getattr(unit, "name", "") or "城防"),
                    "template_key": str(getattr(unit, "template_key", "") or ""),
                    "level": max(0, int(getattr(unit, "level", 0) or 0)),
                    "hp": max(0, int(getattr(unit, "hp", 0) or 0)),
                    "max_hp": max(0, int(getattr(unit, "max_hp", 0) or 0)),
                }
            )
            continue

        if kind != "troop":
            continue
        count = max(0, int(getattr(unit, "troop_strength", 0) or 0))
        if count <= 0:
            continue
        troops.append(
            {
                "name": str(getattr(unit, "name", "") or "护院"),
                "template_key": str(getattr(unit, "template_key", "") or ""),
                "count": count,
            }
        )

    return {"guests": guests, "city_defenses": city_defenses, "troops": troops}


def snapshot_round_lineups(
    attacker_team: Iterable[Any],
    defender_team: Iterable[Any],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """记录本回合任何行动发生前，攻守双方仍可战斗的门客与护院。"""

    return {
        "attacker": _snapshot_lineup_side(attacker_team),
        "defender": _snapshot_lineup_side(defender_team),
    }
