from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError
from django.utils import timezone

from gameplay.models import BotSafetyMetricEvent
from gameplay.services.raid.combat import battle as combat_battle
from gameplay.services.virtual_player_core import maintenance
from gameplay.services.virtual_player_core.contracts import (
    BotLootClampDecision,
    MaintenanceOutcome,
    MaintenanceResult,
    MaintenanceScheduleDisposition,
    MaintenanceTrigger,
)
from gameplay.services.virtual_player_core.safety_metrics import (
    ARENA_SHORTAGE_METRIC,
    H01_CALLBACK_ATTEMPT_METRIC,
    H01_RECOMMENDATION_METRIC,
    HARD_CONSTRAINT_METRIC,
    MAINTENANCE_ATTEMPT_METRIC,
    SAFETY_HEARTBEAT_METRIC,
    finish_maintenance_attempt,
    finish_maintenance_attempts,
    record_arena_shortage,
    record_h01_callback_attempt,
    record_h01_retirement_recommendation,
    record_safety_heartbeat,
    record_safety_metric_failure,
    start_maintenance_attempt,
    start_maintenance_attempts,
)

NOW = datetime(2026, 7, 28, 8, 1, 23, 456789, tzinfo=UTC)


@pytest.mark.django_db
def test_heartbeat_is_idempotent_at_one_minute_resolution() -> None:
    first = record_safety_heartbeat("safety_monitor", now=NOW)
    second = record_safety_heartbeat(
        "safety_monitor",
        now=NOW + timedelta(seconds=30),
    )

    assert first.created is True
    assert second.created is False
    event = BotSafetyMetricEvent.objects.get(event_id=first.event_id)
    assert event.metric_name == SAFETY_HEARTBEAT_METRIC
    assert event.occurred_at == NOW.replace(second=0, microsecond=0)
    assert event.dimensions == {"stream": "safety_monitor"}


@pytest.mark.django_db
def test_maintenance_attempt_uses_started_window_for_one_terminal_result() -> None:
    attempt = start_maintenance_attempt(
        trigger=MaintenanceTrigger.ADMIN,
        operation_id="admin-request-41",
        attempt_ordinal=2,
        started_at=NOW,
    )
    result = MaintenanceResult(
        outcome=MaintenanceOutcome.NO_ACTION,
        trigger=MaintenanceTrigger.ADMIN,
        profile_id=41,
        sequence_before=3,
        sequence_after=4,
        schedule_disposition=(MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE),
        next_growth_at_before=NOW,
        next_growth_at_after=NOW,
        reason="domain_constraint",
    )

    finish_maintenance_attempt(attempt, result=result)
    duplicate = finish_maintenance_attempt(attempt, result=result)

    assert duplicate.created is False
    events = list(BotSafetyMetricEvent.objects.filter(metric_name=MAINTENANCE_ATTEMPT_METRIC).order_by("event_id"))
    assert [event.event_id for event in events] == [
        "maintenance:admin-request-41:2:started",
        "maintenance:admin-request-41:2:terminal",
    ]
    assert {event.occurred_at for event in events} == {NOW}
    assert {event.dimensions["result"] for event in events} == {
        "started",
        "no_action",
    }


@pytest.mark.django_db
def test_maintenance_attempt_batch_pairs_started_and_terminal_events() -> None:
    attempts = start_maintenance_attempts(
        trigger=MaintenanceTrigger.SCHEDULED,
        operation_ids=("scheduled-1", "scheduled-2"),
        started_at=NOW,
    )

    finish_maintenance_attempts(tuple((attempt, MaintenanceOutcome.NO_ACTION.value) for attempt in attempts))

    events = BotSafetyMetricEvent.objects.filter(metric_name=MAINTENANCE_ATTEMPT_METRIC)
    assert events.count() == 4
    assert set(events.values_list("dimensions__result", flat=True)) == {
        "started",
        "no_action",
    }
    assert set(events.values_list("occurred_at", flat=True)) == {NOW}


@pytest.mark.django_db
def test_h01_recommendation_and_callback_attempt_are_distinct_metrics() -> None:
    record_h01_retirement_recommendation(
        operation_id="raid-run-9",
        occurred_at=NOW,
    )
    record_h01_callback_attempt(
        operation_id="raid-run-9",
        result="all",
        occurred_at=NOW,
    )
    record_h01_callback_attempt(
        operation_id="raid-run-9",
        result="degraded",
        occurred_at=NOW,
    )

    assert BotSafetyMetricEvent.objects.filter(metric_name=H01_RECOMMENDATION_METRIC).count() == 1
    attempts = BotSafetyMetricEvent.objects.filter(metric_name=H01_CALLBACK_ATTEMPT_METRIC)
    assert attempts.count() == 2
    assert set(attempts.values_list("dimensions__result", flat=True)) == {
        "all",
        "degraded",
    }


@pytest.mark.django_db
def test_arena_shortage_records_bounded_quantized_ratio_per_scope() -> None:
    record_arena_shortage(
        operation_id="tournament-7-v3",
        mode="tournament",
        prestige_band="newbie",
        missing_count=1,
        capacity=3,
        occurred_at=NOW,
    )

    event = BotSafetyMetricEvent.objects.get(metric_name=ARENA_SHORTAGE_METRIC)
    assert event.dimensions == {
        "kind": "tournament",
        "prestige_band": "newbie",
    }
    assert event.value == Decimal("0.333333333333")


