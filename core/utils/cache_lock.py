from __future__ import annotations

import logging
import time
import uuid
from enum import Enum, auto
from threading import Lock

from django.conf import settings
from django.core.cache import cache

from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS

_LOCAL_LOCKS: dict[str, tuple[str, float]] = {}
_LOCAL_LOCKS_GUARD = Lock()
_NON_ATOMIC_CACHE_LOCK_GUARD = Lock()
_LOCAL_LOCKS_MAX_SIZE = 20000
_LOCAL_LOCK_KEY_PREFIX = "local:"
_CACHE_RELEASE_IF_OWNER_SCRIPT = """
local lock_key = KEYS[1]
local expected_token = ARGV[1]
local current_token = redis.call('GET', lock_key)
if not current_token then
  return 0
end
if current_token == expected_token then
  return redis.call('DEL', lock_key)
end
return 0
"""
_CACHE_RENEW_IF_OWNER_SCRIPT = """
local lock_key = KEYS[1]
local expected_token = ARGV[1]
local timeout_seconds = ARGV[2]
local current_token = redis.call('GET', lock_key)
if not current_token then
  return 0
end
if current_token == expected_token then
  return redis.call('EXPIRE', lock_key, timeout_seconds)
end
return 0
"""


class _AtomicCacheLockReleaseResult(Enum):
    RELEASED = auto()
    NOT_OWNER = auto()
    UNSUPPORTED_BACKEND = auto()
    INFRASTRUCTURE_FAILURE = auto()


class _AtomicCacheLockRenewResult(Enum):
    RENEWED = auto()
    NOT_OWNER = auto()
    UNSUPPORTED_BACKEND = auto()
    INFRASTRUCTURE_FAILURE = auto()


def _cleanup_expired_local_locks(now: float) -> None:
    expired_keys = [key for key, (_token, expires_at) in _LOCAL_LOCKS.items() if expires_at <= now]
    for key in expired_keys:
        _LOCAL_LOCKS.pop(key, None)


def _make_lock_token() -> str:
    return uuid.uuid4().hex


def _release_cache_lock_atomic_if_owner(
    key: str,
    *,
    lock_token: str,
    logger: logging.Logger,
    log_context: str,
) -> _AtomicCacheLockReleaseResult:
    """
    Try atomic compare-and-delete in Redis.

    Returns:
        RELEASED: deleted successfully
        NOT_OWNER: key missing / ownership mismatch
        UNSUPPORTED_BACKEND: non-Redis backend; caller may fallback
        INFRASTRUCTURE_FAILURE: Redis path failed; leave the lock to expire
    """
    try:
        from django_redis import get_redis_connection
    except ImportError:
        return _AtomicCacheLockReleaseResult.UNSUPPORTED_BACKEND

    try:
        redis = get_redis_connection("default")
    except NotImplementedError:
        return _AtomicCacheLockReleaseResult.UNSUPPORTED_BACKEND
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "%s atomic cache lock release failed, leaving lock to expire: key=%s error=%s",
            log_context,
            key,
            exc,
            exc_info=True,
        )
        return _AtomicCacheLockReleaseResult.INFRASTRUCTURE_FAILURE

    redis_key = cache.make_key(key) if hasattr(cache, "make_key") else key  # type: ignore[attr-defined]
    encode = getattr(getattr(cache, "client", None), "encode", None)
    if not callable(encode):
        logger.warning(
            "%s atomic cache lock release failed because the Redis cache encoder is unavailable; "
            "leaving lock to expire: key=%s",
            log_context,
            key,
        )
        return _AtomicCacheLockReleaseResult.INFRASTRUCTURE_FAILURE
    encoded_lock_token = encode(lock_token)
    try:
        deleted = redis.eval(_CACHE_RELEASE_IF_OWNER_SCRIPT, 1, redis_key, encoded_lock_token)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "%s atomic cache lock release failed, leaving lock to expire: key=%s error=%s",
            log_context,
            key,
            exc,
            exc_info=True,
        )
        return _AtomicCacheLockReleaseResult.INFRASTRUCTURE_FAILURE

    if bool(int(deleted or 0)):
        return _AtomicCacheLockReleaseResult.RELEASED
    return _AtomicCacheLockReleaseResult.NOT_OWNER


def _release_cache_lock_non_atomic_if_owner(
    key: str,
    *,
    lock_token: str,
    logger: logging.Logger,
    log_context: str,
) -> bool:
    """Best-effort compare-delete fallback for non-Redis caches."""
    with _NON_ATOMIC_CACHE_LOCK_GUARD:
        try:
            current_token = cache.get(key)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "%s cache lock ownership check failed: key=%s error=%s",
                log_context,
                key,
                exc,
                exc_info=True,
            )
            return False

        if current_token != lock_token:
            return False

        try:
            cache.delete(key)
            return True
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "%s cache lock delete failed: key=%s error=%s",
                log_context,
                key,
                exc,
                exc_info=True,
            )
            return False


