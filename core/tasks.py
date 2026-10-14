from __future__ import annotations

import logging

from celery import shared_task
from celery.signals import after_task_publish
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS

CELERY_BEAT_HEARTBEAT_CACHE_KEY = "health:celery:beat:last_seen"
CELERY_BEAT_PUBLISHED_CACHE_KEY = "health:celery:beat:last_published"
_HEARTBEAT_CACHE_TTL_BUFFER_SECONDS = 300
logger = logging.getLogger(__name__)


def worker_queue_heartbeat_key(queue: str) -> str:
    return f"health:celery:queue:{queue}:last_seen"


def _record_beat_published(sender=None, **kwargs) -> None:
    if sender != "core.record_celery_beat_heartbeat":
        return
    try:
        max_age = max(60, int(getattr(settings, "HEALTH_CHECK_CELERY_BEAT_MAX_AGE_SECONDS", 180)))
        cache.set(CELERY_BEAT_PUBLISHED_CACHE_KEY, timezone.now().timestamp(), timeout=max_age + 300)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Could not record Celery beat publish heartbeat", exc_info=True)


after_task_publish.connect(_record_beat_published, weak=False)


@shared_task(name="core.record_worker_queue_heartbeat", ignore_result=True)
def record_worker_queue_heartbeat(queue: str) -> None:
    allowed = getattr(settings, "CELERY_WORKER_HEALTH_QUEUES", ())
    if queue not in allowed:
        raise ValueError("Unknown worker heartbeat queue")
    max_age = max(60, int(getattr(settings, "HEALTH_CHECK_WORKER_QUEUE_MAX_AGE_SECONDS", 180)))
    cache.set(worker_queue_heartbeat_key(queue), timezone.now().timestamp(), timeout=max_age + 300)


@shared_task(name="core.celery_health_ping")
def celery_health_ping() -> str:
    return "pong"


@shared_task(name="core.record_celery_beat_heartbeat", ignore_result=True)
def record_celery_beat_heartbeat() -> None:
    max_age_seconds = max(60, int(getattr(settings, "HEALTH_CHECK_CELERY_BEAT_MAX_AGE_SECONDS", 180)))
    timeout = max_age_seconds + _HEARTBEAT_CACHE_TTL_BUFFER_SECONDS
    cache.set(CELERY_BEAT_HEARTBEAT_CACHE_KEY, timezone.now().timestamp(), timeout=timeout)
