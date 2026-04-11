from __future__ import annotations

import logging

import pytest
from django_redis.exceptions import ConnectionInterrupted
from kombu.exceptions import OperationalError

from common.utils import celery as celery_utils


class _Task:
    def __init__(self, *, failure: Exception | None = None):
        self.failure = failure
        self.called = 0
        self.last_kwargs = None

    def apply_async(self, **kwargs):
        self.called += 1
        self.last_kwargs = kwargs
        if self.failure is not None:
            raise self.failure


# ============ safe_apply_async tests ============


def test_safe_apply_async_returns_true_on_success():
    """Test that safe_apply_async returns True when dispatch succeeds."""
    task = _Task()

    ok = celery_utils.safe_apply_async(task, args=[1, 2], kwargs={"key": "value"})

    assert ok is True
    assert task.called == 1
    assert task.last_kwargs == {"args": [1, 2], "kwargs": {"key": "value"}, "countdown": None}


def test_safe_apply_async_returns_false_on_failure():
    """Test that safe_apply_async returns False when dispatch fails."""
    task = _Task(failure=OperationalError("dispatch failed"))

    ok = celery_utils.safe_apply_async(task, args=[1])

    assert ok is False
    assert task.called == 1


def test_safe_apply_async_logs_on_failure_when_logger_provided(caplog):
    """Test that safe_apply_async logs warning when dispatch fails and logger is provided."""
    task = _Task(failure=OperationalError("dispatch failed"))
    test_logger = logging.getLogger("test_celery")

    with caplog.at_level(logging.WARNING):
        ok = celery_utils.safe_apply_async(
            task,
            args=[1],
            logger=test_logger,
            log_message="custom error message",
        )

    assert ok is False
    assert "custom error message" in caplog.text


def test_safe_apply_async_merges_log_extra_on_failure(caplog):
    task = _Task(failure=OperationalError("dispatch failed"))
    test_logger = logging.getLogger("test_celery.extra")

    with caplog.at_level(logging.WARNING):
        ok = celery_utils.safe_apply_async(
            task,
            logger=test_logger,
            log_message="dispatch failed with context",
            log_extra={"tech_id": 17, "manor_id": 23},
        )

    assert ok is False
    record = next(record for record in caplog.records if record.message == "dispatch failed with context")
    assert record.tech_id == 17
    assert record.manor_id == 23
    assert record.degraded is True
    assert record.component == "celery_dispatch"


def test_safe_apply_async_can_raise_on_failure():
    task = _Task(failure=OperationalError("dispatch failed"))

    with pytest.raises(OperationalError, match="dispatch failed"):
        celery_utils.safe_apply_async(task, args=[1], raise_on_failure=True)


def test_safe_apply_async_programming_error_bubbles_up():
    task = _Task(failure=AssertionError("broken task contract"))

    with pytest.raises(AssertionError, match="broken task contract"):
        celery_utils.safe_apply_async(task, args=[1])


def test_safe_apply_async_handles_none_args_and_kwargs():
    """Test that safe_apply_async handles None args and kwargs correctly."""
    task = _Task()

    ok = celery_utils.safe_apply_async(task)

    assert ok is True
    assert task.last_kwargs == {"args": [], "kwargs": {}, "countdown": None}


def test_safe_apply_async_passes_countdown():
    """Test that safe_apply_async passes countdown parameter correctly."""
    task = _Task()

    ok = celery_utils.safe_apply_async(task, args=[1], countdown=60)

    assert ok is True
    assert task.last_kwargs["countdown"] == 60


# ============ safe_apply_async_with_dedup tests ============


def test_safe_apply_async_with_dedup_skips_when_key_already_exists(monkeypatch):
    task = _Task()
    monkeypatch.setattr(celery_utils.cache, "add", lambda *_args, **_kwargs: False)

    ok = celery_utils.safe_apply_async_with_dedup(
        task,
        dedup_key="test:key",
        dedup_timeout=5,
        args=[1],
    )

    assert ok is True
    assert task.called == 0


def test_safe_apply_async_with_dedup_dispatches_when_gate_open(monkeypatch):
    task = _Task()
    monkeypatch.setattr(celery_utils.cache, "add", lambda *_args, **_kwargs: True)

    ok = celery_utils.safe_apply_async_with_dedup(
        task,
        dedup_key="test:key2",
        dedup_timeout=5,
        args=[2],
        countdown=3,
    )

    assert ok is True
    assert task.called == 1
    assert task.last_kwargs == {"args": [2], "kwargs": {}, "countdown": 3}


