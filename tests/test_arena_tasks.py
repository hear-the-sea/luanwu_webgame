from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db import DatabaseError

import gameplay.tasks.arena as arena_tasks


def test_scan_arena_tournaments_returns_only_tournament_counts(monkeypatch):
    coop_calls: list[str] = []
    monkeypatch.setattr(arena_tasks, "start_ready_tournaments", lambda *, limit: limit // 10)
    monkeypatch.setattr(arena_tasks, "run_due_arena_rounds", lambda *, limit: limit // 5)
    monkeypatch.setattr(arena_tasks, "cleanup_expired_tournaments", lambda *, limit: limit // 4)
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
        "processed_rounds": 4,
        "cleaned_tournaments": 5,
    }
    assert coop_calls == []


def test_scan_arena_coop_events_returns_only_coop_counts_and_reads_latest_retention(monkeypatch):
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

    monkeypatch.setattr(arena_tasks, "run_due_arena_rounds", _rounds)
    monkeypatch.setattr(arena_tasks, "cleanup_expired_tournaments", _cleanup)
    monkeypatch.setattr(arena_tasks.arena_coop_core, "run_due_arena_coop_events", lambda *, limit: 0)
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: 0,
    )

    with pytest.raises(
        RuntimeError,
        match="start_ready_tournaments, cleanup_expired_tournaments",
    ):
        arena_tasks.scan_arena_tournaments.run(limit=20)

    assert calls == [
        "start:20",
        "rounds:20",
        "cleanup:20",
    ]


def test_scan_arena_coop_events_aggregates_database_failures(monkeypatch):
    calls: list[str] = []
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
        match="cleanup_expired_arena_coop_events",
    ):
        arena_tasks.scan_arena_coop_events.run(limit=20)

    assert calls == ["coop:20", "coop_cleanup:20"]


def test_scan_arena_tournaments_programming_error_bubbles_up(monkeypatch):
    calls: list[str] = []

    def _start(*, limit):
        calls.append(f"start:{limit}")
        raise AssertionError("broken arena start contract")

    monkeypatch.setattr(arena_tasks, "start_ready_tournaments", _start)
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
        "run_due_arena_coop_events",
        lambda *, limit: calls.append(f"coop:{limit}") or (_ for _ in ()).throw(AssertionError("broken coop contract")),
    )
    monkeypatch.setattr(
        arena_tasks.arena_coop_core,
        "cleanup_expired_arena_coop_events",
        lambda *, now, grace_seconds, limit: calls.append(f"coop_cleanup:{limit}")
        or (_ for _ in ()).throw(AssertionError("should not run")),
    )

    with pytest.raises(AssertionError, match="broken coop contract"):
        arena_tasks.scan_arena_coop_events.run(limit=20)

    assert calls == ["coop:20"]


def test_reconcile_arena_virtual_reserve_replenishes_targeted_demand(monkeypatch):
    demand = SimpleNamespace(id=17)
    replenished = SimpleNamespace(ready_count=4, training_count=2)
    monkeypatch.setattr(arena_tasks, "reconcile_tournament_demand", lambda event_id: demand)
    monkeypatch.setattr(
        arena_tasks,
        "reconcile_coop_demand",
        lambda event_id: (_ for _ in ()).throw(AssertionError("wrong mode")),
    )
    monkeypatch.setattr(arena_tasks, "replenish_virtual_reserve", lambda demand_id: replenished)

    result = arena_tasks.reconcile_arena_virtual_reserve.run("tournament", 9)

    assert result == {"reconciled": 1, "ready": 4, "training": 2}


def test_reconcile_arena_virtual_reserve_returns_zero_when_event_has_no_demand(monkeypatch):
    monkeypatch.setattr(arena_tasks, "reconcile_coop_demand", lambda event_id: None)

    result = arena_tasks.reconcile_arena_virtual_reserve.run("coop", 12)

    assert result == {"reconciled": 0, "ready": 0, "training": 0}


def test_scan_arena_virtual_reserves_delegates_to_shared_coordinator(monkeypatch):
    expected = {"scanned": 3, "reconciled": 3, "ready": 8, "training": 2, "filled_entries": 4}
    monkeypatch.setattr(arena_tasks, "scan_virtual_reserve_demands", lambda *, limit: expected)

    assert arena_tasks.scan_arena_virtual_reserves.run(limit=9) == expected


def test_grow_arena_virtual_reserves_runs_growth_then_creation(monkeypatch):
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        arena_tasks,
        "grow_due_virtual_reserves",
        lambda *, limit: calls.append(("grow", limit)) or 5,
    )
    monkeypatch.setattr(
        arena_tasks,
        "create_due_virtual_reserve_profiles",
        lambda *, limit: calls.append(("create", limit)) or 3,
    )

    result = arena_tasks.grow_arena_virtual_reserves.run(limit=40)

    assert result == {"grown": 5, "created": 3}
    assert calls == [("grow", 40), ("create", 40)]
