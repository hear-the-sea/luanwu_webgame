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
