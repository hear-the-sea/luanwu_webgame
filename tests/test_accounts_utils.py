from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.sessions.models import Session
from django.db import DatabaseError
from django.test import RequestFactory
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted

from accounts import signals as account_signals
from accounts import utils as account_utils
from accounts.models import UserActiveSession
from core.utils.degradation import SESSION_SYNC_FAILURE


def test_purge_other_sessions_does_not_release_foreign_lock(monkeypatch):
    user_id = 123
    lock_key = f"{account_utils.USER_LOGIN_LOCK_PREFIX}{user_id}"
    delete_mock = Mock()
    token_holder: dict[str, str] = {}

    def fake_add(key, value, timeout=None):
        assert key == lock_key
        token_holder["token"] = value
        return True

    def fake_get(key, default=None):
        if key == lock_key:
            return "another-token"
        return default

    monkeypatch.setattr(account_utils.cache, "add", fake_add)
    monkeypatch.setattr(account_utils.cache, "get", fake_get)
    monkeypatch.setattr(account_utils, "_sync_active_session_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(account_utils.cache, "delete", delete_mock)

    result = account_utils.purge_other_sessions(user_id, "current-session")
    assert result is True
    delete_mock.assert_not_called()


def test_purge_other_sessions_releases_owned_lock(monkeypatch):
    user_id = 456
    lock_key = f"{account_utils.USER_LOGIN_LOCK_PREFIX}{user_id}"
    delete_mock = Mock()
    token_holder: dict[str, str] = {}

    def fake_add(key, value, timeout=None):
        assert key == lock_key
        token_holder["token"] = value
        return True

    def fake_get(key, default=None):
        if key == lock_key:
            return token_holder["token"]
        return default

    monkeypatch.setattr(account_utils.cache, "add", fake_add)
    monkeypatch.setattr(account_utils.cache, "get", fake_get)
    monkeypatch.setattr(account_utils, "_sync_active_session_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(account_utils.cache, "delete", delete_mock)

    result = account_utils.purge_other_sessions(user_id, "current-session")
    assert result is True
    delete_mock.assert_called_once_with(lock_key)


def test_purge_other_sessions_continues_when_lock_busy(monkeypatch):
    sync_mock = Mock()
    fallback_mock = Mock()
    user_id = 777
    current_session_key = "current-session"
    cache_key = f"{account_utils.USER_SESSION_CACHE_PREFIX}{user_id}"

    monkeypatch.setattr(account_utils, "LOGIN_LOCK_MAX_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(account_utils.cache, "add", lambda *args, **kwargs: False)
    monkeypatch.setattr(account_utils, "_sync_active_session_state", sync_mock)
    monkeypatch.setattr(account_utils, "_purge_sessions_fallback", fallback_mock)

    result = account_utils.purge_other_sessions(user_id, current_session_key)

    assert result is True
    sync_mock.assert_called_once_with(user_id, current_session_key, cache_key)
    fallback_mock.assert_not_called()


def test_purge_other_sessions_returns_false_when_sync_raises(monkeypatch):
    user_id = 999

    monkeypatch.setattr(account_utils.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(account_utils.cache, "get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(account_utils.cache, "delete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        account_utils,
        "_sync_active_session_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db failure")),
    )

    result = account_utils.purge_other_sessions(user_id, "current-session")
    assert result is False


def test_purge_other_sessions_programming_error_bubbles_up(monkeypatch):
    user_id = 1001

    monkeypatch.setattr(account_utils.cache, "add", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(account_utils.cache, "get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(account_utils.cache, "delete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        account_utils,
        "_sync_active_session_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken active session sync contract")),
    )

    with pytest.raises(AssertionError, match="broken active session sync contract"):
        account_utils.purge_other_sessions(user_id, "current-session")


def test_safe_get_cached_session_key_programming_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        account_utils.cache,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken active-session cache read contract")),
    )

    with pytest.raises(AssertionError, match="broken active-session cache read contract"):
        account_utils._safe_get_cached_session_key("user_session:1")


def test_safe_set_cached_session_key_programming_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        account_utils.cache,
        "set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken active-session cache write contract")),
    )

    with pytest.raises(AssertionError, match="broken active-session cache write contract"):
        account_utils._safe_set_cached_session_key("user_session:1", "session-key")


