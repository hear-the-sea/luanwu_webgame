from __future__ import annotations

from django.db.models import QuerySet

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    ArenaWheelEntry,
    ArenaWheelEntryGuest,
    ArenaWheelEvent,
    Manor,
)
from guests.models import Guest

from .helpers import today_bounds, today_local_date


def exclude_active_arena_guests(guests: QuerySet[Guest]) -> QuerySet[Guest]:
    """释放前排除仍被任一竞技赛事占用的门客；调用方须先持久化报名终态。"""
    tournament_guests = ArenaEntryGuest.objects.filter(
        entry__status=ArenaEntry.Status.REGISTERED,
        entry__tournament__status__in=[ArenaTournament.Status.RECRUITING, ArenaTournament.Status.RUNNING],
        guest_id__isnull=False,
    ).values_list("guest_id", flat=True)
    coop_guests = ArenaCoopEntryGuest.objects.filter(
        entry__status=ArenaCoopEntry.Status.REGISTERED,
        entry__event__status__in=[
            ArenaCoopEvent.Status.RECRUITING,
            ArenaCoopEvent.Status.PREPARING,
            ArenaCoopEvent.Status.RUNNING,
        ],
        guest_id__isnull=False,
    ).values_list("guest_id", flat=True)
    wheel_guests = ArenaWheelEntryGuest.objects.filter(
        entry__status__in=[ArenaWheelEntry.Status.REGISTERED, ArenaWheelEntry.Status.ACTIVE],
        entry__event__status__in=[
            ArenaWheelEvent.Status.RECRUITING,
            ArenaWheelEvent.Status.PREPARING,
            ArenaWheelEvent.Status.RUNNING,
        ],
        guest_id__isnull=False,
    ).values_list("guest_id", flat=True)
    return guests.exclude(pk__in=tournament_guests).exclude(pk__in=coop_guests).exclude(pk__in=wheel_guests)


def sync_daily_participation_counter_locked(locked_manor: Manor, *, now=None) -> int:
    """
    同步庄园侧的竞技场日计数。

    说明：
    - 计数以 Manor 字段为准，避免赛事记录被清理后次数“回收”。
    - 当天首次访问且字段未初始化时，回填为当日现存 ArenaEntry 数量，兼容历史数据。
    """
    today = today_local_date(now=now)
    if locked_manor.arena_participation_date == today:
        return max(0, int(locked_manor.arena_participations_today or 0))

    day_start, day_end = today_bounds(now=now)
    today_count = ArenaEntry.objects.filter(manor=locked_manor, joined_at__gte=day_start, joined_at__lt=day_end).count()
    locked_manor.arena_participation_date = today
    locked_manor.arena_participations_today = max(0, int(today_count))
    locked_manor.save(update_fields=["arena_participation_date", "arena_participations_today"])
    return locked_manor.arena_participations_today


def update_daily_participation_counter_locked(locked_manor: Manor, *, delta: int, now=None) -> int:
    current = sync_daily_participation_counter_locked(locked_manor, now=now)
    updated = max(0, int(current) + int(delta))
    locked_manor.arena_participation_date = today_local_date(now=now)
    locked_manor.arena_participations_today = updated
    locked_manor.save(update_fields=["arena_participation_date", "arena_participations_today"])
    return updated
