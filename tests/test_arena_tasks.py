from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.db import DatabaseError

import gameplay.tasks.arena as arena_tasks
from gameplay.services.arena import virtual_reserve_observability as arena_observability
from gameplay.services.virtual_player_core.safety_provider import SafetyMetricEventConflict


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


def test_wake_active_arena_demands_for_population_region_delegates(monkeypatch):
    monkeypatch.setattr(
        arena_tasks,
        "wake_active_arena_demands_for_population_region",
        lambda *, region: 3 if region == "overseas" else 0,
    )

    assert arena_tasks.wake_active_arena_demands_for_population_region_task.run("overseas") == {"woken": 3}


def test_scan_arena_virtual_reserves_delegates_to_shared_coordinator(monkeypatch):
    expected = {"scanned": 3, "reconciled": 3, "ready": 8, "training": 2, "filled_entries": 4}
    monkeypatch.setattr(arena_tasks, "scan_virtual_reserve_demands", lambda *, limit: expected)

    assert arena_tasks.scan_arena_virtual_reserves.run(limit=9) == expected


def test_scan_arena_virtual_reserves_surfaces_database_failures(monkeypatch):
    monkeypatch.setattr(
        arena_tasks,
        "scan_virtual_reserve_demands",
        lambda *, limit: (_ for _ in ()).throw(DatabaseError("temporary database failure")),
    )

    with pytest.raises(RuntimeError, match="arena virtual reserve scan failed"):
        arena_tasks.scan_arena_virtual_reserves.run(limit=9)


def test_retry_arena_shortage_metric_delegates_and_returns_recorded(monkeypatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        arena_tasks,
        "record_arena_shortage_observation",
        lambda **kwargs: calls.append(kwargs),
    )

    result = arena_tasks.retry_arena_shortage_metric.run(
        7,
        "tournament",
        9,
        10,
        2,
        100,
        "operation-1",
        "2026-07-28T08:00:00Z",
        1,
        3,
        2,
        4,
        1,
        "newbie",
    )

    assert result == {"recorded": 1, "retry_scheduled": 0}
    assert calls[0]["snapshot"] == arena_observability.ArenaShortageObservationSnapshot(
        prestige_band="newbie",
        real_entry_count=3,
        virtual_entry_count=2,
        reserve_ready_count=4,
        reserve_training_count=1,
    )


def test_retry_arena_shortage_metric_schedules_one_more_bounded_attempt(monkeypatch):
    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        arena_tasks,
        "record_arena_shortage_observation",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("metric backend unavailable")),
    )
    monkeypatch.setattr(
        arena_tasks,
        "queue_arena_shortage_metric_retry",
        lambda **kwargs: queued.append(kwargs) or True,
    )
    monkeypatch.setattr(arena_tasks, "record_arena_shortage_metric_failure", lambda **kwargs: None)

    result = arena_tasks.retry_arena_shortage_metric.run(
        7,
        "tournament",
        9,
        10,
        2,
        100,
        "operation-1",
        "2026-07-28T08:00:00Z",
        1,
        3,
        2,
        4,
        1,
        "newbie",
    )

    assert result == {"recorded": 0, "retry_scheduled": 1}
    assert queued[0]["retry_attempt"] == 2
    assert queued[0]["real_entry_count"] == 3
    assert queued[0]["virtual_entry_count"] == 2
    assert queued[0]["reserve_ready_count"] == 4
    assert queued[0]["reserve_training_count"] == 1
    assert queued[0]["prestige_band"] == "newbie"


def test_retry_arena_shortage_metric_freezes_fallback_snapshot_before_provider_write(monkeypatch):
    queued: list[dict[str, object]] = []
    snapshot = arena_observability.ArenaShortageObservationSnapshot(
        prestige_band="newbie",
        real_entry_count=3,
        virtual_entry_count=2,
        reserve_ready_count=4,
        reserve_training_count=1,
    )
    monkeypatch.setattr(
        arena_tasks,
        "prepare_arena_shortage_observation_snapshot",
        lambda **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        arena_tasks,
        "record_arena_shortage_observation",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("commit outcome unknown")),
    )
    monkeypatch.setattr(
        arena_tasks,
        "queue_arena_shortage_metric_retry",
        lambda **kwargs: queued.append(kwargs) or True,
    )
    monkeypatch.setattr(arena_tasks, "record_arena_shortage_metric_failure", lambda **_kwargs: None)

    result = arena_tasks.retry_arena_shortage_metric.run(
        7,
        "tournament",
        9,
        10,
        2,
        100,
        "operation-1",
        "2026-07-28T08:00:00Z",
        1,
    )

    assert result == {"recorded": 0, "retry_scheduled": 1}
    assert queued[0]["real_entry_count"] == snapshot.real_entry_count
    assert queued[0]["virtual_entry_count"] == snapshot.virtual_entry_count
    assert queued[0]["reserve_ready_count"] == snapshot.reserve_ready_count
    assert queued[0]["reserve_training_count"] == snapshot.reserve_training_count
    assert queued[0]["prestige_band"] == snapshot.prestige_band


