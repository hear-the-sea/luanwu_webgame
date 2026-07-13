from __future__ import annotations

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