def _renew_cache_lock_atomic_if_owner(
    key: str,
    *,
    lock_token: str,
    timeout_seconds: int,
    logger: logging.Logger,
    log_context: str,
) -> _AtomicCacheLockRenewResult:
    """Try atomic compare-and-expire in Redis."""
    try:
        from django_redis import get_redis_connection
    except ImportError:
        return _AtomicCacheLockRenewResult.UNSUPPORTED_BACKEND

    try:
        redis = get_redis_connection("default")
    except NotImplementedError:
        return _AtomicCacheLockRenewResult.UNSUPPORTED_BACKEND
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "%s atomic cache lock renew failed: key=%s error=%s",
            log_context,
            key,
            exc,
            exc_info=True,
        )
        return _AtomicCacheLockRenewResult.INFRASTRUCTURE_FAILURE

    encode = getattr(getattr(cache, "client", None), "encode", None)
    if not callable(encode):
        logger.warning(
            "%s atomic cache lock renew failed because the Redis cache encoder is unavailable: key=%s",
            log_context,
            key,
        )
        return _AtomicCacheLockRenewResult.INFRASTRUCTURE_FAILURE

    try:
        redis_key = cache.make_key(key) if hasattr(cache, "make_key") else key  # type: ignore[attr-defined]
        encoded_lock_token = encode(lock_token)
        renewed = redis.eval(
            _CACHE_RENEW_IF_OWNER_SCRIPT,
            1,
            redis_key,
            encoded_lock_token,
            max(1, int(timeout_seconds)),
        )
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "%s atomic cache lock renew failed: key=%s error=%s",
            log_context,
            key,
            exc,
            exc_info=True,
        )
        return _AtomicCacheLockRenewResult.INFRASTRUCTURE_FAILURE

    if bool(int(renewed or 0)):
        return _AtomicCacheLockRenewResult.RENEWED
    return _AtomicCacheLockRenewResult.NOT_OWNER


def _renew_cache_lock_non_atomic_if_owner(
    key: str,
    *,
    lock_token: str,
    timeout_seconds: int,
    logger: logging.Logger,
    log_context: str,
) -> bool:
    """Compare and touch for non-Redis, single-process cache backends only."""
    with _NON_ATOMIC_CACHE_LOCK_GUARD:
        try:
            current_token = cache.get(key)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "%s cache lock ownership check failed during renew: key=%s error=%s",
                log_context,
                key,
                exc,
                exc_info=True,
            )
            return False

        if current_token != lock_token:
            return False

        try:
            return bool(cache.touch(key, timeout=max(1, int(timeout_seconds))))
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "%s cache lock touch failed: key=%s error=%s",
                log_context,
                key,
                exc,
                exc_info=True,
            )
            return False


def acquire_best_effort_lock(
    key: str,
    *,
    timeout_seconds: int,
    logger: logging.Logger,
    log_context: str,
    allow_local_fallback: bool | None = None,
) -> tuple[bool, bool, str | None]:
    """
    Acquire lock via cache first; fallback to in-process lock on cache failure.

    Returns:
        (acquired, from_cache, lock_token)
    """
    timeout = max(1, int(timeout_seconds))
    lock_token = _make_lock_token()
    if allow_local_fallback is None:
        allow_local_fallback = bool(getattr(settings, "BEST_EFFORT_LOCK_ALLOW_LOCAL_FALLBACK", True))
    try:
        with _NON_ATOMIC_CACHE_LOCK_GUARD:
            if cache.add(key, lock_token, timeout=timeout):
                return True, True, lock_token
            return False, True, None
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        if not allow_local_fallback:
            logger.warning(
                "%s cache lock unavailable (fail-closed): key=%s degraded=True error=%s",
                log_context,
                key,
                exc,
                exc_info=True,
            )
            from core.utils.task_monitoring import increment_degraded_counter

            increment_degraded_counter("cache_lock_fail_closed")
            return False, False, None
        logger.warning(
            "%s cache lock unavailable, fallback to local lock: key=%s error=%s",
            log_context,
            key,
            exc,
            exc_info=True,
        )

    now = time.monotonic()
    with _LOCAL_LOCKS_GUARD:
        existing = _LOCAL_LOCKS.get(key)
        if existing and existing[1] > now:
            return False, False, None

        _LOCAL_LOCKS[key] = (lock_token, now + timeout)
        from core.utils.task_monitoring import increment_degraded_counter

        increment_degraded_counter("local_lock_fallback")
        if len(_LOCAL_LOCKS) > _LOCAL_LOCKS_MAX_SIZE:
            _cleanup_expired_local_locks(now)
            if len(_LOCAL_LOCKS) > _LOCAL_LOCKS_MAX_SIZE:
                # If still oversized, remove oldest-ish items by earliest expiry.
                for stale_key, _ in sorted(_LOCAL_LOCKS.items(), key=lambda item: item[1][1])[:1000]:
                    _LOCAL_LOCKS.pop(stale_key, None)
        return True, False, lock_token


