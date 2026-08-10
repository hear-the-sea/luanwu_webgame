from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gameplay.models import BotRuntimeRoutingState
from gameplay.services import runtime_configs

HOURLY_END = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
DAILY_END = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)


def _routing_state(*, maintenance_mode: str, revision: int = 4):
    return BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=maintenance_mode,
        calibration_routes=[],
        revision=revision,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "maintenance_mode",
    [
        BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
    ],
)
def test_safety_decision_consumes_hourly_window_and_pauses_v2(
    maintenance_mode: str,
) -> None:
    state = _routing_state(maintenance_mode=maintenance_mode)

    result = runtime_configs.apply_virtual_player_safety_decision(
        expected_revision=4,
        window_id="hourly:2026-07-28T08:00:00Z",
        window_kind="hourly",
        window_end_at=HOURLY_END,
        should_pause=True,
        pause_reason="maintenance_failure_rate",
    )

    assert result.consumed is True
    assert result.paused is True
    assert result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_PAUSED
    assert result.snapshot.revision == 5
    assert result.snapshot.last_hourly_safety_window_end_at == HOURLY_END
    assert result.snapshot.last_pause_window_id == "hourly:2026-07-28T08:00:00Z"
    assert result.snapshot.pause_reason == "maintenance_failure_rate"
    state.refresh_from_db()
    assert state.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED
    assert state.safety_clean_window_kind == "hourly"


@pytest.mark.django_db
def test_safety_decision_advances_daily_cursor_without_pausing() -> None:
    _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
    )

    result = runtime_configs.apply_virtual_player_safety_decision(
        expected_revision=4,
        window_id="daily:2026-07-28",
        window_kind="daily",
        window_end_at=DAILY_END,
        should_pause=False,
    )

    assert result.consumed is True
    assert result.paused is False
    assert result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_ACTIVE
    assert result.snapshot.last_daily_safety_window_end_at == DAILY_END
    assert result.snapshot.last_pause_window_id == ""
    assert result.snapshot.revision == 5


@pytest.mark.django_db
def test_safety_pause_freezes_reserve_leases_inside_the_routing_transaction(monkeypatch) -> None:
    calls: list[str] = []
    _routing_state(maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE)
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.pause_virtual_reserve_member_leases",
        lambda: calls.append("pause") or 0,
    )

    runtime_configs.apply_virtual_player_safety_decision(
        expected_revision=4,
        window_id="hourly:2026-07-28T08:00:00Z",
        window_kind="hourly",
        window_end_at=HOURLY_END,
        should_pause=True,
        pause_reason="maintenance_failure_rate",
    )

    assert calls == ["pause"]


@pytest.mark.django_db
def test_safety_auto_resume_rearms_demands_before_translating_reserve_leases(monkeypatch, settings) -> None:
    calls: list[str] = []
    settings.VIRTUAL_PLAYER_SAFETY_AUTO_RESUME_CLEAN_WINDOWS = 1
    state = _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
    )
    state.pause_reason = "missing_finalized_hourly_window"
    state.safety_clean_window_kind = "hourly"
    state.paused_from_maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    state.save(
        update_fields=[
            "pause_reason",
            "safety_clean_window_kind",
            "paused_from_maintenance_mode",
            "updated_at",
        ]
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.resume_virtual_reserve_member_leases",
        lambda: calls.append("resume") or 0,
    )
    monkeypatch.setattr(
        runtime_configs,
        "_rearm_arena_demands_for_active_routing",
        lambda: calls.append("rearm"),
    )

    result = runtime_configs.apply_virtual_player_safety_decision(
        expected_revision=4,
        window_id="hourly:2026-07-28T09:00:00Z",
        window_kind="hourly",
        window_end_at=datetime(2026, 7, 28, 9, 0, tzinfo=UTC),
        should_pause=False,
        resume_if_healthy=True,
        expected_pause_reason="missing_finalized_hourly_window",
    )

    assert result.resumed is True
    assert calls == ["rearm", "resume"]


