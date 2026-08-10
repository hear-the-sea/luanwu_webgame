from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Final

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from gameplay.models import BotSafetyMetricEvent, BotSafetyMetricWindow
from gameplay.services import runtime_configs

from . import safety_baselines
from .config import MaintenanceMode
from .random_context import canonical_json_bytes
from .safety_metrics import (
    ARENA_SHORTAGE_METRIC,
    DISTRIBUTION_BREACH_METRIC,
    DUPLICATE_OR_PARTIAL_COMMIT_METRIC,
    ECONOMY_CAP_BREACH_METRIC,
    H01_CALLBACK_ATTEMPT_METRIC,
    HARD_CONSTRAINT_METRIC,
    MAINTENANCE_ATTEMPT_METRIC,
    PERFORMANCE_BREACH_METRIC,
    REQUIRED_HEARTBEAT_STREAMS,
    SAFETY_HEARTBEAT_METRIC,
    MaintenanceAttemptResult,
)
from .safety_provider import (
    HARD_VIOLATION_METRIC_NAME,
    SAFETY_WINDOW_GRACE,
    SafetyMetricEventRecord,
    SafetyMetricWindowAggregationInput,
    SafetyMetricWindowFinalizeResult,
    aggregate_and_finalize_safety_metric_window,
)

logger = logging.getLogger(__name__)
SAFETY_SNAPSHOT_SCHEMA_VERSION_V1: Final = 1
SAFETY_SNAPSHOT_SCHEMA_VERSION_V2: Final = 2
SAFETY_SNAPSHOT_SCHEMA_VERSION: Final = 3
SUPPORTED_SAFETY_SNAPSHOT_SCHEMA_VERSIONS: Final = (
    SAFETY_SNAPSHOT_SCHEMA_VERSION_V1,
    SAFETY_SNAPSHOT_SCHEMA_VERSION_V2,
    SAFETY_SNAPSHOT_SCHEMA_VERSION,
)
HEARTBEAT_MAX_GAP: Final = timedelta(seconds=120)
MAINTENANCE_FAILURE_RATE_THRESHOLD: Final = Decimal("0.01")
H01_DEGRADED_RATE_THRESHOLD: Final = Decimal("0.001")
ARENA_SHORTAGE_INCREASE_THRESHOLD: Final = Decimal("0.02")
DEFAULT_WINDOW_LIMIT: Final = 100
DEFAULT_MAX_CAS_ATTEMPTS: Final = 3

_MAINTENANCE_FAILURE_WINDOWS = 2
_H01_DEGRADED_WINDOWS = 1
_PERFORMANCE_BREACH_WINDOWS = 3
_DISTRIBUTION_BREACH_WINDOWS = 2
_MAX_HISTORY_WINDOWS = (
    max(
        _MAINTENANCE_FAILURE_WINDOWS,
        _H01_DEGRADED_WINDOWS,
        _PERFORMANCE_BREACH_WINDOWS,
        _DISTRIBUTION_BREACH_WINDOWS,
    )
    - 1
)


class SafetyMonitorError(RuntimeError):
    pass


class InvalidSafetySnapshotError(SafetyMonitorError):
    pass


class SafetyDecisionConflictExhausted(SafetyMonitorError):
    pass


@dataclass(frozen=True, slots=True)
class SafetyWindowDecision:
    window_id: str
    window_kind: str
    window_start_at: datetime
    window_end_at: datetime
    should_pause: bool
    pause_reasons: tuple[str, ...]

    @property
    def pause_reason(self) -> str:
        return ",".join(self.pause_reasons)


@dataclass(frozen=True, slots=True)
class SafetyMonitorRunResult:
    decisions: tuple[SafetyWindowDecision, ...]
    routing_results: tuple[runtime_configs.SafetyRoutingDecisionResult, ...]
    cas_conflicts: int

    @property
    def consumed_count(self) -> int:
        return sum(result.consumed for result in self.routing_results)

    @property
    def paused(self) -> bool:
        return any(result.paused for result in self.routing_results)


@dataclass(frozen=True, slots=True)
class SafetyMonitorCycleResult:
    finalized_windows: tuple[SafetyMetricWindowFinalizeResult, ...]
    monitor: SafetyMonitorRunResult


@dataclass(frozen=True, slots=True)
class _Scope:
    policy_version: str
    reference_snapshot_version: str
    prestige_band: str

    def label(self) -> str:
        return f"{self.policy_version}:" f"{self.reference_snapshot_version}:" f"{self.prestige_band}"


@dataclass(frozen=True, slots=True)
class _ArenaScope:
    kind: str
    prestige_band: str

    def label(self) -> str:
        return f"{self.kind}:{self.prestige_band}"


@dataclass(frozen=True, slots=True)
class _ArenaRatioFacts:
    ratio: Decimal
    sample_count: int
    real_entry_count_min: int | None = None
    reserve_ready_count_min: int | None = None
    reserve_training_count_max: int | None = None

    @property
    def is_cold_start(self) -> bool:
        return self.real_entry_count_min is not None and self.real_entry_count_min <= int(
            settings.ARENA_SHORTAGE_COLD_START_MAX_REAL_ENTRIES
        )


@dataclass(frozen=True, slots=True)
class _ArenaBaselineFacts:
    ratio: Decimal
    source: str
    expires_at: datetime | None = None
    max_real_entry_count: int | None = None


@dataclass(frozen=True, slots=True)
class _WindowFacts:
    window: BotSafetyMetricWindow
    metrics: Mapping[str, Decimal]
    incomplete_heartbeat_streams: tuple[str, ...]
    aggregation_errors: tuple[str, ...]
    distribution_breaches: frozenset[_Scope]
    arena_ratios: Mapping[_ArenaScope, _ArenaRatioFacts]
    arena_baselines: Mapping[_ArenaScope, _ArenaBaselineFacts]


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise SafetyMonitorError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _window_duration(window_kind: str) -> timedelta:
    if window_kind == BotSafetyMetricWindow.Kind.HOURLY:
        return timedelta(hours=1)
    if window_kind == BotSafetyMetricWindow.Kind.DAILY:
        return timedelta(days=1)
    raise SafetyMonitorError("window_kind must be hourly or daily")


def _validate_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise SafetyMonitorError("limit must be an integer between 1 and 1000")
    return value


def _snapshot_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event_base_id(event_id: str, suffix: str) -> str:
    marker = f":{suffix}"
    return event_id[: -len(marker)] if event_id.endswith(marker) else event_id


def _scope_from_dimensions(
    dimensions: Mapping[str, Any],
) -> _Scope | None:
    policy_version = dimensions.get("policy_version")
    reference_snapshot_version = dimensions.get("reference_snapshot_version")
    prestige_band = dimensions.get("prestige_band")
    if not isinstance(policy_version, str) or not policy_version:
        return None
    if not isinstance(reference_snapshot_version, str) or not reference_snapshot_version:
        return None
    if not isinstance(prestige_band, str) or not prestige_band:
        return None
    return _Scope(
        policy_version=policy_version,
        reference_snapshot_version=reference_snapshot_version,
        prestige_band=prestige_band,
    )


def _arena_scope_from_dimensions(
    dimensions: Mapping[str, Any],
) -> _ArenaScope | None:
    kind = dimensions.get("kind")
    prestige_band = dimensions.get("prestige_band")
    if not isinstance(kind, str) or not kind:
        return None
    if not isinstance(prestige_band, str) or not prestige_band:
        return None
    return _ArenaScope(kind=kind, prestige_band=prestige_band)


