from __future__ import annotations

import asyncio
import json

import pytest

from core.middleware.http_admission import HTTPAdmissionMiddleware


class Request:
    def __init__(self, path="/business", scope_type="http"):
        self.scope = {"type": scope_type, "path": path}
        self.messages = []
        self.body_reads = 0

    async def receive(self):
        self.body_reads += 1
        return {"type": "http.request", "body": b"asset operation"}

    async def send(self, message):
        self.messages.append(message)

    async def run(self, middleware):
        await middleware(self.scope, self.receive, self.send)


class BlockingApp:
    def __init__(self):
        self.started = asyncio.Queue()
        self.release = asyncio.Event()

    async def __call__(self, scope, receive, send):
        await self.started.put(scope["path"])
        await receive()
        await self.release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def test_default_and_environment_limits(monkeypatch):
    for key in (
        "DJANGO_HTTP_ADMISSION_MAX_CONCURRENCY",
        "DJANGO_HTTP_ADMISSION_HEALTH_CONCURRENCY",
        "DJANGO_HTTP_ADMISSION_QUEUE_TIMEOUT_SECONDS",
        "DJANGO_HTTP_ADMISSION_MAX_PENDING",
        "DJANGO_HTTP_ADMISSION_HEALTH_MAX_PENDING",
    ):
        monkeypatch.delenv(key, raising=False)
    middleware = HTTPAdmissionMiddleware(None)
    assert (middleware.max_pending_requests, middleware.health_max_pending_requests) == (48, 2)
    assert (middleware.max_concurrency, middleware.health_concurrency, middleware.queue_timeout_seconds) == (24, 2, 1)
    monkeypatch.setenv("DJANGO_HTTP_ADMISSION_MAX_CONCURRENCY", "12")
    monkeypatch.setenv("DJANGO_HTTP_ADMISSION_HEALTH_CONCURRENCY", "3")
    monkeypatch.setenv("DJANGO_HTTP_ADMISSION_QUEUE_TIMEOUT_SECONDS", "0.5")
    middleware = HTTPAdmissionMiddleware(None)
    assert (middleware.max_concurrency, middleware.health_concurrency, middleware.queue_timeout_seconds) == (12, 3, 0.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_concurrency": 0},
        {"max_concurrency": 1.5},
        {"health_concurrency": -1},
        {"max_concurrency": True},
        {"queue_timeout_seconds": 0},
        {"queue_timeout_seconds": float("inf")},
        {"queue_timeout_seconds": float("nan")},
    ],
)
def test_invalid_limits_fail_at_startup(kwargs):
    with pytest.raises(ValueError):
        HTTPAdmissionMiddleware(None, **kwargs)


@pytest.mark.asyncio
async def test_saturation_rejects_before_body_read_or_business_execution():
    app = BlockingApp()
    middleware = HTTPAdmissionMiddleware(app, max_concurrency=2, queue_timeout_seconds=0.01)
    requests = [Request() for _ in range(2)]
    running = [asyncio.create_task(request.run(middleware)) for request in requests]
    try:
        for _ in requests:
            await app.started.get()
        rejected = Request()
        await rejected.run(middleware)
        assert rejected.body_reads == 0
        assert app.started.empty()
        start = rejected.messages[0]
        assert start["status"] == 503
        headers = dict(start["headers"])
        assert headers[b"retry-after"] == b"2"
        assert headers[b"cache-control"] == b"no-store"
        assert headers[b"content-type"] == b"application/json; charset=utf-8"
        assert rejected.messages[1]["type"] == "http.response.body"
        assert json.loads(rejected.messages[1]["body"]) == {"success": False, "error": "服务器繁忙，请稍后重试"}
        app.release.set()
        await asyncio.gather(*running)
        recovered = Request()
        await recovered.run(middleware)
        assert recovered.messages[0]["status"] == 200
    finally:
        app.release.set()
        await asyncio.gather(*running)


@pytest.mark.asyncio
async def test_healthy_requests_use_reserved_capacity_and_health_budget_is_bounded():
    app = BlockingApp()
    middleware = HTTPAdmissionMiddleware(app, max_concurrency=1, health_concurrency=2, queue_timeout_seconds=0.01)
    running = [
        asyncio.create_task(Request(path).run(middleware)) for path in ["/business", "/health/live", "/health/ready/"]
    ]
    try:
        paths = [await app.started.get() for _ in running]
        assert set(paths) == {"/business", "/health/live", "/health/ready/"}
        for path in ["/health/live/", "/health/ready", "/health/live/extra"]:
            rejected = Request(path)
            await rejected.run(middleware)
            assert rejected.body_reads == 0
            assert rejected.messages[0]["status"] == 503
    finally:
        app.release.set()
        await asyncio.gather(*running)


