"""Optional real-Redis tests; every server is a private loopback fixture."""

from __future__ import annotations

import os
import socket
import subprocess
import time

import pytest
from redis import Redis
from redis.exceptions import ConnectionError

from scripts.migrate_runtime_redis import MigrationError, audit, migrate_databases, snapshot

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RUNTIME_REDIS_MIGRATION_TESTS") != "1",
    reason="Explicit opt-in required for private Redis processes",
)


@pytest.fixture
def redis_pairs(tmp_path):
    servers = []
    clients = []
    try:
        for index in range(2):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            directory = tmp_path / str(index)
            directory.mkdir()
            server = subprocess.Popen(
                [
                    "redis-server",
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                    "--dir",
                    str(directory),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            servers.append(server)
            database_clients = [Redis(host="127.0.0.1", port=port, db=db) for db in (1, 2)]
            clients.extend(database_clients)
            deadline = time.monotonic() + 5
            while True:
                try:
                    database_clients[0].ping()
                    break
                except ConnectionError:
                    if time.monotonic() > deadline or server.poll() is not None:
                        raise RuntimeError("Private Redis startup failed") from None
                    time.sleep(0.05)
        yield [(clients[0], clients[2]), (clients[1], clients[3])]
    finally:
        for client in clients:
            client.close()
        for server in servers:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def populate(pairs):
    channel, cache = (pair[0] for pair in pairs)
    channel.zadd(b"channel\x00binary", {b"\xff\x00message": 10})
    channel.pexpire(b"channel\x00binary", 30_000)
    cache.set(b"lock\x00key", b"\xff\x00owner", px=60_000)
    cache.hset(b"permanent", mapping={b"one": b"\x00", b"two": b"\xff"})
    cache.rpush(b"list", b"first", b"second")
    cache.sadd(b"set", b"a", b"b")


def test_dry_run_preserves_source_and_does_not_write_target(redis_pairs):
    populate(redis_pairs)
    before = [snapshot(source) for source, _ in redis_pairs]
    report = migrate_databases(redis_pairs)
    assert all(not result["applied"] for result in report)
    assert all(target.dbsize() == 0 for _, target in redis_pairs)
    assert [snapshot(source) for source, _ in redis_pairs] == before


def test_binary_values_types_and_absolute_ttl_survive(redis_pairs):
    populate(redis_pairs)
    report = migrate_databases(redis_pairs, apply=True, writers_stopped=True)
    for result, (source, target) in zip(report, redis_pairs):
        assert result["applied"]
        assert audit(snapshot(source)) == audit(snapshot(target))
        for key in source.scan_iter():
            assert source.pexpiretime(key) == target.pexpiretime(key)
            assert source.type(key) == target.type(key)
    assert redis_pairs[1][1].get(b"lock\x00key") == b"\xff\x00owner"


def test_large_unordered_hashes_and_sets_survive(redis_pairs):
    source, target = redis_pairs[1]
    source.hset(b"large-hash", mapping={str(number).encode(): b"x" * 100 for number in range(100)})
    source.sadd(b"large-set", *[f"member-{number}".encode() for number in range(100)])
    migrate_databases(redis_pairs, apply=True, writers_stopped=True)
    assert source.hgetall(b"large-hash") == target.hgetall(b"large-hash")
    assert source.smembers(b"large-set") == target.smembers(b"large-set")


def test_nonempty_second_database_rejects_before_any_write(redis_pairs):
    populate(redis_pairs)
    redis_pairs[1][1].set(b"preexisting", b"untouched")
    with pytest.raises(MigrationError, match="empty"):
        migrate_databases(redis_pairs, apply=True, writers_stopped=True)
    assert redis_pairs[0][1].dbsize() == 0
    assert redis_pairs[1][1].get(b"preexisting") == b"untouched"


def test_apply_requires_all_writers_to_be_stopped(redis_pairs):
    with pytest.raises(MigrationError, match="Stop every"):
        migrate_databases(redis_pairs, apply=True)


def test_source_change_during_copy_cleans_only_owned_target_keys(redis_pairs, monkeypatch):
    populate(redis_pairs)
    source, target = redis_pairs[0]
    original = target.restore

    def restore_then_change(*args, **kwargs):
        result = original(*args, **kwargs)
        source.set(b"unexpected-writer", b"new")
        return result

    monkeypatch.setattr(target, "restore", restore_then_change)
    with pytest.raises(MigrationError, match="owned target keys removed"):
        migrate_databases(redis_pairs, apply=True, writers_stopped=True)
    assert all(target.dbsize() == 0 for _, target in redis_pairs)
    assert source.get(b"unexpected-writer") == b"new"


def test_second_database_failure_rolls_back_first_database_copy(redis_pairs, monkeypatch):
    populate(redis_pairs)

    def fail(*args, **kwargs):
        raise ConnectionError("injected failure")

    monkeypatch.setattr(redis_pairs[1][1], "restore", fail)
    with pytest.raises(MigrationError, match="owned target keys removed"):
        migrate_databases(redis_pairs, apply=True, writers_stopped=True)
    assert all(target.dbsize() == 0 for _, target in redis_pairs)
    assert redis_pairs[0][0].dbsize() == 1
    assert redis_pairs[1][0].dbsize() == 4


def test_change_in_first_source_database_during_second_copy_is_detected(redis_pairs, monkeypatch):
    populate(redis_pairs)
    original = redis_pairs[1][1].restore

    def restore_then_change(*args, **kwargs):
        result = original(*args, **kwargs)
        redis_pairs[0][0].set(b"unexpected-late-writer", b"new")
        return result

    monkeypatch.setattr(redis_pairs[1][1], "restore", restore_then_change)
    with pytest.raises(MigrationError, match="owned target keys removed"):
        migrate_databases(redis_pairs, apply=True, writers_stopped=True)
    assert all(target.dbsize() == 0 for _, target in redis_pairs)


def test_lost_restore_reply_marks_cleanup_incomplete_without_deleting_unknown_data(redis_pairs, monkeypatch):
    populate(redis_pairs)
    target = redis_pairs[0][1]
    original = target.restore

    def restore_then_lose_reply(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("lost reply")

    monkeypatch.setattr(target, "restore", restore_then_lose_reply)
    with pytest.raises(MigrationError, match="cleanup incomplete"):
        migrate_databases(redis_pairs, apply=True, writers_stopped=True)
    assert target.dbsize() == 1
    assert redis_pairs[0][0].dbsize() == 1


def test_expiring_key_is_not_resurrected_or_given_extra_ttl(redis_pairs, monkeypatch):
    source, target = redis_pairs[0]
    source.set(b"short-lock", b"owner", px=100)
    original = target.restore

    def delayed_restore(*args, **kwargs):
        time.sleep(0.15)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, "restore", delayed_restore)
    migrate_databases(redis_pairs, apply=True, writers_stopped=True)
    assert not source.exists(b"short-lock")
    assert not target.exists(b"short-lock")