@pytest.mark.django_db
def test_planned_restart_resume_sequence_starts_without_a_window_kind() -> None:
    state = _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
    )
    state.pause_reason = runtime_configs.PLANNED_RESTART_PAUSE_REASON
    state.paused_from_maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    state.save(update_fields=["pause_reason", "paused_from_maintenance_mode", "updated_at"])

    result = runtime_configs.apply_virtual_player_safety_decision(
        expected_revision=4,
        window_id="hourly:2026-07-28T08:00:00Z",
        window_kind="hourly",
        window_end_at=HOURLY_END,
        should_pause=False,
        resume_if_healthy=True,
        expected_pause_reason=runtime_configs.PLANNED_RESTART_PAUSE_REASON,
    )

    assert result.consumed is True
    assert result.resumed is False
    assert result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_PAUSED
    state.refresh_from_db()
    assert state.safety_clean_window_kind == "hourly"
    assert state.safety_clean_window_streak == 1


@pytest.mark.django_db
def test_safety_decision_replay_is_idempotent_before_revision_check() -> None:
    state = _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
    )
    state.last_hourly_safety_window_end_at = HOURLY_END
    state.revision = 7
    state.save(
        update_fields=[
            "last_hourly_safety_window_end_at",
            "revision",
            "updated_at",
        ]
    )

    result = runtime_configs.apply_virtual_player_safety_decision(
        expected_revision=4,
        window_id="hourly:2026-07-28T08:00:00Z",
        window_kind="hourly",
        window_end_at=HOURLY_END,
        should_pause=False,
    )

    assert result.consumed is False
    assert result.snapshot.revision == 7
    state.refresh_from_db()
    assert state.revision == 7


@pytest.mark.django_db
def test_safety_decision_rejects_revision_conflict_for_new_window() -> None:
    _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
    )

    with pytest.raises(runtime_configs.RuntimeRoutingConflict, match="revision"):
        runtime_configs.apply_virtual_player_safety_decision(
            expected_revision=3,
            window_id="hourly:2026-07-28T08:00:00Z",
            window_kind="hourly",
            window_end_at=HOURLY_END,
            should_pause=False,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "pause_reason",
    [
        "planned_restart",
        "arena_shortage_baseline_missing:tournament:newbie",
        "arena_shortage_baseline_expired:tournament:newbie",
        (
            "heartbeat_incomplete:maintenance_attempt_emitter,"
            "heartbeat_incomplete:h01_callback_attempt_emitter,"
            "heartbeat_incomplete:arena_shortage_emitter,"
            "heartbeat_incomplete:safety_aggregator,"
            "heartbeat_incomplete:safety_monitor"
        ),
    ],
)
def test_recoverable_safety_pause_auto_resumes_after_clean_window_streak(pause_reason: str) -> None:
    state = _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        revision=4,
    )
    state.pause_reason = pause_reason
    state.safety_clean_window_kind = "hourly"
    state.paused_from_maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    state.save(
        update_fields=[
            "pause_reason",
            "safety_clean_window_kind",
            "paused_from_maintenance_mode",
            "updated_at",
        ]
    )

    for index, hour in enumerate((9, 10, 11), start=1):
        result = runtime_configs.apply_virtual_player_safety_decision(
            expected_revision=4 + index - 1,
            window_id=f"hourly:2026-07-28T{hour:02d}:00:00Z",
            window_kind="hourly",
            window_end_at=datetime(2026, 7, 28, hour, 0, tzinfo=UTC),
            should_pause=False,
            resume_if_healthy=True,
            expected_pause_reason=pause_reason,
        )
        assert result.consumed is True
        if index < 3:
            assert result.resumed is False
            assert result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_PAUSED
        else:
            assert result.resumed is True
            assert result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_ACTIVE
            assert result.snapshot.pause_reason == ""

    state.refresh_from_db()
    assert state.safety_clean_window_streak == 0
    assert state.safety_clean_window_kind == ""


@pytest.mark.django_db
def test_daily_pause_is_not_resumed_by_hourly_clean_windows() -> None:
    pause_reason = "missing_finalized_daily_window"
    state = _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        revision=4,
    )
    state.pause_reason = pause_reason
    state.safety_clean_window_kind = "daily"
    state.paused_from_maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    state.save(
        update_fields=[
            "pause_reason",
            "safety_clean_window_kind",
            "paused_from_maintenance_mode",
            "updated_at",
        ]
    )

    for index, hour in enumerate((9, 10, 11), start=1):
        result = runtime_configs.apply_virtual_player_safety_decision(
            expected_revision=4 + index - 1,
            window_id=f"hourly:2026-07-28T{hour:02d}:00:00Z",
            window_kind="hourly",
            window_end_at=datetime(2026, 7, 28, hour, 0, tzinfo=UTC),
            should_pause=False,
            resume_if_healthy=True,
            expected_pause_reason=pause_reason,
        )
        assert result.resumed is False
        assert result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_PAUSED

    state.refresh_from_db()
    assert state.safety_clean_window_streak == 0
    assert state.safety_clean_window_kind == "daily"


