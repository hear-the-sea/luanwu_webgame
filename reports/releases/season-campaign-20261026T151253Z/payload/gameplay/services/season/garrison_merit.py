"""实际驻守时间贡献，撤离时发放一次，行军及超期等待不计入。"""

from datetime import datetime
from typing import Any

from django.utils.dateparse import parse_datetime

from .campaign_rules import campaign_rules
from .merit import grant_merit_locked


def settle_garrison_merit_locked(run: Any, now: datetime) -> None:
    if run.kind != "garrison" or run.status != "stationed" or "garrison_service" in run.result:
        return
    arrived = parse_datetime(run.result.get("stationed_at", ""))
    if arrived is None:
        return
    until = min(now, run.stationed_until or run.due_at)
    seconds = max(0, int((until - arrived).total_seconds()))
    planned = run.result.get("station_seconds")
    if planned is not None:
        seconds = min(seconds, max(0, int(planned)))
    rate = run.result.get(
        "garrison_merit_per_hour", campaign_rules(run.member.season)["expeditions"].get("garrison_merit_per_hour", 0)
    )
    receipt = grant_merit_locked(
        run.member,
        seconds * rate // 3600,
        "combat",
        now,
        event_key=f"garrison-duty:{run.pk}",
        reason="实际驻守时长贡献",
    )
    run.result = {**run.result, "garrison_service": {**receipt, "seconds": seconds, "merit_per_hour": rate}}
