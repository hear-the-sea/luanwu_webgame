from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.conf import settings
from django_redis import get_redis_connection

from core.middleware.single_session import (
    EXPECTED_SESSION_VALIDATION_ERRORS,
    SessionValidationUnavailable,
    is_single_session_request_valid,
    should_fail_open_on_single_session_unavailable,
)
from core.utils.degradation import SESSION_SYNC_FAILURE, record_degradation
from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from websocket.backends.connection_limiter import (
    ConnectionCapacityDecision,
    acquire_connection_slot,
    refresh_connection_slot,
    release_connection_slot,
)
from websocket.backends.worker_lease import get_websocket_worker_lease_manager
from websocket.capacity_state import WebSocketIPCapacityState, get_websocket_ip_capacity_state
from websocket.close_codes import CONNECTION_LIMIT_REACHED_CLOSE_CODE as CAPACITY_LIMIT_CLOSE_CODE
from websocket.close_codes import SERVICE_UNAVAILABLE_CLOSE_CODE
from websocket.exceptions import WebSocketConnectionLimitUnavailable

logger = logging.getLogger(__name__)


class WebSocketSessionValidationUnavailable(RuntimeError):
    """Raised when websocket session validation cannot reach authoritative state."""


class WebSocketSessionValidationResult(Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"

    def __bool__(self) -> bool:
        return self is self.VALID


WEBSOCKET_SESSION_VALIDATION_EXCEPTIONS: tuple[type[Exception], ...] = EXPECTED_SESSION_VALIDATION_ERRORS
WEBSOCKET_CAPACITY_EXCEPTIONS: tuple[type[Exception], ...] = (
    WebSocketConnectionLimitUnavailable,
    *INFRASTRUCTURE_EXCEPTIONS,
)


def is_websocket_session_valid(scope: dict) -> bool:
    user = scope.get("user")
    if not user or not getattr(user, "is_authenticated", False):
        return False

    session = scope.get("session")
    if session is None:
        logger.warning("WebSocket session missing for authenticated user: user_id=%s", getattr(user, "id", None))
        return False

    current_session_key = getattr(session, "session_key", None)
    if not current_session_key:
        return False

    exists = getattr(session, "exists", None)
    if callable(exists):
        try:
            if not exists(current_session_key):
                return False
        except WEBSOCKET_SESSION_VALIDATION_EXCEPTIONS as exc:
            raise WebSocketSessionValidationUnavailable("session existence check unavailable") from exc

    try:
        session_user_id = session.get("_auth_user_id")
    except WEBSOCKET_SESSION_VALIDATION_EXCEPTIONS as exc:
        raise WebSocketSessionValidationUnavailable("session payload check unavailable") from exc

    if str(session_user_id) != str(user.id):
        return False

    try:
        return is_single_session_request_valid(int(user.id), str(current_session_key))
    except SessionValidationUnavailable as exc:
        raise WebSocketSessionValidationUnavailable("single-session validation unavailable") from exc


class SingleSessionWebSocketMixin:
    SESSION_VALIDATION_CACHE_SECONDS = 5.0
    SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE = SERVICE_UNAVAILABLE_CLOSE_CODE
    CONNECTION_LIMIT_REACHED_CLOSE_CODE = CAPACITY_LIMIT_CLOSE_CODE
    _single_session_valid_until: float = 0.0
    _single_session_checked_by_dispatch: bool = False
    _connection_slot_acquired: bool = False
    _connection_slot_heartbeat_task: asyncio.Task | None = None
    _connection_slot_worker_id: str | None = None
    _connection_capacity_state: WebSocketIPCapacityState | None = None

    def _session_validation_now(self) -> float:
        return time.monotonic()

    def _has_recent_session_validation(self) -> bool:
        return self._session_validation_now() < float(getattr(self, "_single_session_valid_until", 0.0) or 0.0)

    def _remember_session_validation(self) -> None:
        self._single_session_valid_until = self._session_validation_now() + float(self.SESSION_VALIDATION_CACHE_SECONDS)

    async def dispatch(self, message):
        if not await self._guard_single_session(message):
            return
        message_type = str(message.get("type", ""))
        if message_type == "websocket.connect" and not await self._guard_connection_capacity():
            return
        self._single_session_checked_by_dispatch = True
        try:
            await super().dispatch(message)
        except BaseException:
            if message_type == "websocket.connect":
                await self._release_connection_slot()
            raise
        finally:
            self._single_session_checked_by_dispatch = False
            if message_type == "websocket.disconnect":
                await self._release_connection_slot()

    def _connection_slot_redis(self):
        try:
            return get_redis_connection("default")
        except INFRASTRUCTURE_EXCEPTIONS as exc:
            raise WebSocketConnectionLimitUnavailable("WebSocket connection limiter unavailable") from exc

    def _acquire_connection_slot_sync(
        self, user_id: int, user_connection_id: str, worker_id: str
    ) -> ConnectionCapacityDecision:
        return acquire_connection_slot(
            self._connection_slot_redis(),
            user_id=user_id,
            worker_id=worker_id,
            connection_id=user_connection_id,
            limit=int(settings.WEBSOCKET_MAX_CONNECTIONS_PER_USER),
            ttl_seconds=int(settings.WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS),
        )

    def _refresh_connection_slot_sync(self, user_id: int, user_connection_id: str, worker_id: str) -> bool:
        return refresh_connection_slot(
            self._connection_slot_redis(),
            user_id=user_id,
            worker_id=worker_id,
            connection_id=user_connection_id,
            ttl_seconds=int(settings.WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS),
        )

    def _release_connection_slot_sync(self, user_id: int, user_connection_id: str, worker_id: str) -> None:
        release_connection_slot(
            self._connection_slot_redis(),
            user_id=user_id,
            worker_id=worker_id,
            connection_id=user_connection_id,
        )

    async def _acquire_connection_slot(
        self, user_id: int, user_connection_id: str, worker_id: str
    ) -> ConnectionCapacityDecision:
        return await sync_to_async(self._acquire_connection_slot_sync, thread_sensitive=True)(
            user_id,
            user_connection_id,
            worker_id,
        )

    async def _refresh_connection_slot(self, user_id: int, user_connection_id: str, worker_id: str) -> bool:
        return await sync_to_async(self._refresh_connection_slot_sync, thread_sensitive=True)(
            user_id,
            user_connection_id,
            worker_id,
        )

    async def _release_connection_slot_backend(self, user_id: int, user_connection_id: str, worker_id: str) -> None:
        await sync_to_async(self._release_connection_slot_sync, thread_sensitive=True)(
            user_id,
            user_connection_id,
            worker_id,
        )

    async def _guard_connection_capacity(self) -> bool:
        user = self.scope.get("user")  # type: ignore[attr-defined]
        user_id = int(user.id)
        user_connection_id = str(self.channel_name)  # type: ignore[attr-defined]
        worker_lease_manager = getattr(self, "_worker_lease_manager", None)
        if worker_lease_manager is None:
            worker_lease_manager = get_websocket_worker_lease_manager()
        try:
            worker_id = await worker_lease_manager.ensure_started()
            decision = await self._acquire_connection_slot(user_id, user_connection_id, worker_id)
        except WEBSOCKET_CAPACITY_EXCEPTIONS:
            logger.error(
                "WebSocket connection limiter unavailable; rejecting connection: user_id=%s path=%s",
                user_id,
                self.scope.get("path"),  # type: ignore[attr-defined]
                exc_info=True,
            )
            await self._reject_websocket_session(
                message_type="websocket.connect",
                close_code=self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE,
            )
            return False

        if not decision.allowed:
            logger.info(
                "WebSocket connection capacity rejected",
                extra={
                    "user_id": user_id,
                    "path": self.scope.get("path"),  # type: ignore[attr-defined]
                    "close_code": self.CONNECTION_LIMIT_REACHED_CLOSE_CODE,
                    "active_slots": decision.active_count,
                    "expired_pruned": decision.expired_pruned,
                    "dead_worker_pruned": decision.dead_worker_pruned,
                    "malformed_members": decision.malformed_members,
                    "worker_id": worker_id[:8],
                },
            )
            await self._reject_websocket_session(
                message_type="websocket.connect",
                close_code=self.CONNECTION_LIMIT_REACHED_CLOSE_CODE,
            )
            return False

        if decision.dead_worker_pruned:
            logger.info(
                "WebSocket user dead worker slots pruned",
                extra={
                    "user_id": user_id,
                    "path": self.scope.get("path"),  # type: ignore[attr-defined]
                    "active_slots": decision.active_count,
                    "expired_pruned": decision.expired_pruned,
                    "dead_worker_pruned": decision.dead_worker_pruned,
                    "malformed_members": decision.malformed_members,
                    "worker_id": worker_id[:8],
                },
            )

        self._connection_slot_acquired = True
        self._connection_slot_worker_id = worker_id
        capacity_state = get_websocket_ip_capacity_state(self.scope)  # type: ignore[attr-defined]
        self._connection_capacity_state = capacity_state
        if capacity_state is not None:
            capacity_state["managed_by_session_guard"] = True
        self._connection_slot_heartbeat_task = asyncio.create_task(
            self._connection_slot_heartbeat_loop(user_id, user_connection_id, worker_id)
        )
        return True

    async def _connection_slot_heartbeat_loop(self, user_id: int, user_connection_id: str, worker_id: str) -> None:
        interval = max(1, int(settings.WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS) // 3)
        ip_interval = max(10, int(settings.WEBSOCKET_IP_CONNECTION_SLOT_TTL_SECONDS) // 3)
        next_ip_refresh = self._session_validation_now() + ip_interval
        capacity_state = self._connection_capacity_state
        try:
            while True:
                await asyncio.sleep(interval)
                refresh_now = self._session_validation_now()
                refreshed = True
                ip_refreshed = True
                refresh_pair = capacity_state["refresh_pair"] if capacity_state else None
                if refresh_now >= next_ip_refresh and refresh_pair is not None:
                    refreshed, ip_refreshed = await refresh_pair(
                        user_id,
                        user_connection_id,
                        worker_id,
                        int(settings.WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS),
                    )
                    next_ip_refresh = refresh_now + ip_interval
                else:
                    refreshed = await self._refresh_connection_slot(user_id, user_connection_id, worker_id)
                if not refreshed or not ip_refreshed:
                    logger.warning(
                        "WebSocket connection capacity slot missing or expired; closing connection",
                        extra={
                            "user_id": user_id,
                            "path": getattr(self, "scope", {}).get("path"),
                            "close_code": self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE,
                        },
                    )
                    await self.close(code=self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE)  # type: ignore[attr-defined]
                    return
        except asyncio.CancelledError:
            return
        except WebSocketConnectionLimitUnavailable:
            logger.error(
                "WebSocket connection slot heartbeat unavailable; closing connection: user_id=%s",
                user_id,
                exc_info=True,
            )
            await self.close(code=self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE)  # type: ignore[attr-defined]

    async def _release_connection_slot(self) -> None:
        heartbeat = self._connection_slot_heartbeat_task
        self._connection_slot_heartbeat_task = None
        try:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                except Exception:
                    logger.exception(
                        "WebSocket connection slot heartbeat failed during cleanup: user_id=%s",
                        getattr(self.scope.get("user"), "id", None),  # type: ignore[attr-defined]
                    )
        finally:
            slot_acquired = self._connection_slot_acquired
            self._connection_slot_acquired = False
            worker_id = self._connection_slot_worker_id
            self._connection_slot_worker_id = None
            self._connection_capacity_state = None
            if slot_acquired and worker_id is not None:
                user = self.scope.get("user")  # type: ignore[attr-defined]
                try:
                    await self._release_connection_slot_backend(
                        int(user.id),
                        str(self.channel_name),  # type: ignore[attr-defined]
                        worker_id,
                    )
                except WebSocketConnectionLimitUnavailable:
                    logger.warning(
                        "WebSocket connection slot release unavailable: user_id=%s",
                        getattr(user, "id", None),
                        exc_info=True,
                    )

    async def _guard_single_session(self, message: dict) -> bool:
        message_type = str(message.get("type", ""))
        if message_type == "websocket.disconnect":
            return True

        validation_result = await self._ensure_valid_session(force=(message_type == "websocket.connect"))
        if validation_result is WebSocketSessionValidationResult.UNAVAILABLE:
            logger.info(
                "Closing WebSocket while session validation is unavailable: "
                "consumer=%s user_id=%s path=%s message_type=%s",
                self.__class__.__name__,
                getattr(self.scope.get("user"), "id", None),  # type: ignore[attr-defined]
                self.scope.get("path"),  # type: ignore[attr-defined]
                message_type,
            )
            await self._reject_websocket_session(
                message_type=message_type,
                close_code=self.SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE,
            )
            return False

        if validation_result is WebSocketSessionValidationResult.VALID:
            return True

        if validation_result is WebSocketSessionValidationResult.INVALID:
            logger.info(
                "Closing stale WebSocket session: consumer=%s user_id=%s path=%s message_type=%s",
                self.__class__.__name__,
                getattr(self.scope.get("user"), "id", None),  # type: ignore[attr-defined]
                self.scope.get("path"),  # type: ignore[attr-defined]
                message_type,
            )
            await self._reject_websocket_session(message_type=message_type)
            return False

        raise RuntimeError(f"Unexpected websocket session validation result: {validation_result!r}")

    def _session_rejection_close_code(self) -> int | None:
        user = self.scope.get("user")  # type: ignore[attr-defined]
        code_attribute = (
            "UNAUTHENTICATED_CLOSE_CODE"
            if not user or not getattr(user, "is_authenticated", False)
            else "INVALID_SESSION_CLOSE_CODE"
        )
        return getattr(self, code_attribute, None)

    async def _reject_websocket_session(self, *, message_type: str, close_code: int | None = None) -> None:
        if close_code is None:
            close_code = self._session_rejection_close_code()
        if message_type == "websocket.connect" and close_code is not None:
            await self.accept()  # type: ignore[attr-defined]
        if close_code is None:
            await self.close()  # type: ignore[attr-defined]
        else:
            await self.close(code=close_code)  # type: ignore[attr-defined]

    async def _ensure_valid_session(self, *, force: bool = False) -> WebSocketSessionValidationResult:
        user = self.scope.get("user")  # type: ignore[attr-defined]
        if not user or not getattr(user, "is_authenticated", False):
            return WebSocketSessionValidationResult.INVALID
        if not force and self._has_recent_session_validation():
            return WebSocketSessionValidationResult.VALID

        try:
            is_valid = await database_sync_to_async(is_websocket_session_valid, thread_sensitive=True)(self.scope)  # type: ignore[attr-defined]
        except WebSocketSessionValidationUnavailable:
            fail_open = should_fail_open_on_single_session_unavailable()
            record_degradation(
                SESSION_SYNC_FAILURE,
                component="single_session_websocket",
                detail=(
                    "websocket session validation unavailable, keeping connection"
                    if fail_open
                    else "websocket session validation unavailable, closing connection"
                ),
                user_id=getattr(user, "id", None),
            )
            if fail_open:
                logger.warning(
                    "WebSocket session validation unavailable; keeping connection: consumer=%s user_id=%s path=%s",
                    self.__class__.__name__,
                    getattr(user, "id", None),
                    self.scope.get("path"),  # type: ignore[attr-defined]
                    exc_info=True,
                )
                self._remember_session_validation()
                return WebSocketSessionValidationResult.VALID

            logger.error(
                "WebSocket session validation unavailable; closing connection: consumer=%s user_id=%s path=%s",
                self.__class__.__name__,
                getattr(user, "id", None),
                self.scope.get("path"),  # type: ignore[attr-defined]
                exc_info=True,
            )
            return WebSocketSessionValidationResult.UNAVAILABLE

        if is_valid:
            self._remember_session_validation()
            return WebSocketSessionValidationResult.VALID
        return WebSocketSessionValidationResult.INVALID
