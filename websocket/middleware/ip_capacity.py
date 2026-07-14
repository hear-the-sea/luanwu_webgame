"""ASGI enforcement for per-IP WebSocket capacity."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from django_redis import get_redis_connection

from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from core.utils.network import get_asgi_client_ip
from websocket.backends.ip_capacity import (
    IPCapacityDecision,
    IPCapacityResult,
    acquire_ip_capacity,
    refresh_ip_capacity_slot,
    release_ip_capacity_slot,
)
from websocket.backends.worker_lease import get_websocket_worker_lease_manager
from websocket.exceptions import WebSocketConnectionLimitUnavailable

logger = logging.getLogger(__name__)

WEBSOCKET_IP_CAPACITY_EXCEPTIONS: tuple[type[Exception], ...] = (
    WebSocketConnectionLimitUnavailable,
    *INFRASTRUCTURE_EXCEPTIONS,
)


def _client_ip_log_id(client_ip: str) -> str:
    return hashlib.sha256(str(client_ip).encode("utf-8")).hexdigest()[:12]


class WebSocketIPCapacityMiddleware:
    CAPACITY_CLOSE_CODE = 4429
    UNAVAILABLE_CLOSE_CODE = 1013

    def __init__(self, app) -> None:
        self.app = app
        self._worker_lease_manager = get_websocket_worker_lease_manager()

    def _capacity_redis(self):
        try:
            return get_redis_connection("default")
        except INFRASTRUCTURE_EXCEPTIONS as exc:
            raise WebSocketConnectionLimitUnavailable("WebSocket IP capacity unavailable") from exc

    def _acquire_capacity_sync(self, client_ip: str, connection_id: str, worker_id: str) -> IPCapacityDecision:
        return acquire_ip_capacity(
            self._capacity_redis(),
            client_ip=client_ip,
            worker_id=worker_id,
            connection_id=connection_id,
            connection_limit=int(settings.WEBSOCKET_MAX_CONNECTIONS_PER_IP),
            rate_per_second=int(settings.WEBSOCKET_HANDSHAKE_RATE_PER_SECOND),
            burst=int(settings.WEBSOCKET_HANDSHAKE_BURST),
            ttl_seconds=int(settings.WEBSOCKET_IP_CONNECTION_SLOT_TTL_SECONDS),
        )

    def _refresh_capacity_sync(self, client_ip: str, connection_id: str, worker_id: str) -> bool:
        return refresh_ip_capacity_slot(
            self._capacity_redis(),
            client_ip=client_ip,
            worker_id=worker_id,
            connection_id=connection_id,
            ttl_seconds=int(settings.WEBSOCKET_IP_CONNECTION_SLOT_TTL_SECONDS),
        )

    def _release_capacity_sync(self, client_ip: str, connection_id: str, worker_id: str) -> None:
        release_ip_capacity_slot(
            self._capacity_redis(),
            client_ip=client_ip,
            worker_id=worker_id,
            connection_id=connection_id,
        )

    async def _acquire_capacity(self, client_ip: str, connection_id: str, worker_id: str) -> IPCapacityDecision:
        return await sync_to_async(self._acquire_capacity_sync, thread_sensitive=True)(
            client_ip, connection_id, worker_id
        )

    async def _refresh_capacity(self, client_ip: str, connection_id: str, worker_id: str) -> bool:
        return await sync_to_async(self._refresh_capacity_sync, thread_sensitive=True)(
            client_ip, connection_id, worker_id
        )

    async def _release_capacity(self, client_ip: str, connection_id: str, worker_id: str) -> None:
        await sync_to_async(self._release_capacity_sync, thread_sensitive=True)(client_ip, connection_id, worker_id)

    async def _run_slot_heartbeat(
        self,
        client_ip: str,
        connection_id: str,
        worker_id: str,
        send,
        path: str | None = None,
    ) -> None:
        interval = max(10, int(settings.WEBSOCKET_IP_CONNECTION_SLOT_TTL_SECONDS) // 3)
        try:
            while True:
                await asyncio.sleep(interval)
                if not await self._refresh_capacity(client_ip, connection_id, worker_id):
                    logger.warning(
                        "WebSocket IP capacity slot expired; closing connection",
                        extra={"client_ip_id": _client_ip_log_id(client_ip), "path": path},
                    )
                    await send({"type": "websocket.close", "code": self.UNAVAILABLE_CLOSE_CODE})
                    return
        except asyncio.CancelledError:
            return
        except WebSocketConnectionLimitUnavailable:
            logger.error(
                "WebSocket IP capacity heartbeat unavailable; closing connection",
                extra={"client_ip_id": _client_ip_log_id(client_ip), "path": path},
                exc_info=True,
            )
            await send({"type": "websocket.close", "code": self.UNAVAILABLE_CLOSE_CODE})

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "websocket":
            await self.app(scope, receive, send)
            return

        client_ip = get_asgi_client_ip(scope, trust_proxy=True)
        connection_id = uuid.uuid4().hex
        try:
            worker_id = await self._worker_lease_manager.ensure_started()
            decision = await self._acquire_capacity(client_ip, connection_id, worker_id)
        except WEBSOCKET_IP_CAPACITY_EXCEPTIONS:
            logger.error(
                "WebSocket IP capacity unavailable; rejecting connection",
                extra={"client_ip_id": _client_ip_log_id(client_ip), "path": scope.get("path")},
                exc_info=True,
            )
            await send({"type": "websocket.close", "code": self.UNAVAILABLE_CLOSE_CODE})
            return

        if decision.result is not IPCapacityResult.ACQUIRED:
            logger.info(
                "WebSocket IP capacity rejected connection",
                extra={
                    "client_ip_id": _client_ip_log_id(client_ip),
                    "path": scope.get("path"),
                    "reason": decision.result.value,
                    "close_code": self.CAPACITY_CLOSE_CODE,
                    "active_slots": decision.active_count,
                    "expired_pruned": decision.expired_pruned,
                    "dead_worker_pruned": decision.dead_worker_pruned,
                    "malformed_members": decision.malformed_members,
                    "worker_id": worker_id[:8],
                },
            )
            await send({"type": "websocket.close", "code": self.CAPACITY_CLOSE_CODE})
            return

        if decision.dead_worker_pruned:
            logger.info(
                "WebSocket IP dead worker slots pruned",
                extra={
                    "client_ip_id": _client_ip_log_id(client_ip),
                    "path": scope.get("path"),
                    "active_slots": decision.active_count,
                    "expired_pruned": decision.expired_pruned,
                    "dead_worker_pruned": decision.dead_worker_pruned,
                    "malformed_members": decision.malformed_members,
                    "worker_id": worker_id[:8],
                },
            )

        heartbeat = asyncio.create_task(
            self._run_slot_heartbeat(client_ip, connection_id, worker_id, send, scope.get("path"))
        )
        try:
            await self.app(scope, receive, send)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            try:
                await self._release_capacity(client_ip, connection_id, worker_id)
            except WebSocketConnectionLimitUnavailable:
                logger.warning(
                    "WebSocket IP capacity release unavailable",
                    extra={"client_ip_id": _client_ip_log_id(client_ip), "path": scope.get("path")},
                    exc_info=True,
                )
