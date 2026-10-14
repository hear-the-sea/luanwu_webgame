from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.conf import settings

import gameplay.tasks as gameplay_tasks
import gameplay.tasks.virtual_players as virtual_player_tasks
from gameplay.services.virtual_player_core.config import MaintenanceMode


def test_maintenance_task_records_independent_stage_durations(monkeypatch, caplog):
    clock = iter([0.0, 7.0, 11.0, 29.0])
    monkeypatch.setattr(virtual_player_tasks, "monotonic", lambda: next(clock))
    monkeypatch.setattr(virtual_player_tasks, "scan_virtual_player_maintenance_completions", lambda **kwargs: [1, 2])
    monkeypatch.setattr(virtual_player_tasks, "schedule_due_virtual_recruitments", lambda **kwargs: 3)
    monkeypatch.setattr(virtual_player_tasks, "maintain_due_virtual_players", lambda **kwargs: 4)
    caplog.set_level("INFO", logger=virtual_player_tasks.__name__)
    assert virtual_player_tasks.scan_virtual_player_maintenance_task.run(limit=10) == 4
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "virtual_player_maintenance_scan_completed"
    )
    assert record.completion_reconciliation_duration_seconds == 7
    assert record.recruitment_scan_duration_seconds == 4
    assert record.maintenance_batch_duration_seconds == 18
    assert record.task_duration_seconds == 29
    assert (
        "completion_seconds=7.000 recruitment_seconds=4.000 maintenance_seconds=18.000 total_seconds=29.000"
        in record.getMessage()
    )


def test_population_task_messages_include_duration_and_budget(monkeypatch, caplog):
    clock = iter([0.0, 0.0, 12.0, 13.0, 20.0, 28.0])
    monkeypatch.setattr(virtual_player_tasks, "monotonic", lambda: next(clock))
    monkeypatch.setattr(virtual_player_tasks, "roll_virtual_player_population", lambda **kwargs: 3)
    monkeypatch.setattr(virtual_player_tasks, "scan_virtual_player_population_demands", lambda **kwargs: ())
    caplog.set_level("INFO", logger=virtual_player_tasks.__name__)
    assert virtual_player_tasks.roll_virtual_players_task.run() == 3
    assert virtual_player_tasks.scan_virtual_player_population_demands_task.run() == []
    roll = next(record for record in caplog.records if getattr(record, "event", "") == "virtual_player_roll_completed")
    scan = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "virtual_player_population_scan_task_completed"
    )
    assert "population_seconds=12.000 total_seconds=13.000 budget_seconds=20.000" in roll.getMessage()
    assert "total_seconds=8.000 budget_seconds=20.000" in scan.getMessage()


