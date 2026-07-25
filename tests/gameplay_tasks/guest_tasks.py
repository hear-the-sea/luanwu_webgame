from __future__ import annotations

import builtins
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.exceptions.task import TaskRescheduleError
from tests.gameplay_tasks.support import Chain


@pytest.mark.django_db
def test_guest_training_fractional_remaining(monkeypatch):
    import guests.tasks as guest_tasks

    now = timezone.now()
    guest = SimpleNamespace(training_complete_at=now + timedelta(milliseconds=300))

    monkeypatch.setattr("guests.models.Guest", SimpleNamespace(objects=Chain(first_result=guest)))
    monkeypatch.setattr("guests.tasks.timezone.now", lambda: now)

    finalized = []
    monkeypatch.setattr(
        "guests.services.training.finalize_guest_training", lambda *_args, **_kwargs: finalized.append(True)
    )

    called = {}

    def _safe_apply_async_with_dedup(*_args, args=None, countdown=None, **_kwargs):
        called["args"] = args
        called["countdown"] = countdown
        return True

    monkeypatch.setattr("guests.tasks.safe_apply_async_with_dedup", _safe_apply_async_with_dedup)

    assert guest_tasks.complete_guest_training.run(101) == "rescheduled"
    assert called["args"] == [101]
    assert called["countdown"] == 1
    assert not finalized


@pytest.mark.django_db
def test_guest_training_dispatch_false(monkeypatch, caplog):
    import guests.tasks as guest_tasks

    now = timezone.now()
    guest = SimpleNamespace(training_complete_at=now + timedelta(seconds=5))

    monkeypatch.setattr("guests.models.Guest", SimpleNamespace(objects=Chain(first_result=guest)))
    monkeypatch.setattr("guests.tasks.timezone.now", lambda: now)
    monkeypatch.setattr("guests.tasks.safe_apply_async_with_dedup", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("guests.services.training.finalize_guest_training", lambda *_args, **_kwargs: False)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(TaskRescheduleError, match="任务需要重新调度，请稍后重试"):
            guest_tasks.complete_guest_training.run(102)

    assert "guest training reschedule dispatch returned False: guest_id=102" in caplog.text


@pytest.mark.django_db
def test_guest_training_runtime_marker_bubbles_up_without_retry(monkeypatch):
    import guests.tasks as guest_tasks

    guest = SimpleNamespace(training_complete_at=None)

    monkeypatch.setattr("guests.models.Guest", SimpleNamespace(objects=Chain(first_result=guest)))
    monkeypatch.setattr(
        "guests.services.training.finalize_guest_training",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("training backend unavailable")),
    )
    monkeypatch.setattr(
        guest_tasks.complete_guest_training,
        "retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(RuntimeError, match="training backend unavailable"):
        guest_tasks.complete_guest_training.run(103)


@pytest.mark.django_db
def test_scan_guest_training_programming_error_bubbles_up(monkeypatch):
    import guests.tasks as guest_tasks

    now = timezone.now()
    guests = [SimpleNamespace(id=1)]

    monkeypatch.setattr(
        "guests.models.Guest",
        SimpleNamespace(objects=Chain(slice_result=guests)),
    )
    monkeypatch.setattr("guests.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guests.services.training.finalize_guest_training",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken guest training scan contract")),
    )

    with pytest.raises(AssertionError, match="broken guest training scan contract"):
        guest_tasks.scan_guest_training()


@pytest.mark.django_db
def test_complete_guest_recruitment_programming_error_bubbles_without_retry(monkeypatch):
    import guests.tasks as guest_tasks

    now = timezone.now()
    recruitment = SimpleNamespace(complete_at=now - timedelta(seconds=1), id=201)
    monkeypatch.setattr(
        "guests.models.GuestRecruitment",
        SimpleNamespace(objects=Chain(first_result=recruitment)),
    )
    monkeypatch.setattr("guests.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        guest_tasks.complete_guest_recruitment,
        "retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr(
        "guests.services.recruitment.finalize_guest_recruitment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken guest recruitment finalize contract")),
    )

    with pytest.raises(AssertionError, match="broken guest recruitment finalize contract"):
        guest_tasks.complete_guest_recruitment.run(201)


