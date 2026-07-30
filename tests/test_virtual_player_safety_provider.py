from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from gameplay.models import BotRuntimeRoutingState, BotSafetyMetricEvent, BotSafetyMetricWindow
from gameplay.services.virtual_player_core.safety_provider import (
    HARD_VIOLATION_METRIC_NAME,
    SAFETY_CLOSED_WINDOW_RETENTION,
    SAFETY_RAW_EVENT_RETENTION,
    InvalidSafetyMetricError,
    LateSafetyMetricEventError,
    SafetyMetricEventConflict,
    SafetyMetricEventRecord,
    SafetyMetricWindowConflict,
    SafetyMetricWindowExpiredError,
    SafetyMetricWindowNotReadyError,
    SafetyProviderError,
    aggregate_and_finalize_safety_metric_window,
    cleanup_safety_metric_retention,
    finalize_safety_metric_window,
    record_safety_metric_event,
    record_safety_metric_events,
)

OCCURRED_AT = datetime(2026, 7, 27, 12, 34, 56, 123456, tzinfo=UTC)
HOURLY_START = OCCURRED_AT.replace(minute=0, second=0, microsecond=0)
HOURLY_END = HOURLY_START + timedelta(hours=1)
DAILY_START = OCCURRED_AT.replace(hour=0, minute=0, second=0, microsecond=0)
DAILY_END = DAILY_START + timedelta(days=1)


def _record_event(
    *,
    event_id: str = "maintenance:operation-1:1:terminal",
    occurred_at: datetime = OCCURRED_AT,
    value: int | float | Decimal = 1,
):
    return record_safety_metric_event(
        event_id=event_id,
        metric_name="virtual_player_maintenance_total",
        occurred_at=occurred_at,
        dimensions={"result": "applied", "policy_version": "1"},
        value=value,
    )


def _seed_finalized_event(clock: list[datetime]) -> tuple[str, str]:
    clock[0] = DAILY_END + timedelta(minutes=5)
    _record_event()
    hourly = finalize_safety_metric_window(
        window_kind="hourly",
        window_start_at=HOURLY_START,
        snapshot={"schema_version": 1, "event_count": 1},
        finalized_at=HOURLY_END + timedelta(minutes=5),
    )
    daily = finalize_safety_metric_window(
        window_kind="daily",
        window_start_at=DAILY_START,
        snapshot={"schema_version": 1, "event_count": 1},
        finalized_at=DAILY_END + timedelta(minutes=5),
    )
    return hourly.window_id, daily.window_id


def _create_routing_state(
    *,
    hourly_cursor: datetime | None,
    daily_cursor: datetime | None,
    last_pause_window_id: str = "",
) -> BotRuntimeRoutingState:
    return BotRuntimeRoutingState.objects.create(
        last_hourly_safety_window_end_at=hourly_cursor,
        last_daily_safety_window_end_at=daily_cursor,
        last_pause_window_id=last_pause_window_id,
    )


@pytest.mark.django_db
def test_event_writer_canonicalizes_payload_and_locks_both_windows_in_order() -> None:
    offset_time = OCCURRED_AT.astimezone(timezone(timedelta(hours=8)))

    with CaptureQueriesContext(connection) as captured:
        result = _record_event(occurred_at=offset_time)

    event = BotSafetyMetricEvent.objects.get(event_id=result.event_id)
    assert result.created is True
    assert event.occurred_at == OCCURRED_AT
    assert event.dimensions == {"policy_version": "1", "result": "applied"}
    assert event.value == Decimal("1.000000000000")
    assert event.payload_digest == result.payload_digest
    assert len(event.payload_digest) == 64

    windows = list(
        BotSafetyMetricWindow.objects.order_by("window_end_at", "kind").values_list(
            "kind",
            "window_start_at",
            "window_end_at",
            "finalized_at",
        )
    )
    assert windows == [
        (
            BotSafetyMetricWindow.Kind.HOURLY,
            HOURLY_START,
            HOURLY_END,
            None,
        ),
        (
            BotSafetyMetricWindow.Kind.DAILY,
            DAILY_START,
            DAILY_START + timedelta(days=1),
            None,
        ),
    ]
    lock_queries = [
        query["sql"].lower()
        for query in captured.captured_queries
        if "gameplay_botsafetymetricwindow" in query["sql"].lower()
        and "order by" in query["sql"].lower()
        and "window_end_at" in query["sql"].lower()
        and "kind" in query["sql"].lower()
    ]
    assert lock_queries
    order_clause = lock_queries[-1].split("order by", 1)[1]
    assert order_clause.index("window_end_at") < order_clause.index("kind")


