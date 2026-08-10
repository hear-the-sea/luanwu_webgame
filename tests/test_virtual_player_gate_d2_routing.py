from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.utils import timezone

from gameplay.models import BotPolicyRelease, BotRuntimeRoutingState
from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core import gate_d2_acceptance_workflow
from gameplay.services.virtual_player_core.calibration import CalibrationUnit
from gameplay.services.virtual_player_core.config import policy_checksum
from gameplay.services.virtual_player_core.policy_registry import release_policy
from tests.test_virtual_player_gate_d2_acceptance_workflow import (
    ATTESTATION_KEY,
    ATTESTATION_KEY_ID,
    UNIT,
    _candidate_report,
    _configured_snapshot,
    _write_candidate_report,
)

pytestmark = pytest.mark.skip(reason="Gate D2 calibration routing retired after the policy 2 cutover")

MIDDLE_UNIT = CalibrationUnit(
    policy_version=1,
    reference_snapshot_version=3,
    prestige_band="middle",
)


def _route(unit: CalibrationUnit) -> dict[str, Any]:
    return {
        "policy_version": unit.policy_version,
        "reference_snapshot_version": unit.reference_snapshot_version,
        "prestige_band": unit.prestige_band,
    }


def _persisted_route(config, unit: CalibrationUnit) -> dict[str, Any]:
    snapshot = config.reference_snapshot_catalog[unit.reference_snapshot_version]
    evidence = snapshot.gate_d2_evidence[(unit.policy_version, unit.prestige_band)]
    return {
        **_route(unit),
        "policy_checksum": config.policy(unit.policy_version).checksum,
        "reference_snapshot_digest": snapshot.digest,
        "evidence_schema_version": evidence.schema_version,
        "evidence_digest": evidence.digest,
    }


def _configure_trusted_d2_files(
    *,
    monkeypatch: pytest.MonkeyPatch,
    settings,
    project_root: Path,
):
    settings.VIRTUAL_PLAYER_GATE_D2_ATTESTATION_KEYS = {ATTESTATION_KEY_ID: ATTESTATION_KEY}
    config, _reports = _configured_snapshot(
        project_root,
        units=(UNIT, MIDDLE_UNIT),
    )
    settings.BASE_DIR = project_root
    monkeypatch.setattr(
        gate_d2_acceptance_workflow,
        "load_virtual_player_v2_config",
        lambda: config,
    )
    monkeypatch.setattr(
        runtime_configs,
        "load_virtual_player_v2_config",
        lambda: config,
    )
    release_policy(
        version=config.policy().version,
        checksum=config.policy().checksum,
        payload=config.policy().payload,
    )
    return config


def _routing_state() -> BotRuntimeRoutingState:
    return BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        calibration_routes=[],
        revision=0,
    )


def test_persisted_calibration_route_requires_an_activation_proof() -> None:
    with pytest.raises(runtime_configs.RuntimeRoutingError, match="missing evidence"):
        runtime_configs.parse_calibration_routes([_route(UNIT)])


@pytest.mark.django_db
def test_routing_enables_and_disables_passed_bands_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    config = _configure_trusted_d2_files(
        monkeypatch=monkeypatch,
        settings=settings,
        project_root=tmp_path,
    )
    _write_candidate_report(
        tmp_path,
        _candidate_report(config, project_root=tmp_path, unit=UNIT),
        unit=UNIT,
    )
    _write_candidate_report(
        tmp_path,
        _candidate_report(config, project_root=tmp_path, unit=MIDDLE_UNIT),
        unit=MIDDLE_UNIT,
    )
    state = _routing_state()

    junior = runtime_configs.transition_virtual_player_routing(
        expected_revision=0,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_routes=[_route(UNIT)],
    )
    assert junior.revision == 1
    assert junior.calibration_routes == (runtime_configs.CalibrationRoute(**_persisted_route(config, UNIT)),)
    state.refresh_from_db()
    assert state.calibration_routes == [_persisted_route(config, UNIT)]

    both = runtime_configs.transition_virtual_player_routing(
        expected_revision=1,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_routes=[_route(UNIT), _route(MIDDLE_UNIT)],
    )
    assert both.revision == 2
    assert tuple(route.prestige_band for route in both.calibration_routes) == (
        "junior",
        "middle",
    )

    middle_only = runtime_configs.transition_virtual_player_routing(
        expected_revision=2,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_routes=[_route(MIDDLE_UNIT)],
    )
    state.refresh_from_db()
    assert middle_only.revision == state.revision == 3
    assert state.calibration_routes == [_persisted_route(config, MIDDLE_UNIT)]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure_kind",
    ("missing_report", "failed_report", "tampered_snapshot"),
)
def test_routing_fails_closed_without_matching_passed_catalog_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
    failure_kind: str,
) -> None:
    config = _configure_trusted_d2_files(
        monkeypatch=monkeypatch,
        settings=settings,
        project_root=tmp_path,
    )
    if failure_kind != "missing_report":
        report = _candidate_report(config, project_root=tmp_path, unit=UNIT)
        if failure_kind == "failed_report":
            report["metrics"]["hard_constraint_violations"] = 1
        _write_candidate_report(tmp_path, report, unit=UNIT)
    if failure_kind == "tampered_snapshot":
        entry = config.reference_snapshot_catalog[UNIT.reference_snapshot_version]
        snapshot_path = tmp_path / entry.artifact_path
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["bands"]["junior"]["profiles"][0]["guest_count"] = 999
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    state = _routing_state()

    with pytest.raises(runtime_configs.RuntimeRoutingGateBlocked, match="Gate D2"):
        runtime_configs.transition_virtual_player_routing(
            expected_revision=0,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="legacy_before_gate",
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
            calibration_routes=[_route(UNIT)],
        )

    state.refresh_from_db()
    assert state.revision == 0
    assert state.calibration_routes == []


