from __future__ import annotations

import pytest
from django.db import DatabaseError

import gameplay.tasks.arena as arena_tasks


def test_scan_arena_tournaments_returns_only_tournament_counts(monkeypatch):
    coop_calls: list[str] = []
    monkeypatch.setattr(arena_tasks, "start_ready_tournaments", lambda *, limit: limit // 10)
    monkeypatch.setattr(arena_tasks, "start_due_virtual_backfill_tournaments", lambda *, limit: limit // 4)
    monkeypatch.setattr(arena_tasks, "run_due_arena_rounds", lambda *, limit: limit // 5)
    monkeypatch.setattr(arena_tasks, "cleanup_expired_tournaments", lambda *, limit: limit // 4)
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "start_due_virtual_backfill_coop_events",
        lambda *, limit: coop_calls.append("backfill") or 0,
    )
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "run_due_arena_coop_events",
        lambda *, limit: coop_calls.append("run") or 0,
    )
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: coop_calls.append("cleanup") or 0,
    )

    result = arena_tasks.scan_arena_tournaments.run(limit=20)

    assert result == {
        "started": 2,
        "virtual_started": 5,
        "processed_rounds": 4,
        "cleaned_tournaments": 5,
    }
    assert coop_calls == []


def test_scan_arena_coop_events_returns_only_coop_counts_and_reads_latest_retention(monkeypatch):
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "start_due_virtual_backfill_coop_events",
        lambda *, limit: limit // 5,
    )
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "run_due_arena_coop_events",
        lambda *, limit: limit // 2,
    )
    monkeypatch.setattr(arena_tasks.arena_coop_core, "ARENA_COOP_COMPLETED_RETENTION_SECONDS", 4321)
    observed: dict[str, int] = {}

    def _cleanup(*, now, grace_seconds, limit):
        observed["grace_seconds"] = grace_seconds
        observed["limit"] = limit
        return limit // 20

    monkeypatch.setattr(arena_tasks.arena_coop_core, "cleanup_expired_arena_coop_events", _cleanup)

    result = arena_tasks.scan_arena_coop_events.run(limit=20)

    assert result == {
        "virtual_coop_prepared": 4,
        "processed_coop_events": 10,
        "cleaned_coop_events": 1,
    }
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

    monkeypatch.setattr(arena_tasks, "start_ready_tournaments", _start)

    def _virtual(*, limit):
        calls.append(f"virtual:{limit}")
        raise DatabaseError("virtual tournament backfill unavailable")

    monkeypatch.setattr(
        arena_tasks,
        "start_due_virtual_backfill_tournaments",
        _virtual,
    )
    monkeypatch.setattr(arena_tasks, "run_due_arena_rounds", _rounds)
    monkeypatch.setattr(arena_tasks, "cleanup_expired_tournaments", _cleanup)
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "start_due_virtual_backfill_coop_events",
        lambda *, limit: 0,
    )
    monkeypatch.setattr(arena_tasks.arena_coop_core, "run_due_arena_coop_events", lambda *, limit: 0)
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: 0,
    )

    with pytest.raises(
        RuntimeError,
        match="start_ready_tournaments, start_due_virtual_backfill_tournaments, cleanup_expired_tournaments",
    ):
        arena_tasks.scan_arena_tournaments.run(limit=20)

    assert calls == [
        "start:20",
        "virtual:20",
        "rounds:20",
        "cleanup:20",
    ]


def test_scan_arena_coop_events_aggregates_database_failures(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "start_due_virtual_backfill_coop_events",
        lambda *, limit: calls.append(f"virtual_coop:{limit}")
        or (_ for _ in ()).throw(DatabaseError("virtual coop unavailable")),
    )
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "run_due_arena_coop_events",
        lambda *, limit: calls.append(f"coop:{limit}") or 4,
    )
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: calls.append(f"coop_cleanup:{limit}")
        or (_ for _ in ()).throw(DatabaseError("coop cleanup unavailable")),
    )

    with pytest.raises(
        RuntimeError,
        match="start_due_virtual_backfill_coop_events, cleanup_expired_arena_coop_events",
    ):
        arena_tasks.scan_arena_coop_events.run(limit=20)

    assert calls == ["virtual_coop:20", "coop:20", "coop_cleanup:20"]


def test_scan_arena_tournaments_programming_error_bubbles_up(monkeypatch):
    calls: list[str] = []

    def _start(*, limit):
        calls.append(f"start:{limit}")
        raise AssertionError("broken arena start contract")

    monkeypatch.setattr(arena_tasks, "start_ready_tournaments", _start)
    monkeypatch.setattr(
        arena_tasks,
        "start_due_virtual_backfill_tournaments",
        lambda *, limit: calls.append(f"virtual:{limit}") or (_ for _ in ()).throw(AssertionError("should not run")),
    )
    monkeypatch.setattr(
        arena_tasks,
        "run_due_arena_rounds",
        lambda *, limit: calls.append(f"rounds:{limit}") or (_ for _ in ()).throw(AssertionError("should not run")),
    )
    monkeypatch.setattr(
        arena_tasks,
        "cleanup_expired_tournaments",
        lambda *, limit: calls.append(f"cleanup:{limit}") or (_ for _ in ()).throw(AssertionError("should not run")),
    )

    with pytest.raises(AssertionError, match="broken arena start contract"):
        arena_tasks.scan_arena_tournaments.run(limit=20)

    assert calls == ["start:20"]


def test_scan_arena_coop_events_programming_error_bubbles_up(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "start_due_virtual_backfill_coop_events",
        lambda *, limit: calls.append(f"virtual_coop:{limit}")
        or (_ for _ in ()).throw(AssertionError("broken coop contract")),
    )
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "run_due_arena_coop_events",
        lambda *, limit: calls.append(f"coop:{limit}") or (_ for _ in ()).throw(AssertionError("should not run")),
    )
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: calls.append(f"coop_cleanup:{limit}")
        or (_ for _ in ()).throw(AssertionError("should not run")),
    )

    with pytest.raises(AssertionError, match="broken coop contract"):
        arena_tasks.scan_arena_coop_events.run(limit=20)

    assert calls == ["virtual_coop:20"]