@pytest.mark.django_db
def test_same_event_id_and_canonical_payload_is_idempotent() -> None:
    first = _record_event(value=1)

    second = record_safety_metric_event(
        event_id=first.event_id,
        metric_name="virtual_player_maintenance_total",
        occurred_at=OCCURRED_AT.astimezone(timezone(timedelta(hours=-4))),
        dimensions={"policy_version": "1", "result": "applied"},
        value=Decimal("1.0"),
    )

    assert second.created is False
    assert second.payload_digest == first.payload_digest
    assert BotSafetyMetricEvent.objects.filter(event_id=first.event_id).count() == 1
    assert BotSafetyMetricEvent.objects.filter(metric_name=HARD_VIOLATION_METRIC_NAME).count() == 0


@pytest.mark.django_db
def test_event_batch_is_atomic_and_idempotent_under_one_window_set() -> None:
    events = tuple(
        SafetyMetricEventRecord(
            event_id=f"maintenance:batch-operation-{index}:1:started",
            metric_name="virtual_player_maintenance_total",
            occurred_at=OCCURRED_AT,
            dimensions={"result": "started", "policy_version": "1"},
            value=Decimal(1),
        )
        for index in range(3)
    )

    first = record_safety_metric_events(events)
    second = record_safety_metric_events(events)

    assert [result.created for result in first] == [True, True, True]
    assert [result.created for result in second] == [False, False, False]
    assert BotSafetyMetricEvent.objects.filter(metric_name="virtual_player_maintenance_total").count() == 3
    assert BotSafetyMetricWindow.objects.count() == 2


@pytest.mark.django_db
def test_event_batch_payload_conflict_persists_hard_violation() -> None:
    events = (
        SafetyMetricEventRecord(
            event_id="maintenance:batch-conflict:1:terminal",
            metric_name="virtual_player_maintenance_total",
            occurred_at=OCCURRED_AT,
            dimensions={"result": "applied"},
            value=Decimal(1),
        ),
        SafetyMetricEventRecord(
            event_id="maintenance:batch-conflict:1:terminal",
            metric_name="virtual_player_maintenance_total",
            occurred_at=OCCURRED_AT,
            dimensions={"result": "failed"},
            value=Decimal(1),
        ),
    )

    with pytest.raises(SafetyMetricEventConflict) as captured:
        record_safety_metric_events(events)

    assert not BotSafetyMetricEvent.objects.filter(event_id="maintenance:batch-conflict:1:terminal").exists()
    hard_violation = BotSafetyMetricEvent.objects.get(event_id=captured.value.hard_violation_event_id)
    assert hard_violation.dimensions == {
        "reason": "event_id_payload_conflict",
        "source_metric": "virtual_player_maintenance_total",
    }


@pytest.mark.django_db
def test_event_id_with_different_payload_persists_hard_violation_then_raises() -> None:
    first = _record_event(value=1)

    with pytest.raises(SafetyMetricEventConflict) as captured:
        _record_event(value=2)

    original = BotSafetyMetricEvent.objects.get(event_id=first.event_id)
    hard_violation = BotSafetyMetricEvent.objects.get(event_id=captured.value.hard_violation_event_id)
    assert original.value == Decimal("1.000000000000")
    assert original.payload_digest == first.payload_digest
    assert hard_violation.metric_name == HARD_VIOLATION_METRIC_NAME
    assert hard_violation.dimensions == {
        "reason": "event_id_payload_conflict",
        "source_metric": "virtual_player_maintenance_total",
    }
    assert hard_violation.value == Decimal("1.000000000000")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        {"event_id": "bad id"},
        {"metric_name": "BadMetric"},
        {"occurred_at": OCCURRED_AT.replace(tzinfo=None)},
        {"dimensions": {"user_id": "42"}},
        {"dimensions": {"result": 1}},
        {"value": True},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": Decimal("1.0000000000001")},
    ],
)
def test_event_writer_rejects_noncanonical_or_unbounded_payloads(overrides) -> None:
    payload = {
        "event_id": "maintenance:invalid:1:terminal",
        "metric_name": "virtual_player_maintenance_total",
        "occurred_at": OCCURRED_AT,
        "dimensions": {"result": "applied"},
        "value": 1,
    }
    payload.update(overrides)

    with pytest.raises(InvalidSafetyMetricError):
        record_safety_metric_event(**payload)

    assert not BotSafetyMetricEvent.objects.exists()
    assert not BotSafetyMetricWindow.objects.exists()