@pytest.mark.django_db
def test_arena_shortage_can_record_population_context_without_changing_ratio() -> None:
    record_arena_shortage(
        operation_id="tournament-7-v4",
        mode="tournament",
        prestige_band="newbie",
        missing_count=9,
        capacity=10,
        real_entry_count=1,
        virtual_entry_count=0,
        reserve_ready_count=3,
        reserve_training_count=24,
        occurred_at=NOW,
    )

    event = BotSafetyMetricEvent.objects.get(metric_name=ARENA_SHORTAGE_METRIC)
    assert event.dimensions == {
        "kind": "tournament",
        "prestige_band": "newbie",
        "real_entry_count": 1,
        "virtual_entry_count": 0,
        "reserve_ready_count": 3,
        "reserve_training_count": 24,
    }
    assert event.value == Decimal("0.900000000000")


@pytest.mark.django_db
def test_metric_write_failure_persists_idempotent_hard_constraint_event() -> None:
    result = record_safety_metric_failure(
        operation="arena-coop-12-v3-20260728T080123456789Z",
        source_metric=ARENA_SHORTAGE_METRIC,
        exc=RuntimeError("provider unavailable"),
        occurred_at=NOW,
    )

    event = BotSafetyMetricEvent.objects.get(event_id=result.event_id)
    assert event.metric_name == HARD_CONSTRAINT_METRIC
    assert event.dimensions == {
        "failure_code": "safety_metric_write_failure",
        "operation": "arena-coop-12-v3-20260728T080123456789Z",
        "reason": "safety_metric_write_failed",
        "source_metric": ARENA_SHORTAGE_METRIC,
    }
    assert event.value == Decimal("1.000000000000")

    duplicate = record_safety_metric_failure(
        operation="arena-coop-12-v3-20260728T080123456789Z",
        source_metric=ARENA_SHORTAGE_METRIC,
        exc=RuntimeError("provider unavailable"),
        occurred_at=NOW,
    )
    assert duplicate.created is False
    assert BotSafetyMetricEvent.objects.filter(metric_name=HARD_CONSTRAINT_METRIC).count() == 1

    other_source = record_safety_metric_failure(
        operation="arena-coop-12-v3-20260728T080123456789Z",
        source_metric="virtual_player_h01_callback_attempt",
        exc=RuntimeError("provider unavailable"),
        occurred_at=NOW,
    )
    assert other_source.event_id != event.event_id
    assert BotSafetyMetricEvent.objects.filter(metric_name=HARD_CONSTRAINT_METRIC).count() == 2


@pytest.mark.django_db
def test_v2_maintenance_boundary_records_one_started_and_terminal_event(
    monkeypatch,
) -> None:
    record_safety_heartbeat("safety_monitor", now=timezone.now())
    result = MaintenanceResult(
        outcome=MaintenanceOutcome.NO_ACTION,
        trigger=MaintenanceTrigger.SCHEDULED,
        profile_id=41,
        sequence_before=3,
        sequence_after=4,
        schedule_disposition=(MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE),
        next_growth_at_before=NOW,
        next_growth_at_after=NOW + timedelta(hours=1),
        reason="domain_constraint",
    )
    plan = object()
    monkeypatch.setattr(
        maintenance,
        "build_virtual_player_v2_maintenance_plan",
        lambda *_args, **_kwargs: plan,
    )
    monkeypatch.setattr(
        maintenance,
        "execute_virtual_player_v2_maintenance_plan",
        lambda candidate, **_kwargs: result if candidate is plan else None,
    )

    observed = maintenance.maintain_virtual_player_v2(
        41,
        trigger=MaintenanceTrigger.SCHEDULED,
        operation_id="scheduled-batch-7-profile-41",
        attempt_ordinal=1,
    )

    assert observed is result
    events = BotSafetyMetricEvent.objects.filter(metric_name=MAINTENANCE_ATTEMPT_METRIC)
    assert events.count() == 2
    assert set(events.values_list("dimensions__result", flat=True)) == {
        "started",
        "no_action",
    }


@pytest.mark.django_db
def test_h01_callback_records_all_and_degraded_without_masking_infrastructure(
    monkeypatch,
) -> None:
    decision = BotLootClampDecision(
        resources={},
        bot_profile_id=9,
        retirement_recommended=True,
    )
    monkeypatch.setattr(
        combat_battle,
        "retire_virtual_player_if_unprotected",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("unavailable")),
    )

    combat_battle._process_bot_loot_retirement(
        decision,
        now=NOW,
        operation_id="raid-7-retirement",
    )

    attempts = BotSafetyMetricEvent.objects.filter(metric_name=H01_CALLBACK_ATTEMPT_METRIC)
    assert attempts.count() == 2
    assert set(attempts.values_list("dimensions__result", flat=True)) == {
        "all",
        "degraded",
    }


@pytest.mark.parametrize(
    ("stream", "error"),
    [
        ("unknown", "unsupported safety heartbeat stream"),
        ("", "unsupported safety heartbeat stream"),
    ],
)
def test_heartbeat_rejects_unknown_streams(stream: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        record_safety_heartbeat(stream, now=NOW)
