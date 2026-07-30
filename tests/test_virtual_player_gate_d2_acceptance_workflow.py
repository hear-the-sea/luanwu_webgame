from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import fields, replace
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from gameplay.services.virtual_player_core.calibration import (
    CalibrationStatus,
    CalibrationUnit,
    canonical_snapshot_digest,
)
from gameplay.services.virtual_player_core.config import (
    V2_PRESTIGE_BAND_NAMES,
    VirtualPlayerConfigError,
    parse_bot_development_v2,
)
from gameplay.services.virtual_player_core.gate_d2_acceptance_workflow import (
    GATE_D2_CANDIDATE_REPORT_SCHEMA_VERSION,
    GateD2AcceptanceError,
    evaluate_gate_d2_acceptance,
    gate_d2_candidate_report_path,
)
from gameplay.services.virtual_player_core.gate_d2_candidate_artifact import (
    GATE_D2_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
    GATE_D2_GENERATOR_ENTRYPOINT,
    GATE_D2_GENERATOR_ID,
    GATE_D2_GENERATOR_SOURCE_FILES,
    GATE_D2_GENERATOR_VERSION,
    GATE_D2_METRIC_ALGORITHM_VERSION,
    GATE_D2_SAMPLE_ORDER,
    GateD2CandidateArtifactError,
    build_gate_d2_generator_attestation,
    current_gate_d2_generator_source_state,
    gate_d2_attestation_payload_digest,
    gate_d2_candidate_artifact_path,
    gate_d2_source_bundle_digest,
    load_gate_d2_candidate_artifact,
)
from gameplay.services.virtual_player_core.gate_d2_metrics import recompute_gate_d2_candidate_evidence
from gameplay.services.virtual_player_core.projection import calculate_guest_arena_power
from gameplay.services.virtual_player_core.reference_snapshot_catalog import load_configured_reference_snapshot
from gameplay.services.virtual_player_core.reference_snapshots import CORE_BUILDING_KEYS
from tests.yaml_schema_new_configs.virtual_players import _minimal_v2_config, _refresh_target_policy_checksum

UNIT = CalibrationUnit(
    policy_version=1,
    reference_snapshot_version=3,
    prestige_band="junior",
)
BAND_PRESTIGE = {
    "newbie": 100,
    "junior": 1_000,
    "middle": 4_000,
    "senior": 12_000,
    "veteran": 40_000,
    "elite": 80_000,
    "legend": 160_000,
    "mythic": 280_000,
}
ATTESTATION_KEY_ID = "gate-d2-test-key"
ATTESTATION_KEY = "gate-d2-test-secret-0123456789-0123456789"


@pytest.fixture(autouse=True)
def _trusted_gate_d2_attestation_key(settings) -> None:
    settings.VIRTUAL_PLAYER_GATE_D2_ATTESTATION_KEYS = {ATTESTATION_KEY_ID: ATTESTATION_KEY}


def _business_key(prefix: str, *, band: str, index: int) -> str:
    digest = sha256(f"{prefix}:3:{band}:{index}".encode("ascii")).hexdigest()
    return f"{prefix}:{digest}"


def _template_catalog(*, profile_count: int) -> dict:
    return {
        "guest_templates": [{"key": "guest-civil", "rarity": "common", "archetype": "civil"}],
        "equipment_templates": [{"key": "training-sword", "rarity": "common", "slot": "weapon"}],
        "skill_templates": [
            {
                "key": f"skill-{index:04d}",
                "kind": "active",
                "rarity": "common",
            }
            for index in range(profile_count)
        ],
        "guard_templates": [{"key": "guard-dao", "class": "dao"}],
        "troop_templates": [{"key": "troop-dao", "class": "dao"}],
        "building_templates": [
            {"key": "granary", "max_level": 100},
            {"key": "silver_vault", "max_level": 100},
        ],
        "resource_keys": ["grain", "silver"],
    }