def _heartbeat_snapshot(
    timestamps_by_stream: Mapping[str, Sequence[datetime]],
    *,
    window_start_at: datetime,
    window_end_at: datetime,
) -> dict[str, Any]:
    streams: dict[str, dict[str, Any]] = {}
    incomplete: list[str] = []
    max_gap_seconds = int(HEARTBEAT_MAX_GAP.total_seconds())
    for stream in REQUIRED_HEARTBEAT_STREAMS:
        timestamps = sorted(set(timestamps_by_stream.get(stream, ())))
        if not timestamps:
            streams[stream] = {
                "complete": False,
                "count": 0,
                "first_at": None,
                "last_at": None,
                "max_gap_seconds": None,
                "reason": "missing",
            }
            incomplete.append(stream)
            continue

        boundary_and_internal_gaps = [
            timestamps[0] - window_start_at,
            window_end_at - timestamps[-1],
            *(current - previous for previous, current in zip(timestamps, timestamps[1:])),
        ]
        observed_max_gap = max(boundary_and_internal_gaps)
        complete = (
            timestamps[0] >= window_start_at
            and timestamps[-1] < window_end_at
            and all(gap >= timedelta(0) for gap in boundary_and_internal_gaps)
            and observed_max_gap <= HEARTBEAT_MAX_GAP
        )
        reason = "" if complete else "gap_exceeded"
        streams[stream] = {
            "complete": complete,
            "count": len(timestamps),
            "first_at": _canonical_timestamp(timestamps[0]),
            "last_at": _canonical_timestamp(timestamps[-1]),
            "max_gap_seconds": int(observed_max_gap.total_seconds()),
            "reason": reason,
        }
        if not complete:
            incomplete.append(stream)

    return {
        "complete": not incomplete,
        "expected_interval_seconds": 60,
        "max_allowed_gap_seconds": max_gap_seconds,
        "incomplete_streams": incomplete,
        "streams": streams,
    }


def _load_events(
    *,
    window_start_at: datetime,
    window_end_at: datetime,
) -> tuple[SafetyMetricEventRecord, ...]:
    rows = BotSafetyMetricEvent.objects.filter(
        occurred_at__gte=window_start_at,
        occurred_at__lt=window_end_at,
    ).order_by("occurred_at", "event_id")
    return tuple(
        SafetyMetricEventRecord(
            event_id=row.event_id,
            metric_name=row.metric_name,
            occurred_at=row.occurred_at.astimezone(UTC),
            dimensions=row.dimensions if isinstance(row.dimensions, Mapping) else {},
            value=Decimal(row.value),
        )
        for row in rows
    )


