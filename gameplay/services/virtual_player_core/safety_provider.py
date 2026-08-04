from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from typing import Any
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from gameplay.models import BotRuntimeRoutingState, BotSafetyMetricEvent, BotSafetyMetricWindow

SAFETY_EVENT_SCHEMA_VERSION = 1
SAFETY_WINDOW_GRACE = timedelta(minutes=5)
SAFETY_RAW_EVENT_RETENTION = timedelta(days=35)
SAFETY_CLOSED_WINDOW_RETENTION = timedelta(days=90)
SAFETY_CLEANUP_MAX_BATCH_SIZE = 10_000
SAFETY_METRIC_VALUE_QUANTUM = Decimal("0.000000000001")
SAFETY_METRIC_VALUE_ABS_LIMIT = Decimal("100000000000000000000")
SAFETY_MAX_DIMENSIONS = 8
SAFETY_MAX_SNAPSHOT_BYTES = 65_536
SAFETY_MAX_SNAPSHOT_DEPTH = 8
SAFETY_MAX_SNAPSHOT_NODES = 2_048
HARD_VIOLATION_METRIC_NAME = "virtual_player_safety_hard_violation"

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIMENSION_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ALLOWED_DIMENSION_KEYS = frozenset(
    {
        "action",
        "category",
        "engine_version",
        "failure_code",
        "from_band",
        "from_mode",
        "kind",
        "operation",
        "phase",
        "policy_version",
        "prestige_band",
        "real_entry_count",
        "reason",
        "reference_snapshot_version",
        "reserve_ready_count",
        "reserve_training_count",
        "result",
        "schedule_disposition",
        "source_metric",
        "stream",
        "to_band",
        "to_mode",
        "trigger",
        "virtual_entry_count",
        "window_kind",
    }
)
_INTEGER_DIMENSION_KEYS = frozenset(
    {
        "real_entry_count",
        "reserve_ready_count",
        "reserve_training_count",
        "virtual_entry_count",
    }
)


class SafetyProviderError(ValueError):
    pass


class InvalidSafetyMetricError(SafetyProviderError):
    pass


class SafetyMetricWindowNotReadyError(SafetyProviderError):
    pass


class SafetyHardViolationPersistenceError(SafetyProviderError):
    pass


class SafetyMetricHardViolationError(SafetyProviderError):
    def __init__(self, message: str, *, hard_violation_event_id: str) -> None:
        super().__init__(message)
        self.hard_violation_event_id = hard_violation_event_id


class SafetyMetricEventConflict(SafetyMetricHardViolationError):
    pass


class LateSafetyMetricEventError(SafetyMetricHardViolationError):
    pass


class SafetyMetricWindowConflict(SafetyMetricHardViolationError):
    pass


class SafetyMetricWindowExpiredError(SafetyProviderError):
    pass


@dataclass(frozen=True, slots=True)
class SafetyMetricEventWriteResult:
    event_id: str
    created: bool
    payload_digest: str


@dataclass(frozen=True, slots=True)
class SafetyMetricWindowFinalizeResult:
    window_id: str
    window_kind: str
    window_start_at: datetime
    window_end_at: datetime
    finalized_at: datetime
    newly_finalized: bool
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class SafetyMetricEventRecord:
    event_id: str
    metric_name: str
    occurred_at: datetime
    dimensions: Mapping[str, str | int]
    value: Decimal


@dataclass(frozen=True, slots=True)
class SafetyMetricWindowAggregationInput:
    window_id: str
    window_kind: str
    window_start_at: datetime
    window_end_at: datetime
    events: tuple[SafetyMetricEventRecord, ...]


@dataclass(frozen=True, slots=True)
class SafetyMetricCleanupResult:
    events_deleted: int
    windows_deleted: int
    event_cutoff: datetime
    window_cutoff: datetime


@dataclass(frozen=True, slots=True)
class _CanonicalEvent:
    event_id: str
    metric_name: str
    occurred_at: datetime
    dimensions: dict[str, str | int]
    value: Decimal
    canonical_value: str
    payload_digest: str


@dataclass(frozen=True, slots=True)
class _WindowSpec:
    window_id: str
    kind: str
    start_at: datetime
    end_at: datetime


def _database_utc_now() -> datetime:
    with connection.cursor() as cursor:
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        value = cursor.fetchone()[0]
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if timezone.is_naive(value):
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _aware_utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise InvalidSafetyMetricError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _canonical_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_event_id(value: object) -> str:
    if not isinstance(value, str) or not _EVENT_ID_RE.fullmatch(value):
        raise InvalidSafetyMetricError("event_id must be a canonical ASCII identifier")
    return value