@pytest.mark.asyncio
async def test_queued_request_waits_then_executes_when_capacity_is_released():
    app = BlockingApp()
    middleware = HTTPAdmissionMiddleware(app, max_concurrency=1)
    first = asyncio.create_task(Request().run(middleware))
    await app.started.get()
    queued = Request()
    second = asyncio.create_task(queued.run(middleware))
    await asyncio.sleep(0)
    assert queued.body_reads == 0
    assert not second.done()
    app.release.set()
    await asyncio.gather(first, second)
    assert queued.body_reads == 1
    assert queued.messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_full_waiting_queue_rejects_immediately_without_reading_body():
    app = BlockingApp()
    middleware = HTTPAdmissionMiddleware(app, max_concurrency=1, max_pending_requests=1)
    first = asyncio.create_task(Request().run(middleware))
    await app.started.get()
    queued = Request()
    second = asyncio.create_task(queued.run(middleware))
    await asyncio.sleep(0)
    try:
        rejected = Request()
        await rejected.run(middleware)
        assert rejected.messages[0]["status"] == 503
        assert rejected.body_reads == 0
        assert not second.done()
        assert app.started.empty()
    finally:
        app.release.set()
        await asyncio.gather(first, second)
    assert queued.messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_timed_out_waiter_frees_queue_space_before_business_finishes():
    app = BlockingApp()
    middleware = HTTPAdmissionMiddleware(
        app,
        max_concurrency=1,
        max_pending_requests=1,
        queue_timeout_seconds=0.02,
    )
    first = asyncio.create_task(Request().run(middleware))
    await app.started.get()
    timed_out = Request()
    await timed_out.run(middleware)
    assert timed_out.messages[0]["status"] == 503
    assert timed_out.body_reads == 0
    waiting = Request()
    second = asyncio.create_task(waiting.run(middleware))
    await asyncio.sleep(0)
    assert not second.done()
    assert waiting.messages == []
    app.release.set()
    await asyncio.gather(first, second)
    assert waiting.messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_queued_cancellation_does_not_consume_a_future_slot():
    app = BlockingApp()
    middleware = HTTPAdmissionMiddleware(app, max_concurrency=1, max_pending_requests=1)
    first = asyncio.create_task(Request().run(middleware))
    await app.started.get()
    queued = Request()
    cancelled = asyncio.create_task(queued.run(middleware))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert queued.body_reads == 0
    recovered = Request()
    recovery = asyncio.create_task(recovered.run(middleware))
    await asyncio.sleep(0)
    assert not recovery.done()
    assert recovered.messages == []
    app.release.set()
    await asyncio.gather(first, recovery)
    assert recovered.messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_cancelled_inflight_application_releases_admission_capacity():
    app = BlockingApp()
    middleware = HTTPAdmissionMiddleware(app, max_concurrency=1)
    first = asyncio.create_task(Request().run(middleware))
    await app.started.get()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    app.release.set()
    recovered = Request()
    await recovered.run(middleware)
    assert recovered.messages[0]["status"] == 200


@pytest.mark.parametrize("error", [RuntimeError("view failed"), TimeoutError("business timeout")])
@pytest.mark.asyncio
async def test_business_exception_propagates_without_503_and_releases_capacity(error):
    calls = 0

    async def app(scope, receive, send):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        await send({"type": "http.response.start", "status": 200, "headers": []})

    middleware = HTTPAdmissionMiddleware(app, max_concurrency=1)
    failed = Request()
    with pytest.raises(type(error), match=str(error)):
        await failed.run(middleware)
    assert failed.messages == []
    recovered = Request()
    await recovered.run(middleware)
    assert recovered.messages[0]["status"] == 200


@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
@pytest.mark.asyncio
async def test_non_http_scopes_pass_through_without_http_budget(scope_type):
    app = BlockingApp()
    middleware = HTTPAdmissionMiddleware(app, max_concurrency=1, queue_timeout_seconds=0.01)
    first = asyncio.create_task(Request().run(middleware))
    await app.started.get()
    request = Request(scope_type=scope_type)
    other = asyncio.create_task(request.run(middleware))
    try:
        await asyncio.wait_for(app.started.get(), timeout=1)
        assert request.body_reads == 1
    finally:
        app.release.set()
        await asyncio.gather(first, other)


def test_asgi_wraps_http_without_changing_websocket_origin_or_session_stack():
    from config.asgi import application

    middleware = application.application_mapping["http"]
    assert isinstance(middleware, HTTPAdmissionMiddleware)
    from channels.security.websocket import OriginValidator

    assert isinstance(application.application_mapping["websocket"], OriginValidator)
