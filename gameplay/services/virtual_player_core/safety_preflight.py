from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final

from django.db import DatabaseError, connection
from django.utils import timezone

from gameplay.models import BotSafetyMetricEvent

from .safety_metrics import SAFETY_HEARTBEAT_METRIC

SAFETY_MONITOR_STREAM: Final = "safety_monitor"
SAFETY_MONITOR_MAX_AGE: Final = timedelta(seconds=120)
# Heartbeats are minute-bucketed by the application clock. Allow one bounded
# minute of application/database clock skew, but keep larger future values fail-closed.
SAFETY_MONITOR_MAX_FUTURE_SKEW: Final = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class SafetyWritePreflightResult:
    allowed: bool
    reason: str
    checked_at: datetime | None
    monitor_heartbeat_at: datetime | None


def _database_utc_now() -> datetime:
    with connection.cursor() as cursor:
        clock_query = "SELECT UTC_TIMESTAMP(6)" if connection.vendor == "mysql" else "SELECT CURRENT_TIMESTAMP"
        cursor.execute(clock_query)
        value = cursor.fetchone()[0]
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise DatabaseError("database clock returned a non-datetime value")
    if timezone.is_naive(value):
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize_now(value: datetime | None) -> datetime:
    resolved = _database_utc_now() if value is None else value
    if not isinstance(resolved, datetime) or timezone.is_naive(resolved):
        raise ValueError("now must be a timezone-aware datetime")
    return resolved.astimezone(UTC)


def check_v2_development_write_preflight(
    *,
    now: datetime | None = None,
) -> SafetyWritePreflightResult:
    """Read persisted monitor health without mutating routing or provider state."""

    try:
        checked_at = _normalize_now(now)
    except DatabaseError:
        return SafetyWritePreflightResult(
            allowed=False,
            reason="safety_provider_unreadable",
            checked_at=None,
            monitor_heartbeat_at=None,
        )
    try:
        heartbeat = (
            BotSafetyMetricEvent.objects.filter(
                metric_name=SAFETY_HEARTBEAT_METRIC,
                dimensions__stream=SAFETY_MONITOR_STREAM,
            )
            .order_by("-occurred_at", "-id")
            .only("event_id", "occurred_at", "dimensions", "value")
            .first()
        )
    except (DatabaseError, InvalidOperation, TypeError):
        return SafetyWritePreflightResult(
            allowed=False,
            reason="safety_provider_unreadable",
            checked_at=checked_at,
            monitor_heartbeat_at=None,
        )
    if heartbeat is None:
        return SafetyWritePreflightResult(
            allowed=False,
            reason="safety_monitor_heartbeat_missing",
            checked_at=checked_at,
            monitor_heartbeat_at=None,
        )

    heartbeat_at = heartbeat.occurred_at
    if not isinstance(heartbeat_at, datetime) or timezone.is_naive(heartbeat_at):
        return SafetyWritePreflightResult(
            allowed=False,
            reason="safety_monitor_heartbeat_invalid",
            checked_at=checked_at,
            monitor_heartbeat_at=None,
        )
    heartbeat_at = heartbeat_at.astimezone(UTC)
    expected_event_id = f"safety-heartbeat:{SAFETY_MONITOR_STREAM}:" f"{heartbeat_at.strftime('%Y%m%dT%H%MZ')}"
    try:
        heartbeat_value = Decimal(heartbeat.value)
    except (InvalidOperation, TypeError, ValueError):
        heartbeat_value = Decimal("NaN")
    if (
        not heartbeat_value.is_finite()
        or heartbeat_value != 1
        or heartbeat.event_id != expected_event_id
        or heartbeat.dimensions != {"stream": SAFETY_MONITOR_STREAM}
        or heartbeat_at.second != 0
        or heartbeat_at.microsecond != 0
    ):
        return SafetyWritePreflightResult(
            allowed=False,
            reason="safety_monitor_heartbeat_invalid",
            checked_at=checked_at,
            monitor_heartbeat_at=heartbeat_at,
        )
    age = checked_at - heartbeat_at
    if age <= -SAFETY_MONITOR_MAX_FUTURE_SKEW:
        return SafetyWritePreflightResult(
            allowed=False,
            reason="safety_monitor_heartbeat_from_future",
            checked_at=checked_at,
            monitor_heartbeat_at=heartbeat_at,
        )
    if age > SAFETY_MONITOR_MAX_AGE:
        return SafetyWritePreflightResult(
            allowed=False,
            reason="safety_monitor_heartbeat_stale",
            checked_at=checked_at,
            monitor_heartbeat_at=heartbeat_at,
        )
    return SafetyWritePreflightResult(
        allowed=True,
        reason="",
        checked_at=checked_at,
        monitor_heartbeat_at=heartbeat_at,
    )


__all__ = [
    "SAFETY_MONITOR_MAX_AGE",
    "SAFETY_MONITOR_MAX_FUTURE_SKEW",
    "SAFETY_MONITOR_STREAM",
    "SafetyWritePreflightResult",
    "check_v2_development_write_preflight",
]
