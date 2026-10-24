"""过期流水按小事务追赶积压，同时遵守批次和时间预算。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from gameplay.models import ResourceEvent
from gameplay.services.manor.core import ensure_manor
from gameplay.tasks import maintenance


@pytest.fixture
def expired_events(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="retention_capacity"))
    ResourceEvent.objects.bulk_create(
        [ResourceEvent(manor=manor, resource_type="silver", delta=1, reason="produce") for _ in range(7)]
    )
    cutoff = timezone.now() - timedelta(days=15)
    # MySQL does not populate primary keys on bulk-created instances.
    ids = list(ResourceEvent.objects.filter(manor=manor).order_by("pk").values_list("pk", flat=True))
    ResourceEvent.objects.filter(pk__in=ids[:6]).update(created_at=cutoff - timedelta(hours=1))
    return cutoff, ids


@pytest.mark.django_db
def test_retention_drains_more_than_one_batch_and_keeps_recent_rows(expired_events, settings):
    cutoff, ids = expired_events
    settings.CLEANUP_TASK_MAX_BATCHES = 10
    settings.CLEANUP_TASK_BUDGET_SECONDS = 60
    assert maintenance._batched_delete_before(ResourceEvent, time_field="created_at", cutoff=cutoff, batch_size=2) == 6
    assert list(ResourceEvent.objects.filter(pk__in=ids).values_list("pk", flat=True)) == ids[6:]


@pytest.mark.django_db
def test_retention_batch_cap_leaves_recoverable_backlog(expired_events, settings, caplog):
    cutoff, ids = expired_events
    settings.CLEANUP_TASK_MAX_BATCHES = 2
    settings.CLEANUP_TASK_BUDGET_SECONDS = 60
    assert maintenance._batched_delete_before(ResourceEvent, time_field="created_at", cutoff=cutoff, batch_size=2) == 4
    assert ResourceEvent.objects.filter(pk__in=ids[:6]).count() == 2
    assert "oldest_expired=" in caplog.text


@pytest.mark.django_db
def test_retention_stops_after_time_budget_and_can_resume(expired_events, settings, monkeypatch):
    cutoff, ids = expired_events
    settings.CLEANUP_TASK_MAX_BATCHES = 10
    settings.CLEANUP_TASK_BUDGET_SECONDS = 1
    ticks = iter((0.0, 1.1))
    monkeypatch.setattr(maintenance, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    assert maintenance._batched_delete_before(ResourceEvent, time_field="created_at", cutoff=cutoff, batch_size=2) == 2
    assert ResourceEvent.objects.filter(pk__in=ids[:6]).count() == 4


@pytest.mark.django_db
def test_retention_uses_configured_history_batch_default(expired_events, settings, monkeypatch):
    cutoff, ids = expired_events
    settings.CLEANUP_TASK_MAX_BATCHES = 1
    settings.CLEANUP_TASK_BUDGET_SECONDS = 60
    monkeypatch.setattr(maintenance, "CLEANUP_HISTORY_BATCH_SIZE", 3)

    assert maintenance._batched_delete_before(ResourceEvent, time_field="created_at", cutoff=cutoff) == 3
    assert ResourceEvent.objects.filter(pk__in=ids[:6]).count() == 3
