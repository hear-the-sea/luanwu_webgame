from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Final

from django.db.models import Q
from django.utils import timezone

from gameplay.models import BotSafetyMetricEvent, BotSafetyMetricWindow
from gameplay.services import runtime_configs

from . import safety_baselines
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

SAFETY_SNAPSHOT_SCHEMA_VERSION: Final = 1
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
class _WindowFacts:
    window: BotSafetyMetricWindow
    metrics: Mapping[str, Decimal]
    incomplete_heartbeat_streams: tuple[str, ...]
    aggregation_errors: tuple[str, ...]
    distribution_breaches: frozenset[_Scope]
    arena_ratios: Mapping[_ArenaScope, Decimal]
    arena_baselines: Mapping[_ArenaScope, Decimal]


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
    arena_ratios: dict[_ArenaScope, list[Decimal]] = defaultdict(list)
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
                arena_ratios[arena_scope].append(event.value)
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
    scoped_arena = [
        {
            "kind": scope.kind,
            "prestige_band": scope.prestige_band,
            "sample_count": len(values),
            "current_ratio_max": _snapshot_number(max(values)),
        }
        for scope, values in sorted(arena_ratios.items(), key=lambda item: item[0].label())
    ]
    frozen_arena_baselines = [
        {
            "kind": baseline.mode,
            "prestige_band": baseline.prestige_band,
            "baseline_ratio": _snapshot_number(baseline.baseline_ratio),
            "frozen_at": _canonical_timestamp(baseline.frozen_at),
            "evidence_id": baseline.evidence_id,
            "evidence_checksum": baseline.evidence_checksum,
            "payload_digest": baseline.payload_digest,
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
    baselines = safety_baselines.lock_frozen_arena_shortage_baselines()
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


def _parse_arena_ratios(value: object) -> dict[_ArenaScope, Decimal]:
    if not isinstance(value, list):
        raise InvalidSafetySnapshotError("scoped_arena_shortage_ratios must be a list")
    ratios: dict[_ArenaScope, Decimal] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise InvalidSafetySnapshotError("arena shortage scope must be a mapping")
        scope = _arena_scope_from_dimensions(item)
        if scope is None:
            raise InvalidSafetySnapshotError("arena shortage scope is incomplete")
        ratio = _snapshot_decimal(item.get("current_ratio_max"), field="arena current_ratio_max")
        if ratio > 1:
            raise InvalidSafetySnapshotError("arena current_ratio_max may not exceed 1")
        if scope in ratios:
            raise InvalidSafetySnapshotError("arena shortage scope is duplicated")
        ratios[scope] = ratio
    return ratios


def _parse_arena_baselines(value: object) -> dict[_ArenaScope, Decimal]:
    if not isinstance(value, list):
        raise InvalidSafetySnapshotError("arena_shortage_baselines must be a list")
    baselines: dict[_ArenaScope, Decimal] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise InvalidSafetySnapshotError("arena baseline must be a mapping")
        if set(item) != {
            "kind",
            "prestige_band",
            "baseline_ratio",
            "frozen_at",
            "evidence_id",
            "evidence_checksum",
            "payload_digest",
        }:
            raise InvalidSafetySnapshotError("arena baseline must contain the canonical provenance fields")
        scope = _arena_scope_from_dimensions(item)
        if scope is None:
            raise InvalidSafetySnapshotError("arena baseline scope is incomplete")
        baseline = _snapshot_decimal(item.get("baseline_ratio"), field="arena baseline_ratio")
        if baseline > 1:
            raise InvalidSafetySnapshotError("arena baseline_ratio may not exceed 1")
        _snapshot_datetime(item.get("frozen_at"), field="arena frozen_at")
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
        baselines[scope] = baseline
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
    if snapshot.get("schema_version") != SAFETY_SNAPSHOT_SCHEMA_VERSION:
        raise InvalidSafetySnapshotError("unsupported safety snapshot schema version")
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
        arena_ratios=_parse_arena_ratios(snapshot.get("scoped_arena_shortage_ratios")),
        arena_baselines=_parse_arena_baselines(snapshot.get("arena_shortage_baselines")),
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


def evaluate_finalized_safety_window(
    window: BotSafetyMetricWindow,
    *,
    history: Iterable[BotSafetyMetricWindow] = (),
) -> SafetyWindowDecision:
    """Evaluate one immutable snapshot without applying routing state changes."""

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

    baseline_values: dict[_ArenaScope, set[Decimal]] = defaultdict(set)
    for facts in [current, *history_facts.values()]:
        for scope, baseline in facts.arena_baselines.items():
            baseline_values[scope].add(baseline)
    for scope, current_ratio in sorted(current.arena_ratios.items(), key=lambda item: item[0].label()):
        baselines = baseline_values.get(scope, set())
        if not baselines:
            reasons.append(f"arena_shortage_baseline_missing:{scope.label()}")
        elif len(baselines) > 1:
            reasons.append(f"arena_shortage_baseline_conflict:{scope.label()}")
        elif current_ratio - next(iter(baselines)) > ARENA_SHORTAGE_INCREASE_THRESHOLD:
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
        try:
            result = runtime_configs.apply_virtual_player_safety_decision(
                expected_revision=routing.revision,
                window_id=decision.window_id,
                window_kind=decision.window_kind,
                window_end_at=decision.window_end_at,
                should_pause=decision.should_pause,
                pause_reason=decision.pause_reason,
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
            ).order_by("-window_end_at")[:_MAX_HISTORY_WINDOWS]
        )
        if gap:
            decision = _fail_closed_decision(
                window,
                reason=f"missing_finalized_{window.kind}_window",
            )
        else:
            try:
                decision = evaluate_finalized_safety_window(
                    window,
                    history=history,
                )
            except InvalidSafetySnapshotError as exc:
                decision = _fail_closed_decision(
                    window,
                    reason=f"invalid_safety_snapshot:{exc}",
                )

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
