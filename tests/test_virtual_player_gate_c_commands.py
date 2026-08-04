from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core import (
    external_reconciliation,
    gate_d1_exit_workflow,
    gate_e_cutover_workflow,
    policy_registry,
    profile_management,
)
from gameplay.services.virtual_player_core.config import BootstrapMode, MaintenanceMode


def _batch_summary() -> SimpleNamespace:
    return SimpleNamespace(
        scanned=2,
        locked=1,
        changed=1,
        skipped=0,
        failed=0,
        reasons=("z-last", "a-first"),
        last_profile_id=9,
    )


def _policy_summary() -> SimpleNamespace:
    summary = _batch_summary()
    summary.version = 1
    summary.checksum = "a" * 64
    return summary


def _routing_summary() -> SimpleNamespace:
    summary = _batch_summary()
    summary.snapshot = runtime_configs.RuntimeRoutingSnapshot(
        bootstrap_mode=BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=MaintenanceMode.LEGACY_BEFORE_GATE,
        calibration_routes=(),
        revision=0,
        last_hourly_safety_window_end_at=None,
        last_daily_safety_window_end_at=None,
        last_pause_window_id="",
        pause_reason="",
        paused_from_maintenance_mode="",
        persisted=False,
    )
    return summary


def _policy_rollout_summary() -> SimpleNamespace:
    summary = _batch_summary()
    summary.snapshot = runtime_configs.PolicyRolloutSnapshot(
        target_version=2,
        enabled=True,
        rollout_percent=25,
        revision=4,
        persisted=True,
    )
    return summary


def _gate_summary() -> SimpleNamespace:
    summary = _routing_summary()
    summary.evidence_id = "gate-test-evidence"
    summary.evidence_digest = "a" * 64
    summary.authorization_basis_digest = "b" * 64
    summary.runtime_eligible_v1_profiles = 0
    return summary


