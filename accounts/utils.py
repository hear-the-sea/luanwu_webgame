from __future__ import annotations

import logging
import time
import uuid
from threading import Lock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from accounts.models import UserActiveSession
from core.utils.cache_lock import release_cache_key_if_owner
from core.utils.infrastructure import DATABASE_CACHE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)

# 用户 session 映射的缓存 key 前缀
USER_SESSION_CACHE_PREFIX = "user_session:"
# 登录锁前缀（防止并发登录竞态条件）
USER_LOGIN_LOCK_PREFIX = "login_lock:"
# 缓存过期时间（与 session 过期时间一致，默认 2 周）
USER_SESSION_CACHE_TTL = getattr(settings, "SESSION_COOKIE_AGE", 1209600)
# 登录锁超时时间（秒）
LOGIN_LOCK_TIMEOUT = 5
LOGIN_LOCK_RETRY_INTERVAL_SECONDS = 0.05
LOGIN_LOCK_MAX_WAIT_SECONDS = 0.30

_LOCAL_LOGIN_LOCKS: dict[str, tuple[str, float]] = {}
_LOCAL_LOGIN_LOCKS_GUARD = Lock()
_LOCAL_LOGIN_LOCKS_MAX_SIZE = 5000


def _session_key_prefix(session_key: str | None) -> str:
    return (str(session_key) if session_key is not None else "<none>")[:8]


def purge_other_sessions(user_id: int, current_session_key: str | None) -> bool:
    """
    Enforce single active session per user without scanning the whole session table.

    数据库中的 UserActiveSession 是权威记录，缓存仅做镜像加速。

    Returns True on success, False if session sync failed (caller should treat
    this as a signal that single-session enforcement may not have taken effect).
    """
    if not current_session_key:
        return True

    cache_key = f"{USER_SESSION_CACHE_PREFIX}{user_id}"
    lock_key = f"{USER_LOGIN_LOCK_PREFIX}{user_id}"
    lock_token = uuid.uuid4().hex
    lock_acquired = False

    try:
        lock_acquired = _acquire_login_lock(lock_key, lock_token)
        if not lock_acquired:
            logger.warning("Login lock busy for user %s, proceeding with database-backed session sync", user_id)

        _sync_active_session_state(user_id, current_session_key, cache_key)
        return True
    except DATABASE_CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "Failed to sync active session for user %s: %s",
            user_id,
            exc,
            extra={"user_id": user_id, "degraded": True},
            exc_info=True,
        )
        return False
    finally:
        if lock_acquired:
            _release_login_lock(lock_key, lock_token)


def _safe_get_cached_session_key(cache_key: str) -> str | None:
    try:
        cached = cache.get(cache_key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.debug("Failed to read active session cache for key=%s", cache_key, exc_info=True)
        return None
    return str(cached) if cached else None


def _safe_set_cached_session_key(cache_key: str, session_key: str) -> None:
    try:
        cache.set(cache_key, session_key, timeout=USER_SESSION_CACHE_TTL)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.debug("Failed to write active session cache for key=%s", cache_key, exc_info=True)


def _sync_active_session_state(user_id: int, current_session_key: str, cache_key: str) -> None:
    user_model = get_user_model()

    with transaction.atomic():
        locked_user = user_model.objects.select_for_update().filter(pk=user_id).only("pk").first()
        if locked_user is None:
            return

        session_record = UserActiveSession.objects.select_for_update().filter(user_id=user_id).first()
        old_session_key = session_record.session_key if session_record else _safe_get_cached_session_key(cache_key)

        if old_session_key and old_session_key != current_session_key:
            Session.objects.filter(session_key=old_session_key).delete()

        if session_record is None:
            UserActiveSession.objects.create(user_id=user_id, session_key=current_session_key)
        elif session_record.session_key != current_session_key:
            session_record.session_key = current_session_key
            session_record.save(update_fields=["session_key", "updated_at"])

    _safe_set_cached_session_key(cache_key, current_session_key)


def _purge_sessions_fallback(user_id: int, current_session_key: str) -> None:
    """
    兼容保留的全量清理工具。

    注意：此方法会遍历有效 session，仅用于排障或手工修复，
    不再作为登录主路径上的降级方案。
    """
    now = timezone.now()
    sessions = Session.objects.filter(expire_date__gt=now).iterator(chunk_size=1000)
    deleted_count = 0

    for session in sessions:
        try:
            data = session.get_decoded()
            if str(data.get("_auth_user_id")) != str(user_id):
                continue
            if session.session_key == current_session_key:
                continue
            session.delete()
            deleted_count += 1
        except (ValueError, KeyError, TypeError) as exc:
            logger.debug(
                "Failed to decode session %s...: %s",
                _session_key_prefix(getattr(session, "session_key", None)),
                type(exc).__name__,
                exc_info=True,
            )
            continue
        except DATABASE_CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "Unexpected error processing session %s...: %s",
                _session_key_prefix(getattr(session, "session_key", None)),
                exc,
                exc_info=True,
            )
            continue

    if deleted_count > 0:
        logger.info("Fallback purged %d sessions for user %s", deleted_count, user_id)


