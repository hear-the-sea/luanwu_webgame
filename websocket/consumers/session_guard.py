from __future__ import annotations

import logging
import time
from enum import Enum

from channels.db import database_sync_to_async

from core.middleware.single_session import (
    EXPECTED_SESSION_VALIDATION_ERRORS,
    SessionValidationUnavailable,
    is_single_session_request_valid,
    should_fail_open_on_single_session_unavailable,
)
from core.utils.degradation import SESSION_SYNC_FAILURE, record_degradation

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
    SESSION_VALIDATION_UNAVAILABLE_CLOSE_CODE = 1013
    _single_session_valid_until: float = 0.0
    _single_session_checked_by_dispatch: bool = False

    def _session_validation_now(self) -> float:
        return time.monotonic()

    def _has_recent_session_validation(self) -> bool:
        return self._session_validation_now() < float(getattr(self, "_single_session_valid_until", 0.0) or 0.0)

    def _remember_session_validation(self) -> None:
        self._single_session_valid_until = self._session_validation_now() + float(self.SESSION_VALIDATION_CACHE_SECONDS)

    async def dispatch(self, message):
        if not await self._guard_single_session(message):
            return
        self._single_session_checked_by_dispatch = True
        try:
            await super().dispatch(message)
        finally:
            self._single_session_checked_by_dispatch = False

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
