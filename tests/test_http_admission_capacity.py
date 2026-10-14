"""Burst proof without production requests or database side effects."""

from __future__ import annotations

import asyncio
from collections import Counter

from core.middleware.http_admission import HTTPAdmissionMiddleware


def test_default_limits_bound_a_100_request_burst_and_keep_health_available():
    async def scenario():
        active = 0
        maximum = 0
        executed = 0
        occupied = asyncio.Event()
        release = asyncio.Event()
        responses = Counter()

        async def application(scope, receive, send):
            nonlocal active, maximum, executed
            if scope["path"] == "/health/live":
                await send({"type": "http.response.start", "status": 200, "headers": []})
                return
            active += 1
            maximum = max(maximum, active)
            executed += 1
            if active == 24:
                occupied.set()
            try:
                await release.wait()
                await send({"type": "http.response.start", "status": 200, "headers": []})
            finally:
                active -= 1

        middleware = HTTPAdmissionMiddleware(application)

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            if message["type"] == "http.response.start":
                responses[message["status"]] += 1

        calls = [
            asyncio.create_task(middleware({"type": "http", "path": "/manor/tasks/"}, receive, send))
            for _ in range(100)
        ]
        await occupied.wait()
        await asyncio.sleep(0)
        assert middleware._business_pool.pending == 48
        await middleware({"type": "http", "path": "/health/live"}, receive, send)
        assert responses[200] == 1  # Health completes while all business slots remain held.
        release.set()
        await asyncio.gather(*calls)
        assert maximum == 24
        assert executed == 72
        assert responses == {200: 73, 503: 28}
        assert middleware._business_pool.pending == 0

    asyncio.run(scenario())
