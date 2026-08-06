"""
Celery signal handlers for task monitoring.

Connects to Celery's built-in signals to automatically record task success,
failure, and retry events via :mod:`core.utils.task_monitoring`.

Signals are registered by calling :func:`connect_task_signals` from the Celery
app configuration (``config/celery.py``).
"""

from __future__ import annotations

import logging
from threading import Lock
from time import monotonic

from celery.signals import task_failure, task_postrun, task_prerun, task_retry, task_success

from core.utils.task_monitoring import record_task_failure, record_task_retry, record_task_runtime, record_task_success

logger = logging.getLogger(__name__)
_TASK_START_TIMES: dict[str, float] = {}
_TASK_START_TIMES_LOCK = Lock()
_TASK_STATES_WITH_TERMINAL_METRICS = {"SUCCESS", "FAILURE", "RETRY"}


def _on_task_success(sender=None, **kwargs):
    """Record a successful task execution."""
    task_name = getattr(sender, "name", None) or str(sender)
    record_task_success(task_name)


def _on_task_failure(sender=None, exception=None, traceback=None, **kwargs):
    """Record a task failure and log structured context."""
    task_name = getattr(sender, "name", None) or str(sender)
    record_task_failure(task_name)
    logger.error(
        "Celery task failed: %s (%s)",
        task_name,
        type(exception).__name__ if exception else "unknown",
        extra={
            "task_name": task_name,
            "exception_type": type(exception).__name__ if exception else None,
            "exception_message": str(exception) if exception else None,
        },
    )


def _on_task_retry(sender=None, reason=None, **kwargs):
    """Record a task retry and log structured context."""
    task_name = getattr(sender, "name", None) or str(sender)
    record_task_retry(task_name)
    logger.warning(
        "Celery task retrying: %s (reason: %s)",
        task_name,
        reason,
        extra={
            "task_name": task_name,
            "retry_reason": str(reason) if reason else None,
        },
    )


def _on_task_prerun(sender=None, task_id=None, **kwargs):
    """Capture a monotonic start time without touching the database."""
    if task_id is None:
        return
    with _TASK_START_TIMES_LOCK:
        _TASK_START_TIMES[str(task_id)] = monotonic()


def _on_task_postrun(sender=None, task_id=None, state=None, **kwargs):
    """Record a bounded runtime bucket for every completed task state."""
    if task_id is None:
        return
    with _TASK_START_TIMES_LOCK:
        started_at = _TASK_START_TIMES.pop(str(task_id), None)
    if started_at is None:
        return
    task_name = getattr(sender, "name", None) or str(sender)
    # Success/failure/retry signals register the task name already; avoid a
    # second registry write on normal task completion. Ignored/rejected tasks
    # do not emit one of those signals, so their runtime metric must register
    # the task name itself.
    ensure_registered = state not in _TASK_STATES_WITH_TERMINAL_METRICS
    record_task_runtime(task_name, max(0.0, monotonic() - started_at), ensure_registered=ensure_registered)


def connect_task_signals() -> None:
    """Register all Celery signal handlers. Safe to call multiple times."""
    task_prerun.connect(_on_task_prerun, weak=False)
    task_postrun.connect(_on_task_postrun, weak=False)
    task_success.connect(_on_task_success, weak=False)
    task_failure.connect(_on_task_failure, weak=False)
    task_retry.connect(_on_task_retry, weak=False)
