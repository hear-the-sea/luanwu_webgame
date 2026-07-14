"""Redis-backed lease for the WebSocket-serving process."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable

from asgiref.sync import sync_to_async
from django.conf import settings
from django_redis import get_redis_connection

from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)

MEMBER_VERSION = "v2"
WORKER_KEY_PREFIX = "websocket:worker:"
_WORKER_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def worker_lease_key(worker_id: str) -> str:
    return f"{WORKER_KEY_PREFIX}{worker_id}"


def encode_worker_owned_member(worker_id: str, connection_id: str) -> str:
    return f"{MEMBER_VERSION}|{worker_id}|{connection_id}"


def decode_worker_owned_member(member: object) -> tuple[str, str] | None:
    if isinstance(member, bytes):
        try:
            member = member.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(member, str):
        return None

    parts = member.split("|", 2)
    if len(parts) != 3:
        return None
    version, worker_id, connection_id = parts
    if version != MEMBER_VERSION or not _WORKER_ID_PATTERN.fullmatch(worker_id) or not connection_id:
        return None
    return worker_id, connection_id


def is_versioned_worker_owned_member(member: object) -> bool:
    if isinstance(member, bytes):
        return member.startswith(f"{MEMBER_VERSION}|".encode("ascii"))
    return isinstance(member, str) and member.startswith(f"{MEMBER_VERSION}|")


def refresh_worker_lease(redis, *, worker_id: str, ttl_seconds: int) -> None:
    redis.set(worker_lease_key(worker_id), "1", ex=int(ttl_seconds))


def delete_worker_lease(redis, *, worker_id: str) -> None:
    redis.delete(worker_lease_key(worker_id))


class WebSocketWorkerLeaseManager:
    def __init__(self, *, worker_id_factory: Callable[[], str] | None = None) -> None:
        self._worker_id_factory = worker_id_factory or (lambda: uuid.uuid4().hex)
        self._worker_id: str | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def worker_id(self) -> str | None:
        return self._worker_id

    @property
    def heartbeat_task(self) -> asyncio.Task[None] | None:
        return self._heartbeat_task

    async def ensure_started(self) -> str:
        async with self._lifecycle_lock:
            if self._heartbeat_task is not None:
                if not self._heartbeat_task.done():
                    return self._get_or_create_worker_id()
                completed_task = self._heartbeat_task
                self._heartbeat_task = None
                await completed_task

            worker_id = self._get_or_create_worker_id()
            await self._refresh_worker_lease()
            self._heartbeat_task = asyncio.create_task(
                self._run_heartbeat(),
                name="websocket-worker-lease-heartbeat",
            )
            return worker_id

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            heartbeat_task = self._heartbeat_task
            self._heartbeat_task = None
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()

            try:
                if heartbeat_task is not None:
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        current_task = asyncio.current_task()
                        if current_task is not None and current_task.cancelling():
                            raise
            finally:
                if self._worker_id is not None:
                    try:
                        await self._delete_worker_lease()
                    except INFRASTRUCTURE_EXCEPTIONS:
                        logger.warning("WebSocket worker lease cleanup failed", exc_info=True)

    def _get_or_create_worker_id(self) -> str:
        if self._worker_id is None:
            self._worker_id = self._worker_id_factory()
        return self._worker_id

    async def _refresh_worker_lease(self) -> None:
        worker_id = self._get_or_create_worker_id()

        def refresh() -> None:
            redis = get_redis_connection("default")
            refresh_worker_lease(
                redis,
                worker_id=worker_id,
                ttl_seconds=int(settings.WEBSOCKET_WORKER_LEASE_TTL_SECONDS),
            )

        await sync_to_async(refresh, thread_sensitive=True)()

    async def _delete_worker_lease(self) -> None:
        worker_id = self._worker_id
        if worker_id is None:
            return

        def delete() -> None:
            redis = get_redis_connection("default")
            delete_worker_lease(redis, worker_id=worker_id)

        await sync_to_async(delete, thread_sensitive=True)()

    async def _run_heartbeat(self) -> None:
        while True:
            await asyncio.sleep(int(settings.WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS))
            try:
                await self._refresh_worker_lease()
            except INFRASTRUCTURE_EXCEPTIONS:
                logger.warning("WebSocket worker lease refresh failed; retrying", exc_info=True)
                await asyncio.sleep(1)
                try:
                    await self._refresh_worker_lease()
                except INFRASTRUCTURE_EXCEPTIONS:
                    logger.error("WebSocket worker lease refresh retry failed", exc_info=True)


_worker_lease_manager: WebSocketWorkerLeaseManager | None = None


def get_websocket_worker_lease_manager() -> WebSocketWorkerLeaseManager:
    global _worker_lease_manager
    if _worker_lease_manager is None:
        _worker_lease_manager = WebSocketWorkerLeaseManager()
    return _worker_lease_manager


__all__ = [
    "MEMBER_VERSION",
    "WORKER_KEY_PREFIX",
    "WebSocketWorkerLeaseManager",
    "decode_worker_owned_member",
    "delete_worker_lease",
    "encode_worker_owned_member",
    "get_websocket_worker_lease_manager",
    "is_versioned_worker_owned_member",
    "refresh_worker_lease",
    "worker_lease_key",
]