@pytest.mark.django_db
def test_routing_revalidates_the_policy_release_when_adding_a_second_band(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    config = _configure_trusted_d2_files(
        monkeypatch=monkeypatch,
        settings=settings,
        project_root=tmp_path,
    )
    for unit in (UNIT, MIDDLE_UNIT):
        _write_candidate_report(
            tmp_path,
            _candidate_report(config, project_root=tmp_path, unit=unit),
            unit=unit,
        )
    state = _routing_state()
    runtime_configs.transition_virtual_player_routing(
        expected_revision=0,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_routes=[_route(UNIT)],
    )
    mismatched_payload = {"name": "mismatched-policy-release"}
    BotPolicyRelease.objects.filter(version=UNIT.policy_version).update(
        checksum=policy_checksum(mismatched_payload),
        payload=mismatched_payload,
    )

    with pytest.raises(
        runtime_configs.RuntimeRoutingGateBlocked,
        match="checksum does not match",
    ):
        runtime_configs.transition_virtual_player_routing(
            expected_revision=1,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="legacy_before_gate",
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
            calibration_routes=[_route(UNIT), _route(MIDDLE_UNIT)],
        )

    state.refresh_from_db()
    assert state.revision == 1
    assert state.calibration_routes == [_persisted_route(config, UNIT)]


@pytest.mark.django_db
def test_gate_d2_routing_dry_run_performs_zero_dml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    config = _configure_trusted_d2_files(
        monkeypatch=monkeypatch,
        settings=settings,
        project_root=tmp_path,
    )
    _write_candidate_report(
        tmp_path,
        _candidate_report(config, project_root=tmp_path, unit=UNIT),
        unit=UNIT,
    )
    state = _routing_state()
    dml_statements: list[str] = []

    def _capture_dml(execute, sql, params, many, context):
        statement = str(sql).lstrip().split(None, 1)[0].upper()
        if statement in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            dml_statements.append(str(sql))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(_capture_dml):
        summary = runtime_configs.transition_virtual_player_routing_operation(
            expected_revision=0,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="legacy_before_gate",
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
            calibration_routes=[_route(UNIT)],
        )

    state.refresh_from_db()
    assert summary.changed == 1
    assert summary.snapshot.revision == 1
    assert dml_statements == []
    assert state.revision == 0
    assert state.calibration_routes == []


@pytest.mark.django_db
def test_gate_d2_artifact_io_finishes_before_the_routing_row_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    config = _configure_trusted_d2_files(
        monkeypatch=monkeypatch,
        settings=settings,
        project_root=tmp_path,
    )
    _write_candidate_report(
        tmp_path,
        _candidate_report(config, project_root=tmp_path, unit=UNIT),
        unit=UNIT,
    )
    _routing_state()
    events: list[str] = []
    original_evaluate = gate_d2_acceptance_workflow.evaluate_gate_d2_acceptance
    original_select_for_update = QuerySet.select_for_update

    def _evaluate(*args, **kwargs):
        events.append("evidence_io")
        return original_evaluate(*args, **kwargs)

    def _select_for_update(queryset, *args, **kwargs):
        if queryset.model is BotRuntimeRoutingState:
            events.append("routing_lock")
        return original_select_for_update(queryset, *args, **kwargs)

    monkeypatch.setattr(
        gate_d2_acceptance_workflow,
        "evaluate_gate_d2_acceptance",
        _evaluate,
    )
    monkeypatch.setattr(QuerySet, "select_for_update", _select_for_update)

    runtime_configs.transition_virtual_player_routing(
        expected_revision=0,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_routes=[_route(UNIT)],
    )

    assert events.index("evidence_io") < events.index("routing_lock")


@pytest.mark.django_db
def test_gate_d2_routing_rejects_a_retired_configured_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    config = _configure_trusted_d2_files(
        monkeypatch=monkeypatch,
        settings=settings,
        project_root=tmp_path,
    )
    _write_candidate_report(
        tmp_path,
        _candidate_report(config, project_root=tmp_path, unit=UNIT),
        unit=UNIT,
    )
    _routing_state()
    BotPolicyRelease.objects.filter(version=UNIT.policy_version).update(retired_at=timezone.now())

    with pytest.raises(runtime_configs.RuntimeRoutingGateBlocked, match="is retired"):
        runtime_configs.transition_virtual_player_routing(
            expected_revision=0,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="legacy_before_gate",
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
            calibration_routes=[_route(UNIT)],
        )
