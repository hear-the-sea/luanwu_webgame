from __future__ import annotations

import unicodedata
from typing import Any, Callable


def normalize_login_throttle_username(username: str | None) -> str | None:
    normalized = unicodedata.normalize("NFKC", str(username or "")).strip().casefold()
    return normalized or None


def login_attempt_keys(ip: str, username: str | None = None) -> tuple[str, str | None]:
    username = normalize_login_throttle_username(username)
    return f"login_attempts:ip:{ip}", f"login_attempts:user:{username}" if username else None


def login_lock_keys(ip: str, username: str | None = None) -> tuple[str, str | None]:
    username = normalize_login_throttle_username(username)
    return f"login_lock:ip:{ip}", f"login_lock:user:{username}" if username else None


def normalize_lock_ttl(
    lock_key: str,
    *,
    cache_obj: Any,
    logger: Any,
    infrastructure_exceptions: tuple[type[BaseException], ...],
    lockout_duration: int,
) -> int:
    if not hasattr(cache_obj, "ttl"):
        return lockout_duration
    try:
        ttl = cache_obj.ttl(lock_key)
    except infrastructure_exceptions:
        logger.warning(
            "Failed to read lock TTL from cache: key=%s",
            lock_key,
            exc_info=True,
            extra={"degraded": True, "component": "login_cache"},
        )
        return lockout_duration
    if ttl is None:
        return lockout_duration
    try:
        ttl_int = int(ttl)
    except (TypeError, ValueError):
        return lockout_duration
    if ttl_int < 0:
        return lockout_duration
    return ttl_int


def check_login_attempts(
    request: Any,
    username: str | None = None,
    *,
    get_login_lock_key: Callable[[Any, str | None], tuple[str, str | None]],
    safe_cache_get: Callable[..., Any],
    normalize_lock_ttl: Callable[[str], int],
) -> tuple[bool, int]:
    ip_lock_key, username_lock_key = get_login_lock_key(request, username)

    if safe_cache_get(ip_lock_key):
        return True, normalize_lock_ttl(ip_lock_key)

    if username_lock_key and safe_cache_get(username_lock_key):
        return True, normalize_lock_ttl(username_lock_key)

    return False, 0


def safe_cache_get(
    key: str,
    default: Any = None,
    *,
    local_cache_get: Callable[..., Any],
    local_cache_set: Callable[..., None],
    local_cache_delete: Callable[..., None],
    cache_obj: Any,
    logger: Any,
    infrastructure_exceptions: tuple[type[BaseException], ...],
    local_cache_timeout: int,
    cache_miss_sentinel: Any,
) -> Any:
    local_value = local_cache_get(key, default)
    try:
        cached = cache_obj.get(key, cache_miss_sentinel)
    except infrastructure_exceptions:
        logger.warning(
            "Failed to read cache key: %s",
            key,
            exc_info=True,
            extra={"degraded": True, "component": "login_cache"},
        )
        return local_value
    if cached is cache_miss_sentinel or cached is None:
        # 清理已失效的共享镜像；写入失败的降级锁仍按原本地期限生效。
        local_cache_delete(key, synced_only=True)
        missing_default = default if cached is cache_miss_sentinel else None
        return local_cache_get(key, missing_default, unsynced_only=True)
    timeout = normalize_lock_ttl(
        key,
        cache_obj=cache_obj,
        logger=logger,
        infrastructure_exceptions=infrastructure_exceptions,
        lockout_duration=local_cache_timeout,
    )
    local_cache_set(key, cached, timeout=timeout, refresh=False)
    return cached


def safe_cache_set(
    key: str,
    value: Any,
    timeout: int,
    *,
    local_cache_set: Callable[..., None],
    cache_obj: Any,
    logger: Any,
    infrastructure_exceptions: tuple[type[BaseException], ...],
) -> None:
    unsynced = False
    try:
        unsynced = cache_obj.set(key, value, timeout=timeout) is False
    except infrastructure_exceptions:
        unsynced = True
        logger.warning(
            "Failed to write cache key: %s",
            key,
            exc_info=True,
            extra={"degraded": True, "component": "login_cache"},
        )
    local_cache_set(key, value, timeout=timeout, unsynced=unsynced)


def safe_cache_delete(
    key: str,
    *,
    local_cache_delete: Callable[[str], None],
    cache_obj: Any,
    logger: Any,
    infrastructure_exceptions: tuple[type[BaseException], ...],
) -> None:
    local_cache_delete(key)
    try:
        cache_obj.delete(key)
    except infrastructure_exceptions:
        logger.warning(
            "Failed to delete cache key: %s",
            key,
            exc_info=True,
            extra={"degraded": True, "component": "login_cache"},
        )


