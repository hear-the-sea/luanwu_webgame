from __future__ import annotations

import pytest
from django.db import DatabaseError

from gameplay.tasks.arena import scan_arena_tournaments


def test_scan_arena_tournaments_returns_counts(monkeypatch):
    monkeypatch.setattr("gameplay.tasks.arena.start_ready_tournaments", lambda *, limit: limit // 10)
    monkeypatch.setattr("gameplay.tasks.arena.run_due_arena_rounds", lambda *, limit: limit // 5)
    monkeypatch.setattr("gameplay.tasks.arena.cleanup_expired_tournaments", lambda *, limit: limit // 4)
    monkeypatch.setattr("gameplay.tasks.arena.arena_coop_core.run_due_arena_coop_events", lambda *, limit: limit // 2)
    monkeypatch.setattr(
        "gameplay.tasks.arena.arena_coop_core.cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: limit // 20,
    )

    result = scan_arena_tournaments.run(limit=20)

    assert result == {
        "started": 2,
        "processed_rounds": 4,
        "processed_coop_events": 10,
        "cleaned_tournaments": 5,
        "cleaned_coop_events": 1,
    }


def test_scan_arena_tournaments_includes_coop_counts(monkeypatch):
    monkeypatch.setattr("gameplay.tasks.arena.start_ready_tournaments", lambda *, limit: 1)
    monkeypatch.setattr("gameplay.tasks.arena.run_due_arena_rounds", lambda *, limit: 2)
    monkeypatch.setattr("gameplay.tasks.arena.cleanup_expired_tournaments", lambda *, limit: 3)
    monkeypatch.setattr("gameplay.tasks.arena.arena_coop_core.run_due_arena_coop_events", lambda *, limit: 4)
    monkeypatch.setattr(
        "gameplay.tasks.arena.arena_coop_core.cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: 5,
    )

    result = scan_arena_tournaments.run(limit=20)

    assert result["processed_coop_events"] == 4
    assert result["cleaned_coop_events"] == 5


def test_scan_arena_tournaments_reads_latest_coop_retention_seconds(monkeypatch):
    monkeypatch.setattr("gameplay.tasks.arena.start_ready_tournaments", lambda *, limit: 0)
    monkeypatch.setattr("gameplay.tasks.arena.run_due_arena_rounds", lambda *, limit: 0)
    monkeypatch.setattr("gameplay.tasks.arena.cleanup_expired_tournaments", lambda *, limit: 0)
    monkeypatch.setattr("gameplay.tasks.arena.arena_coop_core.run_due_arena_coop_events", lambda *, limit: 0)
    monkeypatch.setattr("gameplay.services.arena.coop_core.ARENA_COOP_COMPLETED_RETENTION_SECONDS", 4321)

    observed: dict[str, int] = {}

    def _cleanup(*, now, grace_seconds, limit):
        observed["grace_seconds"] = grace_seconds
        observed["limit"] = limit
        return 0

    monkeypatch.setattr("gameplay.tasks.arena.arena_coop_core.cleanup_expired_arena_coop_events", _cleanup)

    scan_arena_tournaments.run(limit=20)

    assert observed == {"grace_seconds": 4321, "limit": 20}


def test_scan_arena_tournaments_aggregates_database_failures(monkeypatch):
    calls: list[str] = []

    def _start(*, limit):
        calls.append(f"start:{limit}")
        raise DatabaseError("arena table unavailable")

    def _rounds(*, limit):
        calls.append(f"rounds:{limit}")
        return 3

    def _cleanup(*, limit):
        calls.append(f"cleanup:{limit}")
        raise DatabaseError("cleanup table unavailable")

    monkeypatch.setattr("gameplay.tasks.arena.start_ready_tournaments", _start)
    monkeypatch.setattr("gameplay.tasks.arena.run_due_arena_rounds", _rounds)
    monkeypatch.setattr("gameplay.tasks.arena.cleanup_expired_tournaments", _cleanup)
    monkeypatch.setattr(
        "gameplay.tasks.arena.arena_coop_core.run_due_arena_coop_events",
        lambda *, limit: calls.append(f"coop:{limit}") or 4,
    )
    monkeypatch.setattr(
        "gameplay.tasks.arena.arena_coop_core.cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: calls.append(f"coop_cleanup:{limit}") or 5,
    )

    with pytest.raises(RuntimeError, match="start_ready_tournaments, cleanup_expired_tournaments"):
        scan_arena_tournaments.run(limit=20)

    assert calls == ["start:20", "rounds:20", "cleanup:20", "coop:20", "coop_cleanup:20"]


def test_scan_arena_tournaments_programming_error_bubbles_up(monkeypatch):
    calls: list[str] = []

    def _start(*, limit):
        calls.append(f"start:{limit}")
        raise AssertionError("broken arena start contract")

    monkeypatch.setattr("gameplay.tasks.arena.start_ready_tournaments", _start)
    monkeypatch.setattr(
        "gameplay.tasks.arena.run_due_arena_rounds",
        lambda *, limit: calls.append(f"rounds:{limit}") or (_ for _ in ()).throw(AssertionError("should not run")),
    )
    monkeypatch.setattr(
        "gameplay.tasks.arena.cleanup_expired_tournaments",
        lambda *, limit: calls.append(f"cleanup:{limit}") or (_ for _ in ()).throw(AssertionError("should not run")),
    )
    monkeypatch.setattr(
        "gameplay.tasks.arena.arena_coop_core.run_due_arena_coop_events",
        lambda *, limit: calls.append(f"coop:{limit}") or (_ for _ in ()).throw(AssertionError("should not run")),
    )
    monkeypatch.setattr(
        "gameplay.tasks.arena.arena_coop_core.cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: calls.append(f"coop_cleanup:{limit}")
        or (_ for _ in ()).throw(AssertionError("should not run")),
    )

    with pytest.raises(AssertionError, match="broken arena start contract"):
        scan_arena_tournaments.run(limit=20)

    assert calls == ["start:20"]
