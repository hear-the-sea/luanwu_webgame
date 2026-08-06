from types import SimpleNamespace

import pytest

import core.celery_signals as celery_signals


def test_task_runtime_signals_record_elapsed_time(monkeypatch):
    clock_values = iter([10.0, 10.123])
    recorded = []

    monkeypatch.setattr(celery_signals, "monotonic", lambda: next(clock_values))
    monkeypatch.setattr(
        celery_signals,
        "record_task_runtime",
        lambda task_name, runtime_seconds, **_kwargs: recorded.append((task_name, runtime_seconds)),
    )

    sender = SimpleNamespace(name="gameplay.sync_resource_production")
    celery_signals._on_task_prerun(sender=sender, task_id="runtime-task-1")
    celery_signals._on_task_postrun(sender=sender, task_id="runtime-task-1")

    assert recorded[0][0] == "gameplay.sync_resource_production"
    assert recorded[0][1] == pytest.approx(0.123)


def test_task_runtime_postrun_without_prerun_is_ignored(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        celery_signals,
        "record_task_runtime",
        lambda *args: recorded.append(args),
    )

    celery_signals._on_task_postrun(sender=SimpleNamespace(name="task"), task_id="missing-start")

    assert recorded == []


def test_task_runtime_registers_ignored_task_name(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        celery_signals,
        "monotonic",
        iter([20.0, 20.010]).__next__,
    )
    monkeypatch.setattr(
        celery_signals,
        "record_task_runtime",
        lambda task_name, runtime_seconds, **kwargs: recorded.append((task_name, runtime_seconds, kwargs)),
    )

    sender = SimpleNamespace(name="ignored.task")
    celery_signals._on_task_prerun(sender=sender, task_id="ignored-task-1")
    celery_signals._on_task_postrun(sender=sender, task_id="ignored-task-1", state="IGNORED")

    assert recorded[0][0] == "ignored.task"
    assert recorded[0][2]["ensure_registered"] is True