@pytest.mark.parametrize(
    "reason",
    [
        "missing_finalized_hourly_window",
        "missing_finalized_daily_window",
        "arena_shortage_baseline_missing:tournament:newbie",
        "arena_shortage_baseline_expired:tournament:newbie",
        "heartbeat_incomplete:maintenance_attempt_emitter",
        ("heartbeat_incomplete:maintenance_attempt_emitter," "heartbeat_incomplete:h01_callback_attempt_emitter"),
    ],
)
def test_recoverable_pause_reasons_are_recoverable(reason: str) -> None:
    assert runtime_configs.is_recoverable_safety_pause_reason(reason) is True
    assert runtime_configs.is_recoverable_safety_pause_reason(f"{reason},hard_constraint_violation") is False


@pytest.mark.django_db
def test_non_arena_pause_is_never_auto_resumed() -> None:
    state = _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        revision=4,
    )
    state.pause_reason = "maintenance_failure_rate"
    state.save(update_fields=["pause_reason", "updated_at"])

    result = runtime_configs.apply_virtual_player_safety_decision(
        expected_revision=4,
        window_id="hourly:2026-07-28T09:00:00Z",
        window_kind="hourly",
        window_end_at=datetime(2026, 7, 28, 9, 0, tzinfo=UTC),
        should_pause=False,
        resume_if_healthy=True,
        expected_pause_reason="maintenance_failure_rate",
    )

    assert result.resumed is False
    assert result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_PAUSED


@pytest.mark.django_db
def test_cutover_safety_pause_does_not_auto_resume_to_active() -> None:
    pause_reason = "arena_shortage_baseline_missing:tournament:newbie"
    state = _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
        revision=4,
    )

    pause_result = runtime_configs.apply_virtual_player_safety_decision(
        expected_revision=4,
        window_id="hourly:2026-07-28T08:00:00Z",
        window_kind="hourly",
        window_end_at=HOURLY_END,
        should_pause=True,
        pause_reason=pause_reason,
    )

    assert pause_result.paused is True
    assert pause_result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_PAUSED
    state.refresh_from_db()
    assert state.paused_from_maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER

    for index, hour in enumerate((10, 11, 12), start=1):
        result = runtime_configs.apply_virtual_player_safety_decision(
            expected_revision=4 + index,
            window_id=f"hourly:2026-07-28T{hour:02d}:00:00Z",
            window_kind="hourly",
            window_end_at=datetime(2026, 7, 28, hour, 0, tzinfo=UTC),
            should_pause=False,
            resume_if_healthy=True,
            expected_pause_reason=pause_reason,
        )
        assert result.resumed is False
        assert result.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_PAUSED
        assert result.snapshot.paused_from_maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER

    state.refresh_from_db()
    assert state.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED
    assert state.paused_from_maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("window_kind", "window_end_at", "should_pause", "pause_reason", "match"),
    [
        ("weekly", HOURLY_END, False, "", "window_kind"),
        ("hourly", datetime(2026, 7, 28, 9, 1, tzinfo=UTC), False, "", "aligned"),
        ("daily", HOURLY_END, False, "", "aligned"),
        ("hourly", HOURLY_END, True, "", "pause_reason"),
        ("hourly", HOURLY_END, False, "unexpected", "pause_reason"),
    ],
)
def test_safety_decision_rejects_invalid_contract(
    window_kind: str,
    window_end_at: datetime,
    should_pause: bool,
    pause_reason: str,
    match: str,
) -> None:
    _routing_state(
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
    )

    with pytest.raises(runtime_configs.RuntimeRoutingError, match=match):
        runtime_configs.apply_virtual_player_safety_decision(
            expected_revision=4,
            window_id="window-id",
            window_kind=window_kind,
            window_end_at=window_end_at,
            should_pause=should_pause,
            pause_reason=pause_reason,
        )
