from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_ID = "0123456789abcdef0123456789abcdef"


def _lease_module():
    return importlib.import_module("websocket.backends.worker_lease")


class _FakeRedis:
    def __init__(self) -> None:
        self.set_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.delete_calls: list[str] = []

    def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))
        return True

    def delete(self, key):
        self.delete_calls.append(key)
        return 1


def test_worker_owned_member_encoding_round_trips() -> None:
    module = _lease_module()

    member = module.encode_worker_owned_member(WORKER_ID, "connection-7")

    assert member == f"v2|{WORKER_ID}|connection-7"
    assert module.decode_worker_owned_member(member) == (WORKER_ID, "connection-7")
    assert module.decode_worker_owned_member(member.encode("ascii")) == (WORKER_ID, "connection-7")


@pytest.mark.parametrize(
    "member",
    [
        "legacy-connection-id",
        f"v1|{WORKER_ID}|connection-7",
        "v2|too-short|connection-7",
        f"v2|{'g' * 32}|connection-7",
        f"v2|{WORKER_ID}|",
        b"\xff",
        None,
    ],
)
def test_worker_owned_member_decoder_rejects_legacy_and_malformed_values(member) -> None:
    module = _lease_module()

    assert module.decode_worker_owned_member(member) is None


def test_worker_lease_helpers_set_expiry_and_delete_the_worker_key() -> None:
    module = _lease_module()
    redis = _FakeRedis()

    module.refresh_worker_lease(redis, worker_id=WORKER_ID, ttl_seconds=8)
    module.delete_worker_lease(redis, worker_id=WORKER_ID)

    key = f"websocket:worker:{WORKER_ID}"
    assert redis.set_calls == [((key, "1"), {"ex": 8})]
    assert redis.delete_calls == [key]


@pytest.mark.asyncio
async def test_concurrent_ensure_started_creates_one_identity_and_one_heartbeat(monkeypatch) -> None:
    module = _lease_module()
    redis = _FakeRedis()
    uuid4 = Mock(return_value=SimpleNamespace(hex=WORKER_ID))
    monkeypatch.setattr(module.uuid, "uuid4", uuid4)
    monkeypatch.setattr(module, "get_redis_connection", lambda alias: redis)
    monkeypatch.setattr(module.settings, "WEBSOCKET_WORKER_LEASE_TTL_SECONDS", 8)
    monkeypatch.setattr(module.settings, "WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS", 2)
    manager = module.WebSocketWorkerLeaseManager()

    assert manager.worker_id is None
    worker_ids = await asyncio.gather(*(manager.ensure_started() for _ in range(20)))
    heartbeat_task = manager.heartbeat_task

    assert worker_ids == [WORKER_ID] * 20
    assert uuid4.call_count == 1
    assert heartbeat_task is not None
    assert not heartbeat_task.done()
    assert len(redis.set_calls) == 1

    await manager.stop()

    assert manager.heartbeat_task is None
    assert heartbeat_task.done()
    assert redis.delete_calls == [f"websocket:worker:{WORKER_ID}"]


@pytest.mark.asyncio
async def test_ensure_started_propagates_initial_infrastructure_failure(monkeypatch) -> None:
    module = _lease_module()
    manager = module.WebSocketWorkerLeaseManager()
    refresh = AsyncMock(side_effect=ConnectionError("redis down"))
    monkeypatch.setattr(manager, "_refresh_worker_lease", refresh)

    with pytest.raises(ConnectionError, match="redis down"):
        await manager.ensure_started()

    assert manager.worker_id is not None
    assert manager.heartbeat_task is None


@pytest.mark.asyncio
async def test_heartbeat_retries_first_infrastructure_failure_after_one_second(monkeypatch, caplog) -> None:
    module = _lease_module()
    manager = module.WebSocketWorkerLeaseManager()
    retry_succeeded = asyncio.Event()
    refresh_calls = 0
    sleep_delays: list[int] = []
    blocked = asyncio.Event()

    async def refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 2:
            raise ConnectionError("redis down")
        if refresh_calls == 3:
            retry_succeeded.set()

    async def fake_sleep(delay: int) -> None:
        sleep_delays.append(delay)
        if len(sleep_delays) >= 3:
            await blocked.wait()

    monkeypatch.setattr(manager, "_refresh_worker_lease", refresh)
    monkeypatch.setattr(manager, "_delete_worker_lease", AsyncMock())
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(module.settings, "WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS", 2)

    await manager.ensure_started()
    await asyncio.wait_for(retry_succeeded.wait(), timeout=1)

    assert refresh_calls == 3
    assert sleep_delays[:3] == [2, 1, 2]
    assert "WebSocket worker lease refresh failed" in caplog.text

    await manager.stop()


