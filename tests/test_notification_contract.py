from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from bs4 import BeautifulSoup
from channels.consumer import get_handler_name
from django.conf import settings
from django.contrib.sessions.models import Session
from django.urls import reverse

from gameplay.services.utils import notifications as notification_utils
from gameplay.services.utils.notifications import notify_user
from websocket.consumers import NotificationConsumer


def test_base_exposes_authentication_state_and_loads_notifications_for_authenticated_users(
    client,
    authenticated_client,
):
    anonymous_response = client.get(reverse("accounts:login"))
    authenticated_response = authenticated_client.get(reverse("accounts:profile"))

    assert anonymous_response.status_code == 200
    assert authenticated_response.status_code == 200

    anonymous_page = BeautifulSoup(anonymous_response.content, "html.parser")
    authenticated_page = BeautifulSoup(authenticated_response.content, "html.parser")
    anonymous_shell = anonymous_page.select_one("#page-shell")
    authenticated_shell = authenticated_page.select_one("#page-shell")

    assert anonymous_shell is not None
    assert authenticated_shell is not None
    assert anonymous_shell.get("data-authenticated") == "0"
    assert authenticated_shell.get("data-authenticated") == "1"
    assert anonymous_page.select_one('script[src$="js/notifications.js"]') is None
    assert authenticated_page.select_one('script[src$="js/notifications.js"]') is not None


def test_expired_partial_navigation_session_follows_full_login_boundary(authenticated_client):
    session_key = authenticated_client.session.session_key
    assert session_key is not None
    Session.objects.filter(session_key=session_key).delete()
    assert authenticated_client.cookies[settings.SESSION_COOKIE_NAME].value == session_key

    warehouse_url = reverse("gameplay:warehouse")
    login_url = reverse("accounts:login")
    response = authenticated_client.get(
        warehouse_url,
        HTTP_X_PARTIAL_NAVIGATION="1",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        follow=True,
    )

    assert response.redirect_chain == [(f"{login_url}?next={warehouse_url}", 302)]
    assert response.status_code == 200
    assert response.request["PATH_INFO"] == login_url
    assert response.resolver_match.view_name == "accounts:login"

    login_page = BeautifulSoup(response.content, "html.parser")
    page_shell = login_page.select_one("#page-shell")
    assert login_page.select_one('form[method="post"]') is not None
    assert page_shell is not None
    assert page_shell.get("data-authenticated") == "0"
    assert login_page.select_one('script[src$="js/notifications.js"]') is None


def test_notify_user_to_consumer_emits_canonical_notification(monkeypatch):
    sent_event = {}

    class _ChannelLayer:
        def group_send(self, group_name, event):
            sent_event["group_name"] = group_name
            sent_event["event"] = event

    monkeypatch.setattr(notification_utils, "get_channel_layer", _ChannelLayer)
    monkeypatch.setattr(notification_utils, "async_to_sync", lambda callback: callback)

    assert notify_user(
        7,
        {
            "kind": "system",
            "title": "建筑升级完成",
            "building_key": "granary",
            "level": 2,
        },
    )

    consumer = NotificationConsumer()
    consumer.send_json = AsyncMock()
    event = sent_event["event"]
    assert event["type"] == "notify.message"
    handler_name = get_handler_name(event)
    assert handler_name == "notify_message"
    asyncio.run(getattr(consumer, handler_name)(event))

    assert sent_event["group_name"] == "user_7"
    payload = consumer.send_json.await_args.args[0]
    assert event["payload"] == payload
    assert set(payload) == {"type", "kind", "title", "body", "data", "timestamp"}
    timestamp = payload.pop("timestamp")
    assert datetime.fromisoformat(timestamp).tzinfo is not None
    assert payload == {
        "type": "notification",
        "kind": "system",
        "title": "建筑升级完成",
        "body": "",
        "data": {
            "building_key": "granary",
            "level": 2,
        },
    }
