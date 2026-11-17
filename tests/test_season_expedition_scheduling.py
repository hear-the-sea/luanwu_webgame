from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.utils import timezone

from gameplay.services.season import expeditions
from gameplay.tasks.season import advance_season_expedition_task


def test_schedule_rounds_up_and_carries_deadline(monkeypatch):
    now = timezone.now()
    run = SimpleNamespace(pk=42, due_at=now + timedelta(seconds=1, microseconds=100000))
    sent = {}
    monkeypatch.setattr(expeditions.timezone, "now", lambda: now)
    monkeypatch.setattr(expeditions.transaction, "on_commit", lambda callback: callback())
    monkeypatch.setattr(expeditions, "safe_apply_async", lambda task, **kwargs: sent.update(kwargs))

    expeditions._schedule(run)

    assert sent["countdown"] == 2
    assert sent["args"] == [42, run.due_at.isoformat()]


@pytest.mark.parametrize("same_deadline", [True, False])
def test_early_task_reschedules_only_its_own_deadline(monkeypatch, same_deadline):
    from gameplay.models import SeasonExpedition

    now = timezone.now()
    run = SimpleNamespace(pk=42, status="outbound", due_at=now + timedelta(seconds=1))
    expected = run.due_at if same_deadline else run.due_at - timedelta(seconds=30)
    scheduled = []
    monkeypatch.setattr(expeditions, "process_expedition", lambda run_id: False)
    monkeypatch.setattr(expeditions, "_schedule", scheduled.append)
    monkeypatch.setattr(timezone, "now", lambda: now)
    monkeypatch.setattr(SeasonExpedition.objects, "filter", lambda **kwargs: SimpleNamespace(first=lambda: run))

    assert advance_season_expedition_task.run(42, expected.isoformat()) is False
    assert scheduled == ([run] if same_deadline else [])


def test_expedition_completion_uses_timer_queue():
    assert settings.CELERY_TASK_ROUTES["gameplay.advance_season_expedition"]["queue"] == settings.CELERY_TIMER_QUEUE
    assert "gameplay.settle_seasons" in settings.CELERY_TIMER_SCAN_TASKS