def test_acquire_login_lock_falls_back_to_local_when_cache_add_errors(monkeypatch):
    lock_key = "login_lock:local_fallback"
    token = "token-a"

    account_utils._LOCAL_LOGIN_LOCKS.clear()
    monkeypatch.setattr(
        account_utils.cache,
        "add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )
    monkeypatch.setattr(account_utils, "LOGIN_LOCK_MAX_WAIT_SECONDS", 0.0)

    assert account_utils._acquire_login_lock(lock_key, token) is True
    assert account_utils._acquire_login_lock(lock_key, "token-b") is False

    account_utils._release_login_lock(lock_key, token)
    assert account_utils._acquire_login_lock(lock_key, "token-b") is True
    account_utils._release_login_lock(lock_key, "token-b")


def test_acquire_login_lock_runtime_marker_cache_error_bubbles_up(monkeypatch):
    lock_key = "login_lock:runtime_marker"

    account_utils._LOCAL_LOGIN_LOCKS.clear()
    monkeypatch.setattr(
        account_utils.cache,
        "add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        account_utils._acquire_login_lock(lock_key, "token-a")


def test_release_login_lock_programming_error_bubbles_up(monkeypatch):
    lock_key = "login_lock:release_programming_error"
    token = "token-a"

    account_utils._LOCAL_LOGIN_LOCKS[lock_key] = (
        token,
        account_utils.time.monotonic() + account_utils.LOGIN_LOCK_TIMEOUT,
    )
    monkeypatch.setattr(
        account_utils,
        "release_cache_key_if_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken login lock release contract")),
    )

    with pytest.raises(AssertionError, match="broken login lock release contract"):
        account_utils._release_login_lock(lock_key, token)

    assert lock_key not in account_utils._LOCAL_LOGIN_LOCKS