@pytest.mark.django_db
def test_window_finalization_requires_exact_end_plus_five_minute_grace() -> None:
    _record_event()
    snapshot = {"schema_version": 1, "metrics": {"attempts": 1}}

    with pytest.raises(SafetyMetricWindowNotReadyError):
        finalize_safety_metric_window(
            window_kind=BotSafetyMetricWindow.Kind.HOURLY,
            window_start_at=HOURLY_START,
            snapshot=snapshot,
            finalized_at=HOURLY_END + timedelta(minutes=5) - timedelta(microseconds=1),
        )

    open_window = BotSafetyMetricWindow.objects.get(
        kind=BotSafetyMetricWindow.Kind.HOURLY,
        window_start_at=HOURLY_START,
    )
    assert open_window.finalized_at is None
    assert open_window.snapshot == {}
    assert open_window.snapshot_digest == ""

    result = finalize_safety_metric_window(
        window_kind=BotSafetyMetricWindow.Kind.HOURLY,
        window_start_at=HOURLY_START,
        snapshot=snapshot,
        finalized_at=HOURLY_END + timedelta(minutes=5),
    )

    open_window.refresh_from_db()
    assert result.newly_finalized is True
    assert result.window_id == "hourly:20260727T120000Z"
    assert result.window_end_at == HOURLY_END
    assert open_window.finalized_at == HOURLY_END + timedelta(minutes=5)
    assert open_window.snapshot == snapshot
    assert len(open_window.snapshot_digest) == 64
    assert result.snapshot_digest == open_window.snapshot_digest


@pytest.mark.django_db
def test_atomic_aggregation_locks_window_before_reading_events_and_finalizing() -> None:
    _record_event()
    callback_observations: list[tuple[bool, tuple[str, ...]]] = []

    def _snapshot_builder(aggregation):
        callback_observations.append(
            (
                connection.in_atomic_block,
                tuple(event.event_id for event in aggregation.events),
            )
        )
        return {"schema_version": 1, "event_count": len(aggregation.events)}

    with CaptureQueriesContext(connection) as captured:
        result = aggregate_and_finalize_safety_metric_window(
            window_kind="hourly",
            window_start_at=HOURLY_START,
            snapshot_builder=_snapshot_builder,
            finalized_at=HOURLY_END + timedelta(minutes=5),
        )

    statements = [query["sql"].lower() for query in captured.captured_queries]
    first_window_lock = next(
        index
        for index, statement in enumerate(statements)
        if "gameplay_botsafetymetricwindow" in statement and "order by" in statement and "window_end_at" in statement
    )
    event_read = next(
        index
        for index, statement in enumerate(statements)
        if "gameplay_botsafetymetricevent" in statement and statement.lstrip().startswith("select")
    )
    window_update = next(
        index
        for index, statement in enumerate(statements)
        if statement.lstrip().startswith("update") and "gameplay_botsafetymetricwindow" in statement
    )
    assert first_window_lock < event_read < window_update, "\n".join(
        f"{index}: {statement}" for index, statement in enumerate(statements)
    )
    assert callback_observations == [(True, ("maintenance:operation-1:1:terminal",))]
    assert result.newly_finalized is True


@pytest.mark.django_db
def test_future_finalized_at_cannot_bypass_database_clock_grace(monkeypatch) -> None:
    database_now = HOURLY_END + timedelta(minutes=4)
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_provider._database_utc_now",
        lambda: database_now,
    )

    with pytest.raises(SafetyMetricWindowNotReadyError):
        finalize_safety_metric_window(
            window_kind="hourly",
            window_start_at=HOURLY_START,
            snapshot={"schema_version": 1, "event_count": 0},
            finalized_at=HOURLY_END + timedelta(minutes=5),
        )

    assert not BotSafetyMetricWindow.objects.exists()


