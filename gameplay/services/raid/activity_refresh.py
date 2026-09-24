from __future__ import annotations

from typing import Any, Callable

from .combat import refresh_raid_runs
from .scout import refresh_scout_records


def refresh_raid_activity(
    manor: Any,
    *,
    prefer_async: bool = True,
    refresh_scout_records_func: Callable[..., int | None] = refresh_scout_records,
    refresh_raid_runs_func: Callable[..., int | None] = refresh_raid_runs,
) -> int:
    """显式触发侦察/踢馆补偿刷新。"""
    scout_refreshed = refresh_scout_records_func(manor, prefer_async=prefer_async) or 0
    raid_refreshed = refresh_raid_runs_func(manor, prefer_async=prefer_async) or 0
    return int(scout_refreshed) + int(raid_refreshed)
