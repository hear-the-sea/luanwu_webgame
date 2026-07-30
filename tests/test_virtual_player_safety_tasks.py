from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.conf import settings

import gameplay.tasks as gameplay_tasks
import gameplay.tasks.virtual_players as virtual_player_tasks


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
            decisions=[SimpleNamespace(window_id="hourly:20260728T080000Z")],
            consumed_count=1,
            paused=False,
            cas_conflicts=0,
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
        "queue": settings.CELERY_TIMER_QUEUE
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
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_QUEUE}
        entry = settings.CELERY_BEAT_SCHEDULE[schedule_name]
        assert entry["task"] == task_name
        assert entry["schedule"]._orig_minute == "*"