def test_safe_apply_async_with_dedup_returns_false_on_dispatch_error(monkeypatch):
    task = _Task(failure=OperationalError("dispatch failed"))
    deleted = {"keys": []}
    monkeypatch.setattr(celery_utils.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(celery_utils.cache, "delete", lambda key: deleted["keys"].append(key))

    ok = celery_utils.safe_apply_async_with_dedup(
        task,
        dedup_key="test:key3",
        dedup_timeout=5,
        args=[3],
    )

    assert ok is False
    assert task.called == 1
    assert deleted["keys"] == ["test:key3"]


def test_safe_apply_async_with_dedup_rolls_back_and_raises_on_dispatch_error(monkeypatch):
    task = _Task(failure=OperationalError("dispatch failed"))
    deleted = {"keys": []}
    monkeypatch.setattr(celery_utils.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(celery_utils.cache, "delete", lambda key: deleted["keys"].append(key))

    with pytest.raises(OperationalError, match="dispatch failed"):
        celery_utils.safe_apply_async_with_dedup(
            task,
            dedup_key="test:key3:raise",
            dedup_timeout=5,
            args=[3],
            raise_on_failure=True,
        )

    assert task.called == 1
    assert deleted["keys"] == ["test:key3:raise"]


def test_safe_apply_async_with_dedup_falls_back_when_cache_unavailable(monkeypatch):
    task = _Task()

    def _raise(*_args, **_kwargs):
        raise ConnectionInterrupted("cache down")

    monkeypatch.setattr(celery_utils.cache, "add", _raise)

    ok = celery_utils.safe_apply_async_with_dedup(
        task,
        dedup_key="test:key4",
        dedup_timeout=5,
        args=[4],
    )

    assert ok is True
    assert task.called == 1


def test_safe_apply_async_with_dedup_runtime_marker_cache_error_bubbles_up(monkeypatch):
    task = _Task()

    def _raise(*_args, **_kwargs):
        raise RuntimeError("cache down")

    monkeypatch.setattr(celery_utils.cache, "add", _raise)

    with pytest.raises(RuntimeError, match="cache down"):
        celery_utils.safe_apply_async_with_dedup(
            task,
            dedup_key="test:key4:runtime-marker",
            dedup_timeout=5,
            args=[4],
        )


def test_safe_apply_async_with_dedup_cache_programming_error_bubbles_up(monkeypatch):
    task = _Task()

    def _raise(*_args, **_kwargs):
        raise AssertionError("broken cache contract")

    monkeypatch.setattr(celery_utils.cache, "add", _raise)

    with pytest.raises(AssertionError, match="broken cache contract"):
        celery_utils.safe_apply_async_with_dedup(
            task,
            dedup_key="test:key4:programming",
            dedup_timeout=5,
            args=[4],
        )


def test_safe_apply_async_with_dedup_rollback_programming_error_bubbles_up(monkeypatch):
    task = _Task(failure=OperationalError("dispatch failed"))
    monkeypatch.setattr(celery_utils.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        celery_utils.cache,
        "delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken cache delete contract")),
    )

    with pytest.raises(AssertionError, match="broken cache delete contract"):
        celery_utils.safe_apply_async_with_dedup(
            task,
            dedup_key="test:key4:rollback-programming",
            dedup_timeout=5,
            args=[4],
        )


def test_safe_apply_async_with_dedup_dispatch_programming_error_rolls_back_and_bubbles_up(monkeypatch):
    task = _Task(failure=AssertionError("broken dispatch contract"))
    deleted = {"keys": []}
    monkeypatch.setattr(celery_utils.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(celery_utils.cache, "delete", lambda key: deleted["keys"].append(key))

    with pytest.raises(AssertionError, match="broken dispatch contract"):
        celery_utils.safe_apply_async_with_dedup(
            task,
            dedup_key="test:key4:dispatch-programming",
            dedup_timeout=5,
            args=[4],
        )

    assert deleted["keys"] == ["test:key4:dispatch-programming"]


def test_safe_apply_async_with_dedup_rollback_cache_infrastructure_error_is_ignored(monkeypatch):
    task = _Task(failure=OperationalError("dispatch failed"))
    monkeypatch.setattr(celery_utils.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        celery_utils.cache,
        "delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache delete unavailable")),
    )

    ok = celery_utils.safe_apply_async_with_dedup(
        task,
        dedup_key="test:key4:rollback-cache-infra",
        dedup_timeout=5,
        args=[4],
    )

    assert ok is False


def test_safe_apply_async_with_dedup_skips_dedup_when_key_empty(monkeypatch):
    """Test that dedup is skipped when dedup_key is empty."""
    task = _Task()
    cache_add_called = {"count": 0}

    def _track_add(*_args, **_kwargs):
        cache_add_called["count"] += 1
        return True

    monkeypatch.setattr(celery_utils.cache, "add", _track_add)

    ok = celery_utils.safe_apply_async_with_dedup(
        task,
        dedup_key="",  # Empty key
        dedup_timeout=5,
        args=[5],
    )

    assert ok is True
    assert task.called == 1
    assert cache_add_called["count"] == 0  # cache.add should not be called


def test_safe_apply_async_with_dedup_skips_dedup_when_timeout_zero(monkeypatch):
    """Test that dedup is skipped when dedup_timeout is zero."""
    task = _Task()
    cache_add_called = {"count": 0}

    def _track_add(*_args, **_kwargs):
        cache_add_called["count"] += 1
        return True

    monkeypatch.setattr(celery_utils.cache, "add", _track_add)

    ok = celery_utils.safe_apply_async_with_dedup(
        task,
        dedup_key="test:key6",
        dedup_timeout=0,  # Zero timeout
        args=[6],
    )

    assert ok is True
    assert task.called == 1
    assert cache_add_called["count"] == 0  # cache.add should not be called


def test_safe_apply_async_with_dedup_passes_kwargs(monkeypatch):
    """Test that kwargs are passed correctly through dedup wrapper."""
    task = _Task()
    monkeypatch.setattr(celery_utils.cache, "add", lambda *_args, **_kwargs: True)

    ok = celery_utils.safe_apply_async_with_dedup(
        task,
        dedup_key="test:key7",
        dedup_timeout=5,
        args=[7],
        kwargs={"foo": "bar"},
        countdown=10,
    )

    assert ok is True
    assert task.last_kwargs == {"args": [7], "kwargs": {"foo": "bar"}, "countdown": 10}
