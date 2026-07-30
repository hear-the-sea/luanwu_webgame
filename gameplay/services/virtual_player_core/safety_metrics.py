from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid4

from django.utils import timezone

from .contracts import MaintenanceResult, MaintenanceTrigger
from .safety_provider import SafetyMetricEventRecord, record_safety_metric_event, record_safety_metric_events

logger = logging.getLogger(__name__)


SAFETY_HEARTBEAT_METRIC: Final = "virtual_player_safety_heartbeat"
MAINTENANCE_ATTEMPT_METRIC: Final = "virtual_player_maintenance_attempt"
H01_RECOMMENDATION_METRIC: Final = "virtual_player_loot_retirement_recommendation_total"
H01_CALLBACK_ATTEMPT_METRIC: Final = "virtual_player_loot_retirement_post_commit_attempt_total"
ARENA_SHORTAGE_METRIC: Final = "virtual_player_arena_reserve_shortage_total"
HARD_CONSTRAINT_METRIC: Final = "virtual_player_hard_constraint_violation"
ECONOMY_CAP_BREACH_METRIC: Final = "virtual_player_economy_cap_breach"
DUPLICATE_OR_PARTIAL_COMMIT_METRIC: Final = "virtual_player_duplicate_or_partial_commit"
PERFORMANCE_BREACH_METRIC: Final = "virtual_player_performance_breach"
DISTRIBUTION_BREACH_METRIC: Final = "virtual_player_distribution_breach"

REQUIRED_HEARTBEAT_STREAMS: Final = (
    "maintenance_attempt_emitter",
    "h01_callback_attempt_emitter",
    "arena_shortage_emitter",
    "safety_aggregator",
    "safety_monitor",
)


class MaintenanceAttemptResult(StrEnum):
    STARTED = "started"
    APPLIED = "applied"
    NO_ACTION = "no_action"
    BUSY = "busy"
    PAUSED = "paused"
    INELIGIBLE = "ineligible"
    FAILED = "failed"
    COMMIT_UNCERTAIN = "commit_uncertain"


@dataclass(frozen=True, slots=True)
class MaintenanceAttempt:
    operation_id: str
    attempt_ordinal: int
    started_at: datetime
    trigger: MaintenanceTrigger

    @property
    def event_id_prefix(self) -> str:
        return f"maintenance:{self.operation_id}:{self.attempt_ordinal}"