def _raw_profile(
    *,
    band: str,
    index: int,
    prefix: str,
    candidate_archetype: str | None = None,
) -> dict:
    building_shift = 2 if candidate_archetype == "rich" else 0
    force_shift = 2 if candidate_archetype == "dojo" else 0
    troop_shift = 20 if candidate_archetype == "guard" else 0
    if candidate_archetype == "abandoned":
        force_shift = -90
    base_hp = 2_600 if candidate_archetype == "abandoned" else 3_000
    guest_level = 10 + index
    equipment_level = 8 + index
    guests = [
        {
            "ordinal": ordinal,
            "template": "guest-civil",
            "level": guest_level + ordinal,
            "rarity": "common",
            "archetype": "civil",
            "base_hp": base_hp,
            "force": 100 + index + force_shift + ordinal,
            "intellect": 80 + index + ordinal,
            "defense": 60 + index + ordinal,
            "hp_bonus": 10 + index + ordinal,
            "equipment": [
                {
                    "template": "training-sword",
                    "level": equipment_level + ordinal,
                    "rarity": "common",
                    "slot": "weapon",
                }
            ],
            "skills": [
                {
                    "key": f"skill-{index:04d}",
                    "kind": "active",
                    "rarity": "common",
                }
            ],
        }
        for ordinal in range(3)
    ]
    profile = {
        "business_key": _business_key(prefix, band=band, index=index),
        "prestige": BAND_PRESTIGE[band] + index,
        "account_age_days": 120 + index,
        "days_since_last_strength_increase": 10,
        "buildings": [
            {"key": "granary", "level": 10 + index + building_shift},
            {"key": "silver_vault", "level": 10 + 2 * index + building_shift},
        ],
        "guests": guests,
        "guards": [{"template": "guard-dao", "class": "dao", "level": 1 + index}],
        "troops": [
            {
                "template": "troop-dao",
                "class": "dao",
                "count": 1_000 + 10 * index + troop_shift,
            }
        ],
        "resources": [
            {"key": "grain", "amount": 100 + index, "capacity": 10_000},
            {"key": "silver", "amount": 200 + index, "capacity": 10_000},
        ],
    }
    if candidate_archetype is not None:
        profile["archetype"] = candidate_archetype
    return profile


def _snapshot_profile(raw_profile: Mapping[str, Any]) -> dict[str, int | str]:
    buildings = raw_profile["buildings"]
    guests = raw_profile["guests"]
    troops = raw_profile["troops"]
    assert isinstance(buildings, list)
    assert isinstance(guests, list)
    assert isinstance(troops, list)
    arena_power = sum(
        calculate_guest_arena_power(
            force=int(guest["force"]),
            intellect=int(guest["intellect"]),
            defense=int(guest["defense"]),
            hp_bonus=int(guest["hp_bonus"]),
            archetype=str(guest["archetype"]),
            base_hp=int(guest["base_hp"]),
        )
        for guest in guests
    )
    return {
        "business_key": str(raw_profile["business_key"]),
        "prestige": int(raw_profile["prestige"]),
        "core_building_level": max(
            int(building["level"]) for building in buildings if building["key"] in CORE_BUILDING_KEYS
        ),
        "guest_count": len(guests),
        "max_guest_level": max(int(guest["level"]) for guest in guests),
        "arena_lineup_power": arena_power,
        "troop_total": sum(int(troop["count"]) for troop in troops),
    }


def _snapshot_payload(*, profile_count: int = 30) -> dict:
    return {
        "schema_version": 1,
        "reference_snapshot_version": 3,
        "bands": {
            band: {
                "profile_count": profile_count,
                "profiles": sorted(
                    (
                        _snapshot_profile(
                            _raw_profile(
                                band=band,
                                index=index,
                                prefix="human-ref-v3",
                            )
                        )
                        for index in range(profile_count)
                    ),
                    key=lambda profile: str(profile["business_key"]),
                ),
            }
            for band in V2_PRESTIGE_BAND_NAMES
        },
    }


