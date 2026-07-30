from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from gameplay.models import BotRuntimeRoutingState, BotSafetyMetricEvent, BotSafetyMetricWindow
from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core import safety_baselines, safety_monitor
from gameplay.services.virtual_player_core.safety_metrics import (
    ECONOMY_CAP_BREACH_METRIC,
    H01_CALLBACK_ATTEMPT_METRIC,
    HARD_CONSTRAINT_METRIC,
    REQUIRED_HEARTBEAT_STREAMS,
    SAFETY_HEARTBEAT_METRIC,
)
from gameplay.services.virtual_player_core.safety_monitor import (
    SafetyDecisionConflictExhausted,
    build_safety_window_snapshot,
    evaluate_finalized_safety_window,
    finalize_due_safety_windows,
    monitor_finalized_safety_windows,
)
from tests.helpers.model_dml_audit import find_model_dml

HOURLY_START = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _snapshot(
    *,
    kind: str,
    start_at: datetime,
    metrics: dict[str, int | float] | None = None,
    incomplete_streams: tuple[str, ...] = (),
    distribution: list[dict] | None = None,
    arena: list[dict] | None = None,
    baselines: list[dict] | None = None,
    aggregation_errors: list[str] | None = None,
) -> dict:
    end_at = start_at + (timedelta(hours=1) if kind == "hourly" else timedelta(days=1))
    metric_values: dict[str, int | float | None] = {
        "hard_constraint_violation_count": 0,
        "economy_cap_breach_count": 0,
        "duplicate_or_partial_commit_count": 0,
        "maintenance_failure_count": 0,
        "maintenance_eligible_attempt_count": 0,
        "maintenance_failure_rate": None,
        "h01_post_commit_attempt_degraded_count": 0,
        "h01_post_commit_attempt_count": 0,
        "h01_post_commit_attempt_degraded_rate": None,
        "performance_breach_count": 0,
    }
    metric_values.update(metrics or {})
    streams = {
        stream: {
            "complete": stream not in incomplete_streams,
            "count": 30,
            "first_at": _timestamp(start_at),
            "last_at": _timestamp(end_at - timedelta(minutes=2)),
            "max_gap_seconds": 120,
            "reason": "gap_exceeded" if stream in incomplete_streams else "",
        }
        for stream in REQUIRED_HEARTBEAT_STREAMS
    }
    return {
        "schema_version": 1,
        "window_kind": kind,
        "window_start_at": _timestamp(start_at),
        "window_end_at": _timestamp(end_at),
        "event_count": 0,
        "metrics": metric_values,
        "heartbeat": {
            "complete": not incomplete_streams,
            "expected_interval_seconds": 60,
            "max_allowed_gap_seconds": 120,
            "incomplete_streams": list(incomplete_streams),
            "streams": streams,
        },
        "aggregation_errors": aggregation_errors or [],
        "scoped_distribution_breaches": distribution or [],
        "scoped_arena_shortage_ratios": arena or [],
        "arena_shortage_baselines": baselines or [],
    }


def _snapshot_digest(snapshot: dict) -> str:
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def _arena_baseline(
    *,
    kind: str = "coop",
    prestige_band: str = "junior",
    baseline_ratio: str = "0.1",
) -> dict:
    request = safety_baselines.normalize_arena_shortage_baseline_request(
        mode=kind,
        prestige_band=prestige_band,
        baseline_ratio=baseline_ratio,
        evidence_id="arena-shortage-evidence-20260728",
        evidence_checksum="a" * 64,
    )
    return {
        "kind": request.mode,
        "prestige_band": request.prestige_band,
        "baseline_ratio": float(request.baseline_ratio),
        "frozen_at": _timestamp(HOURLY_START - timedelta(days=1)),
        "evidence_id": request.evidence_id,
        "evidence_checksum": request.evidence_checksum,
        "payload_digest": request.payload_digest,
    }


