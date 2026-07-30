from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from django.db import connection

PROJECT_ROOT = Path(__file__).resolve().parents[3]
GATE_A_MANIFEST_PATH = PROJECT_ROOT / "docs" / "virtual_player_gate_evidence_manifest_2026-07-30.yaml"
GATE_D1_EVIDENCE_PATH = PROJECT_ROOT / "docs" / "virtual_player_gate_d1_evidence_2026-07-30.yaml"
GATE_E_EVIDENCE_PATH = PROJECT_ROOT / "docs" / "virtual_player_gate_e_readiness_evidence_2026-07-30.yaml"
_MAX_EVIDENCE_BYTES = 1_000_000
_MAX_SOURCE_FILES = 160
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


# Readiness evidence must bind the governance inputs and the owners that can
# change the guarded runtime, not only whichever files happened to be listed
# when an artifact was recorded.
_COMMON_REQUIRED_SOURCE_FILES = frozenset(
    {
        "Makefile",
        "data/virtual_players.yaml",
        "docs/virtual_player_gate_a_acceptance_config_2026-07-27.yaml",
        "docs/virtual_player_gate_evidence_manifest_2026-07-30.yaml",
        "gameplay/migrations/0139_botprofile_v2_fields.py",
        "gameplay/migrations/0141_bot_runtime_policy_rollout.py",
        "gameplay/models/bots.py",
        "gameplay/services/runtime_configs.py",
        "gameplay/services/virtual_players.py",
        "gameplay/services/virtual_player_core/calibration_runtime.py",
        "gameplay/services/virtual_player_core/config.py",
        "gameplay/services/virtual_player_core/contracts.py",
        "gameplay/services/virtual_player_core/gate_evidence.py",
        "gameplay/services/virtual_player_core/policy_registry.py",
        "gameplay/services/virtual_player_core/profile_store.py",
        "gameplay/services/virtual_player_core/random_context.py",
        "gameplay/services/virtual_player_core/reference_snapshots.py",
        "gameplay/signals.py",
        "gameplay/tasks/virtual_players.py",
        "scripts/record_virtual_player_evidence.py",
        "tests/conftest.py",
        "tests/raid_concurrency_integration/h01_cross_races.py",
        "tests/test_pytest_configuration.py",
        "tests/test_virtual_player_architecture_gate.py",
        "tests/test_virtual_player_baseline_audit.py",
        "tests/test_virtual_player_gate_acceptance_config.py",
        "tests/test_virtual_player_gate_activation_evidence.py",
        "tests/test_virtual_player_gate_evidence_manifest.py",
        "tests/test_virtual_player_evidence_recorder.py",
        "tests/test_virtual_player_maintenance_contracts.py",
        "tests/test_virtual_player_random_context.py",
    }
)

