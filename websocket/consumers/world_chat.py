from __future__ import annotations

import logging
from functools import partial
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import ProgrammingError, transaction
from django_redis import get_redis_connection

from common.utils.celery import safe_apply_async
from core.exceptions import InsufficientStockError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.manor.bootstrap import ManorNotFoundError
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.world_chat_delivery import (
    WorldChatOperationConflictError,
    WorldChatValidationError,
    create_world_chat_attempt,
)
from websocket.exceptions import WorldChatInfrastructureError

from .session_guard import SingleSessionWebSocketMixin, WebSocketSessionValidationResult
from .world_chat_support import (
    filter_chat_message_payload,
    get_history_sync_for_consumer,
    rate_limit_sync_for_consumer,
    resolve_display_name_sync,
    safe_cache_get,
    safe_cache_set,
    send_connect_payloads,
)

User = get_user_model()

logger = logging.getLogger(__name__)


class WorldChatConsumer(SingleSessionWebSocketMixin, AsyncJsonWebsocketConsumer):
    """WebSocket consumer for the world chat channel."""

    CHANNEL = "world"
    GROUP_NAME = "chat_world"
    HISTORY_KEY = "chat:world:history"
    HISTORY_LIMIT = 200
    HISTORY_ON_CONNECT = 60
    HISTORY_MESSAGE_TTL_SECONDS = 24 * 60 * 60
    RATE_LIMIT_WINDOW_SECONDS = 8
    RATE_LIMIT_MAX_MESSAGES = 6
    DISPLAY_NAME_CACHE_TTL = 300
    user_id: int | None = None
    display_name: str = ""
    CHAT_UNAVAILABLE_MESSAGE = "世界频道暂时不可用，请稍后重试"
    HISTORY_UNAVAILABLE_MESSAGE = "历史消息暂时不可用，已跳过历史记录加载"

    _history_degraded: bool = False

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            logger.warning(
                "WebSocket authentication failed for WorldChatConsumer",
                extra={
                    "path": self.scope.get("path"),
                    "client": self.scope.get("client"),
                },
            )
            await self.close()
            return
        validation_result = await self._ensure_valid_session()
        if validation_result is WebSocketSessionValidationResult.UNAVAILABLE:
            await self.close(code=self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE)
            return
        if validation_result is WebSocketSessionValidationResult.INVALID:
            await self.close()
            return
        if validation_result is not WebSocketSessionValidationResult.VALID:
            raise RuntimeError(f"Unexpected websocket session validation result: {validation_result!r}")

        self.user_id = int(user.id)
        self.display_name = await self._get_display_name(self.user_id)

        await self.channel_layer.group_add(self.GROUP_NAME, self.channel_name)
        await self.accept()

        history = await self._get_history()
        await send_connect_payloads(
            self.send_json,
            channel=self.CHANNEL,
            user_id=self.user_id,
            display_name=self.display_name,
            history=history,
            history_degraded=self._history_degraded,
            history_status_message=self.HISTORY_UNAVAILABLE_MESSAGE if self._history_degraded else "",
        )

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.GROUP_NAME, self.channel_name)

    async def _process_send_message(self, content: dict) -> None:
        raw_operation_id = content.get("operation_id")
        if not isinstance(raw_operation_id, str):
            await self.send_json({"type": "error", "code": "invalid_operation_id", "message": "消息格式错误"})
            return
        try:
            operation_id = str(UUID(raw_operation_id))
        except ValueError:
            await self.send_json({"type": "error", "code": "invalid_operation_id", "message": "消息格式错误"})
            return

        raw_text = content.get("text", "")
        if not isinstance(raw_text, str):
            await self.send_json(
                {
                    "type": "error",
                    "code": "invalid_text",
                    "message": "消息格式错误",
                    "operation_id": operation_id,
                }
            )
            return

        try:
            allowed, retry_after = await self._rate_limit(self.user_id)
        except WorldChatInfrastructureError:
            await self.send_json(
                {
                    "type": "error",
                    "code": "chat_unavailable",
                    "message": self.CHAT_UNAVAILABLE_MESSAGE,
                    "operation_id": operation_id,
                }
            )
            return
        if not allowed:
            tip = "发送太快，请稍候再试"
            if retry_after:
                tip = f"发送太快，请 {retry_after}s 后再试"
            await self.send_json(
                {
                    "type": "error",
                    "code": "rate_limited",
                    "message": tip,
                    "operation_id": operation_id,
                }
            )
            return

        try:
            ack = await self._create_world_chat_attempt(
                operation_id=operation_id,
                raw_text=raw_text,
            )
        except WorldChatValidationError as exc:
            await self.send_json(
                {
                    "type": "error",
                    "code": "invalid_text",
                    "message": str(exc),
                    "operation_id": operation_id,
                }
            )
            return
        except WorldChatOperationConflictError as exc:
            await self.send_json(
                {
                    "type": "error",
                    "code": "operation_conflict",
                    "message": str(exc),
                    "operation_id": operation_id,
                }
            )
            return
        except ManorNotFoundError as exc:
            await self.send_json(
                {
                    "type": "error",
                    "code": "manor_not_found",
                    "message": str(exc),
                    "operation_id": operation_id,
                }
            )
            return
        except InsufficientStockError as exc:
            await self.send_json(
                {
                    "type": "error",
                    "code": "no_trumpet",
                    "message": str(exc),
                    "operation_id": operation_id,
                }
            )
            return
        except ProgrammingError:
            raise
        except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
            await self.send_json(
                {
                    "type": "error",
                    "code": "chat_unavailable",
                    "message": self.CHAT_UNAVAILABLE_MESSAGE,
                    "operation_id": operation_id,
                }
            )
            return

        await self.send_json(
            {
                "type": "send_ack",
                "operation_id": operation_id,
                "status": ack["status"],
                "created": ack["created"],
            }
        )

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.send_json({"type": "error", "code": "invalid_payload", "message": "消息格式错误"})
            return

        msg_type = content.get("type")

        if msg_type == "ping":
            await self.send_json({"type": "pong"})
            return

        if not getattr(self, "_single_session_checked_by_dispatch", False):
            validation_result = await self._ensure_valid_session()
            if validation_result is WebSocketSessionValidationResult.UNAVAILABLE:
                await self.close(code=self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE)
                return
            if validation_result is WebSocketSessionValidationResult.INVALID:
                await self.close()
                return
            if validation_result is not WebSocketSessionValidationResult.VALID:
                raise RuntimeError(f"Unexpected websocket session validation result: {validation_result!r}")

        if msg_type != "send":
            return
        await self._process_send_message(content)

    async def chat_message(self, event):
        await self.send_json(filter_chat_message_payload(event.get("payload", {})))

    def _get_redis(self):
        return get_redis_connection("default")

    def _safe_cache_get(self, key: str):
        return safe_cache_get(
            cache,
            key,
            logger_instance=logger,
            cache_infrastructure_exceptions=CACHE_INFRASTRUCTURE_EXCEPTIONS,
        )

    def _safe_cache_set(self, key: str, value: str, timeout: int) -> None:
        safe_cache_set(
            cache,
            key,
            value,
            timeout,
            logger_instance=logger,
            cache_infrastructure_exceptions=CACHE_INFRASTRUCTURE_EXCEPTIONS,
        )

    @database_sync_to_async
    def _get_display_name(self, user_id: int) -> str:
        return resolve_display_name_sync(
            user_id=user_id,
            cache_key=f"user:display_name:{user_id}",
            user_model=User,
            cache_ttl=self.DISPLAY_NAME_CACHE_TTL,
            cache_get_fn=self._safe_cache_get,
            cache_set_fn=self._safe_cache_set,
            logger_instance=logger,
        )

    def _dispatch_world_chat_attempt(self, attempt_id: int, status: str) -> None:
        from gameplay.tasks.world_chat import publish_world_chat_attempt_task, refund_world_chat_attempt_task

        task = None
        if status == "pending":
            task = publish_world_chat_attempt_task
        elif status == "refund_pending":
            task = refund_world_chat_attempt_task
        if task is None:
            return
        safe_apply_async(
            task,
            args=[attempt_id],
            logger=logger,
            log_message=f"world chat attempt dispatch failed: attempt_id={attempt_id} status={status}",
            log_extra={"attempt_id": attempt_id, "status": status},
        )

    @database_sync_to_async
    def _create_world_chat_attempt(self, *, operation_id: str, raw_text: str) -> dict:
        if self.user_id is None:
            raise WorldChatValidationError("user_id 必须是正整数")
        with transaction.atomic():
            attempt, created = create_world_chat_attempt(
                user_id=self.user_id,
                operation_id=operation_id,
                text=raw_text,
            )
            status = str(attempt.status)
            if status in {
                "pending",
                "refund_pending",
            }:
                transaction.on_commit(partial(self._dispatch_world_chat_attempt, attempt.pk, status))
            return {
                "operation_id": str(attempt.operation_id),
                "status": "queued" if status == "pending" else status,
                "created": created,
            }

    def _get_history_sync(self) -> list[dict]:
        redis = self._get_redis()
        messages, degraded = get_history_sync_for_consumer(
            redis,
            history_key=self.HISTORY_KEY,
            history_on_connect=self.HISTORY_ON_CONNECT,
            history_limit=self.HISTORY_LIMIT,
            history_message_ttl_seconds=self.HISTORY_MESSAGE_TTL_SECONDS,
            user_id=self.user_id,
        )
        self._history_degraded = degraded
        return messages

    async def _get_history(self) -> list[dict]:
        return await sync_to_async(self._get_history_sync, thread_sensitive=True)()

    def _rate_limit_sync(self, user_id: int | None) -> tuple[bool, int | None]:
        redis = self._get_redis()
        return rate_limit_sync_for_consumer(
            user_id,
            redis,
            rate_limit_window_seconds=self.RATE_LIMIT_WINDOW_SECONDS,
            rate_limit_max_messages=self.RATE_LIMIT_MAX_MESSAGES,
        )

    async def _rate_limit(self, user_id: int | None) -> tuple[bool, int | None]:
        return await sync_to_async(self._rate_limit_sync, thread_sensitive=True)(user_id)
