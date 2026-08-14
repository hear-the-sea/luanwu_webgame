from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from django.db import connection

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT_DATE = "2026-08-11"
ARTIFACT_DATE = os.environ.get("VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE", DEFAULT_ARTIFACT_DATE).strip()
try:
    if datetime.strptime(ARTIFACT_DATE, "%Y-%m-%d").strftime("%Y-%m-%d") != ARTIFACT_DATE:
        raise ValueError
except (TypeError, ValueError) as exc:
    raise ValueError("VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE must use YYYY-MM-DD") from exc

GATE_A_MANIFEST_PATH = PROJECT_ROOT / f"docs/virtual_player_gate_evidence_manifest_{ARTIFACT_DATE}.yaml"
GATE_D1_EVIDENCE_PATH = PROJECT_ROOT / f"docs/virtual_player_gate_d1_evidence_{ARTIFACT_DATE}.yaml"
GATE_E_EVIDENCE_PATH = PROJECT_ROOT / f"docs/virtual_player_gate_e_readiness_evidence_{ARTIFACT_DATE}.yaml"
_MAX_EVIDENCE_BYTES = 1_000_000
# The policy-2 maintenance strategy adds durable recovery, growth-control,
# arena-budget, and cycle owners to the governed source bundle. Keep a bounded
# ceiling while leaving room for the next strategy slice to be added without
# silently weakening the completeness check.
_MAX_SOURCE_FILES = 300
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OBJECT_PATTERN = re.compile(r"[0-9a-f]{40,64}\Z")


# Readiness evidence must bind the governance inputs and the owners that can
# change the guarded runtime, not only whichever files happened to be listed
# when an artifact was recorded.
_COMMON_REQUIRED_SOURCE_FILES = frozenset(
    {
        ".flake8",
        ".github/workflows/ci.yml",
        ".github/workflows/virtual_player_readiness.yml",
        ".env.docker.prod.example",
        "Makefile",
        "common/constants/virtual_players.py",
        "config/settings/base.py",
        "config/settings/database.py",
        "data/virtual_players.yaml",
        "docker-compose.prod.yml",
        "docs/runbook_deploy_docker.md",
        "docs/virtual_player_gate_a_acceptance_config_2026-07-27.yaml",
        f"docs/virtual_player_gate_evidence_manifest_{ARTIFACT_DATE}.yaml",
        "gameplay/migrations/0139_botprofile_v2_fields.py",
        "gameplay/migrations/0141_bot_runtime_policy_rollout.py",
        "gameplay/migrations/0145_alter_resourceevent_reason.py",
        "gameplay/migrations/0146_virtual_player_health_and_recovery.py",
        "gameplay/models/__init__.py",
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
        "gameplay/services/virtual_player_core/health.py",
        "gameplay/signals.py",
        "gameplay/tasks/__init__.py",
        "gameplay/tasks/arena.py",
        "gameplay/tasks/virtual_players.py",
        "package-lock.json",
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "requirements-dev.txt",
        "requirements.lock.txt",
        "requirements.txt",
        "scripts/record_virtual_player_evidence.py",
        "scripts/check_env_services_ready.py",
        "tests/conftest.py",
        "tests/test_deployment_configuration.py",
        "tests/raid_concurrency_integration/h01_cross_races.py",
        "tests/test_pytest_configuration.py",
        "tests/test_arena_schedule.py",
        "tests/test_arena_tasks.py",
        "tests/test_virtual_player_architecture_gate.py",
        "tests/test_virtual_player_baseline_audit.py",
        "tests/test_virtual_player_gate_acceptance_config.py",
        "tests/test_virtual_player_gate_activation_evidence.py",
        "tests/test_virtual_player_gate_evidence_manifest.py",
        "tests/test_virtual_player_evidence_recorder.py",
        "tests/test_virtual_player_gate_e_automation.py",
        "tests/test_virtual_player_health.py",
        "tests/test_virtual_player_health_mysql_integration.py",
        "tests/test_virtual_player_maintenance_contracts.py",
        "tests/test_virtual_player_random_context.py",
    }
)