GATE_D1_REQUIRED_SOURCE_FILES = _COMMON_REQUIRED_SOURCE_FILES | frozenset(
    {
        "gameplay/migrations/0140_bot_population_recompute_demand.py",
        "gameplay/services/arena/virtual_backfill.py",
        "gameplay/services/arena/virtual_lineups.py",
        "gameplay/services/arena/virtual_protection.py",
        "gameplay/services/arena/virtual_reserve.py",
        "gameplay/services/arena/virtual_reserve_demand.py",
        "gameplay/services/arena/virtual_reserve_fill.py",
        "gameplay/services/arena/virtual_reserve_observability.py",
        "gameplay/services/arena/virtual_reserve_pool.py",
        "gameplay/services/arena/virtual_reserve_reconcile.py",
        "gameplay/services/arena/virtual_reserve_references.py",
        "gameplay/services/arena/virtual_reserve_scan.py",
        "gameplay/services/manor/core.py",
        "gameplay/services/manor/coordinates.py",
        "gameplay/services/manor/prestige.py",
        "gameplay/services/virtual_player_core/bootstrap.py",
        "gameplay/services/virtual_player_core/bootstrap_assets.py",
        "gameplay/services/virtual_player_core/bootstrap_catalog.py",
        "gameplay/services/virtual_player_core/bootstrap_materializer.py",
        "gameplay/services/virtual_player_core/economy.py",
        "gameplay/services/virtual_player_core/gate_d1_exit_workflow.py",
        "gameplay/services/virtual_player_core/identity.py",
        "gameplay/services/virtual_player_core/inventory_budget.py",
        "gameplay/services/virtual_player_core/lifecycle.py",
        "gameplay/services/virtual_player_core/maintenance_rules.py",
        "gameplay/services/virtual_player_core/population_runtime.py",
        "gameplay/services/virtual_player_core/projection.py",
        "gameplay/services/virtual_player_core/selectors.py",
        "gameplay/services/virtual_player_core/strategy.py",
        "tests/arena_services/test_virtual_backfill.py",
        "tests/arena_services/test_virtual_reserve.py",
        "tests/test_arena_virtual_lineups.py",
        "tests/test_arena_virtual_population_concurrency_integration.py",
        "tests/test_manor_coordinate_concurrency_integration.py",
        "tests/test_virtual_player_backfill.py",
        "tests/test_virtual_player_bootstrap_routing.py",
        "tests/test_virtual_player_bootstrap_routing_concurrency_integration.py",
        "tests/test_virtual_player_bootstrap_v2.py",
        "tests/test_virtual_player_config.py",
        "tests/test_virtual_player_economy.py",
        "tests/test_virtual_player_gate_d1_concurrency_integration.py",
        "tests/test_virtual_player_gate_d1_evidence.py",
        "tests/test_virtual_player_gate_exit_workflows.py",
        "tests/test_virtual_player_population_consumer.py",
        "tests/test_virtual_player_population_demand.py",
        "tests/test_virtual_player_prestige_transitions.py",
        "tests/test_virtual_player_projection.py",
        "tests/test_virtual_player_reference_snapshots_v2.py",
        "tests/test_virtual_player_registration_population.py",
        "tests/test_virtual_player_strength_budget.py",
    }
)

