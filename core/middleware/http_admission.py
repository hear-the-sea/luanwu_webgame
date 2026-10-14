"""Bound HTTP work before Django reads request bodies or starts synchronous views.

Limits are per ASGI process. Configure DJANGO_HTTP_ADMISSION_MAX_CONCURRENCY
(24), DJANGO_HTTP_ADMISSION_HEALTH_CONCURRENCY (2), and
DJANGO_HTTP_ADMISSION_MAX_PENDING (48), DJANGO_HTTP_ADMISSION_HEALTH_MAX_PENDING
(2), and DJANGO_HTTP_ADMISSION_QUEUE_TIMEOUT_SECONDS (1). WebSockets are unaffected.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from typing import Any

from asgiref.typing import (
    ASGI3Application,
    ASGIReceiveCallable,
    ASGISendCallable,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    Scope,
)

HEALTH_PATHS = frozenset({"/health/live", "/health/live/", "/health/ready", "/health/ready/"})
OVERLOAD_BODY = json.dumps({"success": False, "error": "服务器繁忙，请稍后重试"}, ensure_ascii=False).encode("utf-8")


def positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a positive integer") from None
    if isinstance(value, bool) or parsed <= 0 or str(parsed) != str(value).strip():
        raise ValueError(f"{name} must be a positive integer")
    return parsed


class _AdmissionPool:
    def __init__(self, concurrency: int, max_pending: int):
        self.slots = asyncio.Semaphore(concurrency)
        self.max_pending = max_pending
        self.pending = 0

    async def acquire(self, timeout: float) -> bool:
        waiting = self.slots.locked()
        if waiting:
            if self.pending >= self.max_pending:
                return False
            self.pending += 1
        try:
            # Only admission is timed; no detached acquire can take a slot
            # after the client has timed out or cancelled its queued request.
            async with asyncio.timeout(timeout):
                await self.slots.acquire()
            return True
        except TimeoutError:
            return False
        finally:
            if waiting:
                self.pending -= 1


class HTTPAdmissionMiddleware:
    def __init__(
        self,
        application: ASGI3Application,
        *,
        max_concurrency: int | None = None,
        health_concurrency: int | None = None,
        max_pending_requests: int | None = None,
        health_max_pending_requests: int | None = None,
        queue_timeout_seconds: float | None = None,
    ) -> None:
        self.application = application
        self.max_concurrency = positive_int(
            (
                max_concurrency
                if max_concurrency is not None
                else os.environ.get("DJANGO_HTTP_ADMISSION_MAX_CONCURRENCY", "24")
            ),
            "HTTP admission concurrency",
        )
        self.health_concurrency = positive_int(
            (
                health_concurrency
                if health_concurrency is not None
                else os.environ.get("DJANGO_HTTP_ADMISSION_HEALTH_CONCURRENCY", "2")
            ),
            "HTTP health admission concurrency",
        )
        self.max_pending_requests = positive_int(
            (
                max_pending_requests
                if max_pending_requests is not None
                else os.environ.get("DJANGO_HTTP_ADMISSION_MAX_PENDING", "48")
            ),
            "HTTP admission pending limit",
        )
        self.health_max_pending_requests = positive_int(
            (
                health_max_pending_requests
                if health_max_pending_requests is not None
                else os.environ.get("DJANGO_HTTP_ADMISSION_HEALTH_MAX_PENDING", "2")
            ),
            "HTTP health admission pending limit",
        )
        raw_timeout = (
            queue_timeout_seconds
            if queue_timeout_seconds is not None
            else os.environ.get("DJANGO_HTTP_ADMISSION_QUEUE_TIMEOUT_SECONDS", "1")
        )
        try:
            self.queue_timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("HTTP admission timeout must be finite and positive") from None
        if (
            isinstance(raw_timeout, bool)
            or not math.isfinite(self.queue_timeout_seconds)
            or self.queue_timeout_seconds <= 0
        ):
            raise ValueError("HTTP admission timeout must be finite and positive")
        self._business_pool = _AdmissionPool(self.max_concurrency, self.max_pending_requests)
        self._health_pool = _AdmissionPool(self.health_concurrency, self.health_max_pending_requests)

    async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        if scope.get("type") != "http":
            return await self.application(scope, receive, send)
        pool = self._health_pool if scope.get("path") in HEALTH_PATHS else self._business_pool
        if not await pool.acquire(self.queue_timeout_seconds):
            start: HTTPResponseStartEvent = {
                "type": "http.response.start",
                "status": 503,
                "trailers": False,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(OVERLOAD_BODY)).encode("ascii")),
                    (b"retry-after", b"2"),
                    (b"cache-control", b"no-store"),
                ],
            }
            body: HTTPResponseBodyEvent = {"type": "http.response.body", "body": OVERLOAD_BODY, "more_body": False}
            await send(start)
            await send(body)
            return
        try:
            return await self.application(scope, receive, send)
        finally:
            pool.slots.release()