def _window(
    *,
    kind: str = "hourly",
    start_at: datetime = HOURLY_START,
    finalized: bool = True,
    **snapshot_overrides,
) -> BotSafetyMetricWindow:
    duration = timedelta(hours=1) if kind == "hourly" else timedelta(days=1)
    end_at = start_at + duration
    snapshot = _snapshot(kind=kind, start_at=start_at, **snapshot_overrides)
    return BotSafetyMetricWindow.objects.create(
        window_id=f"{kind}:{start_at.strftime('%Y%m%dT%H%M%SZ')}",
        kind=kind,
        window_start_at=start_at,
        window_end_at=end_at,
        snapshot=snapshot if finalized else {},
        snapshot_digest=_snapshot_digest(snapshot) if finalized else "",
        finalized_at=end_at + timedelta(minutes=5) if finalized else None,
    )


def _routing_state(
    *,
    revision: int = 4,
    hourly_cursor: datetime | None = None,
    daily_cursor: datetime | None = HOURLY_START.replace(hour=0),
) -> BotRuntimeRoutingState:
    return BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
        revision=revision,
        last_hourly_safety_window_end_at=hourly_cursor,
        last_daily_safety_window_end_at=daily_cursor,
    )


def _event(
    event_id: str,
    *,
    metric_name: str,
    occurred_at: datetime,
    dimensions: dict[str, str] | None = None,
    value: int | float | Decimal = 1,
) -> BotSafetyMetricEvent:
    return BotSafetyMetricEvent(
        event_id=event_id,
        metric_name=metric_name,
        occurred_at=occurred_at,
        dimensions=dimensions or {},
        value=Decimal(str(value)),
        payload_digest=sha256(event_id.encode("ascii")).hexdigest(),
    )


def _heartbeat_events(*, gap_stream: str | None = None) -> list[BotSafetyMetricEvent]:
    events: list[BotSafetyMetricEvent] = []
    for stream in REQUIRED_HEARTBEAT_STREAMS:
        for offset in range(0, 3600, 120):
            if stream == gap_stream and offset == 120:
                continue
            occurred_at = HOURLY_START + timedelta(seconds=offset)
            events.append(
                _event(
                    f"heartbeat:{stream}:{offset}",
                    metric_name=SAFETY_HEARTBEAT_METRIC,
                    occurred_at=occurred_at,
                    dimensions={"stream": stream},
                )
            )
    return events


@pytest.mark.django_db
def test_aggregator_uses_half_open_window_and_h01_all_as_denominator() -> None:
    events = _heartbeat_events()
    events.extend(
        [
            _event(
                "hard-at-start",
                metric_name=HARD_CONSTRAINT_METRIC,
                occurred_at=HOURLY_START,
            ),
            _event(
                "economy-at-end",
                metric_name=ECONOMY_CAP_BREACH_METRIC,
                occurred_at=HOURLY_START + timedelta(hours=1),
            ),
            _event(
                "h01:all",
                metric_name=H01_CALLBACK_ATTEMPT_METRIC,
                occurred_at=HOURLY_START + timedelta(minutes=10),
                dimensions={"result": "all"},
            ),
            _event(
                "h01:degraded",
                metric_name=H01_CALLBACK_ATTEMPT_METRIC,
                occurred_at=HOURLY_START + timedelta(minutes=10),
                dimensions={"result": "degraded"},
            ),
        ]
    )
    BotSafetyMetricEvent.objects.bulk_create(events)

    snapshot = build_safety_window_snapshot(
        window_kind="hourly",
        window_start_at=HOURLY_START,
    )

    assert snapshot["event_count"] == 153
    assert snapshot["metrics"]["hard_constraint_violation_count"] == 1
    assert snapshot["metrics"]["economy_cap_breach_count"] == 0
    assert snapshot["metrics"]["h01_post_commit_attempt_count"] == 1
    assert snapshot["metrics"]["h01_post_commit_attempt_degraded_count"] == 1
    assert snapshot["metrics"]["h01_post_commit_attempt_degraded_rate"] == 1.0
    assert snapshot["heartbeat"]["complete"] is True