def build_safety_window_snapshot(
    *,
    window_kind: str,
    window_start_at: datetime,
    _events: Sequence[SafetyMetricEventRecord] | None = None,
    _arena_baselines: Sequence[safety_baselines.ArenaShortageBaselineSnapshot] = (),
) -> dict[str, Any]:
    """Aggregate one fixed UTC window without mutating provider state."""

    duration = _window_duration(window_kind)
    start_at = _aware_utc(window_start_at, field="window_start_at")
    if window_kind == BotSafetyMetricWindow.Kind.HOURLY:
        aligned = start_at.replace(minute=0, second=0, microsecond=0)
    else:
        aligned = start_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if aligned != start_at:
        raise SafetyMonitorError("window_start_at must align to its fixed UTC boundary")
    end_at = start_at + duration
    events = _load_events(window_start_at=start_at, window_end_at=end_at) if _events is None else tuple(_events)

    metrics: defaultdict[str, Decimal] = defaultdict(Decimal)
    heartbeat_timestamps: dict[str, list[datetime]] = defaultdict(list)
    maintenance_started: dict[str, Decimal] = defaultdict(Decimal)
    maintenance_terminal: dict[str, Decimal] = defaultdict(Decimal)
    distribution_breaches: dict[_Scope, Decimal] = defaultdict(Decimal)
    arena_ratios: dict[_ArenaScope, list[tuple[Decimal, dict[str, int]]]] = defaultdict(list)
    aggregation_errors: set[str] = set()

    for event in events:
        if event.value < 0:
            aggregation_errors.add(f"negative_metric:{event.metric_name}")
            continue
        dimensions = event.dimensions
        if event.metric_name == SAFETY_HEARTBEAT_METRIC:
            stream = dimensions.get("stream")
            if not isinstance(stream, str) or stream not in REQUIRED_HEARTBEAT_STREAMS:
                aggregation_errors.add("invalid_heartbeat_stream")
                continue
            heartbeat_timestamps[stream].append(event.occurred_at)
        elif event.metric_name in {
            HARD_CONSTRAINT_METRIC,
            HARD_VIOLATION_METRIC_NAME,
        }:
            if event.metric_name == HARD_VIOLATION_METRIC_NAME:
                reason = dimensions.get("reason")
                normalized_reason = reason if isinstance(reason, str) and reason else "unknown"
                aggregation_errors.add(f"safety_provider_violation:{normalized_reason}")
            elif dimensions.get("reason") == "safety_metric_write_failed":
                aggregation_errors.add("safety_metric_write_failed")
            else:
                metrics["hard_constraint_violation_count"] += event.value
        elif event.metric_name == ECONOMY_CAP_BREACH_METRIC:
            metrics["economy_cap_breach_count"] += event.value
        elif event.metric_name == DUPLICATE_OR_PARTIAL_COMMIT_METRIC:
            metrics["duplicate_or_partial_commit_count"] += event.value
        elif event.metric_name == PERFORMANCE_BREACH_METRIC:
            metrics["performance_breach_count"] += event.value
        elif event.metric_name == DISTRIBUTION_BREACH_METRIC:
            distribution_scope = _scope_from_dimensions(dimensions)
            if distribution_scope is None:
                aggregation_errors.add("invalid_distribution_scope")
            else:
                distribution_breaches[distribution_scope] += event.value
        elif event.metric_name == ARENA_SHORTAGE_METRIC:
            arena_scope = _arena_scope_from_dimensions(dimensions)
            if arena_scope is None or event.value > 1:
                aggregation_errors.add("invalid_arena_shortage_ratio")
            else:
                context: dict[str, int] = {}
                invalid_context = False
                for field in (
                    "real_entry_count",
                    "virtual_entry_count",
                    "reserve_ready_count",
                    "reserve_training_count",
                ):
                    value = dimensions.get(field)
                    if value is None:
                        continue
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        invalid_context = True
                        break
                    context[field] = value
                if invalid_context:
                    aggregation_errors.add("invalid_arena_population_context")
                else:
                    arena_ratios[arena_scope].append((event.value, context))
        elif event.metric_name == MAINTENANCE_ATTEMPT_METRIC:
            result = dimensions.get("result")
            if result == MaintenanceAttemptResult.STARTED:
                maintenance_started[_event_base_id(event.event_id, "started")] += event.value
            elif result in {
                MaintenanceAttemptResult.APPLIED,
                MaintenanceAttemptResult.NO_ACTION,
                MaintenanceAttemptResult.FAILED,
                MaintenanceAttemptResult.BUSY,
                MaintenanceAttemptResult.PAUSED,
                MaintenanceAttemptResult.INELIGIBLE,
                MaintenanceAttemptResult.COMMIT_UNCERTAIN,
            }:
                base_id = _event_base_id(event.event_id, "terminal")
                maintenance_terminal[base_id] += event.value
                if result in {
                    MaintenanceAttemptResult.APPLIED,
                    MaintenanceAttemptResult.NO_ACTION,
                    MaintenanceAttemptResult.FAILED,
                }:
                    metrics["maintenance_eligible_attempt_count"] += event.value
                if result == MaintenanceAttemptResult.FAILED:
                    metrics["maintenance_failure_count"] += event.value
                if result == MaintenanceAttemptResult.COMMIT_UNCERTAIN:
                    metrics["duplicate_or_partial_commit_count"] += event.value
            else:
                aggregation_errors.add("invalid_maintenance_attempt_result")
        elif event.metric_name == H01_CALLBACK_ATTEMPT_METRIC:
            result = dimensions.get("result")
            if result == "all":
                metrics["h01_post_commit_attempt_count"] += event.value
            elif result == "degraded":
                metrics["h01_post_commit_attempt_degraded_count"] += event.value
            else:
                aggregation_errors.add("invalid_h01_callback_result")

    for base_id, started_count in maintenance_started.items():
        terminal_count = maintenance_terminal.get(base_id, Decimal(0))
        if terminal_count < started_count:
            metrics["duplicate_or_partial_commit_count"] += started_count - terminal_count
    for base_id, terminal_count in maintenance_terminal.items():
        if base_id not in maintenance_started:
            metrics["duplicate_or_partial_commit_count"] += terminal_count

    metric_names = (
        "hard_constraint_violation_count",
        "economy_cap_breach_count",
        "duplicate_or_partial_commit_count",
        "maintenance_failure_count",
        "maintenance_eligible_attempt_count",
        "h01_post_commit_attempt_degraded_count",
        "h01_post_commit_attempt_count",
        "performance_breach_count",
    )
    metric_snapshot: dict[str, int | float | None] = {name: _snapshot_number(metrics[name]) for name in metric_names}
    maintenance_denominator = metrics["maintenance_eligible_attempt_count"]
    h01_denominator = metrics["h01_post_commit_attempt_count"]
    metric_snapshot["maintenance_failure_rate"] = (
        None if maintenance_denominator == 0 else float(metrics["maintenance_failure_count"] / maintenance_denominator)
    )
    metric_snapshot["h01_post_commit_attempt_degraded_rate"] = (
        None if h01_denominator == 0 else float(metrics["h01_post_commit_attempt_degraded_count"] / h01_denominator)
    )

    scoped_distribution = [
        {
            "policy_version": scope.policy_version,
            "reference_snapshot_version": scope.reference_snapshot_version,
            "prestige_band": scope.prestige_band,
            "breach_count": _snapshot_number(count),
        }
        for scope, count in sorted(distribution_breaches.items(), key=lambda item: item[0].label())
    ]
    sorted_arena = sorted(arena_ratios.items(), key=lambda item: item[0].label())
    scoped_arena = [
        {
            "kind": scope.kind,
            "prestige_band": scope.prestige_band,
            "sample_count": len(observations),
            "current_ratio_max": _snapshot_number(max(ratio for ratio, _context in observations)),
        }
        for scope, observations in sorted_arena
    ]
    for item, (_scope, observations) in zip(scoped_arena, sorted_arena):
        context_values = [context for _ratio, context in observations]
        if context_values and all("real_entry_count" in context for context in context_values):
            item["real_entry_count_min"] = min(context["real_entry_count"] for context in context_values)
        if context_values and all("reserve_ready_count" in context for context in context_values):
            item["reserve_ready_count_min"] = min(context["reserve_ready_count"] for context in context_values)
        if context_values and all("reserve_training_count" in context for context in context_values):
            item["reserve_training_count_max"] = max(context["reserve_training_count"] for context in context_values)
    frozen_arena_baselines = [
        {
            "kind": baseline.mode,
            "prestige_band": baseline.prestige_band,
            "baseline_ratio": _snapshot_number(baseline.baseline_ratio),
            "frozen_at": _canonical_timestamp(baseline.frozen_at),
            "evidence_id": baseline.evidence_id,
            "evidence_checksum": baseline.evidence_checksum,
            "payload_digest": baseline.payload_digest,
            "source": baseline.source,
            "expires_at": (_canonical_timestamp(baseline.expires_at) if baseline.expires_at is not None else None),
            "max_real_entry_count": baseline.max_real_entry_count,
        }
        for baseline in sorted(
            _arena_baselines,
            key=lambda item: (item.mode, item.prestige_band),
        )
    ]
    return {
        "schema_version": SAFETY_SNAPSHOT_SCHEMA_VERSION,
        "window_kind": window_kind,
        "window_start_at": _canonical_timestamp(start_at),
        "window_end_at": _canonical_timestamp(end_at),
        "event_count": len(events),
        "metrics": metric_snapshot,
        "heartbeat": _heartbeat_snapshot(
            heartbeat_timestamps,
            window_start_at=start_at,
            window_end_at=end_at,
        ),
        "aggregation_errors": sorted(aggregation_errors),
        "scoped_distribution_breaches": scoped_distribution,
        "scoped_arena_shortage_ratios": scoped_arena,
        "arena_shortage_baselines": frozen_arena_baselines,
    }


def _build_provider_snapshot(
    aggregation: SafetyMetricWindowAggregationInput,
) -> Mapping[str, Any]:
    baselines = safety_baselines.lock_frozen_arena_shortage_baselines(now=timezone.now())
    return build_safety_window_snapshot(
        window_kind=aggregation.window_kind,
        window_start_at=aggregation.window_start_at,
        _events=aggregation.events,
        _arena_baselines=baselines,
    )


def finalize_due_safety_windows(
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_WINDOW_LIMIT,
) -> tuple[SafetyMetricWindowFinalizeResult, ...]:
    """Aggregate and freeze open windows whose provider grace has elapsed."""

    resolved_limit = _validate_limit(limit)
    resolved_now = _aware_utc(
        timezone.now() if now is None else now,
        field="now",
    )
    due_before = resolved_now - SAFETY_WINDOW_GRACE
    windows = tuple(
        BotSafetyMetricWindow.objects.filter(
            finalized_at__isnull=True,
            window_end_at__lte=due_before,
        ).order_by(
            "window_end_at", "kind", "window_id"
        )[:resolved_limit]
    )
    results: list[SafetyMetricWindowFinalizeResult] = []
    for window in windows:
        results.append(
            aggregate_and_finalize_safety_metric_window(
                window_kind=window.kind,
                window_start_at=window.window_start_at,
                snapshot_builder=_build_provider_snapshot,
                finalized_at=resolved_now,
            )
        )
    return tuple(results)


def _snapshot_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise InvalidSafetySnapshotError(f"{field} must be numeric")
    try:
        normalized = Decimal(str(value))
    except InvalidOperation as exc:
        raise InvalidSafetySnapshotError(f"{field} must be numeric") from exc
    if not normalized.is_finite() or normalized < 0:
        raise InvalidSafetySnapshotError(f"{field} must be finite and non-negative")
    return normalized