_CURRENT_V2_SHARED_SOURCE_FILES = frozenset(
    {
        "gameplay/management/commands/preflight_virtual_player_v2.py",
        "gameplay/migrations/0151_manor_resource_updated_index.py",
        "gameplay/migrations/0152_performance_scan_indexes.py",
        "gameplay/migrations/0153_arena_virtual_reserve_growth_retry_state.py",
        "gameplay/migrations/0154_alter_arenavirtualdemand_status.py",
        "gameplay/migrations/0155_arena_virtual_demand_activity_targets.py",
        "gameplay/migrations/0156_arena_virtual_reserve_roster_target.py",
        "gameplay/migrations/0157_arena_growth_effective_progress.py",
        "gameplay/migrations/0158_arena_growth_budget_and_admission_high_water.py",
        "gameplay/migrations/0159_arena_virtual_demand_admission_guard.py",
        "gameplay/migrations/0160_arena_growth_objective_snapshot.py",
        "gameplay/migrations/0161_arena_growth_digest_schema.py",
        "gameplay/migrations/0162_arena_admission_probe_and_member_lease.py",
        "gameplay/migrations/0163_arena_training_policy_snapshot.py",
        "gameplay/migrations/0164_virtual_player_cycles_recovery_and_control.py",
        "gameplay/migrations/0165_bot_maintenance_recovery_success.py",
        "gameplay/migrations/0166_arena_growth_control_snapshot.py",
        "gameplay/migrations/0167_growth_control_snapshot_immutability.py",
        "gameplay/migrations/0168_virtualplayergrowthcontrolpointer_and_more.py",
        "gameplay/migrations/0169_alter_botmaintenancerecovery_scope.py",
        "gameplay/migrations/0170_arena_growth_target_driven_lifecycle.py",
        "gameplay/migrations/0171_bot_maintenance_cycle_schedule.py",
        "gameplay/migrations/0172_bot_maintenance_cycle_interval_seed.py",
        "gameplay/migrations/0173_remove_legacy_arena_lifecycle_fields.py",
        "gameplay/migrations/0174_bot_maintenance_completion_event.py",
        "gameplay/migrations/0175_botmaintenanceattempt_action_kind_and_more.py",
        "gameplay/migrations/0176_virtual_player_recruitment_due_and_cycle_budget.py",
        "gameplay/migrations/0177_virtual_player_attempt_trigger_dimensions_index.py",
        "guests/migrations/0071_guestrecruitment_virtual_source.py",
        "gameplay/services/arena/virtual_reserve_growth_budget.py",
        "gameplay/services/arena/virtual_reserve_policy.py",
        "gameplay/services/arena/virtual_reserve_training_policy.py",
        "gameplay/services/virtual_player_core/arena_healing.py",
        "gameplay/services/virtual_player_core/arena_population.py",
        "gameplay/services/virtual_player_core/archetype_pacing.py",
        "gameplay/services/virtual_player_core/business_metrics.py",
        "gameplay/services/virtual_player_core/growth_control.py",
        "gameplay/services/virtual_player_core/maintenance_completion.py",
        "gameplay/services/virtual_player_core/maintenance.py",
        "gameplay/services/virtual_player_core/population.py",
        "gameplay/services/virtual_player_core/profile_management.py",
        "gameplay/services/virtual_player_core/recovery.py",
        "gameplay/services/virtual_player_core/runtime_assessment.py",
        "gameplay/services/virtual_player_core/runtime_helpers.py",
        "gameplay/services/virtual_player_core/runtime_preflight.py",
        "gameplay/services/virtual_player_core/recruitment.py",
        "gameplay/services/virtual_player_core/stage_metrics.py",
    }
)

_CURRENT_V2_MAINTENANCE_SOURCE_FILES = frozenset(
    {
        "gameplay/management/commands/requeue_virtual_player_recovery.py",
        "gameplay/services/virtual_player_core/maintenance_arena_projection.py",
        "gameplay/services/virtual_player_core/maintenance_candidate_assessment.py",
        "gameplay/services/virtual_player_core/maintenance_cycle.py",
        "gameplay/services/virtual_player_core/maintenance_resources.py",
        "gameplay/services/virtual_player_core/recruitment.py",
        "gameplay/services/virtual_player_core/virtual_assets.py",
        "gameplay/services/virtual_player_core/virtual_candidate_pools.py",
        "tests/test_virtual_player_archetype_pacing.py",
        "tests/test_virtual_player_business_metrics.py",
        "tests/test_virtual_player_maintenance_cycle.py",
        "tests/test_virtual_player_recruitment.py",
    }
)