@pytest.mark.django_db
def test_aggregator_marks_heartbeat_gap_over_120_seconds_incomplete() -> None:
    BotSafetyMetricEvent.objects.bulk_create(_heartbeat_events(gap_stream="safety_monitor"))

    snapshot = build_safety_window_snapshot(
        window_kind="hourly",
        window_start_at=HOURLY_START,
    )

    assert snapshot["heartbeat"]["complete"] is False
    assert snapshot["heartbeat"]["incomplete_streams"] == ["safety_monitor"]
    assert snapshot["heartbeat"]["streams"]["safety_monitor"]["max_gap_seconds"] == 240


@pytest.mark.django_db
def test_due_finalizer_waits_until_exact_end_plus_five_minutes() -> None:
    window = _window(finalized=False)
    end_at = window.window_end_at

    early = finalize_due_safety_windows(now=end_at + timedelta(minutes=5) - timedelta(microseconds=1))
    exact = finalize_due_safety_windows(now=end_at + timedelta(minutes=5))

    assert early == ()
    assert len(exact) == 1
    assert exact[0].window_id == window.window_id
    window.refresh_from_db()
    assert window.finalized_at == end_at + timedelta(minutes=5)
    assert window.snapshot["window_start_at"] == _timestamp(HOURLY_START)
    assert window.snapshot["window_end_at"] == _timestamp(end_at)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("metric", "reason"),
    [
        ("hard_constraint_violation_count", "hard_constraint_violation"),
        ("economy_cap_breach_count", "economy_cap_breach"),
        ("duplicate_or_partial_commit_count", "duplicate_or_partial_commit"),
    ],
)
def test_immediate_count_thresholds_pause(metric: str, reason: str) -> None:
    window = _window(metrics={metric: 1})

    decision = evaluate_finalized_safety_window(window)

    assert decision.should_pause is True
    assert reason in decision.pause_reasons


@pytest.mark.django_db
def test_maintenance_requires_two_consecutive_hourly_rates_strictly_over_one_percent() -> None:
    first = _window(
        start_at=HOURLY_START,
        metrics={
            "maintenance_failure_count": 2,
            "maintenance_eligible_attempt_count": 100,
        },
    )
    second = _window(
        start_at=HOURLY_START + timedelta(hours=1),
        metrics={
            "maintenance_failure_count": 2,
            "maintenance_eligible_attempt_count": 100,
        },
    )
    exact = _window(
        start_at=HOURLY_START + timedelta(hours=2),
        metrics={
            "maintenance_failure_count": 1,
            "maintenance_eligible_attempt_count": 100,
        },
    )

    first_decision = evaluate_finalized_safety_window(first)
    second_decision = evaluate_finalized_safety_window(second, history=(first,))
    exact_decision = evaluate_finalized_safety_window(exact, history=(second,))

    assert "maintenance_failure_rate" not in first_decision.pause_reasons
    assert "maintenance_failure_rate" in second_decision.pause_reasons
    assert "maintenance_failure_rate" not in exact_decision.pause_reasons


@pytest.mark.django_db
def test_h01_degraded_rate_pauses_in_one_hour_and_is_strictly_greater() -> None:
    breached = _window(
        metrics={
            "h01_post_commit_attempt_degraded_count": 2,
            "h01_post_commit_attempt_count": 1000,
        }
    )
    exact = _window(
        start_at=HOURLY_START + timedelta(hours=1),
        metrics={
            "h01_post_commit_attempt_degraded_count": 1,
            "h01_post_commit_attempt_count": 1000,
        },
    )

    assert "h01_post_commit_attempt_degraded_rate" in (evaluate_finalized_safety_window(breached).pause_reasons)
    assert "h01_post_commit_attempt_degraded_rate" not in (evaluate_finalized_safety_window(exact).pause_reasons)


