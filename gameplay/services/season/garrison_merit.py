"""实际驻守时间贡献；按自然日与赛季周分段，行军及超期等待不计入。"""

from datetime import datetime, time, timedelta
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .campaign_rules import campaign_rules
from .merit import grant_merit_locked


def _duty_bounds(run: Any, now: datetime) -> tuple[datetime, datetime] | None:
    if run.kind != "garrison" or run.status != "stationed":
        return None
    arrived = parse_datetime(run.result.get("stationed_at", ""))
    if arrived is None:
        return None
    until = min(now, run.stationed_until or run.due_at)
    planned = run.result.get("station_seconds")
    if planned is not None:
        until = min(until, arrived + timedelta(seconds=max(0, int(planned))))
    return arrived, max(arrived, until)


def _rate(run: Any) -> int:
    return run.result.get(
        "garrison_merit_per_hour", campaign_rules(run.member.season)["expeditions"].get("garrison_merit_per_hour", 0)
    )


def garrison_merit_preview(run: Any, now: datetime) -> int:
    """只读估算已驻守贡献；最终以补给与当日贡献上限结算。"""
    bounds = _duty_bounds(run, now)
    if bounds is None:
        return 0
    arrived, until = bounds
    return max(0, int((until - arrived).total_seconds())) * _rate(run) // 3600


def _segments(run: Any, arrived: datetime, until: datetime) -> list[tuple[datetime, int]]:
    """按自然日与赛季周切分，累积取整避免跨界损失零碎时间。"""
    if until <= arrived:
        return []
    local_tz = timezone.get_current_timezone()
    start = run.member.season.starts_at
    week_seconds = 7 * 86400
    rate = _rate(run)
    cursor = arrived
    assigned = 0
    segments = []
    while cursor < until:
        next_date = timezone.localdate(cursor, timezone=local_tz) + timedelta(days=1)
        midnight = datetime.combine(next_date, time.min, tzinfo=local_tz)
        weeks = max(0, int((cursor - start).total_seconds()) // week_seconds + 1)
        next_week = start + timedelta(seconds=weeks * week_seconds)
        end = min(until, midnight, next_week) if next_week > cursor else min(until, midnight)
        accrued = max(0, int((end - arrived).total_seconds())) * rate // 3600
        segments.append((end - timedelta(microseconds=1), accrued - assigned))
        assigned = accrued
        cursor = end
    return segments


def settle_garrison_merit_locked(run: Any, now: datetime) -> None:
    if "garrison_service" in run.result:
        return
    bounds = _duty_bounds(run, now)
    if bounds is None:
        return
    arrived, until = bounds
    seconds = max(0, int((until - arrived).total_seconds()))
    segments = _segments(run, arrived, until)
    if not segments:
        segments = [(now, 0)]
    receipts = []
    for index, (stamp, amount) in enumerate(segments):
        # 单段保留已有凭证键；跨界时用确定性的段序号保证重试幂等。
        key = f"garrison-duty:{run.pk}"
        if len(segments) > 1:
            key += f":{index}:{stamp.isoformat()}"
        receipts.append(
            grant_merit_locked(run.member, amount, "combat", stamp, event_key=key, reason="实际驻守时长贡献")
        )
    receipt = {
        **receipts[-1],
        "merit": sum(row["merit"] for row in receipts),
        "requested_merit": sum(row["requested_merit"] for row in receipts),
        "seconds": seconds,
        "merit_per_hour": _rate(run),
    }
    if len(receipts) > 1:
        receipt["segments"] = receipts
    run.result = {**run.result, "garrison_service": receipt}
