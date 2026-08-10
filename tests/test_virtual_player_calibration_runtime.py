from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest
from django.db import connection, transaction

from gameplay.models import BotProfile, BotRuntimeRoutingState, Manor
from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core import bootstrap
from gameplay.services.virtual_player_core.calibration_runtime import load_active_calibration_reference
from gameplay.services.virtual_player_core.projection import ReferenceSource, SampleTier
from tests.test_virtual_player_gate_d2_acceptance_workflow import UNIT, _candidate_report, _write_candidate_report
from tests.test_virtual_player_gate_d2_routing import _configure_trusted_d2_files, _route, _routing_state
from tests.test_virtual_player_reference_snapshots_v2 import FIXED_NOW

pytestmark = pytest.mark.skip(
    reason="static calibration runtime retired; daily growth-control snapshots are authoritative"
)


def _activate_unit(
    *,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
):
    config = _configure_trusted_d2_files(
        monkeypatch=monkeypatch,
        settings=settings,
        project_root=project_root,
    )
    _write_candidate_report(
        project_root,
        _candidate_report(config, project_root=project_root, unit=UNIT),
        unit=UNIT,
    )
    _routing_state()
    routing = runtime_configs.transition_virtual_player_routing(
        expected_revision=0,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_routes=[_route(UNIT)],
    )
    return config, routing.calibration_routes[0]


@pytest.mark.django_db
def test_active_calibration_reference_revalidates_proof_with_zero_dml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    config, route = _activate_unit(
        project_root=tmp_path,
        monkeypatch=monkeypatch,
        settings=settings,
    )
    dml_statements: list[str] = []

    def _capture_dml(execute, sql, params, many, context):
        statement = str(sql).lstrip().split(None, 1)[0].upper()
        if statement in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            dml_statements.append(str(sql))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(_capture_dml):
        reference = load_active_calibration_reference(
            policy_version=UNIT.policy_version,
            policy_checksum=config.policy(UNIT.policy_version).checksum,
            prestige_band=UNIT.prestige_band,
            config=config,
        )

    assert reference is not None
    assert reference.route == route
    assert reference.profile_count == 30
    assert len(reference.candidates) == 30
    assert all(candidate.business_key.startswith("human-ref-v3:") for candidate in reference.candidates)
    assert dml_statements == []


@pytest.mark.django_db
def test_active_calibration_reference_rejects_a_hot_snapshot_rebind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    config, _route_proof = _activate_unit(
        project_root=tmp_path,
        monkeypatch=monkeypatch,
        settings=settings,
    )
    entry = config.reference_snapshot_catalog[UNIT.reference_snapshot_version]
    rebound_entry = replace(entry, digest="f" * 64)
    rebound_config = replace(
        config,
        reference_snapshot_catalog=MappingProxyType({UNIT.reference_snapshot_version: rebound_entry}),
    )

    reference = load_active_calibration_reference(
        policy_version=UNIT.policy_version,
        policy_checksum=config.policy(UNIT.policy_version).checksum,
        prestige_band=UNIT.prestige_band,
        config=rebound_config,
    )

    assert reference is None


@pytest.mark.django_db
def test_bootstrap_consumes_an_active_frozen_calibration_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
    game_data,
) -> None:
    legacy_config = bootstrap.load_virtual_player_config()
    config, route = _activate_unit(
        project_root=tmp_path,
        monkeypatch=monkeypatch,
        settings=settings,
    )
    monkeypatch.setattr(bootstrap, "load_virtual_player_v2_config", lambda: config)
    monkeypatch.setattr(bootstrap, "load_virtual_player_config", lambda: legacy_config)

    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        UNIT.prestige_band,
        BotProfile.Archetype.BALANCED,
        883_001,
        FIXED_NOW,
    )

    selection = plan.blueprint.reference_selection
    assert plan.bootstrap_mode == bootstrap.V2_BOOTSTRAP_MODE_REFERENCE_CALIBRATED
    assert plan.calibration_route == route
    assert selection.source is ReferenceSource.LOCAL
    assert selection.tier is SampleTier.SUFFICIENT
    assert selection.local_sample_count == 30
    assert selection.anchor is not None
    assert plan.projection.prestige == int(selection.anchor.strength.components["prestige"])
    assert plan.projection.building_level == int(selection.anchor.features["core_building_level"])
    assert plan.projection.guest_count == int(selection.anchor.features["guest_count"])


