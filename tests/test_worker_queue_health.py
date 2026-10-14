from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from core import tasks
from core.views import health


@pytest.fixture
def responsive_worker(monkeypatch):
    from config.celery import app

    monkeypatch.setattr(
        app.control, "inspect", lambda **kwargs: SimpleNamespace(ping=lambda: {"default-worker": "pong"})
    )


@override_settings(
    CELERY_WORKER_HEALTH_QUEUES=("default", "timer"), HEALTH_CHECK_WORKER_QUEUE_MAX_AGE_SECONDS=180, DEBUG=True
)
def test_other_worker_ping_does_not_hide_stalled_timer(responsive_worker):
    now = timezone.now().timestamp()
    cache.set(tasks.worker_queue_heartbeat_key("default"), now)
    cache.set(tasks.worker_queue_heartbeat_key("timer"), now - 300)

    ok, error = health._check_celery_workers_ready()

    assert not ok
    assert "timer" in error


@override_settings(CELERY_WORKER_HEALTH_QUEUES=("default", "timer"))
def test_all_queue_probes_are_required_and_record_actual_execution(responsive_worker):
    tasks.record_worker_queue_heartbeat.run("default")
    tasks.record_worker_queue_heartbeat.run("timer")
    assert health._check_celery_workers_ready() == (True, None)
    cache.delete(tasks.worker_queue_heartbeat_key("timer"))
    assert health._check_celery_workers_ready()[0] is False


@override_settings(CELERY_WORKER_HEALTH_QUEUES=("timer",))
def test_unknown_queue_cannot_write_a_health_probe():
    with pytest.raises(ValueError, match="Unknown worker heartbeat queue"):
        tasks.record_worker_queue_heartbeat.run("unregistered")


@override_settings(DEBUG=True)
def test_beat_publishing_is_separate_from_queued_execution():
    cache.delete(tasks.CELERY_BEAT_PUBLISHED_CACHE_KEY)
    tasks.record_celery_beat_heartbeat.run()
    assert health._check_celery_beat_ready()[0] is False
    tasks._record_beat_published(sender="core.record_celery_beat_heartbeat")
    assert health._check_celery_beat_ready() == (True, None)


def test_other_published_tasks_do_not_refresh_beat_health():
    cache.delete(tasks.CELERY_BEAT_PUBLISHED_CACHE_KEY)
    tasks._record_beat_published(sender="guests.complete_training")
    assert cache.get(tasks.CELERY_BEAT_PUBLISHED_CACHE_KEY) is None


def test_every_queue_gets_an_expiring_execution_probe():
    from django.conf import settings

    for queue in settings.CELERY_WORKER_HEALTH_QUEUES:
        entry = settings.CELERY_BEAT_SCHEDULE[f"record-worker-queue-heartbeat-{queue}"]
        assert entry["task"] == "core.record_worker_queue_heartbeat"
        assert entry["args"] == (queue,)
        assert entry["options"] == {"queue": queue, "expires": 120}
    assert settings.CELERY_TASK_ROUTES["core.record_celery_beat_heartbeat"] == {
        "queue": settings.CELERY_TIMER_SAFETY_QUEUE
    }