@pytest.mark.django_db
def test_performance_requires_three_contiguous_hourly_windows() -> None:
    windows = [
        _window(
            start_at=HOURLY_START + timedelta(hours=index),
            metrics={"performance_breach_count": 1},
        )
        for index in range(3)
    ]

    decision = evaluate_finalized_safety_window(windows[-1], history=reversed(windows[:-1]))

    assert "performance_breach" in decision.pause_reasons


@pytest.mark.django_db
def test_distribution_requires_same_scope_across_two_daily_windows() -> None:
    daily_start = HOURLY_START.replace(hour=0)
    shared_scope = {
        "policy_version": "1",
        "reference_snapshot_version": "3",
        "prestige_band": "middle",
        "breach_count": 1,
    }
    different_scope = {**shared_scope, "prestige_band": "senior"}
    previous = _window(kind="daily", start_at=daily_start, distribution=[shared_scope])
    repeated = _window(
        kind="daily",
        start_at=daily_start + timedelta(days=1),
        distribution=[shared_scope],
    )
    different = _window(
        kind="daily",
        start_at=daily_start + timedelta(days=2),
        distribution=[different_scope],
    )

    repeated_decision = evaluate_finalized_safety_window(repeated, history=(previous,))
    different_decision = evaluate_finalized_safety_window(different, history=(repeated,))

    assert any(reason.startswith("distribution_breach:1:3:middle") for reason in repeated_decision.pause_reasons)
    assert not any(reason.startswith("distribution_breach:") for reason in different_decision.pause_reasons)


@pytest.mark.django_db
def test_arena_shortage_requires_persisted_baseline_and_strict_increase() -> None:
    baseline = [_arena_baseline()]
    equal = _window(
        arena=[
            {
                "kind": "coop",
                "prestige_band": "junior",
                "sample_count": 1,
                "current_ratio_max": 0.12,
            }
        ],
        baselines=baseline,
    )
    breached = _window(
        start_at=HOURLY_START + timedelta(hours=1),
        arena=[
            {
                "kind": "coop",
                "prestige_band": "junior",
                "sample_count": 1,
                "current_ratio_max": 0.120001,
            }
        ],
        baselines=baseline,
    )
    missing = _window(
        start_at=HOURLY_START + timedelta(hours=2),
        arena=[
            {
                "kind": "coop",
                "prestige_band": "junior",
                "sample_count": 1,
                "current_ratio_max": 0.01,
            }
        ],
    )

    assert not any(
        reason.startswith("arena_shortage_absolute_increase")
        for reason in evaluate_finalized_safety_window(equal).pause_reasons
    )
    assert any(
        reason.startswith("arena_shortage_absolute_increase")
        for reason in evaluate_finalized_safety_window(breached).pause_reasons
    )
    assert any(
        reason.startswith("arena_shortage_baseline_missing")
        for reason in evaluate_finalized_safety_window(missing).pause_reasons
    )


@pytest.mark.django_db
def test_arena_shortage_rejects_invalid_baseline_provenance() -> None:
    baseline = _arena_baseline()
    baseline["payload_digest"] = "b" * 64
    window = _window(baselines=[baseline])

    with pytest.raises(
        safety_monitor.InvalidSafetySnapshotError,
        match="arena baseline payload digest differs",
    ):
        evaluate_finalized_safety_window(window)


@pytest.mark.django_db
def test_zero_denominators_need_complete_heartbeat() -> None:
    complete = _window()
    incomplete = _window(
        start_at=HOURLY_START + timedelta(hours=1),
        incomplete_streams=("maintenance_attempt_emitter",),
    )

    complete_decision = evaluate_finalized_safety_window(complete)
    incomplete_decision = evaluate_finalized_safety_window(incomplete)

    assert complete_decision.should_pause is False
    assert incomplete_decision.pause_reasons == ("heartbeat_incomplete:maintenance_attempt_emitter",)