def _release_login_lock(lock_key: str, lock_token: str) -> None:
    """Best-effort lock release with ownership check."""
    try:
        release_cache_key_if_owner(
            lock_key,
            lock_token=lock_token,
            logger=logger,
            log_context="login lock release",
        )
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.debug("Failed to release login lock %s", lock_key, exc_info=True)
    finally:
        _release_local_login_lock(lock_key, lock_token)


def _acquire_local_login_lock(lock_key: str, lock_token: str, deadline: float) -> bool:
    while True:
        now = time.monotonic()
        with _LOCAL_LOGIN_LOCKS_GUARD:
            existing = _LOCAL_LOGIN_LOCKS.get(lock_key)
            if existing is None or existing[1] <= now:
                _LOCAL_LOGIN_LOCKS[lock_key] = (lock_token, now + LOGIN_LOCK_TIMEOUT)
                _cleanup_local_login_locks(now)
                return True

        if now >= deadline:
            return False
        time.sleep(LOGIN_LOCK_RETRY_INTERVAL_SECONDS)


def _release_local_login_lock(lock_key: str, lock_token: str) -> None:
    with _LOCAL_LOGIN_LOCKS_GUARD:
        existing = _LOCAL_LOGIN_LOCKS.get(lock_key)
        if existing and existing[0] == lock_token:
            _LOCAL_LOGIN_LOCKS.pop(lock_key, None)


def _cleanup_local_login_locks(now: float) -> None:
    expired = [key for key, (_token, expire_at) in _LOCAL_LOGIN_LOCKS.items() if expire_at <= now]
    for key in expired[:1000]:
        _LOCAL_LOGIN_LOCKS.pop(key, None)

    if len(_LOCAL_LOGIN_LOCKS) <= _LOCAL_LOGIN_LOCKS_MAX_SIZE:
        return

    for key, _value in sorted(_LOCAL_LOGIN_LOCKS.items(), key=lambda item: item[1][1])[:500]:
        _LOCAL_LOGIN_LOCKS.pop(key, None)


def _acquire_login_lock(lock_key: str, lock_token: str) -> bool:
    """Try to acquire login lock with a short bounded retry window."""
    deadline = time.monotonic() + LOGIN_LOCK_MAX_WAIT_SECONDS
    while True:
        try:
            if cache.add(lock_key, lock_token, timeout=LOGIN_LOCK_TIMEOUT):
                return True
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            logger.warning("Login lock cache unavailable, fallback to local lock: key=%s", lock_key, exc_info=True)
            return _acquire_local_login_lock(lock_key, lock_token, deadline)

        if time.monotonic() >= deadline:
            return False
        time.sleep(LOGIN_LOCK_RETRY_INTERVAL_SECONDS)
