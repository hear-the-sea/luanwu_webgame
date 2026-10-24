from __future__ import annotations

from datetime import timedelta
from importlib import import_module
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import DatabaseError
from django.utils import timezone

from accounts.models import UserActiveSession
from websocket.consumers.notifications import NotificationConsumer
from websocket.consumers.session_guard import WebSocketSessionValidationUnavailable, is_websocket_session_valid


def _build_session_for_user(user):
    session_store = import_module(settings.SESSION_ENGINE).SessionStore()
    session_store["_auth_user_id"] = str(user.id)
    session_store["_auth_user_backend"] = settings.AUTHENTICATION_BACKENDS[0]
    session_store["_auth_user_hash"] = user.get_session_auth_hash()
    session_store.save()
    return session_store


@pytest.mark.django_db
def test_is_websocket_session_valid_accepts_matching_active_session(django_user_model):
    user = django_user_model.objects.create_user(username="ws_guard_ok", password="pass123")
    session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=session.session_key)

    assert is_websocket_session_valid({"user": user, "session": session}) is True


@pytest.mark.django_db
def test_is_websocket_session_valid_rejects_stale_session_after_relogin(django_user_model):
    user = django_user_model.objects.create_user(username="ws_guard_stale", password="pass123")
    stale_session = _build_session_for_user(user)
    fresh_session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=fresh_session.session_key)

    assert is_websocket_session_valid({"user": user, "session": stale_session}) is False


@pytest.mark.django_db
def test_is_websocket_session_valid_rejects_deleted_session(django_user_model):
    user = django_user_model.objects.create_user(username="ws_guard_deleted", password="pass123")
    session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=session.session_key)
    Session.objects.filter(session_key=session.session_key).delete()

    assert is_websocket_session_valid({"user": user, "session": session}) is False


@pytest.mark.django_db
def test_is_websocket_session_valid_raises_when_session_store_unavailable(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="ws_guard_unavailable", password="pass123")
    session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=session.session_key)

    def _boom(_session_store):
        raise DatabaseError("session backend down")

    monkeypatch.setattr(type(session), "load", _boom)

    with pytest.raises(WebSocketSessionValidationUnavailable, match="unavailable"):
        is_websocket_session_valid({"user": user, "session": session})


@pytest.mark.django_db
def test_is_websocket_session_valid_programming_error_from_load_bubbles_up(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="ws_guard_exists_bug", password="pass123")
    session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=session.session_key)

    monkeypatch.setattr(
        type(session),
        "load",
        lambda _session_store: (_ for _ in ()).throw(AssertionError("broken session load contract")),
    )

    with pytest.raises(AssertionError, match="broken session load contract"):
        is_websocket_session_valid({"user": user, "session": session})


@pytest.mark.django_db
def test_is_websocket_session_valid_programming_error_from_user_reload_bubbles_up(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="ws_guard_payload_bug", password="pass123")
    session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=session.session_key)

    monkeypatch.setattr(
        django_user_model.objects,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken user reload contract")),
    )

    with pytest.raises(AssertionError, match="broken user reload contract"):
        is_websocket_session_valid({"user": user, "session": session})


@pytest.mark.django_db
@pytest.mark.parametrize("revocation", ["password", "inactive", "expired", "session_hash", "session_user"])
def test_existing_websocket_scope_rejects_revoked_authentication(django_user_model, revocation):
    user = django_user_model.objects.create_user(username="ws_guard_revoke", password="pass123")
    session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=session.session_key)
    scope = {"user": user, "session": session}
    assert is_websocket_session_valid(scope)

    if revocation == "password":
        current_user = django_user_model.objects.get(pk=user.pk)
        current_user.set_password("ChangedPass456!")
        current_user.save(update_fields=["password"])
    elif revocation == "inactive":
        django_user_model.objects.filter(pk=user.pk).update(is_active=False)
    elif revocation == "expired":
        Session.objects.filter(session_key=session.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )
    else:
        fresh_session = type(session)(session_key=session.session_key)
        fresh_session["_auth_user_hash" if revocation == "session_hash" else "_auth_user_id"] = "invalid"
        fresh_session.save()

    # Reuse the original cached scope to model an already-connected client.
    assert is_websocket_session_valid(scope) is False


@pytest.mark.django_db
def test_websocket_authentication_fails_closed_when_user_reload_unavailable(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="ws_guard_backend_down", password="pass123")
    session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=session.session_key)

    def unavailable(*_args, **_kwargs):
        raise DatabaseError("user backend unavailable")

    monkeypatch.setattr(django_user_model.objects, "get", unavailable)
    with pytest.raises(WebSocketSessionValidationUnavailable, match="unavailable"):
        is_websocket_session_valid({"user": user, "session": session})


@pytest.mark.django_db(transaction=True)
def test_existing_notification_connection_closes_after_account_deactivation(django_user_model):
    user = django_user_model.objects.create_user(username="ws_guard_notification", password="pass123")
    session = _build_session_for_user(user)
    UserActiveSession.objects.create(user=user, session_key=session.session_key)
    consumer = NotificationConsumer()
    consumer.scope = {"user": user, "session": session}
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    assert async_to_sync(consumer._ensure_valid_session)(force=True)

    django_user_model.objects.filter(pk=user.pk).update(is_active=False)
    consumer._single_session_valid_until = 0
    async_to_sync(consumer.dispatch)({"type": "notify.message", "payload": {"message": "private"}})

    consumer.close.assert_awaited_once_with(code=consumer.INVALID_SESSION_CLOSE_CODE)
    consumer.send_json.assert_not_awaited()