def test_release_login_lock_fails_closed_when_owner_release_returns_false(monkeypatch):
    lock_key = "login_lock:release-failed"
    lock_token = "expired-owner-token"
    cache_get = Mock(return_value=lock_token)
    cache_delete = Mock()

    monkeypatch.setattr(account_utils, "release_cache_key_if_owner", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(account_utils.cache, "get", cache_get)
    monkeypatch.setattr(account_utils.cache, "delete", cache_delete)

    account_utils._release_login_lock(lock_key, lock_token)

    cache_get.assert_not_called()
    cache_delete.assert_not_called()


def test_session_key_prefix_handles_none():
    assert account_utils._session_key_prefix(None) == "<none>"


def test_session_key_prefix_truncates_value():
    assert account_utils._session_key_prefix("abcdefghi") == "abcdefgh"


def test_purge_sessions_fallback_scans_beyond_1000_records(monkeypatch):
    class _FakeSession:
        def __init__(self, session_key: str, user_id: str):
            self.session_key = session_key
            self._user_id = user_id
            self.deleted = False

        def get_decoded(self):
            return {"_auth_user_id": self._user_id}

        def delete(self):
            self.deleted = True

    class _FakeQuerySet:
        def __init__(self, sessions):
            self._sessions = sessions

        def iterator(self, chunk_size=1000):
            return iter(self._sessions)

    class _FakeManager:
        def __init__(self, sessions):
            self._sessions = sessions

        def filter(self, **kwargs):
            return _FakeQuerySet(self._sessions)

    target_user = "42"
    current_session = "keep-current"
    sessions = [_FakeSession(f"other-{i}", "1") for i in range(1005)]
    far_session = _FakeSession("far-target", target_user)
    sessions.append(far_session)
    sessions.append(_FakeSession(current_session, target_user))

    fake_session_model = type("FakeSessionModel", (), {"objects": _FakeManager(sessions)})
    monkeypatch.setattr(account_utils, "Session", fake_session_model)

    account_utils._purge_sessions_fallback(int(target_user), current_session)

    assert far_session.deleted is True


def test_purge_sessions_fallback_database_error_is_best_effort(monkeypatch):
    class _FakeSession:
        session_key = "broken-db-session"

        def get_decoded(self):
            return {"_auth_user_id": "42"}

        def delete(self):
            raise DatabaseError("session delete down")

    class _FakeQuerySet:
        def iterator(self, chunk_size=1000):
            return iter([_FakeSession()])

    class _FakeManager:
        def filter(self, **kwargs):
            return _FakeQuerySet()

    fake_session_model = type("FakeSessionModel", (), {"objects": _FakeManager()})
    monkeypatch.setattr(account_utils, "Session", fake_session_model)

    account_utils._purge_sessions_fallback(42, "keep-current")


def test_purge_sessions_fallback_programming_error_bubbles_up(monkeypatch):
    class _FakeSession:
        session_key = "broken-programming-session"

        def get_decoded(self):
            return {"_auth_user_id": "42"}

        def delete(self):
            raise AssertionError("broken fallback session delete contract")

    class _FakeQuerySet:
        def iterator(self, chunk_size=1000):
            return iter([_FakeSession()])

    class _FakeManager:
        def filter(self, **kwargs):
            return _FakeQuerySet()

    fake_session_model = type("FakeSessionModel", (), {"objects": _FakeManager()})
    monkeypatch.setattr(account_utils, "Session", fake_session_model)

    with pytest.raises(AssertionError, match="broken fallback session delete contract"):
        account_utils._purge_sessions_fallback(42, "keep-current")


@pytest.mark.django_db
def test_sync_active_session_state_updates_authoritative_record(django_user_model):
    user = django_user_model.objects.create_user(username="single_session_user", password="pass123")
    Session.objects.create(
        session_key="old-session-key",
        session_data="e30:",
        expire_date=timezone.now() + timedelta(days=1),
    )
    UserActiveSession.objects.create(user=user, session_key="old-session-key")

    account_utils._sync_active_session_state(
        user.id,
        "new-session-key",
        f"{account_utils.USER_SESSION_CACHE_PREFIX}{user.id}",
    )

    assert UserActiveSession.objects.get(user=user).session_key == "new-session-key"
    assert Session.objects.filter(session_key="old-session-key").exists() is False


@pytest.mark.django_db
def test_login_signal_records_active_session(client, django_user_model):
    user = django_user_model.objects.create_user(username="signal_login_user", password="pass123")

    assert client.login(username="signal_login_user", password="pass123") is True

    active_session = UserActiveSession.objects.get(user=user)
    assert active_session.session_key == client.session.session_key


@pytest.mark.django_db
def test_login_signal_records_degradation_when_session_purge_returns_false(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="signal_login_warn", password="pass123")
    request = RequestFactory().get("/accounts/login")
    request.session = Mock()
    request.session.session_key = "session-key"

    recorded: dict[str, object] = {}
    monkeypatch.setattr(account_signals, "purge_other_sessions", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        account_signals,
        "record_degradation",
        lambda code, **kwargs: recorded.update({"code": code, **kwargs}),
    )

    account_signals.sync_active_session_on_login(sender=None, request=request, user=user)

    request.session.save.assert_called_once_with()
    assert recorded == {
        "code": SESSION_SYNC_FAILURE,
        "component": "sync_active_session_on_login",
        "detail": "purge_other_sessions returned False",
        "user_id": user.id,
    }


@pytest.mark.django_db
def test_login_signal_records_degradation_when_session_save_raises(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="signal_login_error", password="pass123")
    request = RequestFactory().get("/accounts/login")
    request.session = Mock()
    request.session.session_key = "session-key"
    request.session.save.side_effect = DatabaseError("session backend down")

    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        account_signals,
        "record_degradation",
        lambda code, **kwargs: recorded.update({"code": code, **kwargs}),
    )

    account_signals.sync_active_session_on_login(sender=None, request=request, user=user)

    assert recorded == {
        "code": SESSION_SYNC_FAILURE,
        "component": "sync_active_session_on_login",
        "detail": "DatabaseError: session backend down",
        "user_id": user.id,
    }


@pytest.mark.django_db
def test_login_signal_programming_error_bubbles_up(django_user_model):
    user = django_user_model.objects.create_user(username="signal_login_programming_error", password="pass123")
    request = RequestFactory().get("/accounts/login")
    request.session = Mock()
    request.session.session_key = "session-key"
    request.session.save.side_effect = AssertionError("broken session save contract")

    with pytest.raises(AssertionError, match="broken session save contract"):
        account_signals.sync_active_session_on_login(sender=None, request=request, user=user)