@pytest.mark.parametrize(
    ("task", "stream"),
    (
        (
            virtual_player_tasks.heartbeat_virtual_player_maintenance_attempt_emitter_task,
            "maintenance_attempt_emitter",
        ),
        (
            virtual_player_tasks.heartbeat_virtual_player_h01_callback_attempt_emitter_task,
            "h01_callback_attempt_emitter",
        ),
        (
            virtual_player_tasks.heartbeat_virtual_player_arena_shortage_emitter_task,
            "arena_shortage_emitter",
        ),
    ),
)
def test_emitter_heartbeat_tasks_use_their_canonical_stream(
    monkeypatch,
    task,
    stream: str,
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(
        virtual_player_tasks,
        "record_safety_heartbeat",
        lambda candidate: observed.append(candidate)
        or SimpleNamespace(event_id=f"heartbeat:{candidate}", created=True),
    )

    result = task.run()

    assert observed == [stream]
    assert result == {
        "stream": stream,
        "event_id": f"heartbeat:{stream}",
        "created": True,
    }


def test_aggregator_heartbeat_is_written_only_after_success(monkeypatch) -> None:
    order: list[str] = []
    windows = [SimpleNamespace(window_id="hourly:20260728T080000Z")]
    monkeypatch.setattr(
        virtual_player_tasks,
        "finalize_due_safety_windows",
        lambda *, limit: order.append(f"aggregate:{limit}") or windows,
    )
    monkeypatch.setattr(
        virtual_player_tasks,
        "record_safety_heartbeat",
        lambda stream: order.append(f"heartbeat:{stream}")
        or SimpleNamespace(event_id="aggregator-heartbeat", created=True),
    )

    result = virtual_player_tasks.aggregate_virtual_player_safety_task.run(limit=7)

    assert order == ["aggregate:7", "heartbeat:safety_aggregator"]
    assert result["finalized_count"] == 1
    assert result["window_ids"] == ["hourly:20260728T080000Z"]


def test_monitor_finalizes_before_deciding_and_heartbeats_after_success(
    monkeypatch,
) -> None:
    order: list[str] = []
    cycle = SimpleNamespace(
        finalized_windows=[SimpleNamespace(window_id="daily:20260728T000000Z")],
        monitor=SimpleNamespace(
            decisions=[SimpleNamespace(window_id="hourly:20260728T080000Z", warning_reasons=())],
            consumed_count=1,
            paused=False,
            cas_conflicts=0,
        ),
    )
    monkeypatch.setattr(
        virtual_player_tasks,
        "read_virtual_player_routing",
        lambda: SimpleNamespace(
            maintenance_mode=MaintenanceMode.V2_PAUSED,
            persisted=True,
            pause_reason="manual",
        ),
    )
    monkeypatch.setattr(
        virtual_player_tasks,
        "run_safety_monitor",
        lambda *, limit: order.append(f"monitor:{limit}") or cycle,
    )
    monkeypatch.setattr(
        virtual_player_tasks,
        "record_safety_heartbeat",
        lambda stream: order.append(f"heartbeat:{stream}")
        or SimpleNamespace(event_id="monitor-heartbeat", created=True),
    )

    result = virtual_player_tasks.monitor_virtual_player_safety_task.run(limit=9)

    assert order == ["monitor:9", "heartbeat:safety_monitor"]
    assert result == {
        "heartbeat": {
            "stream": "safety_monitor",
            "event_id": "monitor-heartbeat",
            "created": True,
        },
        "finalized_count": 1,
        "finalized_window_ids": ["daily:20260728T000000Z"],
        "decision_count": 1,
        "consumed_count": 1,
        "paused": False,
        "routing_mode": "v2_paused",
        "routing_pause_reason": "manual",
        "warning_reasons": [],
        "cas_conflicts": 0,
        "window_ids": ["hourly:20260728T080000Z"],
    }


def test_cleanup_safety_metric_task_delegates_and_serializes(monkeypatch) -> None:
    event_cutoff = datetime(2026, 6, 23, 5, 43, tzinfo=UTC)
    window_cutoff = datetime(2026, 4, 29, 5, 43, tzinfo=UTC)
    observed: list[int] = []

    monkeypatch.setattr(
        virtual_player_tasks,
        "cleanup_safety_metric_retention",
        lambda *, batch_size: observed.append(batch_size)
        or SimpleNamespace(
            events_deleted=17,
            windows_deleted=4,
            event_cutoff=event_cutoff,
            window_cutoff=window_cutoff,
        ),
    )

    result = virtual_player_tasks.cleanup_virtual_player_safety_metrics_task.run(batch_size=250)

    assert observed == [250]
    assert result == {
        "events_deleted": 17,
        "windows_deleted": 4,
        "event_cutoff": "2026-06-23T05:43:00+00:00",
        "window_cutoff": "2026-04-29T05:43:00+00:00",
    }


def test_cleanup_safety_metric_task_is_exported_routed_and_scheduled() -> None:
    assert (
        gameplay_tasks.cleanup_virtual_player_safety_metrics_task
        is virtual_player_tasks.cleanup_virtual_player_safety_metrics_task
    )
    assert (
        virtual_player_tasks.cleanup_virtual_player_safety_metrics_task.name
        == "gameplay.cleanup_virtual_player_safety_metrics"
    )
    assert settings.CELERY_TASK_ROUTES["gameplay.cleanup_virtual_player_safety_metrics"] == {
        "queue": settings.CELERY_TIMER_MAINTENANCE_QUEUE
    }
    schedule = settings.CELERY_BEAT_SCHEDULE["cleanup-virtual-player-safety-metrics"]
    assert schedule["task"] == "gameplay.cleanup_virtual_player_safety_metrics"


def test_all_safety_tasks_are_exported_routed_and_scheduled() -> None:
    expected = {
        "heartbeat-virtual-player-maintenance-attempt-emitter": (
            "gameplay.heartbeat_virtual_player_maintenance_attempt_emitter"
        ),
        "heartbeat-virtual-player-h01-callback-attempt-emitter": (
            "gameplay.heartbeat_virtual_player_h01_callback_attempt_emitter"
        ),
        "heartbeat-virtual-player-arena-shortage-emitter": ("gameplay.heartbeat_virtual_player_arena_shortage_emitter"),
        "aggregate-virtual-player-safety": "gameplay.aggregate_virtual_player_safety",
        "monitor-virtual-player-safety": "gameplay.monitor_virtual_player_safety",
    }

    for schedule_name, task_name in expected.items():
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_SAFETY_QUEUE}
        entry = settings.CELERY_BEAT_SCHEDULE[schedule_name]
        assert entry["task"] == task_name
        assert entry["schedule"]._orig_minute == "*"