def _aware_utc_now(now: datetime | None = None) -> datetime:
    value = timezone.now() if now is None else now
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValueError("safety metric timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_operation_id(value: UUID | str | None) -> str:
    if value is None:
        return uuid4().hex
    if isinstance(value, UUID):
        return value.hex
    if not isinstance(value, str):
        raise ValueError("operation_id must be a UUID or canonical identifier")
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        raise ValueError("operation_id must contain between 1 and 64 characters")
    if any(not (character.isascii() and (character.isalnum() or character in "_.-")) for character in normalized):
        raise ValueError("operation_id must be a canonical ASCII identifier")
    return normalized


def _normalize_attempt_ordinal(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("attempt_ordinal must be a positive integer")
    return value


def normalize_maintenance_operation_id(value: UUID | str | None) -> str:
    """Return the canonical identifier shared by durable maintenance callers."""

    return _normalize_operation_id(value)


def normalize_maintenance_attempt_ordinal(value: int) -> int:
    """Validate a persisted maintenance attempt ordinal."""

    return _normalize_attempt_ordinal(value)


def record_safety_heartbeat(
    stream: str,
    *,
    now: datetime | None = None,
):
    if stream not in REQUIRED_HEARTBEAT_STREAMS:
        raise ValueError(f"unsupported safety heartbeat stream: {stream}")
    occurred_at = _aware_utc_now(now).replace(second=0, microsecond=0)
    return record_safety_metric_event(
        event_id=f"safety-heartbeat:{stream}:{occurred_at.strftime('%Y%m%dT%H%MZ')}",
        metric_name=SAFETY_HEARTBEAT_METRIC,
        occurred_at=occurred_at,
        dimensions={"stream": stream},
        value=1,
    )


def start_maintenance_attempt(
    *,
    trigger: MaintenanceTrigger,
    operation_id: UUID | str | None = None,
    attempt_ordinal: int = 1,
    started_at: datetime | None = None,
) -> MaintenanceAttempt:
    attempt = MaintenanceAttempt(
        operation_id=_normalize_operation_id(operation_id),
        attempt_ordinal=_normalize_attempt_ordinal(attempt_ordinal),
        started_at=_aware_utc_now(started_at),
        trigger=MaintenanceTrigger(trigger),
    )
    record_safety_metric_event(
        event_id=f"{attempt.event_id_prefix}:started",
        metric_name=MAINTENANCE_ATTEMPT_METRIC,
        occurred_at=attempt.started_at,
        dimensions={
            "result": MaintenanceAttemptResult.STARTED,
            "trigger": attempt.trigger.value,
        },
        value=1,
    )
    return attempt


def start_maintenance_attempts(
    *,
    trigger: MaintenanceTrigger,
    operation_ids: Sequence[UUID | str | None],
    attempt_ordinal: int = 1,
    started_at: datetime | None = None,
) -> tuple[MaintenanceAttempt, ...]:
    resolved_trigger = MaintenanceTrigger(trigger)
    resolved_ordinal = _normalize_attempt_ordinal(attempt_ordinal)
    resolved_started_at = _aware_utc_now(started_at)
    attempts = tuple(
        MaintenanceAttempt(
            operation_id=_normalize_operation_id(operation_id),
            attempt_ordinal=resolved_ordinal,
            started_at=resolved_started_at,
            trigger=resolved_trigger,
        )
        for operation_id in operation_ids
    )
    record_safety_metric_events(
        tuple(
            SafetyMetricEventRecord(
                event_id=f"{attempt.event_id_prefix}:started",
                metric_name=MAINTENANCE_ATTEMPT_METRIC,
                occurred_at=attempt.started_at,
                dimensions={
                    "result": MaintenanceAttemptResult.STARTED,
                    "trigger": attempt.trigger.value,
                },
                value=Decimal(1),
            )
            for attempt in attempts
        )
    )
    return attempts


def finish_maintenance_attempt(
    attempt: MaintenanceAttempt,
    *,
    result: MaintenanceResult | MaintenanceAttemptResult | str,
):
    if not isinstance(attempt, MaintenanceAttempt):
        raise ValueError("attempt must be a MaintenanceAttempt")
    if isinstance(result, MaintenanceResult):
        if result.trigger is not attempt.trigger:
            raise ValueError("maintenance result trigger differs from its attempt")
        normalized_result = MaintenanceAttemptResult(result.outcome.value)
    else:
        normalized_result = MaintenanceAttemptResult(result)
    if normalized_result is MaintenanceAttemptResult.STARTED:
        raise ValueError("maintenance terminal result cannot be started")
    return record_safety_metric_event(
        event_id=f"{attempt.event_id_prefix}:terminal",
        metric_name=MAINTENANCE_ATTEMPT_METRIC,
        occurred_at=attempt.started_at,
        dimensions={
            "result": normalized_result.value,
            "trigger": attempt.trigger.value,
        },
        value=1,
    )


def finish_maintenance_attempts(
    attempts: Sequence[
        tuple[
            MaintenanceAttempt,
            MaintenanceResult | MaintenanceAttemptResult | str,
        ]
    ],
):
    events: list[SafetyMetricEventRecord] = []
    for attempt, result in attempts:
        if not isinstance(attempt, MaintenanceAttempt):
            raise ValueError("attempt must be a MaintenanceAttempt")
        if isinstance(result, MaintenanceResult):
            if result.trigger is not attempt.trigger:
                raise ValueError("maintenance result trigger differs from its attempt")
            normalized_result = MaintenanceAttemptResult(result.outcome.value)
        else:
            normalized_result = MaintenanceAttemptResult(result)
        if normalized_result is MaintenanceAttemptResult.STARTED:
            raise ValueError("maintenance terminal result cannot be started")
        events.append(
            SafetyMetricEventRecord(
                event_id=f"{attempt.event_id_prefix}:terminal",
                metric_name=MAINTENANCE_ATTEMPT_METRIC,
                occurred_at=attempt.started_at,
                dimensions={
                    "result": normalized_result.value,
                    "trigger": attempt.trigger.value,
                },
                value=Decimal(1),
            )
        )
    return record_safety_metric_events(tuple(events))


def record_h01_retirement_recommendation(
    *,
    operation_id: UUID | str | None = None,
    occurred_at: datetime | None = None,
):
    normalized_operation_id = _normalize_operation_id(operation_id)
    normalized_occurred_at = _aware_utc_now(occurred_at)
    return record_safety_metric_event(
        event_id=f"h01-recommendation:{normalized_operation_id}",
        metric_name=H01_RECOMMENDATION_METRIC,
        occurred_at=normalized_occurred_at,
        dimensions={"result": "recommended"},
        value=1,
    )


def record_h01_callback_attempt(
    *,
    operation_id: UUID | str,
    result: str,
    occurred_at: datetime,
):
    normalized_operation_id = _normalize_operation_id(operation_id)
    normalized_occurred_at = _aware_utc_now(occurred_at)
    if result not in {"all", "degraded"}:
        raise ValueError("H-01 callback result must be all or degraded")
    return record_safety_metric_event(
        event_id=f"h01-attempt:{normalized_operation_id}:{result}",
        metric_name=H01_CALLBACK_ATTEMPT_METRIC,
        occurred_at=normalized_occurred_at,
        dimensions={"result": result},
        value=1,
    )


def record_arena_shortage(
    *,
    operation_id: UUID | str,
    mode: str,
    prestige_band: str,
    missing_count: int,
    capacity: int,
    occurred_at: datetime | None = None,
):
    normalized_operation_id = _normalize_operation_id(operation_id)
    normalized_occurred_at = _aware_utc_now(occurred_at)
    if mode not in {"tournament", "coop"}:
        raise ValueError("arena shortage mode must be tournament or coop")
    if not isinstance(prestige_band, str) or not prestige_band:
        raise ValueError("prestige_band must be non-empty")
    if isinstance(missing_count, bool) or not isinstance(missing_count, int) or missing_count < 0:
        raise ValueError("missing_count must be a non-negative integer")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise ValueError("capacity must be a positive integer")
    if missing_count > capacity:
        raise ValueError("missing_count cannot exceed capacity")
    ratio = (Decimal(missing_count) / Decimal(capacity)).quantize(
        Decimal("0.000000000001"),
        rounding=ROUND_HALF_UP,
    )
    return record_safety_metric_event(
        event_id=f"arena-shortage:{normalized_operation_id}",
        metric_name=ARENA_SHORTAGE_METRIC,
        occurred_at=normalized_occurred_at,
        dimensions={
            "kind": mode,
            "prestige_band": prestige_band,
        },
        value=ratio,
    )


def log_safety_metric_failure(
    *,
    operation: str,
    exc: Exception,
) -> None:
    logger.exception(
        "Virtual player safety metric write failed: operation=%s error=%s",
        operation,
        exc,
        extra={
            "event": "virtual_player_safety_metric_write_failed",
            "operation": operation,
            "failure_code": type(exc).__name__,
        },
    )


__all__ = [
    "ARENA_SHORTAGE_METRIC",
    "DISTRIBUTION_BREACH_METRIC",
    "DUPLICATE_OR_PARTIAL_COMMIT_METRIC",
    "ECONOMY_CAP_BREACH_METRIC",
    "H01_CALLBACK_ATTEMPT_METRIC",
    "H01_RECOMMENDATION_METRIC",
    "HARD_CONSTRAINT_METRIC",
    "MAINTENANCE_ATTEMPT_METRIC",
    "PERFORMANCE_BREACH_METRIC",
    "REQUIRED_HEARTBEAT_STREAMS",
    "SAFETY_HEARTBEAT_METRIC",
    "MaintenanceAttempt",
    "MaintenanceAttemptResult",
    "finish_maintenance_attempt",
    "finish_maintenance_attempts",
    "log_safety_metric_failure",
    "normalize_maintenance_attempt_ordinal",
    "normalize_maintenance_operation_id",
    "record_arena_shortage",
    "record_h01_callback_attempt",
    "record_h01_retirement_recommendation",
    "record_safety_heartbeat",
    "start_maintenance_attempt",
    "start_maintenance_attempts",
]