def _fallback_debug_attempt_counter(
    key: str,
    *,
    login_attempt_window: int,
    safe_cache_get: Callable[..., Any],
    safe_cache_set: Callable[..., None],
) -> int:
    attempts = 1
    try:
        raw_attempts = safe_cache_get(key, 0)
        attempts = int(raw_attempts or 0) + 1
    except (TypeError, ValueError):
        attempts = 1
    safe_cache_set(key, attempts, timeout=login_attempt_window)
    return attempts


def increment_attempt_counter(
    key: str,
    *,
    cache_obj: Any,
    logger: Any,
    settings_obj: Any,
    infrastructure_exceptions: tuple[type[BaseException], ...],
    login_attempt_limit: int,
    login_attempt_window: int,
    safe_cache_get: Callable[..., Any],
    safe_cache_set: Callable[..., None],
    local_cache_set: Callable[..., None],
    increment_degraded_counter: Callable[[str], None],
) -> int:
    added: bool | None = None
    try:
        added = bool(cache_obj.add(key, 1, timeout=login_attempt_window))
    except infrastructure_exceptions:
        logger.warning(
            "Failed to add login attempts cache key: %s",
            key,
            exc_info=True,
            extra={"degraded": True, "component": "login_cache"},
        )
        added = None

    if added is True:
        local_cache_set(key, 1, timeout=login_attempt_window)
        return 1

    if added is False:
        try:
            attempts = int(cache_obj.incr(key))
            # 只续期，不用旧计数覆盖其他请求已经原子递增的结果。
            cache_obj.touch(key, timeout=login_attempt_window)
            local_cache_set(key, attempts, timeout=login_attempt_window)
            return attempts
        except infrastructure_exceptions:
            logger.warning(
                "Failed to increment login attempts cache key: key=%s degraded=True",
                key,
                exc_info=True,
                extra={"degraded": True, "component": "login_cache"},
            )
            if not settings_obj.DEBUG:
                logger.warning(
                    "Login attempt counter cache unavailable: key=%s degraded=True fallback_mode=fail_closed",
                    key,
                    exc_info=False,
                )
                increment_degraded_counter("login_security_degraded")
                return login_attempt_limit
            logger.warning("Fallback to local login attempt counter path: key=%s", key)
            return _fallback_debug_attempt_counter(
                key,
                login_attempt_window=login_attempt_window,
                safe_cache_get=safe_cache_get,
                safe_cache_set=safe_cache_set,
            )

    logger.warning(
        "Login attempt counter cache unavailable: key=%s degraded=True fallback_mode=%s",
        key,
        "local" if settings_obj.DEBUG else "fail_closed",
    )
    if not settings_obj.DEBUG:
        increment_degraded_counter("login_security_degraded")
        return login_attempt_limit

    return _fallback_debug_attempt_counter(
        key,
        login_attempt_window=login_attempt_window,
        safe_cache_get=safe_cache_get,
        safe_cache_set=safe_cache_set,
    )


def record_failed_attempt(
    request: Any,
    username: str | None = None,
    *,
    get_login_attempt_key: Callable[[Any, str | None], tuple[str, str | None]],
    get_login_lock_key: Callable[[Any, str | None], tuple[str, str | None]],
    increment_attempt_counter: Callable[..., int],
    safe_cache_set: Callable[..., None],
    login_attempt_limit: int,
    login_ip_attempt_limit: int,
    login_lockout_duration: int,
) -> int:
    ip_key, username_key = get_login_attempt_key(request, username)
    ip_lock_key, username_lock_key = get_login_lock_key(request, username)

    ip_attempts = increment_attempt_counter(ip_key, limit=login_ip_attempt_limit)
    if ip_attempts >= login_ip_attempt_limit:
        safe_cache_set(ip_lock_key, 1, timeout=login_lockout_duration)

    user_attempts = 0
    if username_key:
        user_attempts = increment_attempt_counter(username_key)
        if user_attempts >= login_attempt_limit and username_lock_key:
            safe_cache_set(username_lock_key, 1, timeout=login_lockout_duration)

    return user_attempts if username_key else ip_attempts


def clear_login_attempts(
    request: Any,
    username: str | None = None,
    *,
    get_login_attempt_key: Callable[[Any, str | None], tuple[str, str | None]],
    get_login_lock_key: Callable[[Any, str | None], tuple[str, str | None]],
    safe_cache_delete: Callable[..., None],
    clear_ip: bool = True,
) -> None:
    ip_key, username_key = get_login_attempt_key(request, username)
    ip_lock_key, username_lock_key = get_login_lock_key(request, username)
    if clear_ip:
        safe_cache_delete(ip_key)
        safe_cache_delete(ip_lock_key)
    if username_key:
        safe_cache_delete(username_key)
    if username_lock_key:
        safe_cache_delete(username_lock_key)