def _write_snapshot(project_root: Path, *, profile_count: int = 30) -> dict:
    snapshot = _snapshot_payload(profile_count=profile_count)
    relative_path = "data/virtual_player_reference_snapshots/v3.json"
    path = project_root / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(snapshot, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    return {
        "schema_version": 1,
        "digest": canonical_snapshot_digest(snapshot),
        "artifact_path": relative_path,
    }


def _candidate_report(
    config,
    *,
    project_root: Path,
    unit: CalibrationUnit = UNIT,
    **metric_overrides: object,
) -> dict:
    snapshot = load_configured_reference_snapshot(
        unit.reference_snapshot_version,
        config=config,
        project_root=project_root,
    )
    artifact = load_gate_d2_candidate_artifact(unit, project_root=project_root)
    evidence = recompute_gate_d2_candidate_evidence(
        artifact,
        expected_unit=unit,
        config=config,
        reference_band=snapshot.band(unit.prestige_band),
        expected_reference_snapshot_digest=snapshot.digest,
    )
    metrics = {
        field.name: getattr(evidence, field.name)
        for field in fields(type(evidence))
        if field.name not in {"unit", "reference_snapshot_digest"}
    }
    metrics.update(metric_overrides)
    return {
        "schema_version": GATE_D2_CANDIDATE_REPORT_SCHEMA_VERSION,
        "generator_version": GATE_D2_GENERATOR_VERSION,
        "metric_algorithm_version": GATE_D2_METRIC_ALGORITHM_VERSION,
        "candidate_artifact_schema_version": GATE_D2_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
        "unit": {
            "policy_version": unit.policy_version,
            "reference_snapshot_version": unit.reference_snapshot_version,
            "prestige_band": unit.prestige_band,
        },
        "policy_checksum": config.policy(unit.policy_version).checksum,
        "reference_snapshot_digest": config.reference_snapshot_catalog[unit.reference_snapshot_version].digest,
        "candidate_snapshot_digest": artifact.digest,
        "metrics": metrics,
    }


def _write_generator_source_fixture(project_root: Path) -> None:
    for relative_path in GATE_D2_GENERATOR_SOURCE_FILES:
        path = project_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# gate-d2 fixture source: {relative_path}\n", encoding="utf-8")


def _candidate_artifact(
    config,
    *,
    project_root: Path,
    unit: CalibrationUnit,
    profile_count: int,
) -> dict:
    archetypes = ("balanced", "rich", "dojo", "guard", "abandoned")
    reference_profiles = sorted(
        (
            _raw_profile(
                band=unit.prestige_band,
                index=index,
                prefix=f"human-ref-v{unit.reference_snapshot_version}",
            )
            for index in range(profile_count)
        ),
        key=lambda profile: profile["business_key"],
    )
    candidate_profiles = sorted(
        (
            _raw_profile(
                band=unit.prestige_band,
                index=index,
                prefix=f"candidate-v{GATE_D2_GENERATOR_VERSION}",
                candidate_archetype=archetypes[index % len(archetypes)],
            )
            for index in range(profile_count)
        ),
        key=lambda profile: profile["business_key"],
    )
    v1_profiles = sorted(
        (
            _raw_profile(
                band=unit.prestige_band,
                index=index,
                prefix="v1-baseline",
            )
            for index in range(profile_count)
        ),
        key=lambda profile: profile["business_key"],
    )
    inactive_profiles = sorted(
        (
            _raw_profile(
                band=unit.prestige_band,
                index=index,
                prefix="inactive-human-ref",
            )
            for index in range(profile_count)
        ),
        key=lambda profile: profile["business_key"],
    )
    catalog = _template_catalog(profile_count=profile_count)
    source_state = current_gate_d2_generator_source_state(project_root=project_root)
    cohorts = {
        "reference_profiles": reference_profiles,
        "candidate_profiles": candidate_profiles,
        "v1_profiles": v1_profiles,
        "inactive_reference_profiles": inactive_profiles,
    }
    policy = config.policy(unit.policy_version)
    reference_digest = config.reference_snapshot_catalog[unit.reference_snapshot_version].digest
    artifact = {
        "schema_version": GATE_D2_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
        "metric_algorithm_version": GATE_D2_METRIC_ALGORITHM_VERSION,
        "unit": {
            "policy_version": unit.policy_version,
            "reference_snapshot_version": unit.reference_snapshot_version,
            "prestige_band": unit.prestige_band,
        },
        "policy_checksum": policy.checksum,
        "reference_snapshot_digest": reference_digest,
        "generator_provenance": {
            "generator_id": GATE_D2_GENERATOR_ID,
            "generator_version": GATE_D2_GENERATOR_VERSION,
            "entrypoint": GATE_D2_GENERATOR_ENTRYPOINT,
            "engine_version": config.engine_version,
            "rng_version": config.rng_version,
            "plan_schema_version": config.plan_schema_version,
            "policy_checksum": policy.checksum,
            "reference_snapshot_digest": reference_digest,
            "template_catalog_digest": canonical_snapshot_digest(catalog),
            "cohort_digests": {name: canonical_snapshot_digest(payload) for name, payload in cohorts.items()},
            "root_seed_digest": sha256(f"gate-d2-seed:{unit}".encode("ascii")).hexdigest(),
            "sample_order": GATE_D2_SAMPLE_ORDER,
            "source_state": {
                "algorithm": "sha256_canonical_manifest",
                "bundle_digest": gate_d2_source_bundle_digest(source_state),
                "files": [{"path": item.path, "sha256": item.sha256} for item in source_state],
            },
        },
        "template_catalog": catalog,
        **cohorts,
    }
    _resign_candidate_artifact(artifact)
    return artifact


def _resign_candidate_artifact(payload: dict) -> None:
    payload["generator_provenance"]["attestation"] = build_gate_d2_generator_attestation(
        payload,
        key_id=ATTESTATION_KEY_ID,
        key=ATTESTATION_KEY,
    )


def _write_candidate_artifact(
    project_root: Path,
    payload: dict,
    *,
    unit: CalibrationUnit = UNIT,
) -> Path:
    path = project_root / gate_d2_candidate_artifact_path(unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    return path


def _refresh_candidate_cohort_digest(payload: dict) -> None:
    payload["generator_provenance"]["cohort_digests"]["candidate_profiles"] = canonical_snapshot_digest(
        payload["candidate_profiles"]
    )


def _config_with_registered_report(config, report: dict):
    snapshot_version = UNIT.reference_snapshot_version
    evidence_key = (UNIT.policy_version, UNIT.prestige_band)
    snapshot_entry = config.reference_snapshot_catalog[snapshot_version]
    evidence = replace(
        snapshot_entry.gate_d2_evidence[evidence_key],
        schema_version=int(report["schema_version"]),
        digest=canonical_snapshot_digest(report),
    )
    updated_snapshot = replace(
        snapshot_entry,
        gate_d2_evidence=MappingProxyType({evidence_key: evidence}),
    )
    return replace(
        config,
        reference_snapshot_catalog=MappingProxyType({snapshot_version: updated_snapshot}),
    )


def _configured_snapshot(
    project_root: Path,
    *,
    units: Iterable[CalibrationUnit] = (UNIT,),
    profile_count: int = 30,
    policy_minimum: int = 30,
    policy_threshold_overrides: Mapping[str, object] | None = None,
    report_mutations: Mapping[CalibrationUnit, Callable[[dict], None]] | None = None,
    register_evidence: bool = True,
):
    _write_generator_source_fixture(project_root)
    raw_config = _minimal_v2_config()
    raw_config["policies"]["1"]["reference_calibration_min_profiles_per_band"] = policy_minimum
    if policy_threshold_overrides:
        raw_config["policies"]["1"]["reference_calibration_thresholds"].update(policy_threshold_overrides)
    _refresh_target_policy_checksum(raw_config)
    raw_config["reference_snapshot_catalog"] = {"3": _write_snapshot(project_root, profile_count=profile_count)}
    preliminary_config = parse_bot_development_v2(raw_config)
    reports: dict[CalibrationUnit, dict] = {}
    evidence_by_policy: dict[str, dict[str, dict[str, int | str]]] = {}
    for unit in units:
        artifact = _candidate_artifact(
            preliminary_config,
            project_root=project_root,
            unit=unit,
            profile_count=profile_count,
        )
        _write_candidate_artifact(project_root, artifact, unit=unit)
        report = _candidate_report(
            preliminary_config,
            project_root=project_root,
            unit=unit,
        )
        mutation = None if report_mutations is None else report_mutations.get(unit)
        if mutation is not None:
            mutation(report)
        reports[unit] = report
        if register_evidence:
            evidence_by_policy.setdefault(str(unit.policy_version), {})[unit.prestige_band] = {
                "schema_version": int(report["schema_version"]),
                "digest": canonical_snapshot_digest(report),
            }
    if evidence_by_policy:
        raw_config["reference_snapshot_catalog"]["3"]["gate_d2_evidence"] = evidence_by_policy
    return parse_bot_development_v2(raw_config), reports


def _write_candidate_report(
    project_root: Path,
    payload: dict,
    *,
    unit: CalibrationUnit = UNIT,
) -> Path:
    path = project_root / gate_d2_candidate_report_path(unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    return path


def test_gate_d2_workflow_loads_only_the_deterministic_unit_path_and_passes(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    _write_candidate_report(tmp_path, reports[UNIT])

    result = evaluate_gate_d2_acceptance(
        UNIT,
        config=config,
        project_root=tmp_path,
    )

    assert result.passed is True
    assert result.verdict.status is CalibrationStatus.PASSED
    assert result.verdict.reason_codes == ()
    assert result.candidate_report_path == (
        "data/virtual_player_gate_d2_candidate_reports/policy-1/snapshot-3/junior.json"
    )
    assert result.policy_checksum == config.policy(UNIT.policy_version).checksum
    assert result.reference_snapshot_digest == config.reference_snapshot_catalog[UNIT.reference_snapshot_version].digest
    approved_evidence = config.reference_snapshot_catalog[UNIT.reference_snapshot_version].gate_d2_evidence[
        (UNIT.policy_version, UNIT.prestige_band)
    ]
    assert result.evidence_schema_version == approved_evidence.schema_version
    assert result.evidence_digest == approved_evidence.digest


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        (
            lambda payload: payload.update(policy_checksum="f" * 64),
            "policy_checksum_mismatch",
        ),
    ),
)
def test_gate_d2_workflow_fails_only_the_mismatched_unit_evidence(
    tmp_path: Path,
    mutate,
    reason: str,
) -> None:
    config, reports = _configured_snapshot(
        tmp_path,
        report_mutations={UNIT: mutate},
    )
    _write_candidate_report(tmp_path, reports[UNIT])

    result = evaluate_gate_d2_acceptance(
        UNIT,
        config=config,
        project_root=tmp_path,
    )

    assert result.passed is False
    assert result.verdict.status is CalibrationStatus.FAILED
    assert reason in result.verdict.reason_codes


@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload["metrics"].update(reference_profile_count=29),
        lambda payload: payload["unit"].update(prestige_band="middle"),
    ),
)
def test_gate_d2_workflow_rejects_report_claims_that_raw_recomputation_disproves(
    tmp_path: Path,
    mutate,
) -> None:
    config, reports = _configured_snapshot(
        tmp_path,
        report_mutations={UNIT: mutate},
    )
    _write_candidate_report(tmp_path, reports[UNIT])

    with pytest.raises(GateD2AcceptanceError, match="recomputed candidate artifact"):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=config,
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda payload: payload.update(unknown=True), "unknown unknown"),
        (
            lambda payload: payload["metrics"].pop("js_divergence_bits"),
            "missing js_divergence_bits",
        ),
        (lambda payload: payload.update(schema_version=4), "unsupported"),
        (lambda payload: payload.update(schema_version=2.0), "unsupported"),
    ),
)
def test_gate_d2_workflow_rejects_a_non_strict_candidate_report(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    report = reports[UNIT]
    mutate(report)
    _write_candidate_report(tmp_path, report)

    with pytest.raises(GateD2AcceptanceError, match=message):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=config,
            project_root=tmp_path,
        )


