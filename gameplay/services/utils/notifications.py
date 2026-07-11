"""
WebSocket 通知服务模块

提供统一的 WebSocket 推送通知功能。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from core.utils.infrastructure import NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from gameplay.models import Manor


CANONICAL_NOTIFICATION_FIELDS = ("type", "kind", "title", "body", "data", "timestamp")
_LEGACY_NOTIFICATION_FIELDS = {"message"}


def _coerce_notification_text(value: Any) -> str:
    return "" if value is None else str(value)


def _normalize_notification_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def normalize_notification_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """将新旧通知载荷统一为浏览器消费的 canonical DTO。"""
    reserved_fields = set(CANONICAL_NOTIFICATION_FIELDS) | _LEGACY_NOTIFICATION_FIELDS
    nested_data = payload.get("data")
    data = (
        {key: value for key, value in nested_data.items() if key not in reserved_fields}
        if isinstance(nested_data, dict)
        else {}
    )
    legacy_data = {key: value for key, value in payload.items() if key not in reserved_fields}

    body = payload.get("body")
    if body is None:
        body = payload.get("message", "")

    return {
        "type": "notification",
        "kind": _coerce_notification_text(payload.get("kind")),
        "title": _coerce_notification_text(payload.get("title")),
        "body": _coerce_notification_text(body),
        "data": {**legacy_data, **data},
        "timestamp": _normalize_notification_timestamp(payload.get("timestamp")),
    }


def notify_user(
    user_id: int,
    payload: Dict[str, Any],
    *,
    log_context: str = "WebSocket notification",
) -> bool:
    """
    向指定用户发送 WebSocket 通知。

    Args:
        user_id: 用户ID
        payload: 通知内容字典，应包含 'kind' 和 'title' 等字段
        log_context: 日志上下文描述，用于调试

    Returns:
        是否发送成功

    用法示例:
        notify_user(
            user_id=manor.user_id,
            payload={
                "kind": "production_complete",
                "title": "装备锻造完成",
                "equipment_key": "equip_sword",
                "quantity": 10,
            },
            log_context="equipment forging notification",
        )
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        logger.debug("Channel layer not available, skipping %s", log_context)
        return False

    try:
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {"type": "notify.message", "payload": normalize_notification_payload(payload)},
        )
        return True
    except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Failed to send %s via channels: %s", log_context, exc, exc_info=True)
        return False


def notify_manor(
    manor: "Manor",  # noqa: F821
    payload: Dict[str, Any],
    *,
    log_context: str = "WebSocket notification",
) -> bool:
    """
    向指定庄园所有者发送 WebSocket 通知。

    Args:
        manor: 庄园对象（需要有 user_id 属性）
        payload: 通知内容字典
        log_context: 日志上下文描述

    Returns:
        是否发送成功
    """
    return notify_user(manor.user_id, payload, log_context=log_context)
