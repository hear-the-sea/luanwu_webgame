from __future__ import annotations

from typing import Any, Dict, List

from django.db import IntegrityError, transaction

from core.exceptions import MissionDailyLimitError

from ...models import Manor, MissionTemplate
from .time_utils import get_today_date_range

MISSION_CARD_DAILY_LIMIT_PER_MISSION = 5


def _resolve_non_negative_int(raw: Any, *, field_name: str) -> int:
    if raw is None or isinstance(raw, bool):
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission {field_name}: {raw!r}") from exc
    if value < 0:
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    return value


def _resolve_incremented_extra_attempts(current: Any, increment: int) -> int:
    current_count = _resolve_non_negative_int(current, field_name="extra attempts")
    next_count = current_count + increment
    if next_count > MISSION_CARD_DAILY_LIMIT_PER_MISSION:
        raise MissionDailyLimitError(f"该任务今日最多使用 {MISSION_CARD_DAILY_LIMIT_PER_MISSION} 张任务卡")
    return next_count


def get_mission_extra_attempts(manor: Manor, mission: MissionTemplate) -> int:
    from ...models import MissionExtraAttempt

    _, _, today_date = get_today_date_range()
    extra = MissionExtraAttempt.objects.filter(manor=manor, mission=mission, date=today_date).first()
    raw_extra_count = extra.extra_count if extra else 0
    return _resolve_non_negative_int(raw_extra_count, field_name="extra attempts")


def bulk_get_mission_extra_attempts(manor: Manor, missions: List[MissionTemplate]) -> Dict[str, int]:
    from ...models import MissionExtraAttempt

    _, _, today_date = get_today_date_range()
    extras = MissionExtraAttempt.objects.filter(manor=manor, date=today_date, mission__in=missions).select_related(
        "mission"
    )

    result = {m.key: 0 for m in missions}
    for extra in extras:
        result[extra.mission.key] = _resolve_non_negative_int(extra.extra_count, field_name="extra attempts")
    return result


def add_mission_extra_attempt(manor: Manor, mission: MissionTemplate, count: int = 1) -> int:
    from ...models import MissionExtraAttempt

    if isinstance(count, bool):
        raise AssertionError(f"invalid mission extra attempt count: {count!r}")
    try:
        resolved_count = int(count)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission extra attempt count: {count!r}") from exc
    if resolved_count <= 0:
        raise AssertionError(f"invalid mission extra attempt count: {count!r}")

    _resolve_incremented_extra_attempts(0, resolved_count)

    _, _, today_date = get_today_date_range()
    with transaction.atomic():
        extra = (
            MissionExtraAttempt.objects.select_for_update()
            .filter(manor=manor, mission=mission, date=today_date)
            .first()
        )
        if extra:
            extra.extra_count = _resolve_incremented_extra_attempts(extra.extra_count, resolved_count)
            extra.save(update_fields=["extra_count", "updated_at"])
            return extra.extra_count

    try:
        with transaction.atomic():
            extra = MissionExtraAttempt.objects.create(
                manor=manor,
                mission=mission,
                date=today_date,
                extra_count=resolved_count,
            )
            return extra.extra_count
    except IntegrityError:
        with transaction.atomic():
            extra = MissionExtraAttempt.objects.select_for_update().get(manor=manor, mission=mission, date=today_date)
            extra.extra_count = _resolve_incremented_extra_attempts(extra.extra_count, resolved_count)
            extra.save(update_fields=["extra_count", "updated_at"])
            return extra.extra_count


def add_mission_extra_attempt_with_item_cost(
    manor: Manor,
    mission: MissionTemplate,
    *,
    item_key: str,
    count: int = 1,
) -> int:
    from ...services.inventory import core as inventory_core

    with transaction.atomic():
        inventory_core.consume_inventory_item_for_manor_locked(manor, item_key, 1)
        return add_mission_extra_attempt(manor, mission, count)


def get_mission_daily_limit(manor: Manor, mission: MissionTemplate) -> int:
    extra = _resolve_non_negative_int(get_mission_extra_attempts(manor, mission), field_name="extra attempts")
    daily_limit = _resolve_non_negative_int(getattr(mission, "daily_limit", None), field_name="daily_limit")
    if daily_limit <= 0:
        raise AssertionError(f"invalid mission daily_limit: {getattr(mission, 'daily_limit', None)!r}")
    return daily_limit + extra


def mission_attempts_today(manor: Manor, mission: MissionTemplate) -> int:
    start_of_day, end_of_day, _ = get_today_date_range()
    return manor.mission_runs.filter(mission=mission, started_at__gte=start_of_day, started_at__lt=end_of_day).count()


def bulk_mission_attempts_today(manor: Manor, missions: List[MissionTemplate]) -> Dict[str, int]:
    from django.db.models import Count

    start_of_day, end_of_day, _ = get_today_date_range()
    counts = (
        manor.mission_runs.filter(started_at__gte=start_of_day, started_at__lt=end_of_day)
        .values("mission__key")
        .annotate(count=Count("id"))
    )
    result = {m.key: 0 for m in missions}
    for row in counts:
        key = row["mission__key"]
        if key in result:
            result[key] = row["count"]
    return result