def _snapshot_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidSafetySnapshotError(f"{field} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidSafetySnapshotError(f"{field} must be a UTC timestamp") from exc
    if timezone.is_naive(parsed):
        raise InvalidSafetySnapshotError(f"{field} must be a UTC timestamp")
    return parsed.astimezone(UTC)


def _parse_scope_list(value: object) -> frozenset[_Scope]:
    if not isinstance(value, list):
        raise InvalidSafetySnapshotError("scoped_distribution_breaches must be a list")
    scopes: set[_Scope] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise InvalidSafetySnapshotError("distribution scope must be a mapping")
        scope = _scope_from_dimensions(item)
        if scope is None:
            raise InvalidSafetySnapshotError("distribution scope is incomplete")
        count = _snapshot_decimal(item.get("breach_count"), field="distribution breach_count")
        if count > 0:
            scopes.add(scope)
    return frozenset(scopes)


def _parse_arena_ratios(
    value: object,
    *,
    schema_version: int,
) -> dict[_ArenaScope, _ArenaRatioFacts]:
    if not isinstance(value, list):
        raise InvalidSafetySnapshotError("scoped_arena_shortage_ratios must be a list")
    ratios: dict[_ArenaScope, _ArenaRatioFacts] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise InvalidSafetySnapshotError("arena shortage scope must be a mapping")
        scope = _arena_scope_from_dimensions(item)
        if scope is None:
            raise InvalidSafetySnapshotError("arena shortage scope is incomplete")
        ratio = _snapshot_decimal(item.get("current_ratio_max"), field="arena current_ratio_max")
        if ratio > 1:
            raise InvalidSafetySnapshotError("arena current_ratio_max may not exceed 1")
        sample_count = item.get("sample_count")
        if sample_count is None:
            if schema_version != SAFETY_SNAPSHOT_SCHEMA_VERSION_V1:
                raise InvalidSafetySnapshotError("arena sample_count must be a positive integer")
            sample_count = 1
        elif isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
            raise InvalidSafetySnapshotError("arena sample_count must be a positive integer")
        optional_counts: dict[str, int | None] = {}
        for field in (
            "real_entry_count_min",
            "reserve_ready_count_min",
            "reserve_training_count_max",
        ):
            raw_count = item.get(field)
            if raw_count is None:
                optional_counts[field] = None
                continue
            if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                raise InvalidSafetySnapshotError(f"arena {field} must be a non-negative integer")
            optional_counts[field] = raw_count
        if scope in ratios:
            raise InvalidSafetySnapshotError("arena shortage scope is duplicated")
        ratios[scope] = _ArenaRatioFacts(
            ratio=ratio,
            sample_count=sample_count,
            real_entry_count_min=optional_counts["real_entry_count_min"],
            reserve_ready_count_min=optional_counts["reserve_ready_count_min"],
            reserve_training_count_max=optional_counts["reserve_training_count_max"],
        )
    return ratios


def _parse_arena_baselines(
    value: object,
    *,
    schema_version: int,
) -> dict[_ArenaScope, _ArenaBaselineFacts]:
    if not isinstance(value, list):
        raise InvalidSafetySnapshotError("arena_shortage_baselines must be a list")
    baselines: dict[_ArenaScope, _ArenaBaselineFacts] = {}
    provenance_fields = {
        "kind",
        "prestige_band",
        "baseline_ratio",
        "frozen_at",
        "evidence_id",
        "evidence_checksum",
        "payload_digest",
    }
    lifecycle_fields = {
        "source",
        "expires_at",
        "max_real_entry_count",
    }
    expected_fields = provenance_fields | (lifecycle_fields if schema_version >= 3 else set())
    for item in value:
        if not isinstance(item, Mapping):
            raise InvalidSafetySnapshotError("arena baseline must be a mapping")
        if set(item) != expected_fields:
            raise InvalidSafetySnapshotError("arena baseline must contain the canonical provenance fields")
        scope = _arena_scope_from_dimensions(item)
        if scope is None:
            raise InvalidSafetySnapshotError("arena baseline scope is incomplete")
        baseline = _snapshot_decimal(item.get("baseline_ratio"), field="arena baseline_ratio")
        if baseline > 1:
            raise InvalidSafetySnapshotError("arena baseline_ratio may not exceed 1")
        frozen_at = _snapshot_datetime(item.get("frozen_at"), field="arena frozen_at")
        try:
            request = safety_baselines.normalize_arena_shortage_baseline_request(
                mode=scope.kind,
                prestige_band=scope.prestige_band,
                baseline_ratio=baseline,
                evidence_id=item.get("evidence_id"),
                evidence_checksum=item.get("evidence_checksum"),
            )
        except safety_baselines.ArenaShortageBaselineError as exc:
            raise InvalidSafetySnapshotError("arena baseline provenance is invalid") from exc
        if item.get("payload_digest") != request.payload_digest:
            raise InvalidSafetySnapshotError("arena baseline payload digest differs")
        if scope in baselines:
            raise InvalidSafetySnapshotError("arena baseline scope is duplicated")
        source: str = safety_baselines.BASELINE_SOURCE_PRE_ACTIVATION
        expires_at: datetime | None = None
        max_real_entry_count: int | None = None
        if schema_version >= 3:
            raw_source = item.get("source")
            if not isinstance(raw_source, str) or raw_source not in {
                safety_baselines.BASELINE_SOURCE_PRE_ACTIVATION,
                safety_baselines.BASELINE_SOURCE_RUNTIME_BOOTSTRAP,
            }:
                raise InvalidSafetySnapshotError("arena baseline source is invalid")
            source = raw_source
            if source == safety_baselines.BASELINE_SOURCE_RUNTIME_BOOTSTRAP:
                expires_at = _snapshot_datetime(item.get("expires_at"), field="arena baseline expires_at")
                if expires_at <= frozen_at:
                    raise InvalidSafetySnapshotError("arena baseline expires_at must be after frozen_at")
                raw_max = item.get("max_real_entry_count")
                if isinstance(raw_max, bool) or not isinstance(raw_max, int) or raw_max < 1:
                    raise InvalidSafetySnapshotError("arena baseline max_real_entry_count must be positive")
                max_real_entry_count = raw_max
            elif item.get("expires_at") is not None or item.get("max_real_entry_count") is not None:
                raise InvalidSafetySnapshotError("pre-activation baseline must not have lifecycle metadata")
        baselines[scope] = _ArenaBaselineFacts(
            ratio=baseline,
            source=source,
            expires_at=expires_at,
            max_real_entry_count=max_real_entry_count,
        )
    return baselines


def _parse_window_facts(window: BotSafetyMetricWindow) -> _WindowFacts:
    if window.finalized_at is None:
        raise InvalidSafetySnapshotError("safety monitor only accepts finalized windows")
    snapshot = window.snapshot
    if not isinstance(snapshot, Mapping):
        raise InvalidSafetySnapshotError("safety window snapshot must be a mapping")
    try:
        encoded_snapshot = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise InvalidSafetySnapshotError("safety window snapshot is not canonical JSON") from exc
    if sha256(encoded_snapshot).hexdigest() != window.snapshot_digest:
        raise InvalidSafetySnapshotError("safety window snapshot digest differs")
    raw_schema_version = snapshot.get("schema_version")
    if (
        isinstance(raw_schema_version, bool)
        or not isinstance(raw_schema_version, int)
        or raw_schema_version not in SUPPORTED_SAFETY_SNAPSHOT_SCHEMA_VERSIONS
    ):
        raise InvalidSafetySnapshotError("unsupported safety snapshot schema version")
    schema_version = raw_schema_version
    if snapshot.get("window_kind") != window.kind:
        raise InvalidSafetySnapshotError("snapshot window_kind differs from window row")
    start_at = _snapshot_datetime(snapshot.get("window_start_at"), field="window_start_at")
    end_at = _snapshot_datetime(snapshot.get("window_end_at"), field="window_end_at")
    if start_at != window.window_start_at.astimezone(UTC):
        raise InvalidSafetySnapshotError("snapshot window_start_at differs from window row")
    if end_at != window.window_end_at.astimezone(UTC):
        raise InvalidSafetySnapshotError("snapshot window_end_at differs from window row")
    duration = _window_duration(window.kind)
    if end_at - start_at != duration:
        raise InvalidSafetySnapshotError("safety window has invalid fixed duration")
    expected_start = (
        start_at.replace(minute=0, second=0, microsecond=0)
        if window.kind == BotSafetyMetricWindow.Kind.HOURLY
        else start_at.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    if start_at != expected_start:
        raise InvalidSafetySnapshotError("safety window is not UTC aligned")
    expected_window_id = f"{window.kind}:{start_at.strftime('%Y%m%dT%H%M%SZ')}"
    if window.window_id != expected_window_id:
        raise InvalidSafetySnapshotError("safety window_id differs from its boundary")

    raw_metrics = snapshot.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        raise InvalidSafetySnapshotError("snapshot metrics must be a mapping")
    required_metrics = (
        "hard_constraint_violation_count",
        "economy_cap_breach_count",
        "duplicate_or_partial_commit_count",
        "maintenance_failure_count",
        "maintenance_eligible_attempt_count",
        "h01_post_commit_attempt_degraded_count",
        "h01_post_commit_attempt_count",
        "performance_breach_count",
    )
    metrics = {name: _snapshot_decimal(raw_metrics.get(name), field=f"metrics.{name}") for name in required_metrics}
    if metrics["maintenance_failure_count"] > metrics["maintenance_eligible_attempt_count"]:
        raise InvalidSafetySnapshotError("maintenance failures exceed eligible attempts")
    if metrics["h01_post_commit_attempt_degraded_count"] > metrics["h01_post_commit_attempt_count"]:
        raise InvalidSafetySnapshotError("H-01 degraded attempts exceed all attempts")

    raw_heartbeat = snapshot.get("heartbeat")
    if not isinstance(raw_heartbeat, Mapping):
        raise InvalidSafetySnapshotError("snapshot heartbeat must be a mapping")
    raw_streams = raw_heartbeat.get("streams")
    if not isinstance(raw_streams, Mapping):
        raise InvalidSafetySnapshotError("snapshot heartbeat streams must be a mapping")
    incomplete_streams = tuple(
        stream
        for stream in REQUIRED_HEARTBEAT_STREAMS
        if not isinstance(raw_streams.get(stream), Mapping) or raw_streams[stream].get("complete") is not True
    )
    if raw_heartbeat.get("complete") is not (not incomplete_streams):
        raise InvalidSafetySnapshotError("snapshot heartbeat completeness is inconsistent")

    raw_errors = snapshot.get("aggregation_errors")
    if not isinstance(raw_errors, list) or not all(isinstance(item, str) and item for item in raw_errors):
        raise InvalidSafetySnapshotError("aggregation_errors must be a string list")

    return _WindowFacts(
        window=window,
        metrics=metrics,
        incomplete_heartbeat_streams=incomplete_streams,
        aggregation_errors=tuple(sorted(set(raw_errors))),
        distribution_breaches=_parse_scope_list(snapshot.get("scoped_distribution_breaches")),
        arena_ratios=_parse_arena_ratios(
            snapshot.get("scoped_arena_shortage_ratios"),
            schema_version=schema_version,
        ),
        arena_baselines=_parse_arena_baselines(
            snapshot.get("arena_shortage_baselines"),
            schema_version=schema_version,
        ),
    )


def _rate_breached(
    facts: _WindowFacts,
    *,
    numerator: str,
    denominator: str,
    threshold: Decimal,
) -> bool:
    denominator_value = facts.metrics[denominator]
    if denominator_value == 0:
        return False
    return facts.metrics[numerator] / denominator_value > threshold


def _consecutive_facts(
    current: _WindowFacts,
    history: Mapping[datetime, _WindowFacts],
    *,
    count: int,
) -> tuple[_WindowFacts, ...] | None:
    duration = _window_duration(current.window.kind)
    facts = [current]
    expected_end = current.window.window_end_at.astimezone(UTC)
    for _index in range(count - 1):
        expected_end -= duration
        previous = history.get(expected_end)
        if previous is None:
            return None
        facts.append(previous)
    return tuple(facts)


def _parse_window_history(
    window: BotSafetyMetricWindow,
    history: Iterable[BotSafetyMetricWindow],
) -> tuple[_WindowFacts, dict[datetime, _WindowFacts]]:
    current = _parse_window_facts(window)
    history_facts: dict[datetime, _WindowFacts] = {}
    for previous_window in history:
        previous = _parse_window_facts(previous_window)
        if previous.window.kind != window.kind:
            continue
        previous_end = previous.window.window_end_at.astimezone(UTC)
        if previous_end >= window.window_end_at.astimezone(UTC):
            raise InvalidSafetySnapshotError("history must contain older windows only")
        if previous_end in history_facts:
            raise InvalidSafetySnapshotError("history contains a duplicate window end")
        history_facts[previous_end] = previous
    return current, history_facts


_BASELINE_BOOTSTRAP_HEALTH_METRICS: Final = (
    "hard_constraint_violation_count",
    "economy_cap_breach_count",
    "duplicate_or_partial_commit_count",
    "maintenance_failure_count",
    "h01_post_commit_attempt_degraded_count",
    "performance_breach_count",
)


def _bootstrap_window_is_healthy(facts: _WindowFacts) -> bool:
    return (
        not facts.incomplete_heartbeat_streams
        and not facts.aggregation_errors
        and all(facts.metrics[name] == 0 for name in _BASELINE_BOOTSTRAP_HEALTH_METRICS)
    )


def _arena_baseline_facts_from_snapshot(
    baseline: safety_baselines.ArenaShortageBaselineSnapshot,
) -> _ArenaBaselineFacts:
    return _ArenaBaselineFacts(
        ratio=baseline.baseline_ratio,
        source=baseline.source,
        expires_at=baseline.expires_at,
        max_real_entry_count=baseline.max_real_entry_count,
    )


def _arena_baseline_facts_active(
    baseline: _ArenaBaselineFacts,
    *,
    now: datetime,
) -> bool:
    if baseline.source != safety_baselines.BASELINE_SOURCE_RUNTIME_BOOTSTRAP:
        return True
    return baseline.expires_at is not None and baseline.expires_at > now


def _arena_baseline_facts_invalid_reason(
    baseline: _ArenaBaselineFacts,
    *,
    arena_facts: _ArenaRatioFacts,
    scope: _ArenaScope,
    now: datetime,
) -> str | None:
    if baseline.source != safety_baselines.BASELINE_SOURCE_RUNTIME_BOOTSTRAP:
        return None
    if baseline.expires_at is None:
        return f"arena_shortage_baseline_missing:{scope.label()}"
    if baseline.expires_at <= now:
        return f"arena_shortage_baseline_expired:{scope.label()}"
    if baseline.max_real_entry_count is None:
        return f"arena_shortage_baseline_missing:{scope.label()}"
    if arena_facts.real_entry_count_min is None or arena_facts.real_entry_count_min > baseline.max_real_entry_count:
        return f"arena_shortage_baseline_population_exceeded:{scope.label()}"
    return None


def _arena_baseline_bootstrap_ratio_cap(
    arena_observations: Sequence[_ArenaRatioFacts],
) -> Decimal:
    configured_cap = Decimal(str(settings.ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MAX_RATIO))
    if not arena_observations:
        return configured_cap
    early_game_limit = int(settings.ARENA_SHORTAGE_BASELINE_BOOTSTRAP_EARLY_GAME_MAX_REAL_ENTRIES)
    if any(
        facts.real_entry_count_min is None
        or facts.real_entry_count_min < 1
        or facts.real_entry_count_min > early_game_limit
        for facts in arena_observations
    ):
        return configured_cap
    early_game_cap = Decimal(str(settings.ARENA_SHORTAGE_BASELINE_BOOTSTRAP_EARLY_GAME_MAX_RATIO))
    return max(configured_cap, early_game_cap)


def _bootstrap_missing_arena_baselines(
    window: BotSafetyMetricWindow,
    *,
    history: Iterable[BotSafetyMetricWindow],
    now: datetime | None = None,
) -> dict[_ArenaScope, _ArenaBaselineFacts]:
    """Freeze only conservative, repeated observations and return temporary overrides."""
    try:
        current, history_facts = _parse_window_history(window, history)
    except InvalidSafetySnapshotError:
        return {}

    mature_windows = _consecutive_facts(
        current,
        history_facts,
        count=int(settings.ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MIN_MATURE_WINDOWS),
    )
    if mature_windows is None or not all(_bootstrap_window_is_healthy(facts) for facts in mature_windows):
        return {}

    reference_at = _aware_utc(timezone.now() if now is None else now, field="now")
    existing_scopes = {
        scope
        for facts in mature_windows
        for scope, baseline in facts.arena_baselines.items()
        if _arena_baseline_facts_active(baseline, now=reference_at)
    }
    configured_max_ratio = Decimal(str(settings.ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MAX_RATIO))
    overrides: dict[_ArenaScope, _ArenaBaselineFacts] = {}
    for scope in sorted(current.arena_ratios, key=lambda item: item.label()):
        if scope in existing_scopes:
            continue
        observations: list[tuple[_WindowFacts, _ArenaRatioFacts]] = []
        for facts in mature_windows:
            arena_facts = facts.arena_ratios.get(scope)
            if arena_facts is None or arena_facts.real_entry_count_min is None or arena_facts.real_entry_count_min < 1:
                break
            if (
                arena_facts.reserve_ready_count_min is None
                or arena_facts.reserve_training_count_max is None
                or arena_facts.reserve_ready_count_min + arena_facts.reserve_training_count_max <= 0
            ):
                break
            observations.append((facts, arena_facts))
        if len(observations) != len(mature_windows):
            continue

        ratios = [arena_facts.ratio for _facts, arena_facts in observations]
        candidate_ratio = max(ratios)
        effective_max_ratio = _arena_baseline_bootstrap_ratio_cap([arena_facts for _facts, arena_facts in observations])
        if candidate_ratio > effective_max_ratio or candidate_ratio - min(ratios) > ARENA_SHORTAGE_INCREASE_THRESHOLD:
            continue
        max_real_entry_count = max(int(arena_facts.real_entry_count_min or 0) for _facts, arena_facts in observations)

        evidence_payload = {
            "schema_version": 1,
            "scope": {"mode": scope.kind, "prestige_band": scope.prestige_band},
            "observations": [
                {
                    "window_id": facts.window.window_id,
                    "ratio": format(arena_facts.ratio, ".12f"),
                    "real_entry_count_min": arena_facts.real_entry_count_min,
                    "reserve_ready_count_min": arena_facts.reserve_ready_count_min,
                    "reserve_training_count_max": arena_facts.reserve_training_count_max,
                }
                for facts, arena_facts in observations
            ],
            "configured_max_ratio": format(configured_max_ratio, ".12f"),
            "effective_max_ratio": format(effective_max_ratio, ".12f"),
            "early_game_relaxation": effective_max_ratio > configured_max_ratio,
            "max_real_entry_count": max_real_entry_count,
            "max_spread": format(ARENA_SHORTAGE_INCREASE_THRESHOLD, ".12f"),
        }
        evidence_checksum = sha256(canonical_json_bytes(evidence_payload)).hexdigest()
        last_window_end = observations[0][0].window.window_end_at.astimezone(UTC)
        evidence_id = (
            f"auto-bootstrap/{scope.kind}/{scope.prestige_band}/" f"{last_window_end.strftime('%Y%m%dT%H%M%SZ')}"
        )
        try:
            result = safety_baselines.freeze_arena_shortage_baseline_runtime(
                mode=scope.kind,
                prestige_band=scope.prestige_band,
                baseline_ratio=candidate_ratio,
                evidence_id=evidence_id,
                evidence_checksum=evidence_checksum,
                max_real_entry_count=max_real_entry_count,
            )
        except (
            safety_baselines.ArenaShortageBaselineActivationBlocked,
            safety_baselines.ArenaShortageBaselineConflict,
            safety_baselines.ArenaShortageBaselineCorrupt,
        ):
            continue
        if result.created:
            logger.info(
                "arena shortage baseline bootstrapped from mature safety windows",
                extra={
                    "event": "arena_shortage_baseline_auto_bootstrapped",
                    "mode": scope.kind,
                    "prestige_band": scope.prestige_band,
                    "baseline_ratio": str(result.baseline.baseline_ratio),
                    "evidence_id": evidence_id,
                    "window_ids": [facts.window.window_id for facts, _arena_facts in observations],
                },
            )
        overrides[scope] = _arena_baseline_facts_from_snapshot(result.baseline)
    return overrides


def evaluate_finalized_safety_window(
    window: BotSafetyMetricWindow,
    *,
    history: Iterable[BotSafetyMetricWindow] = (),
    baseline_overrides: Mapping[_ArenaScope, _ArenaBaselineFacts] | None = None,
    now: datetime | None = None,
) -> SafetyWindowDecision:
    """Evaluate one immutable snapshot without applying routing state changes."""

    current, history_facts = _parse_window_history(window, history)
    reference_at = timezone.now() if now is None else _aware_utc(now, field="now")

    reasons: list[str] = []
    reasons.extend(f"aggregation_error:{reason}" for reason in current.aggregation_errors)
    reasons.extend(f"heartbeat_incomplete:{stream}" for stream in current.incomplete_heartbeat_streams)
    if current.metrics["hard_constraint_violation_count"] >= 1:
        reasons.append("hard_constraint_violation")
    if current.metrics["economy_cap_breach_count"] >= 1:
        reasons.append("economy_cap_breach")
    if current.metrics["duplicate_or_partial_commit_count"] >= 1:
        reasons.append("duplicate_or_partial_commit")

    if window.kind == BotSafetyMetricWindow.Kind.HOURLY:
        maintenance_facts = _consecutive_facts(
            current,
            history_facts,
            count=_MAINTENANCE_FAILURE_WINDOWS,
        )
        if maintenance_facts is not None and all(
            _rate_breached(
                facts,
                numerator="maintenance_failure_count",
                denominator="maintenance_eligible_attempt_count",
                threshold=MAINTENANCE_FAILURE_RATE_THRESHOLD,
            )
            for facts in maintenance_facts
        ):
            reasons.append("maintenance_failure_rate")

        h01_facts = _consecutive_facts(
            current,
            history_facts,
            count=_H01_DEGRADED_WINDOWS,
        )
        if h01_facts is not None and all(
            _rate_breached(
                facts,
                numerator="h01_post_commit_attempt_degraded_count",
                denominator="h01_post_commit_attempt_count",
                threshold=H01_DEGRADED_RATE_THRESHOLD,
            )
            for facts in h01_facts
        ):
            reasons.append("h01_post_commit_attempt_degraded_rate")

        performance_facts = _consecutive_facts(
            current,
            history_facts,
            count=_PERFORMANCE_BREACH_WINDOWS,
        )
        if performance_facts is not None and all(
            facts.metrics["performance_breach_count"] >= 1 for facts in performance_facts
        ):
            reasons.append("performance_breach")

    if window.kind == BotSafetyMetricWindow.Kind.DAILY:
        distribution_facts = _consecutive_facts(
            current,
            history_facts,
            count=_DISTRIBUTION_BREACH_WINDOWS,
        )
        if distribution_facts is not None:
            repeated_scopes = set(distribution_facts[0].distribution_breaches)
            for facts in distribution_facts[1:]:
                repeated_scopes.intersection_update(facts.distribution_breaches)
            reasons.extend(
                f"distribution_breach:{scope.label()}"
                for scope in sorted(repeated_scopes, key=lambda item: item.label())
            )

    baseline_values: dict[_ArenaScope, set[_ArenaBaselineFacts]] = defaultdict(set)
    for facts in [current, *history_facts.values()]:
        for scope, baseline in facts.arena_baselines.items():
            baseline_values[scope].add(baseline)
    for scope, arena_facts in sorted(current.arena_ratios.items(), key=lambda item: item[0].label()):
        current_ratio = arena_facts.ratio
        candidates = set(baseline_values.get(scope, ()))
        if baseline_overrides is not None and scope in baseline_overrides:
            candidates = {baseline_overrides[scope]}
        invalid_reasons: list[str] = []
        active_baselines: set[_ArenaBaselineFacts] = set()
        for baseline in candidates:
            invalid_reason = _arena_baseline_facts_invalid_reason(
                baseline,
                arena_facts=arena_facts,
                scope=scope,
                now=reference_at,
            )
            if invalid_reason is not None:
                invalid_reasons.append(invalid_reason)
                continue
            active_baselines.add(baseline)
        if invalid_reasons:
            reasons.extend(sorted(set(invalid_reasons)))
            continue
        if not active_baselines:
            if arena_facts.is_cold_start:
                continue
            reasons.append(f"arena_shortage_baseline_missing:{scope.label()}")
            continue
        if len(active_baselines) > 1:
            reasons.append(f"arena_shortage_baseline_conflict:{scope.label()}")
            continue
        active_baseline = next(iter(active_baselines))
        if current_ratio - active_baseline.ratio > ARENA_SHORTAGE_INCREASE_THRESHOLD:
            reasons.append(f"arena_shortage_absolute_increase:{scope.label()}")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return SafetyWindowDecision(
        window_id=window.window_id,
        window_kind=window.kind,
        window_start_at=window.window_start_at.astimezone(UTC),
        window_end_at=window.window_end_at.astimezone(UTC),
        should_pause=bool(unique_reasons),
        pause_reasons=unique_reasons,
    )


def _fail_closed_decision(
    window: BotSafetyMetricWindow,
    *,
    reason: str,
) -> SafetyWindowDecision:
    return SafetyWindowDecision(
        window_id=window.window_id,
        window_kind=window.kind,
        window_start_at=window.window_start_at.astimezone(UTC),
        window_end_at=window.window_end_at.astimezone(UTC),
        should_pause=True,
        pause_reasons=(reason,),
    )


def _missing_window_decision(
    *,
    window_kind: str,
    window_end_at: datetime,
) -> SafetyWindowDecision:
    end_at = window_end_at.astimezone(UTC)
    start_at = end_at - _window_duration(window_kind)
    return SafetyWindowDecision(
        window_id=f"{window_kind}:{start_at.strftime('%Y%m%dT%H%M%SZ')}",
        window_kind=window_kind,
        window_start_at=start_at,
        window_end_at=end_at,
        should_pause=True,
        pause_reasons=(f"missing_finalized_{window_kind}_window",),
    )


def _allow_planned_restart_gap(
    decision: SafetyWindowDecision,
    *,
    routing: runtime_configs.RuntimeRoutingSnapshot,
) -> SafetyWindowDecision:
    """Consume planned-restart evidence gaps while keeping writes fenced.

    The explicit planned-restart marker proves that V2 writes were paused before
    the outage. Only missing-window and heartbeat-gap reasons are therefore
    recoverable here; hard constraints and malformed snapshots remain fail-closed.
    """

    if (
        routing.maintenance_mode is not MaintenanceMode.V2_PAUSED
        or not runtime_configs.is_planned_restart_pause_reason(routing.pause_reason)
        or not decision.pause_reasons
        or not all(
            reason.startswith(("missing_finalized_", "heartbeat_incomplete:")) for reason in decision.pause_reasons
        )
    ):
        return decision
    return SafetyWindowDecision(
        window_id=decision.window_id,
        window_kind=decision.window_kind,
        window_start_at=decision.window_start_at,
        window_end_at=decision.window_end_at,
        should_pause=False,
        pause_reasons=(),
    )


def _latest_mature_window_end(now: datetime, *, window_kind: str) -> datetime:
    grace_cutoff = now.astimezone(UTC) - SAFETY_WINDOW_GRACE
    if window_kind == BotSafetyMetricWindow.Kind.HOURLY:
        return grace_cutoff.replace(minute=0, second=0, microsecond=0)
    if window_kind == BotSafetyMetricWindow.Kind.DAILY:
        return grace_cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    raise SafetyMonitorError("window_kind must be hourly or daily")


def _apply_with_cas_retry(
    decision: SafetyWindowDecision,
    *,
    max_cas_attempts: int,
) -> tuple[runtime_configs.SafetyRoutingDecisionResult, int]:
    conflicts = 0
    for _attempt in range(max_cas_attempts):
        routing = runtime_configs.read_virtual_player_routing()
        if not routing.persisted or routing.revision is None:
            raise SafetyMonitorError("persisted virtual-player routing is required by the safety monitor")
        expected_pause_reason = (
            routing.pause_reason
            if not decision.should_pause
            and routing.maintenance_mode is runtime_configs.MaintenanceMode.V2_PAUSED
            and runtime_configs.is_recoverable_safety_pause_reason(routing.pause_reason)
            else ""
        )
        try:
            result = runtime_configs.apply_virtual_player_safety_decision(
                expected_revision=routing.revision,
                window_id=decision.window_id,
                window_kind=decision.window_kind,
                window_end_at=decision.window_end_at,
                should_pause=decision.should_pause,
                pause_reason=decision.pause_reason,
                resume_if_healthy=bool(expected_pause_reason),
                expected_pause_reason=expected_pause_reason,
            )
        except runtime_configs.RuntimeRoutingConflict:
            conflicts += 1
            continue
        return result, conflicts
    raise SafetyDecisionConflictExhausted(f"routing CAS conflicted {max_cas_attempts} times for {decision.window_id}")


def _unconsumed_window_query(
    routing: runtime_configs.RuntimeRoutingSnapshot,
) -> Q:
    hourly = Q(kind=BotSafetyMetricWindow.Kind.HOURLY)
    daily = Q(kind=BotSafetyMetricWindow.Kind.DAILY)
    if routing.last_hourly_safety_window_end_at is not None:
        hourly &= Q(window_end_at__gt=routing.last_hourly_safety_window_end_at)
    if routing.last_daily_safety_window_end_at is not None:
        daily &= Q(window_end_at__gt=routing.last_daily_safety_window_end_at)
    return hourly | daily


def monitor_finalized_safety_windows(
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_WINDOW_LIMIT,
    max_cas_retries: int = DEFAULT_MAX_CAS_ATTEMPTS,
) -> SafetyMonitorRunResult:
    """Consume finalized snapshots in monotonic cursor order and apply routing CAS."""

    resolved_limit = _validate_limit(limit)
    if isinstance(max_cas_retries, bool) or not isinstance(max_cas_retries, int) or not 1 <= max_cas_retries <= 20:
        raise SafetyMonitorError("max_cas_retries must be an integer between 1 and 20")
    resolved_now = _aware_utc(
        timezone.now() if now is None else now,
        field="now",
    )

    routing = runtime_configs.read_virtual_player_routing()
    if not routing.persisted or routing.revision is None:
        raise SafetyMonitorError("persisted virtual-player routing is required by the safety monitor")
    windows = tuple(
        BotSafetyMetricWindow.objects.filter(
            _unconsumed_window_query(routing),
            finalized_at__isnull=False,
        ).order_by(
            "window_end_at", "kind", "window_id"
        )[:resolved_limit]
    )

    decisions: list[SafetyWindowDecision] = []
    routing_results: list[runtime_configs.SafetyRoutingDecisionResult] = []
    conflict_count = 0
    current_routing = routing
    for window in windows:
        cursor = (
            current_routing.last_hourly_safety_window_end_at
            if window.kind == BotSafetyMetricWindow.Kind.HOURLY
            else current_routing.last_daily_safety_window_end_at
        )
        window_end_at = window.window_end_at.astimezone(UTC)
        if cursor is not None and cursor.astimezone(UTC) >= window_end_at:
            continue

        gap = False
        if cursor is not None:
            expected_end = cursor.astimezone(UTC) + _window_duration(window.kind)
            gap = expected_end != window_end_at
        history = tuple(
            BotSafetyMetricWindow.objects.filter(
                kind=window.kind,
                finalized_at__isnull=False,
                window_end_at__lt=window.window_end_at,
            ).order_by("-window_end_at")[
                : max(
                    _MAX_HISTORY_WINDOWS,
                    int(settings.ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MIN_MATURE_WINDOWS) - 1,
                )
            ]
        )
        if gap:
            decision = _fail_closed_decision(
                window,
                reason=f"missing_finalized_{window.kind}_window",
            )
        else:
            try:
                baseline_overrides = _bootstrap_missing_arena_baselines(
                    window,
                    history=history,
                    now=resolved_now,
                )
                decision = evaluate_finalized_safety_window(
                    window,
                    history=history,
                    baseline_overrides=baseline_overrides,
                    now=resolved_now,
                )
            except InvalidSafetySnapshotError as exc:
                decision = _fail_closed_decision(
                    window,
                    reason=f"invalid_safety_snapshot:{exc}",
                )
        decision = _allow_planned_restart_gap(decision, routing=current_routing)

        routing_result, conflicts = _apply_with_cas_retry(
            decision,
            max_cas_attempts=max_cas_retries,
        )
        decisions.append(decision)
        routing_results.append(routing_result)
        conflict_count += conflicts
        current_routing = routing_result.snapshot

    for window_kind in (
        BotSafetyMetricWindow.Kind.HOURLY,
        BotSafetyMetricWindow.Kind.DAILY,
    ):
        if len(decisions) >= resolved_limit:
            break
        cursor = (
            current_routing.last_hourly_safety_window_end_at
            if window_kind == BotSafetyMetricWindow.Kind.HOURLY
            else current_routing.last_daily_safety_window_end_at
        )
        latest_mature_end = _latest_mature_window_end(
            resolved_now,
            window_kind=window_kind,
        )
        if cursor is not None and cursor.astimezone(UTC) >= latest_mature_end:
            continue
        unconsumed = BotSafetyMetricWindow.objects.filter(
            kind=window_kind,
            finalized_at__isnull=False,
            window_end_at__lte=latest_mature_end,
        )
        if cursor is not None:
            unconsumed = unconsumed.filter(window_end_at__gt=cursor)
        if unconsumed.exists():
            continue
        decision = _missing_window_decision(
            window_kind=window_kind,
            window_end_at=latest_mature_end,
        )
        decision = _allow_planned_restart_gap(decision, routing=current_routing)
        routing_result, conflicts = _apply_with_cas_retry(
            decision,
            max_cas_attempts=max_cas_retries,
        )
        decisions.append(decision)
        routing_results.append(routing_result)
        conflict_count += conflicts
        current_routing = routing_result.snapshot

    return SafetyMonitorRunResult(
        decisions=tuple(decisions),
        routing_results=tuple(routing_results),
        cas_conflicts=conflict_count,
    )


def run_safety_monitor(
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_WINDOW_LIMIT,
    max_cas_retries: int = DEFAULT_MAX_CAS_ATTEMPTS,
) -> SafetyMonitorCycleResult:
    resolved_now = _aware_utc(
        timezone.now() if now is None else now,
        field="now",
    )
    finalized = finalize_due_safety_windows(now=resolved_now, limit=limit)
    monitored = monitor_finalized_safety_windows(
        now=resolved_now,
        limit=limit,
        max_cas_retries=max_cas_retries,
    )
    return SafetyMonitorCycleResult(
        finalized_windows=finalized,
        monitor=monitored,
    )


__all__ = [
    "ARENA_SHORTAGE_INCREASE_THRESHOLD",
    "DEFAULT_MAX_CAS_ATTEMPTS",
    "DEFAULT_WINDOW_LIMIT",
    "H01_DEGRADED_RATE_THRESHOLD",
    "HEARTBEAT_MAX_GAP",
    "MAINTENANCE_FAILURE_RATE_THRESHOLD",
    "SAFETY_SNAPSHOT_SCHEMA_VERSION",
    "SAFETY_SNAPSHOT_SCHEMA_VERSION_V1",
    "SAFETY_SNAPSHOT_SCHEMA_VERSION_V2",
    "SUPPORTED_SAFETY_SNAPSHOT_SCHEMA_VERSIONS",
    "InvalidSafetySnapshotError",
    "SafetyDecisionConflictExhausted",
    "SafetyMonitorCycleResult",
    "SafetyMonitorError",
    "SafetyMonitorRunResult",
    "SafetyWindowDecision",
    "build_safety_window_snapshot",
    "evaluate_finalized_safety_window",
    "finalize_due_safety_windows",
    "monitor_finalized_safety_windows",
    "run_safety_monitor",
]