@pytest.mark.django_db
def test_finalized_window_is_idempotent_for_same_snapshot_and_immutable_otherwise() -> None:
    _record_event()
    finalized_at = HOURLY_END + timedelta(minutes=5)
    first = finalize_safety_metric_window(
        window_kind="hourly",
        window_start_at=HOURLY_START,
        snapshot={"metrics": {"sum": 1, "count": 1}, "schema_version": 1},
        finalized_at=finalized_at,
    )

    second = finalize_safety_metric_window(
        window_kind="hourly",
        window_start_at=HOURLY_START,
        snapshot={"schema_version": 1, "metrics": {"count": 1, "sum": 1}},
        finalized_at=finalized_at + timedelta(hours=1),
    )

    assert second.newly_finalized is False
    assert second.snapshot_digest == first.snapshot_digest
    assert second.finalized_at == finalized_at

    with pytest.raises(SafetyMetricWindowConflict) as captured:
        finalize_safety_metric_window(
            window_kind="hourly",
            window_start_at=HOURLY_START,
            snapshot={"schema_version": 1, "metrics": {"count": 2, "sum": 2}},
            finalized_at=finalized_at + timedelta(hours=1),
        )

    window = BotSafetyMetricWindow.objects.get(window_id=first.window_id)
    hard_violation = BotSafetyMetricEvent.objects.get(event_id=captured.value.hard_violation_event_id)
    assert window.snapshot == {
        "metrics": {"count": 1, "sum": 1},
        "schema_version": 1,
    }
    assert window.snapshot_digest == first.snapshot_digest
    assert window.finalized_at == finalized_at
    assert hard_violation.dimensions["reason"] == ("finalized_window_payload_conflict")


@pytest.mark.django_db
def test_late_new_event_is_rejected_with_hard_violation_but_duplicate_is_idempotent() -> None:
    first = _record_event()
    finalize_safety_metric_window(
        window_kind="hourly",
        window_start_at=HOURLY_START,
        snapshot={"schema_version": 1, "event_count": 1},
        finalized_at=HOURLY_END + timedelta(minutes=5),
    )

    duplicate = _record_event()
    assert duplicate.created is False
    assert duplicate.payload_digest == first.payload_digest

    with pytest.raises(LateSafetyMetricEventError) as captured:
        _record_event(event_id="maintenance:operation-2:1:terminal")

    assert not BotSafetyMetricEvent.objects.filter(event_id="maintenance:operation-2:1:terminal").exists()
    hard_violation = BotSafetyMetricEvent.objects.get(event_id=captured.value.hard_violation_event_id)
    assert hard_violation.dimensions == {
        "reason": "late_event_after_finalization",
        "source_metric": "virtual_player_maintenance_total",
    }


@pytest.mark.django_db
def test_daily_window_uses_fixed_utc_boundaries() -> None:
    result = finalize_safety_metric_window(
        window_kind="daily",
        window_start_at=DAILY_START,
        snapshot={"schema_version": 1, "event_count": 0},
        finalized_at=DAILY_START + timedelta(days=1, minutes=5),
    )

    assert result.window_id == "daily:20260727T000000Z"
    assert result.window_start_at == DAILY_START
    assert result.window_end_at == DAILY_START + timedelta(days=1)
    assert result.newly_finalized is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("window_kind", "window_start_at", "snapshot"),
    [
        ("weekly", HOURLY_START, {"schema_version": 1}),
        ("hourly", HOURLY_START + timedelta(minutes=1), {"schema_version": 1}),
        ("hourly", HOURLY_START.replace(tzinfo=None), {"schema_version": 1}),
        ("hourly", HOURLY_START, {"value": float("nan")}),
    ],
)
def test_window_finalizer_rejects_invalid_kind_boundary_or_snapshot(
    window_kind,
    window_start_at,
    snapshot,
) -> None:
    with pytest.raises(InvalidSafetyMetricError):
        finalize_safety_metric_window(
            window_kind=window_kind,
            window_start_at=window_start_at,
            snapshot=snapshot,
            finalized_at=HOURLY_END + timedelta(days=2),
        )

    assert not BotSafetyMetricWindow.objects.exists()


@pytest.mark.django_db
def test_retention_cleanup_requires_finalized_windows_and_both_decision_cursors(
    monkeypatch,
) -> None:
    clock = [DAILY_END + timedelta(minutes=5)]
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_provider._database_utc_now",
        lambda: clock[0],
    )
    _seed_finalized_event(clock)
    routing = _create_routing_state(
        hourly_cursor=HOURLY_END,
        daily_cursor=None,
    )
    clock[0] = OCCURRED_AT + SAFETY_RAW_EVENT_RETENTION + timedelta(days=1)

    blocked = cleanup_safety_metric_retention()

    assert blocked.events_deleted == 0
    assert BotSafetyMetricEvent.objects.filter(event_id="maintenance:operation-1:1:terminal").exists()

    routing.last_daily_safety_window_end_at = DAILY_END
    routing.save(update_fields=["last_daily_safety_window_end_at", "updated_at"])
    cleaned = cleanup_safety_metric_retention()

    assert cleaned.events_deleted == 1
    assert cleaned.windows_deleted == 0
    assert not BotSafetyMetricEvent.objects.exists()
    assert BotSafetyMetricWindow.objects.count() == 2


