from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from django.core.management import call_command

from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core.config import BootstrapMode, MaintenanceMode


def _summary() -> SimpleNamespace:
    return SimpleNamespace(
        scanned=1,
        locked=0,
        changed=1,
        skipped=0,
        failed=0,
        reasons=(),
        snapshot=runtime_configs.RuntimeRoutingSnapshot(
            bootstrap_mode=BootstrapMode.V2_ACTIVE,
            maintenance_mode=MaintenanceMode.V2_PAUSED,
            calibration_routes=(),
            revision=8,
            last_hourly_safety_window_end_at=None,
            last_daily_safety_window_end_at=None,
            last_pause_window_id="",
            pause_reason=runtime_configs.PLANNED_RESTART_PAUSE_REASON,
            paused_from_maintenance_mode=MaintenanceMode.V2_ACTIVE.value,
            persisted=True,
        ),
    )


def test_planned_restart_command_defaults_to_dry_run_and_delegates_apply(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return _summary()

    monkeypatch.setattr(
        runtime_configs,
        "prepare_virtual_player_planned_restart_operation",
        fake_prepare,
    )

    dry_run_output = StringIO()
    call_command(
        "prepare_virtual_player_planned_restart",
        expected_revision=7,
        stdout=dry_run_output,
        verbosity=0,
    )
    apply_output = StringIO()
    call_command(
        "prepare_virtual_player_planned_restart",
        expected_revision=7,
        apply=True,
        stdout=apply_output,
        verbosity=0,
    )

    assert calls == [
        {"expected_revision": 7, "apply": False},
        {"expected_revision": 7, "apply": True},
    ]
    assert "mode=dry-run" in dry_run_output.getvalue()
    assert "mode=apply" in apply_output.getvalue()


def test_planned_restart_service_uses_v2_active_compare_and_set_fence(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_transition(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return _summary()

    monkeypatch.setattr(
        runtime_configs,
        "transition_virtual_player_routing_operation",
        fake_transition,
    )

    runtime_configs.prepare_virtual_player_planned_restart_operation(
        expected_revision=7,
        apply=True,
    )

    assert calls == [
        {
            "expected_revision": 7,
            "bootstrap_mode": BootstrapMode.V2_ACTIVE,
            "maintenance_mode": MaintenanceMode.V2_PAUSED,
            "calibration_routes": None,
            "expected_bootstrap_mode": BootstrapMode.V2_ACTIVE,
            "expected_maintenance_mode": MaintenanceMode.V2_ACTIVE,
            "pause_reason": runtime_configs.PLANNED_RESTART_PAUSE_REASON,
            "apply": True,
        }
    ]
