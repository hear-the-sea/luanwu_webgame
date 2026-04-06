from __future__ import annotations

import logging
import time
from datetime import timedelta
from threading import Lock
from typing import Any

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from core.utils.degradation import CACHE_FALLBACK, REDIS_FAILURE, record_degradation
from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.online_presence_backend import ONLINE_USERS_TTL_SECONDS, count_online_users
from gameplay.services.online_presence_backend import (
    get_redis_connection_if_supported as _get_redis_connection_if_supported,
)
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)

User = get_user_model()

TOTAL_USERS_CACHE_KEY = "stats:total_users_count"
ONLINE_USERS_CACHE_KEY = "stats:online_users_count"
TOTAL_USERS_CACHE_TIMEOUT = 30
ONLINE_USERS_CACHE_TIMEOUT = 5
ONLINE_USERS_FALLBACK_CACHE_TIMEOUT = 60

_LOCAL_STATS_CACHE: dict[str, tuple[int, float]] = {}
_LOCAL_STATS_CACHE_GUARD = Lock()
_LOCAL_STATS_CACHE_MAX_SIZE = 64


def get_redis_connection(*_args: Any, **_kwargs: Any):
    """
    Backwards-compatible hook for tests and existing monkeypatch call sites.
    """
    return _get_redis_connection_if_supported()


def _safe_cache_get(key: str, default=None):
    try:
        return cache.get(key, default)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to read cache key: %s", key, exc_info=True)
        return default


def _safe_cache_set(key: str, value, timeout: int) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to write cache key: %s", key, exc_info=True)


def _safe_cache_delete(key: str) -> None:
    try:
        cache.delete(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to delete cache key: %s", key, exc_info=True)


def _cleanup_local_stats_cache(now: float) -> None:
    expired_keys = [key for key, (_value, expire_at) in _LOCAL_STATS_CACHE.items() if expire_at <= now]
    for key in expired_keys:
        _LOCAL_STATS_CACHE.pop(key, None)

    if len(_LOCAL_STATS_CACHE) <= _LOCAL_STATS_CACHE_MAX_SIZE:
        return

    for key, _value in sorted(_LOCAL_STATS_CACHE.items(), key=lambda item: item[1][1])[:16]:
        _LOCAL_STATS_CACHE.pop(key, None)


def _get_local_stats_cache(key: str) -> int | None:
    now = time.monotonic()
    with _LOCAL_STATS_CACHE_GUARD:
        record = _LOCAL_STATS_CACHE.get(key)
        if record is None:
            return None
        value, expire_at = record
        if expire_at <= now:
            _LOCAL_STATS_CACHE.pop(key, None)
            return None
        return value


def _set_local_stats_cache(key: str, value: int, timeout: int) -> None:
    expire_at = time.monotonic() + max(1, int(timeout))
    with _LOCAL_STATS_CACHE_GUARD:
        _LOCAL_STATS_CACHE[key] = (int(value), expire_at)
        if len(_LOCAL_STATS_CACHE) > _LOCAL_STATS_CACHE_MAX_SIZE:
            _cleanup_local_stats_cache(time.monotonic())


def _load_cached_stat_or_none(key: str) -> tuple[int | None, bool]:
    try:
        cached = cache.get(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        record_degradation(CACHE_FALLBACK, component="stats_selector", detail=f"stat cache read failed: {key}")
        return _get_local_stats_cache(key), False

    if cached is None:
        return None, True

    resolved = _safe_int(cached, default=0)
    _set_local_stats_cache(key, resolved, timeout=max(ONLINE_USERS_CACHE_TIMEOUT, TOTAL_USERS_CACHE_TIMEOUT))
    return resolved, True


def _persist_stat_cache(key: str, value: int, timeout: int) -> None:
    _set_local_stats_cache(key, value, timeout=timeout)
    _safe_cache_set(key, value, timeout=timeout)


def _safe_int(value, default: int = 0) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, resolved)


def load_total_user_count() -> int:
    cached_total, cache_available = _load_cached_stat_or_none(TOTAL_USERS_CACHE_KEY)
    if cached_total is not None:
        return cached_total

    if not cache_available:
        local_fallback = _get_local_stats_cache(TOTAL_USERS_CACHE_KEY)
        if local_fallback is not None:
            return local_fallback

    total_count = User.objects.filter(is_staff=False, is_superuser=False).count()
    _persist_stat_cache(TOTAL_USERS_CACHE_KEY, total_count, timeout=TOTAL_USERS_CACHE_TIMEOUT)
    return _safe_int(total_count)


def _load_online_user_count_from_redis() -> int:
    redis = get_redis_connection()
    if redis is None:
        raise NotImplementedError("Redis operations are unavailable for the configured cache backend")
    return _safe_int(count_online_users(redis, now_ts=float(time.time()), ttl_seconds=ONLINE_USERS_TTL_SECONDS))


def _load_online_user_count_from_db() -> int:
    time_threshold = timezone.now() - timedelta(minutes=30)
    return User.objects.filter(is_staff=False, is_superuser=False, last_login__gte=time_threshold).count()


def load_online_user_count() -> int:
    cached_online, cache_available = _load_cached_stat_or_none(ONLINE_USERS_CACHE_KEY)
    if cached_online is not None:
        return cached_online

    try:
        online_count = _load_online_user_count_from_redis()
    except NotImplementedError:
        if not cache_available:
            local_fallback = _get_local_stats_cache(ONLINE_USERS_CACHE_KEY)
            if local_fallback is not None:
                return local_fallback
        online_count = _load_online_user_count_from_db()
        _persist_stat_cache(ONLINE_USERS_CACHE_KEY, online_count, timeout=ONLINE_USERS_FALLBACK_CACHE_TIMEOUT)
        return online_count
    except INFRASTRUCTURE_EXCEPTIONS:
        record_degradation(REDIS_FAILURE, component="stats_selector", detail="online user count Redis read failed")
        fallback_cached = _get_local_stats_cache(ONLINE_USERS_CACHE_KEY)
        if fallback_cached is not None:
            return _safe_int(fallback_cached)
        online_count = _load_online_user_count_from_db()
        _persist_stat_cache(ONLINE_USERS_CACHE_KEY, online_count, timeout=ONLINE_USERS_FALLBACK_CACHE_TIMEOUT)
        return online_count

    _persist_stat_cache(ONLINE_USERS_CACHE_KEY, online_count, timeout=ONLINE_USERS_CACHE_TIMEOUT)
    return online_count
