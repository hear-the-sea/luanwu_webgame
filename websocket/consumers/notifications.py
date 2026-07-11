from __future__ import annotations

import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from gameplay.services.utils.notifications import CANONICAL_NOTIFICATION_FIELDS, normalize_notification_payload

from ..utils import filter_payload
from .session_guard import SingleSessionWebSocketMixin, WebSocketSessionValidationResult

logger = logging.getLogger(__name__)


class NotificationConsumer(SingleSessionWebSocketMixin, AsyncJsonWebsocketConsumer):
    """WebSocket consumer for per-user notifications."""

    UNAUTHENTICATED_CLOSE_CODE = 4401
    INVALID_SESSION_CLOSE_CODE = 4403

    group_name: str | None = None

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            logger.warning(
                "WebSocket authentication failed for NotificationConsumer",
                extra={
                    "path": self.scope.get("path"),
                    "client": self.scope.get("client"),
                },
            )
            await self.close(code=self.UNAUTHENTICATED_CLOSE_CODE)
            return
        validation_result = await self._ensure_valid_session()
        if validation_result is WebSocketSessionValidationResult.UNAVAILABLE:
            await self.close(code=self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE)
            return
        if validation_result is WebSocketSessionValidationResult.INVALID:
            await self.close(code=self.INVALID_SESSION_CLOSE_CODE)
            return
        if validation_result is not WebSocketSessionValidationResult.VALID:
            raise RuntimeError(f"Unexpected websocket session validation result: {validation_result!r}")

        self.group_name = f"user_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notify_message(self, event):
        payload = event.get("payload", {})
        safe_payload = filter_payload(normalize_notification_payload(payload), list(CANONICAL_NOTIFICATION_FIELDS))
        await self.send_json(safe_payload)
