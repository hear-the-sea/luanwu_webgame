from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .calibration import (
    CalibrationStatus,
    CalibrationUnit,
    CalibrationVerdict,
    DistributionEvidence,
    calibration_thresholds_from_mapping,
    canonical_snapshot_digest,
    evaluate_calibration_evidence,
)
from .config import (
    V2_PRESTIGE_BAND_NAMES,
    VirtualPlayerConfigError,
    VirtualPlayerV2Config,
    load_virtual_player_v2_config,
)
from .gate_d2_candidate_artifact import (
    GATE_D2_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
    GATE_D2_GENERATOR_VERSION,
    GATE_D2_METRIC_ALGORITHM_VERSION,
    GateD2CandidateArtifactError,
    load_gate_d2_candidate_artifact,
)
from .gate_d2_metrics import recompute_gate_d2_candidate_evidence
from .reference_snapshot_catalog import (
    ReferenceSnapshotCatalogError,
    load_configured_reference_snapshot,
    load_strict_json_document,
    resolve_project_data_json_path,
)

GATE_D2_CANDIDATE_REPORT_SCHEMA_VERSION = 3
GATE_D2_CANDIDATE_REPORT_DIRECTORY = "data/virtual_player_gate_d2_candidate_reports"
_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "generator_version",
        "metric_algorithm_version",
        "candidate_artifact_schema_version",
        "unit",
        "policy_checksum",
        "reference_snapshot_digest",
        "candidate_snapshot_digest",
        "metrics",
    }
)
_UNIT_FIELDS = frozenset({"policy_version", "reference_snapshot_version", "prestige_band"})
_EVIDENCE_METRIC_FIELDS = tuple(
    field.name for field in fields(DistributionEvidence) if field.name not in {"unit", "reference_snapshot_digest"}
)
_EVIDENCE_METRIC_FIELD_SET = frozenset(_EVIDENCE_METRIC_FIELDS)


class GateD2AcceptanceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GateD2CandidateReport:
    schema_version: int
    generator_version: int
    metric_algorithm_version: int
    candidate_artifact_schema_version: int
    artifact_path: str
    digest: str
    policy_checksum: object
    candidate_snapshot_digest: object
    evidence: DistributionEvidence


@dataclass(frozen=True, slots=True)
class GateD2AcceptanceResult:
    unit: CalibrationUnit
    candidate_report_path: str
    candidate_artifact_path: str
    candidate_artifact_digest: str
    policy_checksum: str
    reference_snapshot_digest: str
    evidence_schema_version: int
    evidence_digest: str
    verdict: CalibrationVerdict

    @property
    def passed(self) -> bool:
        return self.verdict.passed