GATE_D1_REQUIRED_SOURCE_FILES = (
    _COMMON_REQUIRED_SOURCE_FILES
    | frozenset(
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
            "tests/test_virtual_player_bootstrap_routing.py",
            "tests/test_virtual_player_bootstrap_v2.py",
            "tests/test_virtual_player_config.py",
            "tests/test_virtual_player_economy.py",
            "tests/test_virtual_player_gate_d1_concurrency_integration.py",
            "tests/test_virtual_player_gate_d1_automation.py",
            "tests/test_virtual_player_gate_d1_evidence.py",
            "tests/test_virtual_player_gate_exit_workflows.py",
            "tests/test_virtual_player_maintenance_rules.py",
            "tests/test_virtual_player_population_consumer.py",
            "tests/test_virtual_player_population_demand.py",
            "tests/test_virtual_player_prestige_transitions.py",
            "tests/test_virtual_player_projection.py",
            "tests/test_virtual_player_reference_snapshots_v2.py",
            "tests/test_virtual_player_registration_population.py",
            "tests/test_virtual_player_strength_budget.py",
        }
    )
    | _CURRENT_V2_SHARED_SOURCE_FILES
    | _CURRENT_V2_MAINTENANCE_SOURCE_FILES
)

GATE_E_REQUIRED_SOURCE_FILES = (
    _COMMON_REQUIRED_SOURCE_FILES
    | frozenset(
        {
            "battle/deployment.py",
            "battle/execution.py",
            "battle/locking.py",
            "config/settings/celery_conf.py",
            "gameplay/admin/__init__.py",
            "gameplay/admin/bots.py",
            "gameplay/migrations/0140_bot_population_recompute_demand.py",
            "gameplay/migrations/0142_botsafetymetricevent_botsafetymetricwindow.py",
            "gameplay/migrations/0143_botarenashortagebaseline.py",
            "gameplay/migrations/0144_arena_growth_claims.py",
            "gameplay/migrations/0147_backfill_grain_warehouse_ledger.py",
            "gameplay/migrations/0148_bot_runtime_safety_window_kind.py",
            "gameplay/migrations/0149_botruntimeroutingstate_paused_from_maintenance_mode_and_more.py",
            "gameplay/migrations/0150_botarenashortagebaseline_expires_at_and_more.py",
            "gameplay/management/commands/cleanup_expired_virtual_player_arena_baselines.py",
            "gameplay/management/commands/resume_virtual_player_gate_e_cutover.py",
            "gameplay/models/arena_virtual.py",
            "gameplay/models/virtual_player_maintenance.py",
            "gameplay/services/arena/coop_core.py",
            "gameplay/services/arena/coop_lifecycle.py",
            "gameplay/services/arena/core.py",
            "gameplay/services/arena/lifecycle_helpers.py",
            "gameplay/services/arena/registration_helpers.py",
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
            "gameplay/services/inventory/core.py",
            "gameplay/services/inventory/guest_item_selector.py",
            "gameplay/services/inventory/guest_items.py",
            "gameplay/services/inventory/guest_reset_helpers.py",
            "gameplay/services/jail.py",
            "gameplay/services/manor/bootstrap.py",
            "gameplay/services/manor/core.py",
            "gameplay/services/manor/treasury.py",
            "gameplay/services/missions_impl/finalization_helpers.py",
            "gameplay/services/missions_impl/launch_command.py",
            "gameplay/services/raid/combat/battle_guest_damage.py",
            "gameplay/services/raid/combat/battle_post_actions.py",
            "gameplay/services/resources.py",
            "gameplay/services/raid/combat/capture.py",
            "gameplay/services/raid/combat/failure.py",
            "gameplay/services/raid/combat/finalize.py",
            "gameplay/services/raid/combat/loot.py",
            "gameplay/services/raid/combat/retreat.py",
            "gameplay/services/raid/combat/run_persistence.py",
            "gameplay/services/raid/utils.py",
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
            "gameplay/services/utils/messages.py",
            "gameplay/services/work.py",
            "gameplay/tasks/resources.py",
            "guests/constants.py",
            "guests/migrations/0067_guest_training_remaining_seconds.py",
            "guests/models.py",
            "guests/services/equipment.py",
            "guests/services/health.py",
            "guests/services/roster.py",
            "guests/services/salary.py",
            "guests/services/skills.py",
            "guests/services/status.py",
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
            "tests/test_salary_service.py",
            "tests/test_technology_upgrade_concurrency_integration.py",
            "tests/test_technology_upgrade_locked.py",
            "tests/test_training_locked.py",
            "tests/test_virtual_player_arena_shortage_baselines.py",
            "tests/test_virtual_player_bootstrap_routing.py",
            "tests/test_virtual_player_external_reconciliation.py",
            "tests/test_virtual_player_external_reconciliation_concurrency_integration.py",
            "tests/test_virtual_player_gate_c_concurrency_integration.py",
            "tests/test_virtual_player_gate_c_persistence.py",
            "tests/test_virtual_player_gate_c_reconciliation.py",
            "tests/test_virtual_player_gate_e_readiness_evidence.py",
            "tests/test_virtual_player_gate_exit_workflows.py",
            "tests/test_virtual_player_jail_cleanup.py",
            "tests/test_virtual_player_jail_cleanup_concurrency_integration.py",
            "tests/test_virtual_player_celery_queue_capacity_integration.py",
            "tests/test_virtual_player_schedule_capacity_integration.py",
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
            "tests/test_virtual_player_stage_metrics.py",
            "tests/test_raid_combat_battle.py",
        }
    )
    | _CURRENT_V2_SHARED_SOURCE_FILES
    | _CURRENT_V2_MAINTENANCE_SOURCE_FILES
    | frozenset(
        {
            "gameplay/services/virtual_player_core/bootstrap_assets.py",
            "gameplay/services/virtual_player_core/bootstrap_catalog.py",
            "gameplay/services/virtual_player_core/bootstrap_materializer.py",
            "gameplay/services/virtual_player_core/gate_d1_exit_workflow.py",
            "gameplay/services/virtual_player_core/identity.py",
            "gameplay/services/virtual_player_core/lifecycle.py",
            "tests/arena_services/test_virtual_reserve_training_policy.py",
            "tests/test_arena_growth_progress_migration.py",
            "tests/test_virtual_player_growth_control.py",
            "tests/test_virtual_player_maintenance_arena_projection.py",
            "tests/test_virtual_player_maintenance_candidate_assessment.py",
            "tests/test_virtual_player_training_development.py",
            "tests/test_virtual_player_recovery.py",
            "tests/test_virtual_player_runtime_assessment.py",
            "tests/test_virtual_reserve_growth_budget.py",
        }
    )
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
    expected_git_commit: str | None = None,
    extra_allowed_dirty_paths: Sequence[str] = (),
) -> None:
    source_state = _mapping(evidence.get("source_state"), field="source_state")
    if source_state.get("digest_algorithm") != "sha256":
        raise GateEvidenceError("source_state.digest_algorithm must be sha256")
    if source_state.get("evidence_applies_to_exact_file_hashes") is not True:
        raise GateEvidenceError("source evidence must apply to exact file hashes")
    if expected_git_commit is not None and source_state.get("git_commit") != expected_git_commit:
        raise GateEvidenceError("source_state.git_commit does not match the expected build commit")
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

    raw_tree = source_state.get("git_tree")
    if raw_tree is not None:
        if _GIT_OBJECT_PATTERN.fullmatch(str(raw_tree)) is None:
            raise GateEvidenceError("source_state.git_tree must be a canonical Git object id")
        source_commit = source_state.get("git_commit")
        if not isinstance(source_commit, str) or _GIT_OBJECT_PATTERN.fullmatch(source_commit) is None:
            raise GateEvidenceError("source_state.git_commit must be a canonical Git object id")
        observed_tree = _git_tree_for_commit(source_commit)
        if observed_tree != raw_tree:
            raise GateEvidenceError("source_state.git_tree does not match the recorded source commit tree")

    if source_state.get("worktree_clean") is not True:
        raise GateEvidenceError("source_state.worktree_clean must be true")
    allowed_dirty_paths = _normalize_allowed_dirty_paths(source_state.get("allowed_dirty_paths"))
    if extra_allowed_dirty_paths:
        allowed_dirty_paths |= _normalize_allowed_dirty_paths(tuple(extra_allowed_dirty_paths))
    unclean_paths = _current_unclean_paths(allowed_dirty_paths)
    if unclean_paths:
        shown = ", ".join(unclean_paths[:5])
        raise GateEvidenceError(f"source worktree is dirty outside allowed evidence artifacts: {shown}")


