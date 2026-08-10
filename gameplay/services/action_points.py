from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from core.exceptions import ActionPointsInsufficientError

ACTION_POINTS_MAX = 1000
ACTION_POINT_EXPEDITION_COST = 10
ACTION_POINT_BUILDING_UPGRADE_COST = 20
ACTION_POINT_RECOVERY_SECONDS = 60


def _coerce_action_points(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        points = int(value)
    except (TypeError, ValueError):
        return 0
    return min(ACTION_POINTS_MAX, max(0, points))


def get_current_action_points(manor: Any, *, now: datetime | None = None) -> int:
    current_time = now or timezone.now()
    stored_points = _coerce_action_points(getattr(manor, "action_points", ACTION_POINTS_MAX))
    if stored_points >= ACTION_POINTS_MAX:
        return ACTION_POINTS_MAX

    updated_at = getattr(manor, "action_points_updated_at", None)
    if updated_at is None:
        return stored_points

    elapsed_seconds = max(0, int((current_time - updated_at).total_seconds()))
    recovered_points = elapsed_seconds // ACTION_POINT_RECOVERY_SECONDS
    return min(ACTION_POINTS_MAX, stored_points + recovered_points)


def _resolve_recovery_checkpoint(
    manor: Any, *, now: datetime, recovered_points: int, available_points: int
) -> datetime:
    updated_at = getattr(manor, "action_points_updated_at", None)
    if updated_at is None or available_points >= ACTION_POINTS_MAX:
        return now
    return updated_at + timedelta(seconds=recovered_points * ACTION_POINT_RECOVERY_SECONDS)


def consume_action_points(
    manor: Any,
    cost: int,
    *,
    now: datetime | None = None,
    insufficient_message: str | None = None,
) -> int:
    if isinstance(cost, bool) or not isinstance(cost, int) or cost <= 0:
        raise ValueError("action point cost must be a positive integer")

    current_time = now or timezone.now()
    stored_points = _coerce_action_points(getattr(manor, "action_points", ACTION_POINTS_MAX))
    updated_at = getattr(manor, "action_points_updated_at", None)
    elapsed_seconds = 0 if updated_at is None else max(0, int((current_time - updated_at).total_seconds()))
    recovered_points = elapsed_seconds // ACTION_POINT_RECOVERY_SECONDS
    available_points = min(ACTION_POINTS_MAX, stored_points + recovered_points)
    if available_points < cost:
        raise ActionPointsInsufficientError(insufficient_message)

    remaining_points = available_points - cost
    manor.action_points = remaining_points
    manor.action_points_updated_at = _resolve_recovery_checkpoint(
        manor,
        now=current_time,
        recovered_points=recovered_points,
        available_points=available_points,
    )
    manor.save(update_fields=["action_points", "action_points_updated_at"])
    return remaining_points


def consume_action_points_for_expedition(manor: Any, *, now: datetime | None = None) -> int:
    return consume_action_points(manor, ACTION_POINT_EXPEDITION_COST, now=now)