@pytest.mark.asyncio
async def test_heartbeat_does_not_swallow_programming_errors(monkeypatch) -> None:
    module = _lease_module()
    manager = module.WebSocketWorkerLeaseManager()
    refresh = AsyncMock(side_effect=[None, ValueError("bug")])
    delete = AsyncMock()

    async def fake_sleep(delay: int) -> None:
        assert delay == 2

    monkeypatch.setattr(manager, "_refresh_worker_lease", refresh)
    monkeypatch.setattr(manager, "_delete_worker_lease", delete)
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(module.settings, "WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS", 2)

    await manager.ensure_started()
    heartbeat_task = manager.heartbeat_task
    assert heartbeat_task is not None

    with pytest.raises(ValueError, match="bug"):
        await heartbeat_task
    with pytest.raises(ValueError, match="bug"):
        await manager.stop()

    delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_ignores_only_infrastructure_errors_during_best_effort_delete(monkeypatch, caplog) -> None:
    module = _lease_module()
    manager = module.WebSocketWorkerLeaseManager()
    monkeypatch.setattr(manager, "_refresh_worker_lease", AsyncMock())
    monkeypatch.setattr(
        manager,
        "_delete_worker_lease",
        AsyncMock(side_effect=ConnectionError("redis down")),
    )

    await manager.ensure_started()
    heartbeat_task = manager.heartbeat_task
    await manager.stop()

    assert heartbeat_task is not None and heartbeat_task.done()
    assert manager.heartbeat_task is None
    assert "WebSocket worker lease cleanup failed" in caplog.text


def test_manager_accepts_an_explicit_worker_id_factory() -> None:
    module = _lease_module()
    manager = module.WebSocketWorkerLeaseManager(worker_id_factory=lambda: WORKER_ID)

    assert manager._get_or_create_worker_id() == WORKER_ID


@pytest.mark.asyncio
async def test_stop_preserves_cancellation_of_its_caller(monkeypatch) -> None:
    module = _lease_module()
    manager = module.WebSocketWorkerLeaseManager()
    heartbeat_started = asyncio.Event()
    heartbeat_cancelled = asyncio.Event()
    never_finishes = asyncio.Event()
    delete = AsyncMock()

    async def stubborn_heartbeat() -> None:
        heartbeat_started.set()
        try:
            await never_finishes.wait()
        except asyncio.CancelledError:
            heartbeat_cancelled.set()
            await never_finishes.wait()

    monkeypatch.setattr(manager, "_refresh_worker_lease", AsyncMock())
    monkeypatch.setattr(manager, "_delete_worker_lease", delete)
    monkeypatch.setattr(manager, "_run_heartbeat", stubborn_heartbeat)

    await manager.ensure_started()
    await heartbeat_started.wait()
    stop_task = asyncio.create_task(manager.stop())
    await heartbeat_cancelled.wait()

    stop_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stop_task
    delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_propagates_programming_errors_during_delete(monkeypatch) -> None:
    module = _lease_module()
    manager = module.WebSocketWorkerLeaseManager()
    monkeypatch.setattr(manager, "_refresh_worker_lease", AsyncMock())
    monkeypatch.setattr(
        manager,
        "_delete_worker_lease",
        AsyncMock(side_effect=ValueError("bug")),
    )

    await manager.ensure_started()

    with pytest.raises(ValueError, match="bug"):
        await manager.stop()


def _run_base_settings_import(**overrides: str) -> subprocess.CompletedProcess[str]:
    setting_names = (
        "DJANGO_WEBSOCKET_MAX_CONNECTIONS_PER_USER",
        "DJANGO_WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS",
        "DJANGO_WEBSOCKET_WORKER_LEASE_TTL_SECONDS",
        "DJANGO_WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS",
    )
    child_env = os.environ.copy()
    child_env["DJANGO_STRICT_INFRA_CONFIG"] = "0"
    for name in setting_names:
        child_env.pop(name, None)
    child_env.update(overrides)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from config.settings import base; "
                "print(base.WEBSOCKET_MAX_CONNECTIONS_PER_USER, "
                "base.WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS, "
                "base.WEBSOCKET_WORKER_LEASE_TTL_SECONDS, "
                "base.WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS)"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_worker_lease_settings_have_safe_defaults() -> None:
    result = _run_base_settings_import()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "9 30 8 2"


def test_worker_lease_settings_clamp_minimum_values() -> None:
    result = _run_base_settings_import(
        DJANGO_WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS="1",
        DJANGO_WEBSOCKET_WORKER_LEASE_TTL_SECONDS="1",
        DJANGO_WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS="0",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "9 6 4 1"


def test_worker_lease_settings_reject_heartbeat_too_close_to_expiry() -> None:
    result = _run_base_settings_import(
        DJANGO_WEBSOCKET_WORKER_LEASE_TTL_SECONDS="4",
        DJANGO_WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS="2",
    )

    assert result.returncode != 0
    assert "worker lease heartbeat must be less than half the TTL" in result.stderr