GATE_E_REQUIRED_SOURCE_FILES = _COMMON_REQUIRED_SOURCE_FILES | frozenset(
    {
        "config/settings/celery_conf.py",
        "gameplay/admin/bots.py",
        "gameplay/migrations/0140_bot_population_recompute_demand.py",
        "gameplay/migrations/0142_botsafetymetricevent_botsafetymetricwindow.py",
        "gameplay/migrations/0143_botarenashortagebaseline.py",
        "gameplay/migrations/0144_arena_growth_claims.py",
        "gameplay/models/arena_virtual.py",
        "gameplay/models/virtual_player_maintenance.py",
        "gameplay/services/arena/coop_core.py",
        "gameplay/services/arena/core.py",
        "gameplay/services/arena/lifecycle_helpers.py",
        "gameplay/services/arena/virtual_backfill.py",
        "gameplay/services/arena/virtual_lineups.py",
        "gameplay/services/arena/virtual_protection.py",
        "gameplay/services/arena/virtual_reserve_demand.py",
        "gameplay/services/arena/virtual_reserve_observability.py",
        "gameplay/services/arena/virtual_reserve_pool.py",
        "gameplay/services/inventory/core.py",
        "gameplay/services/jail.py",
        "gameplay/services/manor/core.py",
        "gameplay/services/resources.py",
        "gameplay/services/technology.py",
        "gameplay/services/technology_runtime.py",
        "gameplay/services/raid/combat/battle.py",
        "gameplay/services/virtual_player_core/bootstrap.py",
        "gameplay/services/virtual_player_core/economy.py",
        "gameplay/services/virtual_player_core/external_reconciliation.py",
        "gameplay/services/virtual_player_core/gate_e_cutover_workflow.py",
        "gameplay/services/virtual_player_core/inventory_budget.py",
        "gameplay/services/virtual_player_core/maintenance.py",
        "gameplay/services/virtual_player_core/maintenance_action_specs.py",
        "gameplay/services/virtual_player_core/maintenance_candidates.py",
        "gameplay/services/virtual_player_core/maintenance_rules.py",
        "gameplay/services/virtual_player_core/maintenance_upgrade_candidates.py",
        "gameplay/services/virtual_player_core/population_runtime.py",
        "gameplay/services/virtual_player_core/projection.py",
        "gameplay/services/virtual_player_core/safety_baselines.py",
        "gameplay/services/virtual_player_core/safety_metrics.py",
        "gameplay/services/virtual_player_core/safety_monitor.py",
        "gameplay/services/virtual_player_core/safety_preflight.py",
        "gameplay/services/virtual_player_core/safety_provider.py",
        "gameplay/services/virtual_player_core/selectors.py",
        "gameplay/services/virtual_player_core/strategy.py",
        "guests/services/equipment.py",
        "guests/services/health.py",
        "guests/services/roster.py",
        "guests/services/salary.py",
        "guests/services/skills.py",
        "guests/services/training.py",
        "guests/tasks.py",
        "gameplay/services/recruitment/recruitment.py",
        "tests/arena_services/test_virtual_backfill.py",
        "tests/arena_services/test_virtual_reserve.py",
        "tests/arena_services/cleanup.py",
        "tests/raid_combat_battle/external_reconciliation.py",
        "tests/test_arena_virtual_lineups.py",
        "tests/test_arena_virtual_population_concurrency_integration.py",
        "tests/test_admin_i18n.py",
        "tests/test_building_upgrade_primitives.py",
        "tests/test_building_upgrade_primitives_concurrency_integration.py",
        "tests/test_guest_equipment_concurrency_integration.py",
        "tests/test_guest_equipment_lock_order_contracts.py",
        "tests/test_guest_equipment_locked.py",
        "tests/test_guest_health_salary_concurrency_integration.py",
        "tests/test_guest_roster_service.py",
        "tests/test_guest_skill_service.py",
        "tests/test_guests_defection.py",
        "tests/test_manor_coordinate_concurrency_integration.py",
        "tests/test_salary_service.py",
        "tests/test_technology_upgrade_concurrency_integration.py",
        "tests/test_technology_upgrade_locked.py",
        "tests/test_training_locked.py",
        "tests/test_virtual_player_arena_shortage_baselines.py",
        "tests/test_virtual_player_bootstrap_routing.py",
        "tests/test_virtual_player_bootstrap_routing_concurrency_integration.py",
        "tests/test_virtual_player_external_reconciliation.py",
        "tests/test_virtual_player_external_reconciliation_concurrency_integration.py",
        "tests/test_virtual_player_gate_c_concurrency_integration.py",
        "tests/test_virtual_player_gate_c_persistence.py",
        "tests/test_virtual_player_gate_c_reconciliation.py",
        "tests/test_virtual_player_gate_e_readiness_evidence.py",
        "tests/test_virtual_player_gate_exit_workflows.py",
        "tests/test_virtual_player_jail_cleanup.py",
        "tests/test_virtual_player_jail_cleanup_concurrency_integration.py",
        "tests/test_virtual_player_maintenance_concurrency_integration.py",
        "tests/test_virtual_player_maintenance_v2.py",
        "tests/test_virtual_player_operational_fixes.py",
        "tests/test_virtual_player_population_demand.py",
        "tests/test_virtual_player_prestige_transitions.py",
        "tests/test_virtual_player_projection.py",
        "tests/test_virtual_player_reference_snapshots_v2.py",
        "tests/test_virtual_player_admin_maintenance.py",
        "tests/test_virtual_player_safety_metrics.py",
        "tests/test_virtual_player_safety_monitor.py",
        "tests/test_virtual_player_safety_preflight.py",
        "tests/test_virtual_player_safety_provider.py",
        "tests/test_virtual_player_safety_real_service_integration.py",
        "tests/test_virtual_player_safety_routing.py",
        "tests/test_virtual_player_safety_tasks.py",
        "tests/test_raid_combat_battle.py",
    }
)


class GateEvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GateReadinessProof:
    gate: str
    evidence_id: str
    evidence_digest: str
    recorded_at_utc: str
    database_backend: str = ""
    database_host: str = ""
    database_port: int | None = None
    database_name: str = ""


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GateEvidenceError(f"{field} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _read_evidence(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise GateEvidenceError(f"cannot read gate evidence {path.name}") from exc
    if not payload or len(payload) > _MAX_EVIDENCE_BYTES:
        raise GateEvidenceError(f"gate evidence {path.name} has an invalid size")
    try:
        parsed = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise GateEvidenceError(f"gate evidence {path.name} is invalid YAML") from exc
    return _mapping(parsed, field="gate evidence"), payload


def _utc_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise GateEvidenceError(f"{field} must be a UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise GateEvidenceError(f"{field} must be a UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise GateEvidenceError(f"{field} must be canonical UTC")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GateEvidenceError(f"{field} must be a positive integer")
    return value


def _verify_test_environment(
    evidence: Mapping[str, Any],
    *,
    business_contact_field: str,
) -> dict[str, Any]:
    environment = _mapping(evidence.get("environment"), field="environment")
    if environment.get("database_backend") != "django.db.backends.mysql":
        raise GateEvidenceError("gate evidence did not use the MySQL test backend")
    if environment.get("database_name") != "test_webgame":
        raise GateEvidenceError("gate evidence did not use test_webgame")
    if not isinstance(environment.get("database_host"), str) or not str(environment["database_host"]).strip():
        raise GateEvidenceError("gate evidence database_host is invalid")
    _positive_int(environment.get("database_port"), field="environment.database_port")
    if environment.get(business_contact_field) is not False:
        raise GateEvidenceError("gate evidence contacted the business database")
    return environment


def _verify_source_state(
    evidence: Mapping[str, Any],
    *,
    required_files: frozenset[str],
) -> None:
    source_state = _mapping(evidence.get("source_state"), field="source_state")
    if source_state.get("digest_algorithm") != "sha256":
        raise GateEvidenceError("source_state.digest_algorithm must be sha256")
    if source_state.get("evidence_applies_to_exact_file_hashes") is not True:
        raise GateEvidenceError("source evidence must apply to exact file hashes")
    files = _mapping(source_state.get("files"), field="source_state.files")
    if not 1 <= len(files) <= _MAX_SOURCE_FILES:
        raise GateEvidenceError("source_state.files has an invalid size")
    missing_required_files = sorted(required_files - files.keys())
    if missing_required_files:
        joined = ", ".join(missing_required_files)
        raise GateEvidenceError(f"source_state.files is missing required files: {joined}")

    for relative_path, expected_digest in files.items():
        if _SHA256_PATTERN.fullmatch(str(expected_digest)) is None:
            raise GateEvidenceError(f"invalid source digest for {relative_path}")
        candidate = (PROJECT_ROOT / relative_path).resolve()
        try:
            candidate.relative_to(PROJECT_ROOT)
        except ValueError as exc:
            raise GateEvidenceError(f"source path escapes project root: {relative_path}") from exc
        if not candidate.is_file():
            raise GateEvidenceError(f"source file is missing: {relative_path}")
        observed = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if observed != expected_digest:
            raise GateEvidenceError(f"source digest changed: {relative_path}")


def _proof(
    *,
    gate: str,
    evidence: Mapping[str, Any],
    payload: bytes,
    environment: Mapping[str, Any],
) -> GateReadinessProof:
    evidence_id = evidence.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise GateEvidenceError("evidence_id must not be blank")
    recorded_at = _utc_timestamp(
        evidence.get("recorded_at_utc"),
        field="recorded_at_utc",
    )
    return GateReadinessProof(
        gate=gate,
        evidence_id=evidence_id,
        evidence_digest=hashlib.sha256(payload).hexdigest(),
        recorded_at_utc=recorded_at,
        database_backend=str(environment["database_backend"]),
        database_host=str(environment["database_host"]),
        database_port=int(environment["database_port"]),
        database_name=str(environment["database_name"]),
    )


def assert_current_evidence_environment(proof: GateReadinessProof) -> None:
    settings = connection.settings_dict
    expected = {
        "database_backend": proof.database_backend,
        "database_host": proof.database_host,
        "database_port": str(proof.database_port or ""),
        "database_name": proof.database_name,
    }
    observed = {
        "database_backend": str(settings.get("ENGINE") or ""),
        "database_host": str(settings.get("HOST") or ""),
        "database_port": str(settings.get("PORT") or ""),
        "database_name": str(settings.get("NAME") or ""),
    }
    mismatched_fields = sorted(field for field, expected_value in expected.items() if observed[field] != expected_value)
    if mismatched_fields:
        raise GateEvidenceError(
            "gate evidence environment does not match the current database fields: " + ", ".join(mismatched_fields)
        )


def _verify_canonical_gate_a(evidence: Mapping[str, Any]) -> None:
    canonical = _mapping(
        _mapping(evidence.get("regression_evidence"), field="regression_evidence").get("canonical_gate_a"),
        field="regression_evidence.canonical_gate_a",
    )
    manifest, _payload = _read_evidence(GATE_A_MANIFEST_PATH)
    if manifest.get("schema_version") != 1:
        raise GateEvidenceError("canonical Gate A manifest schema is unsupported")
    manifest_scope = _mapping(manifest.get("scope"), field="Gate A manifest scope")
    if manifest_scope.get("environment") != "test" or manifest_scope.get("production") is not False:
        raise GateEvidenceError("canonical Gate A manifest is not test-scoped")
    collection = _mapping(manifest.get("collection"), field="Gate A manifest collection")
    expected_count = _positive_int(
        collection.get("expected_nodeid_count"),
        field="Gate A manifest expected_nodeid_count",
    )
    execution = _mapping(
        _mapping(manifest.get("canonical_gate"), field="Gate A manifest canonical_gate").get("execution"),
        field="Gate A manifest execution",
    )
    expected_result = f"{expected_count} passed"
    execution_timestamp = _utc_timestamp(
        execution.get("execution_timestamp_utc"),
        field="Gate A manifest execution timestamp",
    )
    detail = canonical.get("detail")
    if not isinstance(detail, str) or not detail.strip():
        raise GateEvidenceError("canonical Gate A detail is missing")
    if (
        canonical.get("status") != "passed"
        or execution.get("status") != "passed"
        or canonical.get("result") != expected_result
        or canonical.get("execution_timestamp_utc") != execution_timestamp
        or execution.get("result_summary") != f"{expected_result} ({detail})"
    ):
        raise GateEvidenceError("canonical Gate A evidence does not match its manifest")


def verify_gate_d1_readiness() -> GateReadinessProof:
    evidence, payload = _read_evidence(GATE_D1_EVIDENCE_PATH)
    if evidence.get("schema_version") != 1:
        raise GateEvidenceError("Gate D1 evidence schema is unsupported")
    if evidence.get("gate") != "gate_d1_bootstrap_activation":
        raise GateEvidenceError("Gate D1 evidence identity is invalid")
    verdict = _mapping(evidence.get("verdict"), field="verdict")
    if verdict.get("required_implementation_and_test_evidence") != "passed":
        raise GateEvidenceError("Gate D1 implementation evidence did not pass")
    if verdict.get("review_disposition") != "ready_for_gate_exit_review":
        raise GateEvidenceError("Gate D1 is not ready for exit review")
    activation = _mapping(
        evidence.get("activation_preconditions_outside_this_evidence"),
        field="activation_preconditions_outside_this_evidence",
    )
    if activation.get("canonical_gate_a_execution_status") != "passed":
        raise GateEvidenceError("canonical Gate A evidence has not passed")
    scope = _mapping(evidence.get("scope"), field="scope")
    if scope.get("environment") != "test" or scope.get("production") is not False:
        raise GateEvidenceError("Gate D1 evidence is not scoped to the test environment")
    environment = _verify_test_environment(
        evidence,
        business_contact_field="business_database_touched",
    )
    _verify_source_state(evidence, required_files=GATE_D1_REQUIRED_SOURCE_FILES)
    return _proof(
        gate="d1",
        evidence=evidence,
        payload=payload,
        environment=environment,
    )


def verify_gate_e_readiness() -> GateReadinessProof:
    evidence, payload = _read_evidence(GATE_E_EVIDENCE_PATH)
    if evidence.get("schema_version") != 1:
        raise GateEvidenceError("Gate E evidence schema is unsupported")
    scope = _mapping(evidence.get("scope"), field="scope")
    if scope.get("gate") != "E" or scope.get("readiness_status") != "passed":
        raise GateEvidenceError("Gate E readiness evidence did not pass")
    if scope.get("gate_exit_executed") is not False:
        raise GateEvidenceError("Gate E readiness artifact must not claim gate exit")
    if scope.get("environment") != "test" or scope.get("production") is not False:
        raise GateEvidenceError("Gate E evidence is not scoped to the test environment")

    benchmark = _mapping(evidence.get("maintenance_benchmark"), field="maintenance_benchmark")
    if benchmark.get("all_six_cells_passed") is not True:
        raise GateEvidenceError("Gate E benchmark matrix did not pass")
    matrix = benchmark.get("matrix")
    if not isinstance(matrix, list):
        raise GateEvidenceError("Gate E benchmark matrix must be a list")
    observed_cells: set[tuple[int, int]] = set()
    for index, value in enumerate(matrix):
        cell = _mapping(value, field=f"maintenance_benchmark.matrix[{index}]")
        if cell.get("status") != "passed":
            raise GateEvidenceError("Gate E benchmark contains a failed cell")
        batch_size = cell.get("batch_size")
        concurrency = cell.get("concurrency")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise GateEvidenceError("Gate E benchmark batch_size is invalid")
        if isinstance(concurrency, bool) or not isinstance(concurrency, int):
            raise GateEvidenceError("Gate E benchmark concurrency is invalid")
        observed_cells.add((batch_size, concurrency))
    expected_cells = {(batch_size, concurrency) for batch_size in (1, 10, 100) for concurrency in (1, 2)}
    if observed_cells != expected_cells or len(matrix) != len(expected_cells):
        raise GateEvidenceError("Gate E benchmark matrix is incomplete")

    _verify_canonical_gate_a(evidence)

    static_gates = _mapping(evidence.get("static_gates"), field="static_gates")
    required_static_results = {
        "black_check": "passed",
        "isort_check": "passed",
        "flake8": "passed",
        "javascript_check": "passed",
        "javascript_tests": "passed",
        "django_check": "passed",
        "makemigrations_check_dry_run": "no_changes_detected",
        "compileall": "passed",
        "git_diff_check": "passed",
    }
    if any(static_gates.get(key) != value for key, value in required_static_results.items()):
        raise GateEvidenceError("Gate E static gates are incomplete")
    full_mypy = _mapping(static_gates.get("full_mypy"), field="full_mypy")
    if full_mypy.get("status") != "passed":
        raise GateEvidenceError("Gate E full mypy evidence did not pass")

    environment = _verify_test_environment(
        evidence,
        business_contact_field="business_database_contacted",
    )
    _verify_source_state(evidence, required_files=GATE_E_REQUIRED_SOURCE_FILES)
    return _proof(
        gate="e",
        evidence=evidence,
        payload=payload,
        environment=environment,
    )


__all__ = [
    "GateEvidenceError",
    "GateReadinessProof",
    "assert_current_evidence_environment",
    "verify_gate_d1_readiness",
    "verify_gate_e_readiness",
]
