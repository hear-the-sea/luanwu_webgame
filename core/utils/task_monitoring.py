"""
Celery task monitoring utilities.

Provides lightweight cache-backed counters for tracking task success, failure,
and retry events. Cache-backed snapshots are visible across Django/Celery
processes when the configured cache backend is shared (for example Redis).
In-process counters remain as a fallback when cache access fails.

Each metric field is stored under its own cache key so that increments map to
atomic ``cache.incr()`` calls (Redis INCR), eliminating the read-modify-write
race condition present in multi-worker deployments.

Key layout::

    metrics:celery:task_monitoring:index  → Redis set of known task names
    task_name_registry:{task_name}        → per-task presence marker
    metrics:celery:{task}:success         → int counter
    metrics:celery:{task}:failure         → int counter
    metrics:celery:{task}:retry           → int counter

Usage::

    from core.utils.task_monitoring import record_task_success, get_task_metrics

    record_task_success("trade.refresh_shop_stock")
    metrics = get_task_metrics()
    # => {"trade.refresh_shop_stock": {"success": 1, "failure": 0, "retry": 0}}
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any

from django.core.cache import cache

from core.utils import task_monitoring_registry
from core.utils.atomic_cache import increment_counter
from core.utils.degradation import CELERY_TASK_RETRY, record_degradation
from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)

# Legacy/non-Redis registry key: stores a set of known task names.
TASK_METRICS_CACHE_KEY = "metrics:celery:task_monitoring"
_TASK_METRICS_REDIS_REGISTRY_KEY = "metrics:celery:task_monitoring:index"
_TASK_NAME_REGISTRY_KEY_PREFIX = "task_name_registry:"

# Prefix for per-metric atomic counter keys.
_TASK_METRIC_KEY_PREFIX = "metrics:celery:"

# In-process fallback state (used only when cache is unavailable).
_metrics: dict[str, dict[str, int]] = defaultdict(lambda: {"success": 0, "failure": 0, "retry": 0})
_metrics_lock = threading.Lock()
_registry_lock = threading.Lock()

_FIELDS = ("success", "failure", "retry")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_task_entry(task_name: str) -> dict[str, int]:
    """Return the in-process counter dict for *task_name*, creating it if needed."""
    return _metrics[task_name]


def _empty_counts() -> dict[str, int]:
    return {"success": 0, "failure": 0, "retry": 0}


def _metric_key(task_name: str, field: str) -> str:
    return task_monitoring_registry.build_metric_key(_TASK_METRIC_KEY_PREFIX, task_name, field)


def _registry_member_key(task_name: str) -> str:
    return task_monitoring_registry.build_registry_member_key(_TASK_NAME_REGISTRY_KEY_PREFIX, task_name)


def _coerce_registry(value: object) -> set[str]:
    return task_monitoring_registry.coerce_registry(value)


def _get_redis_registry_client() -> Any | None:
    # django-redis may return different backend client types; keep the wrapper untyped here.
    return task_monitoring_registry.get_redis_registry_client(logger)


def _decode_redis_value(value: object) -> str:
    return task_monitoring_registry.decode_redis_value(value)


def _get_redis_registry_key() -> str:
    return task_monitoring_registry.get_redis_registry_key(cache, _TASK_METRICS_REDIS_REGISTRY_KEY)


def _get_redis_registry_member_prefix() -> str:
    return task_monitoring_registry.get_redis_registry_member_prefix(cache, _TASK_NAME_REGISTRY_KEY_PREFIX)


def _get_registry_from_redis() -> set[str] | None:
    return task_monitoring_registry.get_registry_from_redis(
        cache_backend=cache,
        logger=logger,
        registry_key=_TASK_METRICS_REDIS_REGISTRY_KEY,
        marker_prefix=_TASK_NAME_REGISTRY_KEY_PREFIX,
        get_client=_get_redis_registry_client,
        decode_value=_decode_redis_value,
    )


def _register_task_name(task_name: str) -> None:
    """Add *task_name* to the shared task-name registry."""
    marker_key = _registry_member_key(task_name)
    try:
        cache.add(marker_key, 1, timeout=None)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to write task metrics registry marker for %s", task_name, exc_info=True)

    redis_client = _get_redis_registry_client()
    if redis_client is not None:
        try:
            redis_client.sadd(_get_redis_registry_key(), task_name)
            return
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            logger.warning("Failed to update Redis task metrics registry index", exc_info=True)
            return

    try:
        with _registry_lock:
            registry = _coerce_registry(cache.get(TASK_METRICS_CACHE_KEY))
            if task_name in registry:
                return
            registry.add(task_name)
            cache.set(TASK_METRICS_CACHE_KEY, registry, timeout=None)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to update task metrics registry", exc_info=True)


def _get_registry() -> set[str] | None:
    """Return the set of known task names, or None on cache error."""
    redis_registry = _get_registry_from_redis()
    if redis_registry is not None:
        return redis_registry

    try:
        return _coerce_registry(cache.get(TASK_METRICS_CACHE_KEY))
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to read task metrics registry", exc_info=True)
        return None


def _increment_metric_atomic(task_name: str, field: str) -> None:
    """Atomically increment the cache counter for *task_name*/*field*.

    ``cache.incr()`` maps to Redis INCR (atomic) and is also lock-protected in
    Django's LocMemCache, making this safe for both backends.  Falls back to
    the in-process counter when cache access fails.
    """
    key = _metric_key(task_name, field)
    try:
        cache.incr(key, delta=1)
    except ValueError:
        # Key does not exist yet; initialise it with add() to avoid
        # a first-write lost-update race across workers.
        try:
            if not cache.add(key, 1, timeout=None):
                try:
                    cache.incr(key, delta=1)
                except ValueError:
                    cache.set(key, 1, timeout=None)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            logger.warning("Failed to initialise task metric %s.%s", task_name, field, exc_info=True)
            with _metrics_lock:
                _ensure_task_entry(task_name)[field] += 1
            return
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to increment task metric %s.%s", task_name, field, exc_info=True)
        with _metrics_lock:
            _ensure_task_entry(task_name)[field] += 1
        return

    # Persist the task name so get_task_metrics() can enumerate all tasks.
    _register_task_name(task_name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_task_success(task_name: str) -> None:
    """Increment the success counter for *task_name*."""
    _increment_metric_atomic(task_name, "success")


def record_task_failure(task_name: str) -> None:
    """Increment the failure counter for *task_name*."""
    _increment_metric_atomic(task_name, "failure")


def record_task_retry(task_name: str) -> None:
    """Increment the retry counter for *task_name* and record a degradation event."""
    _increment_metric_atomic(task_name, "retry")

    record_degradation(
        CELERY_TASK_RETRY,
        component="task_monitoring",
        detail=f"task {task_name} retried",
        task_name=task_name,
    )


def get_task_metrics() -> dict[str, dict[str, int]]:
    """Return a snapshot of task metrics, preferring the shared cache view."""
    registry = _get_registry()

    if registry is None:
        # Cache unavailable; fall back to in-process counters.
        with _metrics_lock:
            return {name: dict(counts) for name, counts in _metrics.items()}

    if not registry:
        return {}

    result: dict[str, dict[str, int]] = {}
    for task_name in registry:
        keys = [_metric_key(task_name, f) for f in _FIELDS]
        try:
            values = cache.get_many(keys)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            logger.warning("Failed to read metric keys for task %s", task_name, exc_info=True)
            with _metrics_lock:
                result[task_name] = dict(_ensure_task_entry(task_name))
            continue

        result[task_name] = {field: int(values.get(_metric_key(task_name, field), 0) or 0) for field in _FIELDS}

    return result


def reset_task_metrics() -> None:
    """Reset all task metrics (useful in tests)."""
    with _metrics_lock:
        _metrics.clear()

    registry = _get_registry() or set()
    keys_to_delete = [TASK_METRICS_CACHE_KEY, _TASK_METRICS_REDIS_REGISTRY_KEY]
    for task_name in registry:
        keys_to_delete.append(_registry_member_key(task_name))
        for field in _FIELDS:
            keys_to_delete.append(_metric_key(task_name, field))

    try:
        cache.delete_many(keys_to_delete)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to clear task metrics cache", exc_info=True)
        # Best-effort fallback: delete keys one by one.
        for key in keys_to_delete:
            try:
                cache.delete(key)
            except CACHE_INFRASTRUCTURE_EXCEPTIONS:
                pass


# --- Degraded path counters ---

_DEGRADED_COUNTER_TTL = 86400  # 24 hours


def increment_degraded_counter(component: str) -> None:
    """
    Increment the degraded-path counter for a named component.

    Counters are keyed by component name and reset daily.
    Safe to call from any context; cache failures are silently ignored.
    """
    from django.utils import timezone

    today = timezone.now().date().isoformat()
    key = f"degraded:{component}:{today}"
    try:
        increment_counter(key, ttl=_DEGRADED_COUNTER_TTL)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        return


def get_degraded_counter(component: str, *, date_str: str | None = None) -> int:
    """
    Read the degraded-path counter for a named component.
    Returns 0 if counter is missing or cache is unavailable.
    """
    from django.utils import timezone

    if date_str is None:
        date_str = timezone.now().date().isoformat()
    key = f"degraded:{component}:{date_str}"
    try:
        value = cache.get(key)
        return int(value or 0)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        return 0
