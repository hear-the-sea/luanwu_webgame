from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

from django.conf import settings
from django.core.cache import cache

from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

from .online_presence_backend import (
    ONLINE_USER_TOUCH_CACHE_KEY_PREFIX,
    ONLINE_USER_TOUCH_CACHE_TIMEOUT,
    ONLINE_USERS_CACHE_KEY,
    ONLINE_USERS_TTL_SECONDS,
    get_redis_connection_if_supported,
    touch_http_presence,
)

logger = logging.getLogger(__name__)

_presence_circuit_lock = threading.Lock()
_presence_attempt_lock = threading.Lock()
_presence_degraded_until = 0.0


def _presence_circuit_is_open() -> bool:
    with _presence_circuit_lock:
        return time.monotonic() < _presence_degraded_until


def _trip_presence_circuit() -> None:
    cooldown = max(1, int(getattr(settings, "ONLINE_PRESENCE_FAILURE_COOLDOWN_SECONDS", 30)))
    global _presence_degraded_until
    with _presence_circuit_lock:
        _presence_degraded_until = max(_presence_degraded_until, time.monotonic() + cooldown)


def _clear_presence_circuit() -> None:
    global _presence_degraded_until
    with _presence_circuit_lock:
        _presence_degraded_until = 0.0


def _reset_presence_circuit() -> None:
    """Reset the process-local circuit for isolated tests and process reloads."""
    _clear_presence_circuit()


def _safe_cache_add(key: str, value: Any, timeout: int) -> bool | None:
    try:
        return cache.add(key, value, timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        _trip_presence_circuit()
        logger.warning("Failed to add cache key: %s", key, exc_info=True)
        return None


def _safe_cache_delete(key: str) -> bool:
    try:
        cache.delete(key)
        return True
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        _trip_presence_circuit()
        logger.warning("Failed to delete cache key: %s", key, exc_info=True)
        return False


def _refresh_online_presence_for_user(*, user_id: int, username: str) -> None:
    touch_cache_key = f"{ONLINE_USER_TOUCH_CACHE_KEY_PREFIX}{int(user_id)}:{username}"
    should_refresh = _safe_cache_add(touch_cache_key, 1, timeout=ONLINE_USER_TOUCH_CACHE_TIMEOUT)
    # ``None`` means the cache gate itself failed. Do not continue into a
    # second Redis operation after that failure; the 15-second key TTL is
    # sufficient for a best-effort presence update.
    if should_refresh is not True:
        return

    infrastructure_failure = False
    try:
        redis = get_redis_connection_if_supported()
        if redis is None:
            return
        now_ts = float(time.time())
        touch_http_presence(redis, user_id=int(user_id), now_ts=now_ts, ttl_seconds=ONLINE_USERS_TTL_SECONDS)
        if _safe_cache_delete(ONLINE_USERS_CACHE_KEY):
            _clear_presence_circuit()
    except NotImplementedError:
        return
    except INFRASTRUCTURE_EXCEPTIONS:
        infrastructure_failure = True
        _trip_presence_circuit()
        logger.warning("Failed to refresh online user presence from HTTP request", exc_info=True)
    finally:
        # Avoid a second potentially stalled Redis call after an infrastructure
        # failure. Programming errors still run the historical compensating
        # delete and therefore remain visible to tests and error monitoring.
        if sys.exc_info()[0] is not None and not infrastructure_failure:
            _safe_cache_delete(touch_cache_key)


def refresh_online_presence_from_request(user) -> None:
    if not getattr(user, "is_authenticated", False):
        return
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return

    user_id = getattr(user, "id", None)
    if not user_id or _presence_circuit_is_open():
        return

    # Presence is best effort. A non-blocking process-local single-flight gate
    # prevents a Redis stall from making every Daphne request wait on its own
    # socket; skipped requests are covered by the next 15-second touch window.
    if not _presence_attempt_lock.acquire(blocking=False):
        return
    try:
        if _presence_circuit_is_open():
            return
        username = str(getattr(user, "username", "") or "").strip()
        _refresh_online_presence_for_user(user_id=int(user_id), username=username)
    finally:
        _presence_attempt_lock.release()