@pytest.mark.django_db
def test_scan_guest_recruitments_programming_error_bubbles_up(monkeypatch):
    import guests.tasks as guest_tasks

    now = timezone.now()
    recruitments = [SimpleNamespace(id=1)]

    class _Status:
        PENDING = "pending"

    monkeypatch.setattr(
        "guests.models.GuestRecruitment",
        SimpleNamespace(objects=Chain(slice_result=recruitments), Status=_Status),
    )
    monkeypatch.setattr("guests.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guests.services.recruitment.finalize_guest_recruitment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken guest recruitment scan contract")),
    )

    with pytest.raises(AssertionError, match="broken guest recruitment scan contract"):
        guest_tasks.scan_guest_recruitments()


@pytest.mark.django_db
def test_guest_training_via_safe_apply_async(monkeypatch):
    import guests.services.training as guest_training_service
    import guests.tasks as guest_tasks

    called = {}

    def _safe_apply_async(task, *, args=None, countdown=None, logger=None, log_message="", **_kwargs):
        called["task"] = task
        called["args"] = args
        called["countdown"] = countdown
        called["logger"] = logger
        called["log_message"] = log_message
        return True

    def _apply_async_should_not_run(*_args, **_kwargs):
        raise AssertionError("direct apply_async should not be used")

    monkeypatch.setattr(guest_training_service, "safe_apply_async", _safe_apply_async)
    monkeypatch.setattr(guest_tasks.complete_guest_training, "apply_async", _apply_async_should_not_run)

    guest_training_service._try_enqueue_complete_guest_training(
        SimpleNamespace(id=77, training_complete_at=timezone.now()),
        countdown=5,
        source="test",
    )

    assert called["task"] is guest_tasks.complete_guest_training
    assert called["args"] == [77]
    assert called["countdown"] == 5
    assert called["logger"] is guest_training_service.logger
    assert called["log_message"] == "guest training task dispatch failed"


def test_guest_training_missing_target_module_degrades(monkeypatch):
    from django.conf import settings

    import guests.services.training as guest_training_service

    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "guests.tasks":
            exc = ModuleNotFoundError("No module named 'guests.tasks'")
            exc.name = "guests.tasks"
            raise exc
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(
        guest_training_service,
        "finalize_guest_training",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not finalize training")),
    )
    monkeypatch.setattr(
        guest_training_service,
        "safe_apply_async",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not dispatch training task")),
    )

    guest_training_service._try_enqueue_complete_guest_training(
        SimpleNamespace(id=78, training_complete_at=timezone.now()),
        countdown=5,
        source="test-missing-target",
    )


def test_guest_training_unexpected_import_error_bubbles_up(monkeypatch):
    import guests.services.training as guest_training_service

    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "guests.tasks":
            raise RuntimeError("broken task module")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    with pytest.raises(RuntimeError, match="broken task module"):
        guest_training_service._try_enqueue_complete_guest_training(
            SimpleNamespace(id=79, training_complete_at=timezone.now()),
            countdown=5,
            source="test-broken-import",
        )


def test_guest_training_nested_import_error_bubbles_up(monkeypatch):
    import guests.services.training as guest_training_service

    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "guests.tasks":
            exc = ModuleNotFoundError("No module named 'redis'")
            exc.name = "redis"
            raise exc
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    with pytest.raises(ModuleNotFoundError, match="redis"):
        guest_training_service._try_enqueue_complete_guest_training(
            SimpleNamespace(id=80, training_complete_at=timezone.now()),
            countdown=5,
            source="test-nested-import",
        )