def test_retry_arena_shortage_metric_does_not_retry_provider_terminal_error(monkeypatch):
    failures: list[dict[str, object]] = []
    conflict = SafetyMetricEventConflict(
        "canonical payload conflict",
        hard_violation_event_id="hard-violation-1",
    )
    monkeypatch.setattr(
        arena_tasks,
        "record_arena_shortage_observation",
        lambda **kwargs: (_ for _ in ()).throw(conflict),
    )
    monkeypatch.setattr(
        arena_tasks,
        "record_arena_shortage_metric_failure",
        lambda **kwargs: failures.append(kwargs),
    )
    monkeypatch.setattr(
        arena_tasks,
        "queue_arena_shortage_metric_retry",
        lambda **_kwargs: pytest.fail("provider terminal errors must not be retried"),
    )

    with pytest.raises(RuntimeError, match="arena shortage metric retry exhausted"):
        arena_tasks.retry_arena_shortage_metric.run(
            7,
            "tournament",
            9,
            10,
            2,
            100,
            "operation-1",
            "2026-07-28T08:00:00Z",
            1,
            3,
            2,
            4,
            1,
            "newbie",
        )

    assert failures[0]["operation_id"] == "operation-1"


def test_retry_arena_shortage_metric_fails_closed_after_last_attempt(monkeypatch):
    failures: list[dict[str, object]] = []
    monkeypatch.setattr(
        arena_tasks,
        "record_arena_shortage_observation",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("metric backend unavailable")),
    )
    monkeypatch.setattr(
        arena_tasks,
        "record_arena_shortage_metric_failure",
        lambda **kwargs: failures.append(kwargs),
    )
    monkeypatch.setattr(arena_tasks, "queue_arena_shortage_metric_retry", lambda **kwargs: False)

    with pytest.raises(RuntimeError, match="arena shortage metric retry exhausted"):
        arena_tasks.retry_arena_shortage_metric.run(
            7,
            "tournament",
            9,
            10,
            2,
            100,
            "operation-1",
            "2026-07-28T08:00:00Z",
            2,
            3,
            2,
            4,
            1,
            "newbie",
        )

    assert failures[0]["operation_id"] == "operation-1"
    assert failures[0]["observed_at"].isoformat() == "2026-07-28T08:00:00+00:00"


def test_queue_arena_shortage_metric_retry_defers_missing_context_capture(monkeypatch):
    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        arena_observability,
        "_capture_arena_shortage_observation_context",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("context capture must be deferred")),
    )
    monkeypatch.setattr(
        arena_observability,
        "current_app",
        SimpleNamespace(signature=lambda name: name),
    )
    monkeypatch.setattr(
        arena_observability,
        "safe_apply_async",
        lambda task, **kwargs: queued.append({"task": task, **kwargs}) or True,
    )

    assert (
        arena_observability.queue_arena_shortage_metric_retry(
            demand_id=7,
            mode="tournament",
            event_id=9,
            capacity=10,
            missing_count=2,
            population_prestige=100,
            operation_id="operation-1",
            observed_at=datetime(2026, 7, 28, 8, tzinfo=dt_timezone.utc),
            retry_attempt=1,
        )
        is True
    )
    assert queued[0]["args"][-5:] == [None, None, None, None, None]