@pytest.mark.django_db
def test_monitor_reads_only_finalized_windows() -> None:
    _routing_state()
    finalized = _window(start_at=HOURLY_START)
    open_window = _window(start_at=HOURLY_START + timedelta(hours=1), finalized=False)

    result = monitor_finalized_safety_windows(now=finalized.window_end_at + timedelta(minutes=5))

    assert [decision.window_id for decision in result.decisions] == [finalized.window_id]
    assert result.consumed_count == 1
    open_window.refresh_from_db()
    assert open_window.finalized_at is None


@pytest.mark.django_db
def test_monitor_fails_closed_when_no_finalized_window_exists() -> None:
    state = _routing_state()
    now = HOURLY_START + timedelta(hours=1, minutes=5)

    result = monitor_finalized_safety_windows(now=now)

    assert result.paused is True
    assert result.decisions[0].window_id == "hourly:20260727T010000Z"
    assert result.decisions[0].pause_reasons == ("missing_finalized_hourly_window",)
    state.refresh_from_db()
    assert state.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED


@pytest.mark.django_db
def test_monitor_checks_missing_daily_window_independently() -> None:
    latest_hourly_end = HOURLY_START + timedelta(hours=1)
    _routing_state(
        hourly_cursor=latest_hourly_end,
        daily_cursor=None,
    )

    result = monitor_finalized_safety_windows(now=latest_hourly_end + timedelta(minutes=5))

    assert result.decisions[0].window_kind == "daily"
    assert result.decisions[0].pause_reasons == ("missing_finalized_daily_window",)
    assert result.paused is True


@pytest.mark.django_db
def test_monitor_fails_closed_on_intermediate_window_gap() -> None:
    cursor = HOURLY_START + timedelta(hours=1)
    _routing_state(hourly_cursor=cursor)
    after_gap = _window(start_at=HOURLY_START + timedelta(hours=2))

    result = monitor_finalized_safety_windows(now=after_gap.window_end_at + timedelta(minutes=5))

    assert result.decisions[0].pause_reasons == ("missing_finalized_hourly_window",)
    assert result.paused is True


@pytest.mark.django_db
def test_monitor_reloads_routing_after_first_cas_conflict(monkeypatch) -> None:
    _routing_state()
    window = _window()
    original_apply = runtime_configs.apply_virtual_player_safety_decision
    calls = 0

    def conflict_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise runtime_configs.RuntimeRoutingConflict("revision changed")
        return original_apply(**kwargs)

    monkeypatch.setattr(
        runtime_configs,
        "apply_virtual_player_safety_decision",
        conflict_once,
    )

    result = monitor_finalized_safety_windows(
        now=window.window_end_at + timedelta(minutes=5),
        max_cas_retries=3,
    )

    assert calls == 2
    assert result.cas_conflicts == 1
    assert result.consumed_count == 1


@pytest.mark.django_db
def test_monitor_stops_after_bounded_cas_conflicts(monkeypatch) -> None:
    _routing_state()
    window = _window()
    calls = 0

    def always_conflict(**_kwargs):
        nonlocal calls
        calls += 1
        raise runtime_configs.RuntimeRoutingConflict("revision changed")

    monkeypatch.setattr(
        runtime_configs,
        "apply_virtual_player_safety_decision",
        always_conflict,
    )

    with pytest.raises(SafetyDecisionConflictExhausted, match="3 times"):
        monitor_finalized_safety_windows(
            now=window.window_end_at + timedelta(minutes=5),
            max_cas_retries=3,
        )

    assert calls == 3


def test_monitor_has_no_direct_safety_model_dml() -> None:
    source_path = Path(__file__).resolve().parents[1] / "gameplay/services/virtual_player_core/safety_monitor.py"
    source = source_path.read_text(encoding="utf-8")

    assert (
        find_model_dml(
            source,
            model_name="BotSafetyMetricEvent",
            filename=str(source_path),
        )
        == ()
    )
    assert (
        find_model_dml(
            source,
            model_name="BotSafetyMetricWindow",
            filename=str(source_path),
        )
        == ()
    )
