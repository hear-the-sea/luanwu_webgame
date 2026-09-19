from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from gameplay.tasks._scheduled import maybe_reschedule_for_future


def test_persistent_task_reschedule_defers_long_eta_to_scan(monkeypatch):
    now = timezone.now()
    calls = []
    monkeypatch.setattr(settings, "CELERY_PERSISTENT_ETA_MAX_SECONDS", 300, raising=False)

    result, observed_now = maybe_reschedule_for_future(
        task_func=object(),
        record_id=7,
        eta_value=now + timedelta(seconds=301),
        dedup_key="timer:7",
        schedule_func=lambda *args, **kwargs: calls.append((args, kwargs)) or True,
        logger=__import__("logging").getLogger("tests.scheduled_policy"),
        now_func=lambda: now,
        log_message="reschedule failed",
        failure_message="reschedule failed",
    )

    assert result == "deferred_to_scan"
    assert observed_now == now
    assert calls == []


def test_persistent_task_reschedule_keeps_near_term_eta(monkeypatch):
    now = timezone.now()
    calls = []
    monkeypatch.setattr(settings, "CELERY_PERSISTENT_ETA_MAX_SECONDS", 300, raising=False)

    result, observed_now = maybe_reschedule_for_future(
        task_func=object(),
        record_id=8,
        eta_value=now + timedelta(seconds=5),
        dedup_key="timer:8",
        schedule_func=lambda *args, **kwargs: calls.append((args, kwargs)) or True,
        logger=__import__("logging").getLogger("tests.scheduled_policy"),
        now_func=lambda: now,
        log_message="reschedule failed",
        failure_message="reschedule failed",
    )

    assert result == "rescheduled"
    assert observed_now == now
    assert calls[0][1]["countdown"] == 5
