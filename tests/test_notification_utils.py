from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from django_redis.exceptions import ConnectionInterrupted

from gameplay.services.utils import notifications as notification_utils
from gameplay.services.utils.notifications import normalize_notification_payload, notify_user


def test_normalize_notification_payload_coerces_missing_text_fields():
    payload = normalize_notification_payload({"message": "旧版正文"})

    assert payload["kind"] == ""
    assert payload["title"] == ""
    assert payload["body"] == "旧版正文"


def test_normalize_notification_payload_rejects_non_dict_data():
    payload = normalize_notification_payload({"data": ["invalid"], "building_key": "granary"})

    assert payload["data"] == {"building_key": "granary"}


def test_normalize_notification_payload_prefers_canonical_data():
    payload = normalize_notification_payload({"level": 1, "data": {"level": 2}})

    assert payload["data"] == {"level": 2}


def test_normalize_notification_payload_removes_reserved_data_fields():
    payload = normalize_notification_payload(
        {
            "kind": "system",
            "title": "顶层标题",
            "body": "顶层正文",
            "timestamp": "2026-07-10T12:00:00+00:00",
            "data": {
                "type": "nested-type",
                "kind": "battle",
                "title": "嵌套标题",
                "body": "嵌套正文",
                "timestamp": "nested-timestamp",
                "message": "嵌套旧正文",
                "level": 2,
            },
        }
    )

    assert payload == {
        "type": "notification",
        "kind": "system",
        "title": "顶层标题",
        "body": "顶层正文",
        "timestamp": "2026-07-10T12:00:00+00:00",
        "data": {"level": 2},
    }


def test_normalize_notification_payload_emits_timezone_aware_iso_timestamp():
    payload = normalize_notification_payload({"timestamp": 1})

    parsed_timestamp = datetime.fromisoformat(payload["timestamp"])
    assert parsed_timestamp.tzinfo is not None


def test_notify_user_returns_false_on_connection_interrupted(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(notification_utils, "logger", logger)
    monkeypatch.setattr(notification_utils, "async_to_sync", lambda fn: fn)
    monkeypatch.setattr(
        notification_utils,
        "get_channel_layer",
        lambda: MagicMock(group_send=lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("down"))),
    )

    assert notify_user(1, {"kind": "system", "title": "t"}) is False
    logger.warning.assert_called_once()


def test_notify_user_runtime_marker_error_bubbles_up(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(notification_utils, "logger", logger)
    monkeypatch.setattr(notification_utils, "async_to_sync", lambda fn: fn)
    monkeypatch.setattr(
        notification_utils,
        "get_channel_layer",
        lambda: MagicMock(
            group_send=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("notification backend down"))
        ),
    )

    with pytest.raises(RuntimeError, match="notification backend down"):
        notify_user(1, {"kind": "system", "title": "t"})

    logger.exception.assert_not_called()


def test_notify_user_unexpected_runtime_error_bubbles_up(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(notification_utils, "logger", logger)
    monkeypatch.setattr(notification_utils, "async_to_sync", lambda fn: fn)
    monkeypatch.setattr(
        notification_utils,
        "get_channel_layer",
        lambda: MagicMock(group_send=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad payload"))),
    )

    with pytest.raises(RuntimeError, match="bad payload"):
        notify_user(1, {"kind": "system", "title": "t"})

    logger.exception.assert_not_called()


def test_notify_user_unserializable_payload_bubbles_up(monkeypatch):
    def _serialize_event(_group_name, event):
        json.dumps(event)

    monkeypatch.setattr(notification_utils, "async_to_sync", lambda fn: fn)
    monkeypatch.setattr(
        notification_utils,
        "get_channel_layer",
        lambda: MagicMock(group_send=_serialize_event),
    )

    with pytest.raises(TypeError, match="not JSON serializable"):
        notify_user(1, {"kind": "system", "title": "t", "domain_value": object()})