@pytest.mark.parametrize(
    ("command_name", "service_module", "service_name", "options", "summary_factory"),
    [
        (
            "release_virtual_player_policy",
            policy_registry,
            "release_configured_policy_operation",
            {"version": 1},
            _policy_summary,
        ),
        (
            "retire_virtual_player_policy",
            policy_registry,
            "retire_policy_release_operation",
            {"version": 1, "expected_checksum": "a" * 64},
            _policy_summary,
        ),
        (
            "enroll_virtual_players_v2",
            profile_management,
            "enroll_virtual_players_batch",
            {"after_id": 4, "batch_size": 25},
            _batch_summary,
        ),
        (
            "reclassify_virtual_player_prestige_bands",
            profile_management,
            "reclassify_virtual_player_prestige_bands_batch",
            {"after_id": 4, "batch_size": 25},
            _batch_summary,
        ),
        (
            "repair_virtual_player_rng",
            profile_management,
            "repair_virtual_player_rng",
            {
                "profile_id": 7,
                "expected_rng_version": 9,
                "target_rng_version": 1,
                "recovery_basis": "incident-123",
            },
            _batch_summary,
        ),
        (
            "repair_virtual_player_plan",
            profile_management,
            "repair_virtual_player_plan",
            {
                "profile_id": 7,
                "expected_plan_schema_version": 1,
                "recovery_basis": "incident-124",
            },
            _batch_summary,
        ),
        (
            "upgrade_virtual_player_policy",
            profile_management,
            "upgrade_virtual_player_policy_batch",
            {
                "expected_policy_version": 1,
                "expected_policy_checksum": "a" * 64,
                "target_policy_version": 2,
                "target_policy_checksum": "b" * 64,
                "after_id": 4,
                "batch_size": 25,
            },
            _batch_summary,
        ),
        (
            "rollout_virtual_player_policy",
            profile_management,
            "rollout_virtual_player_policy_batch",
            {
                "expected_revision": 3,
                "expected_policy_version": 1,
                "expected_policy_checksum": "a" * 64,
                "target_policy_checksum": "b" * 64,
                "after_id": 4,
                "batch_size": 25,
            },
            _batch_summary,
        ),
        (
            "requeue_virtual_player_reconciliation",
            external_reconciliation,
            "requeue_quarantined_reconciliation_operation",
            {
                "reconciliation_id": 11,
                "expected_failure_code": "profile_contract_error",
                "expected_attempt_count": 12,
                "recovery_basis": "incident-125",
            },
            _batch_summary,
        ),
        (
            "transition_virtual_player_routing",
            runtime_configs,
            "transition_virtual_player_routing_operation",
            {
                "expected_absent": True,
                "bootstrap_mode": "legacy_before_gate",
                "maintenance_mode": "legacy_before_gate",
            },
            _routing_summary,
        ),
        (
            "transition_virtual_player_policy_rollout",
            runtime_configs,
            "transition_virtual_player_policy_rollout_operation",
            {
                "expected_revision": 3,
                "expected_target_version": 1,
                "expected_disabled": True,
                "expected_rollout_percent": 0,
                "target_version": 2,
                "enable": True,
                "rollout_percent": 25,
            },
            _policy_rollout_summary,
        ),
    ],
)
def test_gate_c_commands_default_to_dry_run_and_delegate_apply(
    monkeypatch,
    command_name: str,
    service_module: Any,
    service_name: str,
    options: dict[str, Any],
    summary_factory: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_service(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return summary_factory()

    monkeypatch.setattr(service_module, service_name, fake_service)
    dry_run_stdout = StringIO()
    call_command(command_name, stdout=dry_run_stdout, verbosity=0, **options)
    apply_stdout = StringIO()
    call_command(command_name, stdout=apply_stdout, verbosity=0, apply=True, **options)

    assert calls[0]["apply"] is False
    assert calls[1]["apply"] is True
    assert "mode=dry-run scanned=2 locked=1 changed=1 skipped=0 failed=0 reasons=2" in dry_run_stdout.getvalue()
    assert "mode=apply scanned=2 locked=1 changed=1 skipped=0 failed=0 reasons=2" in apply_stdout.getvalue()
    assert dry_run_stdout.getvalue().index("reason=a-first") < dry_run_stdout.getvalue().index("reason=z-last")


def test_routing_command_parses_structured_routes_and_requires_expected_current_modes(
    monkeypatch,
) -> None:
    with pytest.raises(CommandError, match="expected-bootstrap-mode"):
        call_command(
            "transition_virtual_player_routing",
            expected_revision=3,
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
            verbosity=0,
        )

    calls: list[dict[str, Any]] = []

    def fake_transition(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return _routing_summary()

    monkeypatch.setattr(runtime_configs, "transition_virtual_player_routing_operation", fake_transition)
    route = '{"policy_version":1,"reference_snapshot_version":2,"prestige_band":"newbie"}'
    call_command(
        "transition_virtual_player_routing",
        expected_revision=3,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_route=[route],
        stdout=StringIO(),
        verbosity=0,
    )

    assert calls[0]["expected_revision"] == 3
    assert calls[0]["calibration_routes"] == (
        {
            "policy_version": 1,
            "reference_snapshot_version": 2,
            "prestige_band": "newbie",
        },
    )
    assert "approved_calibration_routes" not in calls[0]


def test_routing_command_rejects_duplicate_json_keys() -> None:
    duplicate_route = (
        '{"policy_version":1,"policy_version":2,' '"reference_snapshot_version":3,"prestige_band":"newbie"}'
    )

    with pytest.raises(CommandError, match="duplicate key 'policy_version'"):
        call_command(
            "transition_virtual_player_routing",
            expected_revision=0,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="legacy_before_gate",
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
            calibration_route=[duplicate_route],
            verbosity=0,
        )


@pytest.mark.parametrize(
    ("command_name", "service_module", "service_name"),
    (
        (
            "exit_virtual_player_gate_d1",
            gate_d1_exit_workflow,
            "exit_gate_d1_operation",
        ),
        (
            "enter_virtual_player_gate_e_cutover",
            gate_e_cutover_workflow,
            "enter_gate_e_cutover_operation",
        ),
        (
            "exit_virtual_player_gate_e",
            gate_e_cutover_workflow,
            "exit_gate_e_operation",
        ),
        (
            "resume_virtual_player_gate_e_cutover",
            gate_e_cutover_workflow,
            "resume_gate_e_cutover_operation",
        ),
    ),
)
def test_gate_transition_commands_delegate_explicit_authorization(
    command_name: str,
    service_module,
    service_name: str,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_operation(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return _gate_summary()

    monkeypatch.setattr(service_module, service_name, fake_operation)
    stdout = StringIO()
    call_command(
        command_name,
        expected_revision=9,
        authorization_basis="approved-change-record-42",
        apply=True,
        stdout=stdout,
        verbosity=0,
    )

    assert calls == [
        {
            "expected_revision": 9,
            "authorization_basis": "approved-change-record-42",
            "apply": True,
        }
    ]
    output = stdout.getvalue()
    assert "mode=apply" in output
    assert "evidence_digest=" + "a" * 64 in output
    assert "authorization_basis_digest=" + "b" * 64 in output
    assert "approved-change-record-42" not in output


@pytest.mark.parametrize(
    ("command_name", "service_module", "service_name"),
    (
        (
            "enter_virtual_player_gate_e_cutover",
            gate_e_cutover_workflow,
            "enter_gate_e_cutover_operation",
        ),
        (
            "exit_virtual_player_gate_e",
            gate_e_cutover_workflow,
            "exit_gate_e_operation",
        ),
        (
            "resume_virtual_player_gate_e_cutover",
            gate_e_cutover_workflow,
            "resume_gate_e_cutover_operation",
        ),
    ),
)
def test_gate_e_commands_forward_an_explicit_expected_git_commit(
    command_name: str,
    service_module,
    service_name: str,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    expected_git_commit = "c" * 40

    monkeypatch.setattr(
        service_module,
        service_name,
        lambda **kwargs: calls.append(kwargs) or _gate_summary(),
    )

    call_command(
        command_name,
        expected_revision=9,
        authorization_basis="approved-change-record-42",
        expected_git_commit=expected_git_commit,
        apply=False,
        verbosity=0,
    )

    assert calls == [
        {
            "expected_revision": 9,
            "authorization_basis": "approved-change-record-42",
            "apply": False,
            "expected_git_commit": expected_git_commit,
        }
    ]


@pytest.mark.parametrize(
    ("command_name", "service_name", "extra_args"),
    [
        ("release_virtual_player_policy", "release_configured_policy_operation", ()),
        (
            "retire_virtual_player_policy",
            "retire_policy_release_operation",
            ("--expected-checksum", "a" * 64),
        ),
    ],
)
def test_policy_commands_reserve_version_for_the_policy_version(
    monkeypatch,
    command_name: str,
    service_name: str,
    extra_args: tuple[str, ...],
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_service(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return _policy_summary()

    monkeypatch.setattr(policy_registry, service_name, fake_service)
    call_command(
        command_name,
        "--version",
        "1",
        *extra_args,
        stdout=StringIO(),
        verbosity=0,
    )

    assert calls == [
        {
            "version": 1,
            **({} if not extra_args else {"expected_checksum": "a" * 64}),
            "apply": False,
        }
    ]


@pytest.mark.parametrize(
    ("command_name", "options", "message"),
    [
        (
            "retire_virtual_player_policy",
            {"version": 1, "expected_checksum": "bad"},
            "SHA-256",
        ),
        ("enroll_virtual_players_v2", {"batch_size": 1001}, "between 1 and 1000"),
        (
            "repair_virtual_player_rng",
            {
                "profile_id": 1,
                "expected_rng_version": 2,
                "target_rng_version": 1,
                "recovery_basis": " ",
            },
            "must not be blank",
        ),
        (
            "requeue_virtual_player_reconciliation",
            {
                "reconciliation_id": 1,
                "expected_failure_code": "profile_contract_error",
                "expected_attempt_count": -1,
                "recovery_basis": "incident-125",
            },
            "between 1 and 12",
        ),
    ],
)
def test_gate_c_commands_reject_invalid_operational_preconditions(
    command_name: str,
    options: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(CommandError, match=message):
        call_command(command_name, verbosity=0, **options)


def test_gate_c_command_modules_do_not_own_orm_or_transactions() -> None:
    command_names = (
        "release_virtual_player_policy",
        "retire_virtual_player_policy",
        "enroll_virtual_players_v2",
        "reclassify_virtual_player_prestige_bands",
        "repair_virtual_player_rng",
        "repair_virtual_player_plan",
        "upgrade_virtual_player_policy",
        "rollout_virtual_player_policy",
        "requeue_virtual_player_reconciliation",
        "transition_virtual_player_routing",
        "transition_virtual_player_policy_rollout",
    )
    command_dir = Path("gameplay/management/commands")
    for command_name in command_names:
        source = (command_dir / f"{command_name}.py").read_text(encoding="utf-8")
        assert "gameplay.models" not in source
        assert ".objects" not in source
        assert "transaction" not in source