def test_arena_shortage_callback_freezes_fallback_snapshot_for_retry(monkeypatch):
    queued: list[dict[str, object]] = []
    snapshot = arena_observability.ArenaShortageObservationSnapshot(
        prestige_band="newbie",
        real_entry_count=3,
        virtual_entry_count=2,
        reserve_ready_count=4,
        reserve_training_count=1,
    )
    prepare_results = iter((RuntimeError("database unavailable"), snapshot))

    def prepare_snapshot(**_kwargs):
        result = next(prepare_results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        arena_observability,
        "prepare_arena_shortage_observation_snapshot",
        prepare_snapshot,
    )
    monkeypatch.setattr(
        arena_observability,
        "record_arena_shortage_observation",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("commit outcome unknown")),
    )
    monkeypatch.setattr(
        arena_observability,
        "queue_arena_shortage_metric_retry",
        lambda **kwargs: queued.append(kwargs) or True,
    )
    monkeypatch.setattr(arena_observability, "record_arena_shortage_metric_failure", lambda **_kwargs: None)
    monkeypatch.setattr(arena_observability, "log_safety_metric_failure", lambda **_kwargs: None)
    monkeypatch.setattr(arena_observability.transaction, "on_commit", lambda callback: callback())
    demand = SimpleNamespace(
        id=7,
        tournament_id=9,
        tournament=SimpleNamespace(player_limit=10),
        coop_event_id=None,
        version=2,
        missing_entry_count=2,
    )

    arena_observability.emit_arena_shortage_after_commit(
        demand,
        population_prestige=100,
        observed_at=datetime(2026, 7, 28, 8, tzinfo=dt_timezone.utc),
    )

    assert queued[0]["real_entry_count"] == snapshot.real_entry_count
    assert queued[0]["virtual_entry_count"] == snapshot.virtual_entry_count
    assert queued[0]["reserve_ready_count"] == snapshot.reserve_ready_count
    assert queued[0]["reserve_training_count"] == snapshot.reserve_training_count
    assert queued[0]["prestige_band"] == snapshot.prestige_band


def test_record_arena_shortage_observation_uses_only_frozen_snapshot(monkeypatch):
    calls: list[dict[str, object]] = []
    snapshot = arena_observability.ArenaShortageObservationSnapshot(
        prestige_band="newbie",
        real_entry_count=3,
        virtual_entry_count=2,
        reserve_ready_count=4,
        reserve_training_count=1,
    )
    monkeypatch.setattr(
        arena_observability,
        "_capture_arena_shortage_observation_context",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("provider writes must not read live context")),
    )
    monkeypatch.setattr(
        arena_observability,
        "load_virtual_player_v2_config",
        lambda: (_ for _ in ()).throw(AssertionError("provider writes must not reload band configuration")),
    )
    monkeypatch.setattr(
        arena_observability,
        "record_arena_shortage",
        lambda **kwargs: calls.append(kwargs),
    )

    arena_observability.record_arena_shortage_observation(
        mode="tournament",
        capacity=10,
        missing_count=2,
        operation_id="operation-1",
        observed_at=datetime(2026, 7, 28, 8, tzinfo=dt_timezone.utc),
        snapshot=snapshot,
    )

    assert calls[0]["prestige_band"] == "newbie"
    assert calls[0]["real_entry_count"] == 3
    assert calls[0]["virtual_entry_count"] == 2
    assert calls[0]["reserve_ready_count"] == 4
    assert calls[0]["reserve_training_count"] == 1


def test_arena_shortage_snapshot_rejects_silent_context_coercion() -> None:
    with pytest.raises(ValueError, match="real_entry_count"):
        arena_observability.ArenaShortageObservationSnapshot(
            prestige_band="newbie",
            real_entry_count=True,
            virtual_entry_count=2,
            reserve_ready_count=4,
            reserve_training_count=1,
        )

    with pytest.raises(ValueError, match="prestige_band"):
        arena_observability.prepare_arena_shortage_observation_snapshot(
            demand_id=7,
            mode="tournament",
            event_id=9,
            population_prestige=100,
            prestige_band=123,  # type: ignore[arg-type]
            real_entry_count=3,
            virtual_entry_count=2,
            reserve_ready_count=4,
            reserve_training_count=1,
        )


def test_grow_arena_virtual_reserves_runs_growth(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        arena_tasks,
        "grow_due_virtual_reserves",
        lambda *, limit: calls.append(limit) or 5,
    )

    result = arena_tasks.grow_arena_virtual_reserves.run(limit=40)

    assert result == {"grown": 5}
    assert calls == [40]


def test_grow_arena_virtual_reserves_surfaces_growth_database_failure(monkeypatch):
    def fail_growth(*, limit):
        del limit
        raise DatabaseError("growth database unavailable")

    monkeypatch.setattr(arena_tasks, "grow_due_virtual_reserves", fail_growth)

    with pytest.raises(RuntimeError, match="grow_due_virtual_reserves"):
        arena_tasks.grow_arena_virtual_reserves.run(limit=40)
