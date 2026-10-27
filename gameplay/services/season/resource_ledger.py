"""阵营资源流量审计；纯更新JSON，调用方与实物变动一并保存。"""

from datetime import datetime
from typing import Any

from django.utils import timezone


def record_flow(state: Any, reason: str, delta: dict[str, int], now: datetime) -> None:
    changes = {key: int(value) for key, value in delta.items() if value and key in {"grain", "wood", "iron"}}
    if not changes:
        return
    data = dict(state.economy_state)
    totals = data.setdefault("flow_totals", {})
    category = totals.setdefault(reason, {})
    for key, value in changes.items():
        category[key] = category.get(key, 0) + value
    day = timezone.localdate(now).isoformat()
    daily = data.setdefault("daily_flows", {})
    line = daily.setdefault(day, {}).setdefault(reason, {})
    for key, value in changes.items():
        line[key] = line.get(key, 0) + value
    data["daily_flows"] = {key: daily[key] for key in sorted(daily)[-7:]}
    state.economy_state = data