def _normalize_metric_name(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise InvalidSafetyMetricError("metric_name must use canonical lowercase ASCII snake_case")
    return value


def _normalize_dimensions(value: object) -> dict[str, str | int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InvalidSafetyMetricError("dimensions must be a mapping")
    if len(value) > SAFETY_MAX_DIMENSIONS:
        raise InvalidSafetyMetricError(f"dimensions may contain at most {SAFETY_MAX_DIMENSIONS} entries")
    normalized: dict[str, str | int] = {}
    for key, raw_dimension in value.items():
        if not isinstance(key, str) or key not in _ALLOWED_DIMENSION_KEYS:
            raise InvalidSafetyMetricError(f"unsupported safety dimension: {key!r}")
        if key in _INTEGER_DIMENSION_KEYS:
            if isinstance(raw_dimension, bool) or not isinstance(raw_dimension, int) or raw_dimension < 0:
                raise InvalidSafetyMetricError(f"dimensions.{key} must be a non-negative integer")
            normalized[key] = raw_dimension
            continue
        if not isinstance(raw_dimension, str) or not _DIMENSION_VALUE_RE.fullmatch(raw_dimension):
            raise InvalidSafetyMetricError(f"dimensions.{key} must be a canonical bounded ASCII string")
        normalized[key] = raw_dimension
    return dict(sorted(normalized.items()))


def _normalize_metric_value(value: object) -> tuple[Decimal, str]:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise InvalidSafetyMetricError("value must be a finite numeric value")
    try:
        normalized = Decimal(str(value))
    except InvalidOperation as exc:
        raise InvalidSafetyMetricError("value must be a finite numeric value") from exc
    if not normalized.is_finite() or abs(normalized) >= SAFETY_METRIC_VALUE_ABS_LIMIT:
        raise InvalidSafetyMetricError("value must be finite and fit the safety metric numeric range")
    try:
        with localcontext() as context:
            context.prec = 40
            quantized = normalized.quantize(SAFETY_METRIC_VALUE_QUANTUM)
    except InvalidOperation as exc:
        raise InvalidSafetyMetricError("value must fit the safety metric numeric precision") from exc
    if quantized != normalized:
        raise InvalidSafetyMetricError("value may contain at most 12 fractional decimal places")
    if quantized == 0:
        quantized = Decimal(0).quantize(SAFETY_METRIC_VALUE_QUANTUM)
    canonical = format(quantized, "f").rstrip("0").rstrip(".")
    return quantized, canonical or "0"


def _event_payload_digest(
    *,
    metric_name: str,
    occurred_at: datetime,
    dimensions: Mapping[str, str | int],
    canonical_value: str,
) -> str:
    payload = {
        "schema_version": SAFETY_EVENT_SCHEMA_VERSION,
        "metric_name": metric_name,
        "occurred_at": _canonical_datetime(occurred_at),
        "dimensions": dict(dimensions),
        "value": canonical_value,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def _canonicalize_event(
    *,
    event_id: object,
    metric_name: object,
    occurred_at: object,
    dimensions: object,
    value: object,
) -> _CanonicalEvent:
    normalized_event_id = _normalize_event_id(event_id)
    normalized_metric_name = _normalize_metric_name(metric_name)
    normalized_occurred_at = _aware_utc(occurred_at, field="occurred_at")
    normalized_dimensions = _normalize_dimensions(dimensions)
    normalized_value, canonical_value = _normalize_metric_value(value)
    return _CanonicalEvent(
        event_id=normalized_event_id,
        metric_name=normalized_metric_name,
        occurred_at=normalized_occurred_at,
        dimensions=normalized_dimensions,
        value=normalized_value,
        canonical_value=canonical_value,
        payload_digest=_event_payload_digest(
            metric_name=normalized_metric_name,
            occurred_at=normalized_occurred_at,
            dimensions=normalized_dimensions,
            canonical_value=canonical_value,
        ),
    )


def _window_id(kind: str, start_at: datetime) -> str:
    return f"{kind}:{start_at.strftime('%Y%m%dT%H%M%SZ')}"


def _window_spec(window_kind: object, window_start_at: object) -> _WindowSpec:
    if not isinstance(window_kind, str):
        raise InvalidSafetyMetricError("window_kind must be hourly or daily")
    try:
        kind = BotSafetyMetricWindow.Kind(window_kind)
    except (TypeError, ValueError) as exc:
        raise InvalidSafetyMetricError("window_kind must be hourly or daily") from exc
    start_at = _aware_utc(window_start_at, field="window_start_at")
    if kind == BotSafetyMetricWindow.Kind.HOURLY:
        aligned_start = start_at.replace(minute=0, second=0, microsecond=0)
        duration = timedelta(hours=1)
    else:
        aligned_start = start_at.replace(hour=0, minute=0, second=0, microsecond=0)
        duration = timedelta(days=1)
    if start_at != aligned_start:
        raise InvalidSafetyMetricError(f"{kind} window_start_at must align to a fixed UTC boundary")
    return _WindowSpec(
        window_id=_window_id(kind, start_at),
        kind=kind,
        start_at=start_at,
        end_at=start_at + duration,
    )


def _event_window_specs(occurred_at: datetime) -> tuple[_WindowSpec, _WindowSpec]:
    hourly_start = occurred_at.replace(minute=0, second=0, microsecond=0)
    daily_start = occurred_at.replace(hour=0, minute=0, second=0, microsecond=0)
    specs = (
        _window_spec(BotSafetyMetricWindow.Kind.HOURLY, hourly_start),
        _window_spec(BotSafetyMetricWindow.Kind.DAILY, daily_start),
    )
    ordered = sorted(specs, key=lambda item: (item.end_at, item.kind))
    return ordered[0], ordered[1]


def _window_matches_spec(
    window: BotSafetyMetricWindow,
    spec: _WindowSpec,
) -> bool:
    return (
        window.window_id == spec.window_id
        and window.kind == spec.kind
        and window.window_start_at.astimezone(UTC) == spec.start_at
        and window.window_end_at.astimezone(UTC) == spec.end_at
    )


def _decision_cursor_covers(spec: _WindowSpec) -> bool:
    field = (
        "last_hourly_safety_window_end_at"
        if spec.kind == BotSafetyMetricWindow.Kind.HOURLY
        else "last_daily_safety_window_end_at"
    )
    cursor = (
        BotRuntimeRoutingState.objects.filter(
            pk=BotRuntimeRoutingState.GLOBAL_KEY,
        )
        .values_list(field, flat=True)
        .first()
    )
    return cursor is not None and _aware_utc(cursor, field=field) >= spec.end_at


def _ensure_window_row(spec: _WindowSpec) -> None:
    defaults = {
        "kind": spec.kind,
        "window_start_at": spec.start_at,
        "window_end_at": spec.end_at,
    }
    try:
        with transaction.atomic():
            window, created = BotSafetyMetricWindow.objects.get_or_create(
                window_id=spec.window_id,
                defaults=defaults,
            )
    except IntegrityError:
        candidate = (
            BotSafetyMetricWindow.objects.filter(
                kind=spec.kind,
                window_start_at=spec.start_at,
            ).first()
            or BotSafetyMetricWindow.objects.filter(window_id=spec.window_id).first()
        )
        if candidate is None:
            raise
        window = candidate
        created = False
    if not _window_matches_spec(window, spec):
        raise SafetyProviderError("safety window identity already has different fixed UTC boundaries")
    if created and _decision_cursor_covers(spec):
        window.delete()
        raise SafetyMetricWindowExpiredError("safety window was already consumed and removed by retention cleanup")


def _select_locked_window_rows(
    ordered_specs: tuple[_WindowSpec, ...],
    *,
    shared: bool,
) -> tuple[BotSafetyMetricWindow, ...]:
    window_ids = [spec.window_id for spec in ordered_specs]
    if not shared or connection.vendor != "mysql":
        return tuple(
            BotSafetyMetricWindow.objects.select_for_update()
            .filter(window_id__in=window_ids)
            .order_by("window_end_at", "kind")
        )

    table = connection.ops.quote_name(BotSafetyMetricWindow._meta.db_table or "")
    window_id_column = connection.ops.quote_name(BotSafetyMetricWindow._meta.get_field("window_id").column or "")
    end_column = connection.ops.quote_name(BotSafetyMetricWindow._meta.get_field("window_end_at").column or "")
    kind_column = connection.ops.quote_name(BotSafetyMetricWindow._meta.get_field("kind").column or "")
    placeholders = ", ".join(["%s"] * len(window_ids))
    kind_db_column = connection.ops.quote_name(BotSafetyMetricWindow._meta.get_field("kind").column or "")
    start_column = connection.ops.quote_name(BotSafetyMetricWindow._meta.get_field("window_start_at").column or "")
    finalized_column = connection.ops.quote_name(BotSafetyMetricWindow._meta.get_field("finalized_at").column or "")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {window_id_column}, {kind_db_column}, {start_column}, "
            f"{end_column}, {finalized_column} FROM {table} "
            f"WHERE {window_id_column} IN ({placeholders}) "
            f"ORDER BY {end_column}, {kind_column} FOR SHARE",
            window_ids,
        )
        rows = cursor.fetchall()
    return tuple(
        BotSafetyMetricWindow(
            window_id=window_id,
            kind=kind,
            window_start_at=(
                window_start_at.replace(tzinfo=UTC) if timezone.is_naive(window_start_at) else window_start_at
            ),
            window_end_at=(window_end_at.replace(tzinfo=UTC) if timezone.is_naive(window_end_at) else window_end_at),
            finalized_at=(
                None
                if finalized_at is None
                else finalized_at.replace(tzinfo=UTC) if timezone.is_naive(finalized_at) else finalized_at
            ),
        )
        for window_id, kind, window_start_at, window_end_at, finalized_at in rows
    )


def _lock_window_rows(
    specs: tuple[_WindowSpec, ...],
    *,
    shared: bool = False,
) -> tuple[BotSafetyMetricWindow, ...]:
    ordered_specs = tuple(sorted(specs, key=lambda item: (item.end_at, item.kind)))
    window_ids = [spec.window_id for spec in ordered_specs]
    # Do not hold a partial window lock set while creating missing rows. Two first
    # writers could otherwise each hold one window and deadlock on the other.
    existing_ids = set(
        BotSafetyMetricWindow.objects.filter(window_id__in=window_ids).values_list(
            "window_id",
            flat=True,
        )
    )
    for spec in ordered_specs:
        if spec.window_id not in existing_ids:
            _ensure_window_row(spec)
    locked = _select_locked_window_rows(ordered_specs, shared=shared)
    if len(locked) != len(ordered_specs):
        raise SafetyProviderError("failed to lock every required safety window")
    expected = {spec.window_id: spec for spec in ordered_specs}
    if any(not _window_matches_spec(window, expected[window.window_id]) for window in locked):
        raise SafetyProviderError("locked safety window boundaries changed")
    return locked


def _stored_event_matches(
    event: BotSafetyMetricEvent,
    canonical: _CanonicalEvent,
) -> bool:
    try:
        stored_value, stored_canonical_value = _normalize_metric_value(event.value)
        stored_digest = _event_payload_digest(
            metric_name=event.metric_name,
            occurred_at=_aware_utc(event.occurred_at, field="stored occurred_at"),
            dimensions=_normalize_dimensions(event.dimensions),
            canonical_value=stored_canonical_value,
        )
    except InvalidSafetyMetricError:
        return False
    return (
        event.event_id == canonical.event_id
        and event.metric_name == canonical.metric_name
        and event.occurred_at.astimezone(UTC) == canonical.occurred_at
        and event.dimensions == canonical.dimensions
        and stored_value == canonical.value
        and event.payload_digest == canonical.payload_digest == stored_digest
    )


def _event_result(
    event: BotSafetyMetricEvent,
    *,
    created: bool,
) -> SafetyMetricEventWriteResult:
    return SafetyMetricEventWriteResult(
        event_id=event.event_id,
        created=created,
        payload_digest=event.payload_digest,
    )


def _write_event_once(
    canonical: _CanonicalEvent,
) -> tuple[SafetyMetricEventWriteResult | None, str]:
    with transaction.atomic():
        existing = BotSafetyMetricEvent.objects.filter(event_id=canonical.event_id).first()
        if existing is not None:
            if _stored_event_matches(existing, canonical):
                return _event_result(existing, created=False), ""
            return None, "event_id_payload_conflict"
        if canonical.occurred_at < _database_utc_now() - SAFETY_RAW_EVENT_RETENTION:
            return None, "event_outside_retention"

        locked_windows = _lock_window_rows(
            _event_window_specs(canonical.occurred_at),
            shared=True,
        )
        existing = BotSafetyMetricEvent.objects.filter(event_id=canonical.event_id).first()
        if existing is not None:
            if _stored_event_matches(existing, canonical):
                return _event_result(existing, created=False), ""
            return None, "event_id_payload_conflict"
        if any(window.finalized_at is not None for window in locked_windows):
            return None, "late_event_after_finalization"

        try:
            with transaction.atomic():
                event = BotSafetyMetricEvent.objects.create(
                    event_id=canonical.event_id,
                    metric_name=canonical.metric_name,
                    occurred_at=canonical.occurred_at,
                    dimensions=canonical.dimensions,
                    value=canonical.value,
                    payload_digest=canonical.payload_digest,
                )
        except IntegrityError:
            existing = BotSafetyMetricEvent.objects.get(event_id=canonical.event_id)
            if _stored_event_matches(existing, canonical):
                return _event_result(existing, created=False), ""
            return None, "event_id_payload_conflict"
        return _event_result(event, created=True), ""


def _write_events_once(
    canonicals: Sequence[_CanonicalEvent],
) -> tuple[tuple[SafetyMetricEventWriteResult, ...] | None, str, str]:
    if not canonicals:
        return (), "", ""

    canonical_by_id: dict[str, _CanonicalEvent] = {}
    for canonical in canonicals:
        existing_canonical = canonical_by_id.get(canonical.event_id)
        if existing_canonical is not None and existing_canonical.payload_digest != canonical.payload_digest:
            return None, "event_id_payload_conflict", canonical.metric_name
        canonical_by_id[canonical.event_id] = canonical

    event_ids = tuple(canonical_by_id)
    created_ids: set[str] = set()
    with transaction.atomic():
        stored_by_id = BotSafetyMetricEvent.objects.in_bulk(
            event_ids,
            field_name="event_id",
        )
        for event_id, stored in stored_by_id.items():
            canonical = canonical_by_id[event_id]
            if not _stored_event_matches(stored, canonical):
                return None, "event_id_payload_conflict", canonical.metric_name

        missing_by_id = {
            event_id: canonical for event_id, canonical in canonical_by_id.items() if event_id not in stored_by_id
        }
        if missing_by_id:
            retention_cutoff = _database_utc_now() - SAFETY_RAW_EVENT_RETENTION
            for canonical in missing_by_id.values():
                if canonical.occurred_at < retention_cutoff:
                    return None, "event_outside_retention", canonical.metric_name

            specs_by_id = {
                spec.window_id: spec
                for canonical in missing_by_id.values()
                for spec in _event_window_specs(canonical.occurred_at)
            }
            locked_windows = _lock_window_rows(
                tuple(specs_by_id.values()),
                shared=True,
            )
            locked_by_id = {window.window_id: window for window in locked_windows}

            for canonical in missing_by_id.values():
                if any(
                    locked_by_id[spec.window_id].finalized_at is not None
                    for spec in _event_window_specs(canonical.occurred_at)
                ):
                    return None, "late_event_after_finalization", canonical.metric_name

            pending = dict(missing_by_id)
            while pending:
                events = [
                    BotSafetyMetricEvent(
                        event_id=canonical.event_id,
                        metric_name=canonical.metric_name,
                        occurred_at=canonical.occurred_at,
                        dimensions=canonical.dimensions,
                        value=canonical.value,
                        payload_digest=canonical.payload_digest,
                    )
                    for canonical in pending.values()
                ]
                try:
                    with transaction.atomic():
                        BotSafetyMetricEvent.objects.bulk_create(events)
                except IntegrityError:
                    raced_by_id = BotSafetyMetricEvent.objects.in_bulk(
                        tuple(pending),
                        field_name="event_id",
                    )
                    if not raced_by_id:
                        raise
                    for event_id, stored in raced_by_id.items():
                        canonical = pending[event_id]
                        if not _stored_event_matches(stored, canonical):
                            return (
                                None,
                                "event_id_payload_conflict",
                                canonical.metric_name,
                            )
                        stored_by_id[event_id] = stored
                        pending.pop(event_id)
                    continue
                created_ids.update(pending)
                pending.clear()

    seen_ids: set[str] = set()
    results: list[SafetyMetricEventWriteResult] = []
    for canonical in canonicals:
        created = canonical.event_id in created_ids and canonical.event_id not in seen_ids
        results.append(
            SafetyMetricEventWriteResult(
                event_id=canonical.event_id,
                created=created,
                payload_digest=canonical.payload_digest,
            )
        )
        seen_ids.add(canonical.event_id)
    return tuple(results), "", ""


def _persist_hard_violation(*, reason: str, source_metric: str) -> str:
    canonical = _canonicalize_event(
        event_id=f"safety-hard:{uuid4().hex}",
        metric_name=HARD_VIOLATION_METRIC_NAME,
        occurred_at=_database_utc_now(),
        dimensions={"reason": reason, "source_metric": source_metric},
        value=1,
    )
    result, nested_violation = _write_event_once(canonical)
    if result is None:
        raise SafetyHardViolationPersistenceError(
            "failed to persist safety hard violation: " f"{nested_violation or 'unknown_provider_error'}"
        )
    return result.event_id


def record_safety_metric_event(
    *,
    event_id: str,
    metric_name: str,
    occurred_at: datetime,
    dimensions: Mapping[str, str | int] | None,
    value: int | float | Decimal,
) -> SafetyMetricEventWriteResult:
    """Persist one canonical event, or durably record and raise a hard violation."""

    canonical = _canonicalize_event(
        event_id=event_id,
        metric_name=metric_name,
        occurred_at=occurred_at,
        dimensions=dimensions,
        value=value,
    )
    result, violation = _write_event_once(canonical)
    if result is not None:
        return result

    hard_event_id = _persist_hard_violation(
        reason=violation,
        source_metric=canonical.metric_name,
    )
    if violation == "event_id_payload_conflict":
        raise SafetyMetricEventConflict(
            "safety event_id already has a different canonical payload",
            hard_violation_event_id=hard_event_id,
        )
    raise LateSafetyMetricEventError(
        "safety event belongs to an already finalized UTC window",
        hard_violation_event_id=hard_event_id,
    )


def record_safety_metric_events(
    events: Sequence[SafetyMetricEventRecord],
) -> tuple[SafetyMetricEventWriteResult, ...]:
    """Persist a bounded caller-owned event batch under one window lock set."""

    canonicals = tuple(
        _canonicalize_event(
            event_id=event.event_id,
            metric_name=event.metric_name,
            occurred_at=event.occurred_at,
            dimensions=event.dimensions,
            value=event.value,
        )
        for event in events
    )
    results, violation, source_metric = _write_events_once(canonicals)
    if results is not None:
        return results

    hard_event_id = _persist_hard_violation(
        reason=violation,
        source_metric=source_metric,
    )
    if violation == "event_id_payload_conflict":
        raise SafetyMetricEventConflict(
            "safety event_id already has a different canonical payload",
            hard_violation_event_id=hard_event_id,
        )
    raise LateSafetyMetricEventError(
        "safety event belongs to an already finalized UTC window",
        hard_violation_event_id=hard_event_id,
    )


def _validate_snapshot_node(value: object, *, depth: int, state: list[int]) -> None:
    state[0] += 1
    if state[0] > SAFETY_MAX_SNAPSHOT_NODES:
        raise InvalidSafetyMetricError(f"snapshot may contain at most {SAFETY_MAX_SNAPSHOT_NODES} nodes")
    if depth > SAFETY_MAX_SNAPSHOT_DEPTH:
        raise InvalidSafetyMetricError(f"snapshot depth may not exceed {SAFETY_MAX_SNAPSHOT_DEPTH}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidSafetyMetricError("snapshot object keys must be strings")
            _validate_snapshot_node(item, depth=depth + 1, state=state)
        return
    if isinstance(value, list):
        for item in value:
            _validate_snapshot_node(item, depth=depth + 1, state=state)
        return
    if value is None or type(value) in {bool, int, str}:
        return
    if isinstance(value, float) and Decimal(str(value)).is_finite():
        return
    raise InvalidSafetyMetricError("snapshot must contain finite canonical JSON values")


def _canonical_snapshot(value: object) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        raise InvalidSafetyMetricError("snapshot must be a mapping")
    _validate_snapshot_node(value, depth=0, state=[0])
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise InvalidSafetyMetricError("snapshot must be canonical JSON") from exc
    if len(encoded) > SAFETY_MAX_SNAPSHOT_BYTES:
        raise InvalidSafetyMetricError(f"snapshot may contain at most {SAFETY_MAX_SNAPSHOT_BYTES} bytes")
    normalized = json.loads(encoded.decode("ascii"))
    return normalized, sha256(encoded).hexdigest()


def _window_finalize_result(
    window: BotSafetyMetricWindow,
    *,
    newly_finalized: bool,
) -> SafetyMetricWindowFinalizeResult:
    if window.finalized_at is None:
        raise SafetyProviderError("finalized safety window is missing finalized_at")
    return SafetyMetricWindowFinalizeResult(
        window_id=window.window_id,
        window_kind=window.kind,
        window_start_at=window.window_start_at.astimezone(UTC),
        window_end_at=window.window_end_at.astimezone(UTC),
        finalized_at=window.finalized_at.astimezone(UTC),
        newly_finalized=newly_finalized,
        snapshot_digest=window.snapshot_digest,
    )


def finalize_safety_metric_window(
    *,
    window_kind: str,
    window_start_at: datetime,
    snapshot: Mapping[str, Any],
    finalized_at: datetime | None = None,
) -> SafetyMetricWindowFinalizeResult:
    """Freeze one fixed UTC window after the five-minute late-event grace."""

    spec = _window_spec(window_kind, window_start_at)
    database_now = _database_utc_now()
    resolved_finalized_at = database_now if finalized_at is None else _aware_utc(finalized_at, field="finalized_at")
    grace_deadline = spec.end_at + SAFETY_WINDOW_GRACE
    if database_now < grace_deadline or resolved_finalized_at < grace_deadline:
        raise SafetyMetricWindowNotReadyError("safety window cannot finalize before window_end plus five minutes")
    if resolved_finalized_at > database_now:
        raise InvalidSafetyMetricError("finalized_at may not be later than the database clock")
    normalized_snapshot, snapshot_digest = _canonical_snapshot(snapshot)

    conflict = False
    with transaction.atomic():
        window = _lock_window_rows((spec,))[0]
        if window.finalized_at is not None:
            try:
                stored_snapshot, stored_digest = _canonical_snapshot(window.snapshot)
            except InvalidSafetyMetricError:
                stored_snapshot, stored_digest = None, ""
            if stored_snapshot == normalized_snapshot and stored_digest == snapshot_digest == window.snapshot_digest:
                return _window_finalize_result(window, newly_finalized=False)
            conflict = True
        else:
            window.snapshot = normalized_snapshot
            window.snapshot_digest = snapshot_digest
            window.finalized_at = resolved_finalized_at
            window.save(
                update_fields=[
                    "snapshot",
                    "snapshot_digest",
                    "finalized_at",
                    "updated_at",
                ]
            )
            return _window_finalize_result(window, newly_finalized=True)

    if not conflict:
        raise SafetyProviderError("safety window finalization reached invalid state")
    hard_event_id = _persist_hard_violation(
        reason="finalized_window_payload_conflict",
        source_metric="virtual_player_safety_window",
    )
    raise SafetyMetricWindowConflict(
        "finalized safety window snapshot is immutable",
        hard_violation_event_id=hard_event_id,
    )


def aggregate_and_finalize_safety_metric_window(
    *,
    window_kind: str,
    window_start_at: datetime,
    snapshot_builder: Callable[[SafetyMetricWindowAggregationInput], Mapping[str, Any]],
    finalized_at: datetime | None = None,
) -> SafetyMetricWindowFinalizeResult:
    """Build and freeze a snapshot while its event window is write-locked."""

    if not callable(snapshot_builder):
        raise InvalidSafetyMetricError("snapshot_builder must be callable")
    spec = _window_spec(window_kind, window_start_at)
    with transaction.atomic():
        _lock_window_rows((spec,))
        events = tuple(
            SafetyMetricEventRecord(
                event_id=event.event_id,
                metric_name=event.metric_name,
                occurred_at=event.occurred_at.astimezone(UTC),
                dimensions=dict(event.dimensions),
                value=Decimal(event.value),
            )
            for event in BotSafetyMetricEvent.objects.filter(
                occurred_at__gte=spec.start_at,
                occurred_at__lt=spec.end_at,
            ).order_by("occurred_at", "event_id")
        )
        snapshot = snapshot_builder(
            SafetyMetricWindowAggregationInput(
                window_id=spec.window_id,
                window_kind=str(spec.kind),
                window_start_at=spec.start_at,
                window_end_at=spec.end_at,
                events=events,
            )
        )
        return finalize_safety_metric_window(
            window_kind=spec.kind,
            window_start_at=spec.start_at,
            snapshot=snapshot,
            finalized_at=finalized_at,
        )


def _cursor_covers_window(
    window: BotSafetyMetricWindow,
    *,
    hourly_cursor: datetime | None,
    daily_cursor: datetime | None,
) -> bool:
    cursor = hourly_cursor if window.kind == BotSafetyMetricWindow.Kind.HOURLY else daily_cursor
    return cursor is not None and cursor >= window.window_end_at.astimezone(UTC)


def _cursors_cover_specs(
    specs: tuple[_WindowSpec, ...],
    *,
    hourly_cursor: datetime | None,
    daily_cursor: datetime | None,
) -> bool:
    for spec in specs:
        cursor = hourly_cursor if spec.kind == BotSafetyMetricWindow.Kind.HOURLY else daily_cursor
        if cursor is None or cursor < spec.end_at:
            return False
    return True


def cleanup_safety_metric_retention(
    *,
    batch_size: int = 1_000,
) -> SafetyMetricCleanupResult:
    """Delete only expired safety data already covered by durable decisions."""

    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= SAFETY_CLEANUP_MAX_BATCH_SIZE
    ):
        raise InvalidSafetyMetricError(f"batch_size must be between 1 and {SAFETY_CLEANUP_MAX_BATCH_SIZE}")

    database_now = _database_utc_now()
    event_cutoff = database_now - SAFETY_RAW_EVENT_RETENTION
    window_cutoff = database_now - SAFETY_CLOSED_WINDOW_RETENTION

    with transaction.atomic():
        routing = (
            BotRuntimeRoutingState.objects.select_for_update().filter(pk=BotRuntimeRoutingState.GLOBAL_KEY).first()
        )
        if routing is None:
            raise SafetyProviderError("runtime routing state is required for safety retention cleanup")
        hourly_cursor = (
            None
            if routing.last_hourly_safety_window_end_at is None
            else _aware_utc(
                routing.last_hourly_safety_window_end_at,
                field="last_hourly_safety_window_end_at",
            )
        )
        daily_cursor = (
            None
            if routing.last_daily_safety_window_end_at is None
            else _aware_utc(
                routing.last_daily_safety_window_end_at,
                field="last_daily_safety_window_end_at",
            )
        )

        event_candidates = list(
            BotSafetyMetricEvent.objects.filter(occurred_at__lt=event_cutoff)
            .order_by("occurred_at", "id")
            .values_list("id", "occurred_at")[:batch_size]
        )
        event_specs: dict[int, tuple[_WindowSpec, _WindowSpec]] = {}
        required_window_ids: set[str] = set()
        for event_id, occurred_at in event_candidates:
            specs = _event_window_specs(_aware_utc(occurred_at, field="stored occurred_at"))
            event_specs[event_id] = specs
            required_window_ids.update(spec.window_id for spec in specs)

        finalized_window_ids = set(
            BotSafetyMetricWindow.objects.select_for_update()
            .filter(
                window_id__in=required_window_ids,
                finalized_at__isnull=False,
            )
            .order_by("window_end_at", "kind")
            .values_list("window_id", flat=True)
        )
        event_ids_to_delete = [
            event_id
            for event_id, specs in event_specs.items()
            if all(spec.window_id in finalized_window_ids for spec in specs)
            and _cursors_cover_specs(
                specs,
                hourly_cursor=hourly_cursor,
                daily_cursor=daily_cursor,
            )
        ]
        if event_ids_to_delete:
            BotSafetyMetricEvent.objects.filter(id__in=event_ids_to_delete).delete()

        window_candidates = list(
            BotSafetyMetricWindow.objects.select_for_update()
            .filter(
                finalized_at__isnull=False,
                window_end_at__lt=window_cutoff,
            )
            .exclude(window_id=routing.last_pause_window_id)
            .order_by("window_end_at", "kind")[:batch_size]
        )
        window_ids_to_delete = []
        for window in window_candidates:
            if not _cursor_covers_window(
                window,
                hourly_cursor=hourly_cursor,
                daily_cursor=daily_cursor,
            ):
                continue
            if BotSafetyMetricEvent.objects.filter(
                occurred_at__gte=window.window_start_at,
                occurred_at__lt=window.window_end_at,
            ).exists():
                continue
            window_ids_to_delete.append(window.id)
        if window_ids_to_delete:
            BotSafetyMetricWindow.objects.filter(id__in=window_ids_to_delete).delete()

    return SafetyMetricCleanupResult(
        events_deleted=len(event_ids_to_delete),
        windows_deleted=len(window_ids_to_delete),
        event_cutoff=event_cutoff,
        window_cutoff=window_cutoff,
    )


__all__ = [
    "HARD_VIOLATION_METRIC_NAME",
    "SAFETY_CLOSED_WINDOW_RETENTION",
    "SAFETY_RAW_EVENT_RETENTION",
    "SAFETY_WINDOW_GRACE",
    "InvalidSafetyMetricError",
    "LateSafetyMetricEventError",
    "SafetyHardViolationPersistenceError",
    "SafetyMetricEventConflict",
    "SafetyMetricEventRecord",
    "SafetyMetricEventWriteResult",
    "SafetyMetricCleanupResult",
    "SafetyMetricHardViolationError",
    "SafetyMetricWindowConflict",
    "SafetyMetricWindowExpiredError",
    "SafetyMetricWindowFinalizeResult",
    "SafetyMetricWindowAggregationInput",
    "SafetyMetricWindowNotReadyError",
    "SafetyProviderError",
    "aggregate_and_finalize_safety_metric_window",
    "cleanup_safety_metric_retention",
    "finalize_safety_metric_window",
    "record_safety_metric_event",
    "record_safety_metric_events",
]