def _strict_fields(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unknown:
        details.append(f"unknown {', '.join(unknown)}")
    raise GateD2AcceptanceError(f"{label} has {'; '.join(details)}")


def _parse_positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GateD2AcceptanceError(f"{label} must be a positive integer")
    return value


def _parse_unit(value: Any) -> CalibrationUnit:
    if not isinstance(value, Mapping):
        raise GateD2AcceptanceError("Gate D2 candidate report unit must be a mapping")
    _strict_fields(value, _UNIT_FIELDS, label="Gate D2 candidate report unit")
    prestige_band = value["prestige_band"]
    if not isinstance(prestige_band, str) or prestige_band not in V2_PRESTIGE_BAND_NAMES:
        raise GateD2AcceptanceError("Gate D2 candidate report prestige_band is invalid")
    return CalibrationUnit(
        policy_version=_parse_positive_int(
            value["policy_version"],
            label="Gate D2 candidate report policy_version",
        ),
        reference_snapshot_version=_parse_positive_int(
            value["reference_snapshot_version"],
            label="Gate D2 candidate report reference_snapshot_version",
        ),
        prestige_band=prestige_band,
    )


def _validate_expected_unit(unit: CalibrationUnit) -> None:
    if not isinstance(unit, CalibrationUnit):
        raise GateD2AcceptanceError("Gate D2 unit must be a CalibrationUnit")
    _parse_positive_int(unit.policy_version, label="Gate D2 policy_version")
    _parse_positive_int(
        unit.reference_snapshot_version,
        label="Gate D2 reference_snapshot_version",
    )
    if unit.prestige_band not in V2_PRESTIGE_BAND_NAMES:
        raise GateD2AcceptanceError("Gate D2 prestige_band is invalid")


def gate_d2_candidate_report_path(unit: CalibrationUnit) -> str:
    _validate_expected_unit(unit)
    return (
        f"{GATE_D2_CANDIDATE_REPORT_DIRECTORY}/"
        f"policy-{unit.policy_version}/"
        f"snapshot-{unit.reference_snapshot_version}/"
        f"{unit.prestige_band}.json"
    )


def load_gate_d2_candidate_report(
    unit: CalibrationUnit,
    *,
    project_root: Path | None = None,
) -> GateD2CandidateReport:
    artifact_path = gate_d2_candidate_report_path(unit)
    try:
        resolved = resolve_project_data_json_path(
            artifact_path,
            project_root=project_root,
        )
        raw = load_strict_json_document(
            resolved,
            label="Gate D2 candidate report",
        )
    except ReferenceSnapshotCatalogError as exc:
        raise GateD2AcceptanceError(str(exc)) from exc
    _strict_fields(raw, _REPORT_FIELDS, label="Gate D2 candidate report")
    schema_version = raw["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != GATE_D2_CANDIDATE_REPORT_SCHEMA_VERSION
    ):
        raise GateD2AcceptanceError(f"unsupported Gate D2 candidate report schema version {schema_version!r}")
    generator_version = _parse_positive_int(
        raw["generator_version"],
        label="Gate D2 candidate report generator_version",
    )
    if generator_version != GATE_D2_GENERATOR_VERSION:
        raise GateD2AcceptanceError(f"unsupported Gate D2 generator version {generator_version!r}")
    metric_algorithm_version = _parse_positive_int(
        raw["metric_algorithm_version"],
        label="Gate D2 candidate report metric_algorithm_version",
    )
    if metric_algorithm_version != GATE_D2_METRIC_ALGORITHM_VERSION:
        raise GateD2AcceptanceError(
            "unsupported Gate D2 candidate metric algorithm version " f"{metric_algorithm_version!r}"
        )
    candidate_artifact_schema_version = _parse_positive_int(
        raw["candidate_artifact_schema_version"],
        label="Gate D2 candidate report candidate_artifact_schema_version",
    )
    if candidate_artifact_schema_version != GATE_D2_CANDIDATE_ARTIFACT_SCHEMA_VERSION:
        raise GateD2AcceptanceError(
            "unsupported Gate D2 candidate artifact schema version " f"{candidate_artifact_schema_version!r}"
        )
    candidate_snapshot_digest = raw["candidate_snapshot_digest"]
    if not _is_lower_sha256(candidate_snapshot_digest):
        raise GateD2AcceptanceError(
            "Gate D2 candidate report candidate_snapshot_digest must be a lowercase SHA-256 digest"
        )
    metrics = raw["metrics"]
    if not isinstance(metrics, Mapping):
        raise GateD2AcceptanceError("Gate D2 candidate report metrics must be a mapping")
    _strict_fields(
        metrics,
        _EVIDENCE_METRIC_FIELD_SET,
        label="Gate D2 candidate report metrics",
    )
    reported_unit = _parse_unit(raw["unit"])
    evidence_values = {field_name: metrics[field_name] for field_name in _EVIDENCE_METRIC_FIELDS}
    evidence = DistributionEvidence(
        unit=reported_unit,
        reference_snapshot_digest=raw["reference_snapshot_digest"],
        **evidence_values,
    )
    return GateD2CandidateReport(
        schema_version=schema_version,
        generator_version=generator_version,
        metric_algorithm_version=metric_algorithm_version,
        candidate_artifact_schema_version=candidate_artifact_schema_version,
        artifact_path=artifact_path,
        digest=_candidate_report_digest(raw),
        policy_checksum=raw["policy_checksum"],
        candidate_snapshot_digest=candidate_snapshot_digest,
        evidence=evidence,
    )


def _is_lower_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _candidate_report_digest(raw: Mapping[str, Any]) -> str:
    try:
        return canonical_snapshot_digest(raw)
    except (OverflowError, TypeError, ValueError) as exc:
        raise GateD2AcceptanceError("Gate D2 candidate report is not canonical JSON data") from exc


def _merge_workflow_reasons(
    verdict: CalibrationVerdict,
    *,
    incomplete: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
) -> CalibrationVerdict:
    reason_codes = tuple(dict.fromkeys((*verdict.reason_codes, *incomplete, *failed)))
    if verdict.status is CalibrationStatus.INCOMPLETE or incomplete:
        status = CalibrationStatus.INCOMPLETE
    elif verdict.status is CalibrationStatus.FAILED or failed:
        status = CalibrationStatus.FAILED
    else:
        status = CalibrationStatus.PASSED
    return CalibrationVerdict(
        unit=verdict.unit,
        status=status,
        reason_codes=reason_codes,
    )


def evaluate_gate_d2_acceptance(
    unit: CalibrationUnit,
    *,
    config: VirtualPlayerV2Config | None = None,
    project_root: Path | None = None,
) -> GateD2AcceptanceResult:
    _validate_expected_unit(unit)
    try:
        resolved_config = config or load_virtual_player_v2_config()
        if resolved_config is None:
            raise GateD2AcceptanceError("virtual-player V2 configuration is unavailable")
        policy = resolved_config.policy(unit.policy_version)
        snapshot_entry = resolved_config.reference_snapshot_catalog[unit.reference_snapshot_version]
        snapshot = load_configured_reference_snapshot(
            unit.reference_snapshot_version,
            config=resolved_config,
            project_root=project_root,
        )
        snapshot_band = snapshot.band(unit.prestige_band)
    except KeyError as exc:
        raise GateD2AcceptanceError(
            f"reference snapshot version {unit.reference_snapshot_version} is not cataloged"
        ) from exc
    except (ReferenceSnapshotCatalogError, VirtualPlayerConfigError) as exc:
        raise GateD2AcceptanceError(str(exc)) from exc
    report = load_gate_d2_candidate_report(unit, project_root=project_root)
    try:
        approved_evidence = snapshot_entry.gate_d2_evidence[(unit.policy_version, unit.prestige_band)]
    except KeyError as exc:
        raise GateD2AcceptanceError(
            "Gate D2 candidate report is not registered in the reference snapshot catalog"
        ) from exc
    if approved_evidence.schema_version != report.schema_version:
        raise GateD2AcceptanceError("Gate D2 candidate report schema does not match the catalog")
    if approved_evidence.digest != report.digest:
        raise GateD2AcceptanceError("Gate D2 candidate report digest does not match the catalog")
    try:
        candidate_artifact = load_gate_d2_candidate_artifact(
            unit,
            project_root=project_root,
        )
        recomputed_evidence = recompute_gate_d2_candidate_evidence(
            candidate_artifact,
            expected_unit=unit,
            config=resolved_config,
            reference_band=snapshot_band,
            expected_reference_snapshot_digest=snapshot.digest,
        )
    except GateD2CandidateArtifactError as exc:
        raise GateD2AcceptanceError(str(exc)) from exc
    if report.generator_version != candidate_artifact.generator_provenance.generator_version:
        raise GateD2AcceptanceError("Gate D2 candidate report generator version does not match the candidate artifact")
    if report.metric_algorithm_version != candidate_artifact.metric_algorithm_version:
        raise GateD2AcceptanceError("Gate D2 candidate report metric algorithm does not match the candidate artifact")
    if report.candidate_artifact_schema_version != candidate_artifact.schema_version:
        raise GateD2AcceptanceError("Gate D2 candidate report artifact schema does not match the candidate artifact")
    if report.candidate_snapshot_digest != candidate_artifact.digest:
        raise GateD2AcceptanceError("Gate D2 candidate report snapshot digest does not match the candidate artifact")
    if report.evidence != recomputed_evidence:
        raise GateD2AcceptanceError(
            "Gate D2 candidate report metrics do not match the recomputed candidate artifact metrics"
        )
    try:
        thresholds = calibration_thresholds_from_mapping(policy.reference_calibration_thresholds)
        verdict = evaluate_calibration_evidence(
            unit,
            snapshot.digest,
            recomputed_evidence,
            minimum_profiles_per_cohort=(policy.reference_calibration_min_profiles_per_band),
            thresholds=thresholds,
        )
    except (TypeError, ValueError, VirtualPlayerConfigError) as exc:
        raise GateD2AcceptanceError(f"Gate D2 policy calibration thresholds are invalid: {exc}") from exc

    incomplete: list[str] = []
    failed: list[str] = []
    if not _is_lower_sha256(report.policy_checksum):
        incomplete.append("invalid_policy_checksum")
    elif report.policy_checksum != policy.checksum:
        failed.append("policy_checksum_mismatch")
    reference_profile_count = recomputed_evidence.reference_profile_count
    if (
        isinstance(reference_profile_count, int)
        and not isinstance(reference_profile_count, bool)
        and reference_profile_count != snapshot_band.profile_count
    ):
        failed.append("reference_profile_count_mismatch")
    verdict = _merge_workflow_reasons(
        verdict,
        incomplete=tuple(incomplete),
        failed=tuple(failed),
    )
    return GateD2AcceptanceResult(
        unit=unit,
        candidate_report_path=report.artifact_path,
        candidate_artifact_path=candidate_artifact.artifact_path,
        candidate_artifact_digest=candidate_artifact.digest,
        policy_checksum=policy.checksum,
        reference_snapshot_digest=snapshot.digest,
        evidence_schema_version=report.schema_version,
        evidence_digest=report.digest,
        verdict=verdict,
    )


__all__ = [
    "GATE_D2_CANDIDATE_REPORT_DIRECTORY",
    "GATE_D2_CANDIDATE_REPORT_SCHEMA_VERSION",
    "GateD2AcceptanceError",
    "GateD2AcceptanceResult",
    "GateD2CandidateReport",
    "evaluate_gate_d2_acceptance",
    "gate_d2_candidate_report_path",
    "load_gate_d2_candidate_report",
]
