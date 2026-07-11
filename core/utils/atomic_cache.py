from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from threading import Lock
from time import monotonic, sleep
from typing import Final

from django.core.cache import cache

from core.utils.cache_lock import release_cache_key_if_owner
from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)

_UNSET: Final[object] = object()
_MERGE_LOCK_TIMEOUT_SECONDS: Final[float] = 5.0
_MERGE_LOCK_POLL_INTERVAL_SECONDS: float = 0.01
_LOCAL_COUNTER_FALLBACK: dict[str, int] = {}
_LOCAL_COUNTER_FALLBACK_LOCK = Lock()
_LOCAL_ID_SET_FALLBACK: dict[str, list[int]] = {}
_LOCAL_ID_SET_FALLBACK_LOCK = Lock()


def normalize_int_ids(values: Iterable[object]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values:
        if not str(value).strip():
            continue
        parsed = int(str(value))
        if parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return normalized


def _resolve_timeout(*, ttl: int | None, timeout: object) -> int | None:
    if timeout is _UNSET:
        return ttl
    if ttl is not None:
        raise TypeError("merge_int_id_set received both ttl and timeout")
    if timeout is None:
        return None
    if isinstance(timeout, int):
        return timeout
    raise TypeError("merge_int_id_set timeout must be int | None")


def _merge_ids(existing: Iterable[object], new_ids: Iterable[object]) -> list[int]:
    return normalize_int_ids([*existing, *new_ids])


def _merge_local_fallback_ids(key: str, values: list[int]) -> list[int]:
    with _LOCAL_ID_SET_FALLBACK_LOCK:
        merged = _merge_ids(_LOCAL_ID_SET_FALLBACK.get(key, []), values)
        _LOCAL_ID_SET_FALLBACK[key] = merged
        return list(merged)


def _get_local_fallback_ids(key: str) -> list[int]:
    with _LOCAL_ID_SET_FALLBACK_LOCK:
        return list(_LOCAL_ID_SET_FALLBACK.get(key, []))


def clear_local_int_id_set_fallback(key: str) -> None:
    with _LOCAL_ID_SET_FALLBACK_LOCK:
        _LOCAL_ID_SET_FALLBACK.pop(key, None)


def _drain_local_fallback_ids(key: str, applied_ids: list[int]) -> None:
    if not applied_ids:
        return
    applied = set(applied_ids)
    with _LOCAL_ID_SET_FALLBACK_LOCK:
        current = _LOCAL_ID_SET_FALLBACK.get(key, [])
        remaining = [value for value in current if value not in applied]
        if remaining:
            _LOCAL_ID_SET_FALLBACK[key] = remaining
            return
        _LOCAL_ID_SET_FALLBACK.pop(key, None)


def _merge_with_lock(
    key: str,
    lock_key: str,
    lock_token: str,
    normalized: list[int],
    *,
    cache_timeout: int | None,
) -> list[int]:
    try:
        pending_ids = _get_local_fallback_ids(key)
        try:
            existing = cache.get(key) or []
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            return _merge_local_fallback_ids(key, normalized)

        merged = _merge_ids(existing, [*pending_ids, *normalized])
        try:
            cache.set(key, merged, timeout=cache_timeout)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            return _merge_local_fallback_ids(key, merged)
        _drain_local_fallback_ids(key, pending_ids)
        return merged
    finally:
        release_cache_key_if_owner(
            lock_key,
            lock_token=lock_token,
            logger=logger,
            log_context="atomic int id set merge lock release",
        )


def merge_int_id_set(
    key: str,
    ids: Iterable[object],
    *,
    ttl: int | None = None,
    timeout: object = _UNSET,
) -> list[int]:
    normalized = normalize_int_ids(ids)
    if not normalized:
        return []

    cache_timeout = _resolve_timeout(ttl=ttl, timeout=timeout)
    lock_key = f"{key}:lock"
    deadline = monotonic() + _MERGE_LOCK_TIMEOUT_SECONDS

    while True:
        try:
            lock_token = uuid.uuid4().hex
            if cache.add(lock_key, lock_token, timeout=int(_MERGE_LOCK_TIMEOUT_SECONDS)):
                return _merge_with_lock(
                    key,
                    lock_key,
                    lock_token,
                    normalized,
                    cache_timeout=cache_timeout,
                )
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            return _merge_local_fallback_ids(key, normalized)

        if monotonic() >= deadline:
            return _merge_local_fallback_ids(key, normalized)
        sleep(_MERGE_LOCK_POLL_INTERVAL_SECONDS)


def get_int_id_set(
    key: str,
    *,
    ttl: int | None = None,
    timeout: object = _UNSET,
) -> list[int]:
    cache_timeout = _resolve_timeout(ttl=ttl, timeout=timeout)
    pending_ids = _get_local_fallback_ids(key)

    try:
        existing = cache.get(key) or []
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        return _merge_ids([], pending_ids)

    merged = _merge_ids(existing, pending_ids)
    if pending_ids:
        try:
            cache.set(key, merged, timeout=cache_timeout)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            return merged
        _drain_local_fallback_ids(key, pending_ids)
    return merged


def _claim_local_counter_fallback(key: str) -> int:
    with _LOCAL_COUNTER_FALLBACK_LOCK:
        pending = _LOCAL_COUNTER_FALLBACK.pop(key, 0)
        return pending


def _restore_local_counter_fallback(key: str, count: int) -> int:
    if count <= 0:
        with _LOCAL_COUNTER_FALLBACK_LOCK:
            return _LOCAL_COUNTER_FALLBACK.get(key, 0)
    with _LOCAL_COUNTER_FALLBACK_LOCK:
        restored = _LOCAL_COUNTER_FALLBACK.get(key, 0) + count
        _LOCAL_COUNTER_FALLBACK[key] = restored
        return restored


def increment_counter(key: str, *, ttl: int | None) -> int:
    pending_count = _claim_local_counter_fallback(key)
    delta = pending_count + 1

    try:
        value = int(cache.incr(key, delta=delta))
    except ValueError:
        try:
            if cache.add(key, delta, timeout=ttl):
                return delta
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            return _restore_local_counter_fallback(key, delta)

        try:
            value = int(cache.incr(key, delta=delta))
        except ValueError:
            try:
                current = int(cache.get(key) or 0)
            except CACHE_INFRASTRUCTURE_EXCEPTIONS:
                return _restore_local_counter_fallback(key, delta)
            next_value = current + delta
            try:
                cache.set(key, next_value, timeout=ttl)
            except CACHE_INFRASTRUCTURE_EXCEPTIONS:
                return _restore_local_counter_fallback(key, delta)
            return next_value
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            return _restore_local_counter_fallback(key, delta)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        return _restore_local_counter_fallback(key, delta)

    return value


__all__ = [
    "clear_local_int_id_set_fallback",
    "get_int_id_set",
    "increment_counter",
    "merge_int_id_set",
    "normalize_int_ids",
]