@pytest.mark.django_db
def test_retention_cleanup_protects_last_pause_window(monkeypatch) -> None:
    clock = [DAILY_END + timedelta(minutes=5)]
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_provider._database_utc_now",
        lambda: clock[0],
    )
    hourly_window_id, _daily_window_id = _seed_finalized_event(clock)
    routing = _create_routing_state(
        hourly_cursor=HOURLY_END,
        daily_cursor=DAILY_END,
        last_pause_window_id=hourly_window_id,
    )
    clock[0] = OCCURRED_AT + SAFETY_CLOSED_WINDOW_RETENTION + timedelta(days=1)

    first = cleanup_safety_metric_retention(batch_size=1)

    assert first.events_deleted == 1
    assert first.windows_deleted == 1
    assert BotSafetyMetricWindow.objects.count() == 1

    second = cleanup_safety_metric_retention()

    assert second.windows_deleted == 0
    assert BotSafetyMetricWindow.objects.filter(window_id=hourly_window_id).exists()

    routing.last_pause_window_id = ""
    routing.save(update_fields=["last_pause_window_id", "updated_at"])
    third = cleanup_safety_metric_retention()

    assert third.windows_deleted == 1
    assert not BotSafetyMetricWindow.objects.exists()


@pytest.mark.django_db
def test_retention_cleanup_keeps_windows_while_raw_events_remain(monkeypatch) -> None:
    clock = [DAILY_END + timedelta(minutes=5)]
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_provider._database_utc_now",
        lambda: clock[0],
    )
    _seed_finalized_event(clock)
    _create_routing_state(
        hourly_cursor=HOURLY_END,
        daily_cursor=None,
    )
    clock[0] = OCCURRED_AT + SAFETY_CLOSED_WINDOW_RETENTION + timedelta(days=1)

    result = cleanup_safety_metric_retention()

    assert result.events_deleted == 0
    assert result.windows_deleted == 0
    assert BotSafetyMetricEvent.objects.exists()
    assert BotSafetyMetricWindow.objects.count() == 2


@pytest.mark.django_db
def test_retention_cleanup_fails_closed_without_routing_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_provider._database_utc_now",
        lambda: OCCURRED_AT + SAFETY_CLOSED_WINDOW_RETENTION + timedelta(days=1),
    )

    with pytest.raises(SafetyProviderError, match="routing state"):
        cleanup_safety_metric_retention()


@pytest.mark.django_db
def test_cleaned_history_cannot_be_recreated_by_late_event_or_finalizer(
    monkeypatch,
) -> None:
    clock = [DAILY_END + timedelta(minutes=5)]
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_provider._database_utc_now",
        lambda: clock[0],
    )
    hourly_window_id, daily_window_id = _seed_finalized_event(clock)
    _create_routing_state(
        hourly_cursor=HOURLY_END,
        daily_cursor=DAILY_END,
    )
    clock[0] = OCCURRED_AT + SAFETY_CLOSED_WINDOW_RETENTION + timedelta(days=1)
    cleaned = cleanup_safety_metric_retention()
    assert cleaned.events_deleted == 1
    assert cleaned.windows_deleted == 2

    with pytest.raises(LateSafetyMetricEventError) as captured:
        _record_event()

    hard_violation = BotSafetyMetricEvent.objects.get(event_id=captured.value.hard_violation_event_id)
    assert hard_violation.dimensions["reason"] == "event_outside_retention"
    assert not BotSafetyMetricWindow.objects.filter(window_id__in=[hourly_window_id, daily_window_id]).exists()

    with pytest.raises(SafetyMetricWindowExpiredError):
        finalize_safety_metric_window(
            window_kind="hourly",
            window_start_at=HOURLY_START,
            snapshot={"schema_version": 1, "event_count": 1},
            finalized_at=clock[0],
        )

    assert not BotSafetyMetricWindow.objects.filter(window_id=hourly_window_id).exists()


@pytest.mark.parametrize("batch_size", [True, 0, 10_001])
def test_retention_cleanup_rejects_invalid_batch_size(batch_size) -> None:
    with pytest.raises(InvalidSafetyMetricError, match="batch_size"):
        cleanup_safety_metric_retention(batch_size=batch_size)


def test_safety_models_are_exported_from_gameplay_models() -> None:
    assert BotSafetyMetricEvent.__name__ == "BotSafetyMetricEvent"
    assert BotSafetyMetricWindow.__name__ == "BotSafetyMetricWindow"