def build_action_lock_key(namespace: str, action: str, owner_id: int, scope: str) -> str:
    return f"{namespace}:{action}:{int(owner_id)}:{scope}"


def acquire_action_lock(
    namespace: str,
    action: str,
    owner_id: int,
    scope: str,
    *,
    timeout_seconds: int,
    logger: logging.Logger,
    log_context: str,
    allow_local_fallback: bool | None = None,
) -> tuple[bool, str, str | None]:
    key = build_action_lock_key(namespace, action, owner_id, scope)
    acquired, from_cache, lock_token = acquire_best_effort_lock(
        key,
        timeout_seconds=timeout_seconds,
        logger=logger,
        log_context=log_context,
        allow_local_fallback=allow_local_fallback,
    )
    if not acquired:
        return False, "", None
    if from_cache:
        return True, key, lock_token
    return True, f"{_LOCAL_LOCK_KEY_PREFIX}{key}", lock_token


def renew_best_effort_lock(
    key: str,
    *,
    from_cache: bool,
    lock_token: str | None,
    timeout_seconds: int,
    logger: logging.Logger,
    log_context: str,
) -> bool:
    """Renew a lock only while ``lock_token`` still owns it."""
    if not lock_token:
        logger.warning("%s lock_token missing, skip renew: key=%s", log_context, key)
        return False

    timeout = max(1, int(timeout_seconds))
    if from_cache:
        renew_result = _renew_cache_lock_atomic_if_owner(
            key,
            lock_token=lock_token,
            timeout_seconds=timeout,
            logger=logger,
            log_context=log_context,
        )
        if renew_result is _AtomicCacheLockRenewResult.RENEWED:
            return True
        if renew_result is _AtomicCacheLockRenewResult.UNSUPPORTED_BACKEND:
            return _renew_cache_lock_non_atomic_if_owner(
                key,
                lock_token=lock_token,
                timeout_seconds=timeout,
                logger=logger,
                log_context=log_context,
            )
        return False

    now = time.monotonic()
    with _LOCAL_LOCKS_GUARD:
        existing = _LOCAL_LOCKS.get(key)
        if not existing:
            return False
        if existing[1] <= now:
            _LOCAL_LOCKS.pop(key, None)
            return False
        if existing[0] != lock_token:
            return False
        _LOCAL_LOCKS[key] = (lock_token, now + timeout)
        return True


def release_best_effort_lock(
    key: str,
    *,
    from_cache: bool,
    lock_token: str | None,
    logger: logging.Logger,
    log_context: str,
) -> None:
    if not lock_token:
        logger.warning("%s lock_token missing, skip release to avoid unsafe unlock: key=%s", log_context, key)
        return

    if from_cache:
        release_result = _release_cache_lock_atomic_if_owner(
            key,
            lock_token=lock_token,
            logger=logger,
            log_context=log_context,
        )
        if release_result is _AtomicCacheLockReleaseResult.RELEASED:
            return
        if release_result is _AtomicCacheLockReleaseResult.UNSUPPORTED_BACKEND:
            _release_cache_lock_non_atomic_if_owner(
                key,
                lock_token=lock_token,
                logger=logger,
                log_context=log_context,
            )
        return

    with _LOCAL_LOCKS_GUARD:
        existing = _LOCAL_LOCKS.get(key)
        if not existing:
            return
        if existing[0] != lock_token:
            return
        _LOCAL_LOCKS.pop(key, None)


def release_action_lock(
    lock_key: str,
    *,
    lock_token: str | None,
    logger: logging.Logger,
    log_context: str,
) -> None:
    if not lock_key:
        return

    from_cache = True
    actual_key = lock_key
    if lock_key.startswith(_LOCAL_LOCK_KEY_PREFIX):
        from_cache = False
        actual_key = lock_key[len(_LOCAL_LOCK_KEY_PREFIX) :]

    release_best_effort_lock(
        actual_key,
        from_cache=from_cache,
        lock_token=lock_token,
        logger=logger,
        log_context=log_context,
    )


def release_cache_key_if_owner(
    key: str,
    *,
    lock_token: str | None,
    logger: logging.Logger,
    log_context: str,
) -> bool:
    """
    Release a cache-backed lock key only when the token matches ownership.

    Returns:
        True when key was deleted by owner, otherwise False.
    """
    if not lock_token:
        logger.warning("%s lock_token missing, skip release: key=%s", log_context, key)
        return False

    release_result = _release_cache_lock_atomic_if_owner(
        key,
        lock_token=lock_token,
        logger=logger,
        log_context=log_context,
    )
    if release_result is _AtomicCacheLockReleaseResult.RELEASED:
        return True
    if release_result is _AtomicCacheLockReleaseResult.UNSUPPORTED_BACKEND:
        return _release_cache_lock_non_atomic_if_owner(
            key,
            lock_token=lock_token,
            logger=logger,
            log_context=log_context,
        )
    return False
