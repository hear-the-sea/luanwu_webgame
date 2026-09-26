"""全服顶部公告的发布、读取与在线推送。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from django.core.cache import cache
from django.utils import timezone

from gameplay.models import Manor

from .utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS
from .utils.notifications import notify_user

logger = logging.getLogger(__name__)

GLOBAL_ANNOUNCEMENT_CACHE_KEY = "gameplay:global_announcement:active"
GLOBAL_ANNOUNCEMENT_MAX_DURATION_SECONDS = 7 * 24 * 60 * 60


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _normalize_announcement(raw: object, *, now: datetime | None = None) -> dict[str, str] | None:
    if not isinstance(raw, Mapping):
        return None

    announcement_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    body = str(raw.get("body") or "").strip()
    expires_at = _parse_datetime(raw.get("expires_at"))
    starts_at = _parse_datetime(raw.get("starts_at"))
    current_time = now or timezone.now()
    if not announcement_id or not title or not body or expires_at is None:
        return None
    if starts_at is not None and starts_at > current_time:
        return None
    if expires_at <= current_time:
        return None

    return {
        "id": announcement_id,
        "title": title,
        "body": body,
        "starts_at": (starts_at or current_time).isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def get_active_global_announcement(*, now: datetime | None = None) -> dict[str, str] | None:
    """读取当前有效公告；缓存基础设施异常时不影响正常页面。"""
    try:
        raw = cache.get(GLOBAL_ANNOUNCEMENT_CACHE_KEY)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to read global announcement cache", exc_info=True)
        return None

    announcement = _normalize_announcement(raw, now=now)
    if announcement is not None:
        return announcement
    if raw is not None:
        try:
            cache.delete(GLOBAL_ANNOUNCEMENT_CACHE_KEY)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            logger.warning("Failed to clear expired global announcement cache", exc_info=True)
    return None


def publish_global_announcement(
    *,
    title: str,
    body: str,
    duration_seconds: int,
    announcement_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """写入一条全服公告并返回可供 WebSocket/页面使用的规范载荷。"""
    normalized_title = str(title or "").strip()
    normalized_body = str(body or "").strip()
    if not normalized_title:
        raise ValueError("公告标题不能为空")
    if not normalized_body:
        raise ValueError("公告正文不能为空")

    duration = int(duration_seconds)
    if duration <= 0 or duration > GLOBAL_ANNOUNCEMENT_MAX_DURATION_SECONDS:
        raise ValueError("公告持续时间必须在 1 秒至 7 天之间")

    current_time = now or timezone.now()
    announcement = {
        "id": str(announcement_id or uuid4().hex),
        "title": normalized_title,
        "body": normalized_body,
        "starts_at": current_time.isoformat(),
        "expires_at": (current_time + timedelta(seconds=duration)).isoformat(),
    }
    try:
        cache.set(GLOBAL_ANNOUNCEMENT_CACHE_KEY, announcement, timeout=duration)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Failed to publish global announcement cache", exc_info=True)
        raise RuntimeError("全服公告缓存暂不可用") from exc
    return announcement


def broadcast_global_announcement(announcement: Mapping[str, str]) -> tuple[int, int]:
    """把公告推送到所有真实玩家的通知组，返回（尝试数，成功数）。"""
    payload = {
        "kind": "announcement",
        "title": announcement["title"],
        "body": announcement["body"],
        "announcement_id": announcement["id"],
        "expires_at": announcement["expires_at"],
    }
    attempted = 0
    delivered = 0
    user_ids = (
        Manor.objects.filter(bot_profile__isnull=True, user__is_active=True)
        .order_by("user_id")
        .values_list("user_id", flat=True)
    )
    for user_id in user_ids.iterator(chunk_size=500):
        attempted += 1
        if notify_user(int(user_id), payload, log_context="global announcement"):
            delivered += 1
    return attempted, delivered


__all__ = [
    "GLOBAL_ANNOUNCEMENT_CACHE_KEY",
    "GLOBAL_ANNOUNCEMENT_MAX_DURATION_SECONDS",
    "broadcast_global_announcement",
    "get_active_global_announcement",
    "publish_global_announcement",
]
