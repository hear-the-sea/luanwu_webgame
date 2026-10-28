"""统一计算普通出征与集结所使用的每日次数。"""

from datetime import date
from typing import Any

from django.utils import timezone


def daily_dispatches_used(member: Any, day: date) -> int:
    spent = member.actions.filter(kind__in=("dispatch", "rally_create", "rally_join"), date=day).count()
    refunded = member.actions.filter(kind="dispatch_refund", date=day).count()
    return max(0, spent - refunded)


def refund_unused_quota_locked(run: Any, now: Any) -> None:
    from uuid import uuid4

    from gameplay.models import SeasonAction, SeasonClaim

    member = run.member
    key = f"unused-dispatch:{run.pk}"
    if member.claims.filter(key=key).exists():
        return
    member.refresh_from_db(fields=["troop_date", "troops_drawn"])
    day = (
        date.fromisoformat(run.result["quota_date"])
        if run.result.get("quota_date")
        else timezone.localdate(run.launched_at)
    )
    if member.troop_date == day:
        member.troops_drawn = max(0, member.troops_drawn - sum(run.initial_troops.values()))
        member.save(update_fields=["troops_drawn"])
    SeasonClaim.objects.create(member=member, key=key)
    SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="unused-action-refund",
        kind="dispatch_refund",
        date=day,
        node_key=run.node_key,
        result={"run_id": run.pk, "message": "目标已变化且未交战，出征次数与领兵额度已退回"},
    )
    run.result = {**run.result, "refund_quota": False, "quota_refunded": True}


def guest_dispatch_counts(season: Any, day: date) -> dict[int, int]:
    """按派遣日统计赛季内真实出征；集合取消和未使用额度的退回不计入。"""
    from collections import Counter
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    from django.db.models import Q

    from gameplay.models import SeasonExpedition

    start = datetime.combine(day, time.min, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = (
        SeasonExpedition.objects.filter(member__season=season)
        .filter(
            Q(result__quota_date=day.isoformat())
            | Q(result__quota_date__isnull=True, launched_at__gte=start, launched_at__lt=start + timedelta(days=1))
        )
        .values_list("guest_ids", "result")
    )
    counts: Counter[int] = Counter()
    for ids, result in rows:
        if result.get("quota_refunded"):
            continue
        counts.update(set(ids))
    return dict(counts)


def enforce_guest_daily_limit(season: Any, guest_ids: list[int], now: Any) -> None:
    from zoneinfo import ZoneInfo

    from core.exceptions import GameError

    from .campaign_rules import campaign_rules

    limit = campaign_rules(season)["expeditions"].get("guest_daily_limit", 0)
    if not limit:
        return
    counts = guest_dispatch_counts(season, timezone.localdate(now, timezone=ZoneInfo("Asia/Shanghai")))
    if any(counts.get(guest_id, 0) >= limit for guest_id in guest_ids):
        raise GameError(f"所选门客今日赛季出征次数已用完，每名每天最多{limit}次，请选择其他门客")