def test_gate_d2_candidate_artifact_rejects_metric_algorithm_v1(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    artifact_path = tmp_path / gate_d2_candidate_artifact_path(UNIT)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["metric_algorithm_version"] = 1
    artifact_path.write_text(
        json.dumps(artifact, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    _write_candidate_report(tmp_path, reports[UNIT])

    with pytest.raises(
        GateD2AcceptanceError,
        match="unsupported Gate D2 metric algorithm version 1",
    ):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=config,
            project_root=tmp_path,
        )


def test_gate_d2_workflow_fails_closed_when_the_unit_report_is_missing(
    tmp_path: Path,
) -> None:
    config, _reports = _configured_snapshot(tmp_path)

    with pytest.raises(GateD2AcceptanceError, match="does not exist"):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=config,
            project_root=tmp_path,
        )


def test_gate_d2_workflow_rejects_an_unregistered_candidate_report(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(tmp_path, register_evidence=False)
    _write_candidate_report(tmp_path, reports[UNIT])

    with pytest.raises(GateD2AcceptanceError, match="not registered"):
        evaluate_gate_d2_acceptance(UNIT, config=config, project_root=tmp_path)


def test_gate_d2_workflow_rejects_candidate_report_tampering(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    report = reports[UNIT]
    report["metrics"]["normalized_wasserstein"] = 0.24
    _write_candidate_report(tmp_path, report)

    with pytest.raises(GateD2AcceptanceError, match="digest does not match"):
        evaluate_gate_d2_acceptance(UNIT, config=config, project_root=tmp_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda report: report.pop("generator_version"), "missing generator_version"),
        (
            lambda report: report.update(generator_version=0),
            "generator_version must be a positive integer",
        ),
        (
            lambda report: report.pop("candidate_snapshot_digest"),
            "missing candidate_snapshot_digest",
        ),
        (
            lambda report: report.update(candidate_snapshot_digest="f" * 63),
            "candidate_snapshot_digest must be a lowercase SHA-256 digest",
        ),
    ),
)
def test_gate_d2_workflow_rejects_missing_or_invalid_input_identity(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    report = reports[UNIT]
    mutate(report)
    _write_candidate_report(tmp_path, report)

    with pytest.raises(GateD2AcceptanceError, match=message):
        evaluate_gate_d2_acceptance(UNIT, config=config, project_root=tmp_path)


def test_gate_d2_workflow_applies_the_policy_specific_sample_minimum(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(
        tmp_path,
        profile_count=30,
        policy_minimum=31,
    )
    _write_candidate_report(tmp_path, reports[UNIT])

    result = evaluate_gate_d2_acceptance(
        UNIT,
        config=config,
        project_root=tmp_path,
    )

    assert result.verdict.status is CalibrationStatus.FAILED
    assert "sample_below_minimum:reference_profile_count" in result.verdict.reason_codes
    assert "sample_below_minimum:candidate_profile_count" in result.verdict.reason_codes


def test_gate_d2_workflow_applies_policy_versioned_distribution_thresholds(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(
        tmp_path,
        policy_threshold_overrides={"normalized_wasserstein_max": 0.02},
    )
    _write_candidate_report(tmp_path, reports[UNIT])

    result = evaluate_gate_d2_acceptance(
        UNIT,
        config=config,
        project_root=tmp_path,
    )

    assert result.verdict.status is CalibrationStatus.FAILED
    assert "threshold_exceeded:normalized_wasserstein" in result.verdict.reason_codes


def test_gate_d2_candidate_report_non_finite_json_is_a_domain_error(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    report = reports[UNIT]
    raw = json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True)
    metric = json.dumps(report["metrics"]["normalized_wasserstein"])
    raw = raw.replace(
        f'"normalized_wasserstein":{metric}',
        '"normalized_wasserstein":1e999',
    )
    path = tmp_path / gate_d2_candidate_report_path(UNIT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(GateD2AcceptanceError, match="not canonical JSON data"):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=config,
            project_root=tmp_path,
        )


def test_gate_d2_workflow_translates_config_loader_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_config_load():
        raise VirtualPlayerConfigError("invalid fixture config")

    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.gate_d2_acceptance_workflow.load_virtual_player_v2_config",
        _fail_config_load,
    )

    with pytest.raises(GateD2AcceptanceError, match="invalid fixture config"):
        evaluate_gate_d2_acceptance(UNIT)


def test_gate_d2_candidate_artifact_rejects_derived_claim_fields(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    artifact_path = tmp_path / gate_d2_candidate_artifact_path(UNIT)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["candidate_profiles"][0]["hard_constraint_violations"] = []
    artifact_path.write_text(
        json.dumps(artifact, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    _write_candidate_report(tmp_path, reports[UNIT])
    with pytest.raises(GateD2AcceptanceError, match="unknown hard_constraint_violations"):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=config,
            project_root=tmp_path,
        )


def test_gate_d2_attestation_rejects_synchronized_artifact_and_report_forgery(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    artifact_path = tmp_path / gate_d2_candidate_artifact_path(UNIT)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["candidate_profiles"][0]["business_key"] = f"candidate-v{GATE_D2_GENERATOR_VERSION}:" + "0" * 64
    artifact["candidate_profiles"].sort(key=lambda profile: profile["business_key"])
    artifact["template_catalog"]["skill_templates"].append(
        {"key": "unused-forged-skill", "kind": "active", "rarity": "common"}
    )
    artifact["template_catalog"]["skill_templates"].sort(key=lambda item: item["key"])
    artifact["generator_provenance"]["template_catalog_digest"] = canonical_snapshot_digest(
        artifact["template_catalog"]
    )
    artifact["generator_provenance"]["root_seed_digest"] = "f" * 64
    _refresh_candidate_cohort_digest(artifact)
    artifact["generator_provenance"]["attestation"]["payload_digest"] = gate_d2_attestation_payload_digest(artifact)
    artifact_path.write_text(
        json.dumps(artifact, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    forged_report = json.loads(json.dumps(reports[UNIT]))
    forged_report["candidate_snapshot_digest"] = canonical_snapshot_digest(artifact)
    forged_config = _config_with_registered_report(config, forged_report)
    _write_candidate_report(tmp_path, forged_report)

    with pytest.raises(GateD2AcceptanceError, match="generator attestation"):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=forged_config,
            project_root=tmp_path,
        )


def test_gate_d2_raw_hard_constraint_is_recomputed_and_fails_acceptance(
    tmp_path: Path,
) -> None:
    config, _reports = _configured_snapshot(tmp_path)
    artifact_path = tmp_path / gate_d2_candidate_artifact_path(UNIT)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["candidate_profiles"][0]["guests"][0]["level"] = 0
    _refresh_candidate_cohort_digest(artifact)
    _resign_candidate_artifact(artifact)
    artifact_path.write_text(
        json.dumps(artifact, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    report = _candidate_report(config, project_root=tmp_path, unit=UNIT)
    updated_config = _config_with_registered_report(config, report)
    _write_candidate_report(tmp_path, report)

    result = evaluate_gate_d2_acceptance(
        UNIT,
        config=updated_config,
        project_root=tmp_path,
    )
    assert result.verdict.status is CalibrationStatus.FAILED
    assert "threshold_exceeded:hard_constraint_violations" in result.verdict.reason_codes


def test_gate_d2_generator_source_drift_invalidates_candidate_artifact(
    tmp_path: Path,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    source_path = tmp_path / GATE_D2_GENERATOR_SOURCE_FILES[0]
    source_path.write_text("# drifted\n", encoding="utf-8")
    _write_candidate_report(tmp_path, reports[UNIT])

    with pytest.raises(GateD2AcceptanceError, match="source_state"):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=config,
            project_root=tmp_path,
        )


def test_gate_d2_candidate_artifact_fails_closed_without_a_trusted_attestation_key(
    tmp_path: Path,
    settings,
) -> None:
    config, reports = _configured_snapshot(tmp_path)
    settings.VIRTUAL_PLAYER_GATE_D2_ATTESTATION_KEYS = {}
    _write_candidate_report(tmp_path, reports[UNIT])

    with pytest.raises(GateD2AcceptanceError, match="not trusted"):
        evaluate_gate_d2_acceptance(
            UNIT,
            config=config,
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    "prestige_band",
    ("../escape", "", "unknown"),
)
def test_gate_d2_candidate_artifact_path_is_confined_to_known_bands(
    prestige_band: str,
) -> None:
    with pytest.raises(GateD2CandidateArtifactError, match="prestige_band"):
        gate_d2_candidate_artifact_path(
            CalibrationUnit(
                policy_version=1,
                reference_snapshot_version=3,
                prestige_band=prestige_band,
            )
        )