def _git_tree_for_commit(commit: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"{commit}^{{tree}}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateEvidenceError("cannot resolve the recorded source commit tree") from exc
    tree = result.stdout.strip()
    if _GIT_OBJECT_PATTERN.fullmatch(tree) is None:
        raise GateEvidenceError("recorded source commit tree is not a canonical object id")
    return tree


def _normalize_allowed_dirty_paths(value: object) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple)):
        raise GateEvidenceError("source_state.allowed_dirty_paths must be a list")
    normalized: set[str] = set()
    for raw_path in value:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise GateEvidenceError("source_state.allowed_dirty_paths entries must be non-empty strings")
        candidate = (PROJECT_ROOT / raw_path.strip()).resolve()
        try:
            candidate.relative_to(PROJECT_ROOT)
        except ValueError as exc:
            raise GateEvidenceError(f"allowed dirty path escapes project root: {raw_path}") from exc
        normalized.add(str(candidate.relative_to(PROJECT_ROOT)))
    return frozenset(normalized)


def _current_unclean_paths(allowed_dirty_paths: frozenset[str]) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateEvidenceError("cannot inspect the current Git worktree") from exc

    unclean: set[str] = set()
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry and entry not in allowed_dirty_paths:
            unclean.add(entry)
    return tuple(sorted(unclean))


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