@pytest.mark.django_db
def test_bootstrap_falls_back_and_rejects_an_inflight_plan_after_snapshot_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
    game_data,
    django_user_model,
) -> None:
    legacy_config = bootstrap.load_virtual_player_config()
    config, _route_proof = _activate_unit(
        project_root=tmp_path,
        monkeypatch=monkeypatch,
        settings=settings,
    )
    monkeypatch.setattr(bootstrap, "load_virtual_player_v2_config", lambda: config)
    monkeypatch.setattr(bootstrap, "load_virtual_player_config", lambda: legacy_config)
    calibrated_plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        UNIT.prestige_band,
        BotProfile.Archetype.BALANCED,
        883_002,
        FIXED_NOW,
    )

    snapshot_path = tmp_path / config.reference_snapshot_catalog[UNIT.reference_snapshot_version].artifact_path
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["bands"][UNIT.prestige_band]["profiles"][0]["guest_count"] += 1
    snapshot_path.write_text(
        json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    cold_plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        UNIT.prestige_band,
        BotProfile.Archetype.BALANCED,
        883_003,
        FIXED_NOW,
    )
    assert cold_plan.bootstrap_mode == (bootstrap.V2_BOOTSTRAP_MODE_CONSERVATIVE_COLD_START)
    assert cold_plan.calibration_route is None

    counts_before = (
        django_user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    )
    dml_statements: list[str] = []

    def _capture_dml(execute, sql, params, many, context):
        statement = str(sql).lstrip().split(None, 1)[0].upper()
        if statement in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            dml_statements.append(str(sql))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(_capture_dml):
        with transaction.atomic():
            permit = bootstrap._issue_v2_bootstrap_population_permit(
                region=calibrated_plan.region,
                prestige_band=calibrated_plan.prestige_band,
            )
            with pytest.raises(bootstrap.V2BootstrapError, match="replan required"):
                bootstrap.create_virtual_player_v2(
                    plan=calibrated_plan,
                    population_permit=permit,
                    now=FIXED_NOW,
                )
    assert dml_statements == []
    assert (
        django_user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    ) == counts_before


@pytest.mark.parametrize(
    "drifted_field",
    (
        None,
        "policy_checksum",
        "reference_snapshot_digest",
        "evidence_schema_version",
        "evidence_digest",
    ),
    ids=(
        "route-removed",
        "policy-proof",
        "snapshot-proof",
        "evidence-schema-proof",
        "evidence-digest-proof",
    ),
)
@pytest.mark.django_db
def test_bootstrap_falls_back_and_rejects_an_inflight_plan_after_route_proof_drift(
    drifted_field: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
    game_data,
    django_user_model,
) -> None:
    legacy_config = bootstrap.load_virtual_player_config()
    config, route = _activate_unit(
        project_root=tmp_path,
        monkeypatch=monkeypatch,
        settings=settings,
    )
    monkeypatch.setattr(bootstrap, "load_virtual_player_v2_config", lambda: config)
    monkeypatch.setattr(bootstrap, "load_virtual_player_config", lambda: legacy_config)
    calibrated_plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        UNIT.prestige_band,
        BotProfile.Archetype.BALANCED,
        883_004,
        FIXED_NOW,
    )

    if drifted_field is None:
        drifted_routes: list[dict[str, int | str]] = []
    else:
        drifted_route = route.to_payload()
        if drifted_field == "evidence_schema_version":
            drifted_route[drifted_field] = route.evidence_schema_version + 1
        else:
            current_digest = str(drifted_route[drifted_field])
            drifted_route[drifted_field] = "0" * 64 if current_digest != "0" * 64 else "1" * 64
        drifted_routes = [drifted_route]
    BotRuntimeRoutingState.objects.filter(key=BotRuntimeRoutingState.GLOBAL_KEY).update(
        calibration_routes=drifted_routes
    )

    cold_plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        UNIT.prestige_band,
        BotProfile.Archetype.BALANCED,
        883_005,
        FIXED_NOW,
    )
    assert cold_plan.bootstrap_mode == (bootstrap.V2_BOOTSTRAP_MODE_CONSERVATIVE_COLD_START)
    assert cold_plan.calibration_route is None

    counts_before = (
        django_user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    )
    dml_statements: list[str] = []

    def _capture_dml(execute, sql, params, many, context):
        statement = str(sql).lstrip().split(None, 1)[0].upper()
        if statement in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            dml_statements.append(str(sql))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(_capture_dml):
        with transaction.atomic():
            permit = bootstrap._issue_v2_bootstrap_population_permit(
                region=calibrated_plan.region,
                prestige_band=calibrated_plan.prestige_band,
            )
            with pytest.raises(bootstrap.V2BootstrapError, match="replan required"):
                bootstrap.create_virtual_player_v2(
                    plan=calibrated_plan,
                    population_permit=permit,
                    now=FIXED_NOW,
                )
    assert dml_statements == []
    assert (
        django_user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    ) == counts_before