def _verify_recorded_canonical_gate_a_execution(evidence: Mapping[str, Any]) -> None:
    recorded = evidence.get("canonical_gate_a_execution")
    if recorded is None:
        return
    execution = _mapping(recorded, field="canonical_gate_a_execution")
    if execution.get("command") != "DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-a":
        raise GateEvidenceError("recorded canonical Gate A command is invalid")
    if execution.get("status") != "passed":
        raise GateEvidenceError("recorded canonical Gate A execution did not pass")
    _utc_timestamp(
        execution.get("execution_timestamp_utc"),
        field="canonical_gate_a_execution.execution_timestamp_utc",
    )
    _positive_int(execution.get("contract_passed"), field="canonical_gate_a_execution.contract_passed")
    _positive_int(execution.get("real_service_passed"), field="canonical_gate_a_execution.real_service_passed")
    duration_seconds = execution.get("duration_seconds")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or duration_seconds < 0:
        raise GateEvidenceError("canonical_gate_a_execution.duration_seconds is invalid")


def verify_gate_d1_readiness(
    *,
    evidence_path: Path | None = None,
    expected_git_commit: str | None = None,
    extra_allowed_dirty_paths: Sequence[str] = (),
) -> GateReadinessProof:
    evidence, payload = _read_evidence(evidence_path or GATE_D1_EVIDENCE_PATH)
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
    _verify_recorded_canonical_gate_a_execution(evidence)
    scope = _mapping(evidence.get("scope"), field="scope")
    if scope.get("environment") != "test" or scope.get("production") is not False:
        raise GateEvidenceError("Gate D1 evidence is not scoped to the test environment")
    environment = _verify_test_environment(
        evidence,
        business_contact_field="business_database_touched",
    )
    _verify_source_state(
        evidence,
        required_files=GATE_D1_REQUIRED_SOURCE_FILES,
        expected_git_commit=expected_git_commit,
        extra_allowed_dirty_paths=extra_allowed_dirty_paths,
    )
    return _proof(
        gate="d1",
        evidence=evidence,
        payload=payload,
        environment=environment,
    )


def verify_gate_e_readiness(
    *,
    expected_git_commit: str | None = None,
) -> GateReadinessProof:
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
    _verify_source_state(
        evidence,
        required_files=GATE_E_REQUIRED_SOURCE_FILES,
        expected_git_commit=expected_git_commit,
    )
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
