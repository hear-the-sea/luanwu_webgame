from __future__ import annotations

import base64
import json
import subprocess
import sys

import pytest

from gameplay.services.virtual_player_core import gate_evidence, runtime_preflight
from scripts import record_virtual_player_evidence as recorder

pytestmark = pytest.mark.evidence


def test_recorder_script_bootstraps_project_imports_outside_repository(tmp_path) -> None:
    result = subprocess.run(
        [sys.executable, str(recorder.__file__), "--artifact-date", "2099-01-01"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 2
    assert "GATE_A_MANIFEST_PATH must point to virtual_player_gate_evidence_manifest_2099-01-01.yaml" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_recorder_reads_canonical_makefile_suite_variables() -> None:
    assert "tests/test_virtual_player_gate_d1_concurrency_integration.py" in recorder._read_makefile_paths(
        recorder.GATE_D1_CORE_VARIABLE
    )
    assert "tests/test_virtual_player_maintenance_concurrency_integration.py" in recorder._read_makefile_paths(
        recorder.GATE_E_REAL_VARIABLE
    )


def test_gate_e_evidence_sources_cover_all_canonical_suite_files() -> None:
    canonical_suite_files = set(recorder._read_makefile_paths(recorder.GATE_E_CONTRACT_VARIABLE))
    canonical_suite_files.update(recorder._read_makefile_paths(recorder.GATE_E_REAL_VARIABLE))

    assert canonical_suite_files <= gate_evidence.GATE_E_REQUIRED_SOURCE_FILES


def test_gate_evidence_binds_every_current_v2_maintenance_owner() -> None:
    current_runtime_files = {
        "gameplay/migrations/0170_arena_growth_target_driven_lifecycle.py",
        "gameplay/migrations/0171_bot_maintenance_cycle_schedule.py",
        "gameplay/migrations/0172_bot_maintenance_cycle_interval_seed.py",
        "gameplay/migrations/0173_remove_legacy_arena_lifecycle_fields.py",
        "gameplay/migrations/0174_bot_maintenance_completion_event.py",
        "gameplay/migrations/0175_botmaintenanceattempt_action_kind_and_more.py",
        "gameplay/migrations/0176_virtual_player_recruitment_due_and_cycle_budget.py",
        "gameplay/migrations/0177_virtual_player_attempt_trigger_dimensions_index.py",
        "guests/migrations/0071_guestrecruitment_virtual_source.py",
        "gameplay/services/virtual_player_core/archetype_pacing.py",
        "gameplay/services/virtual_player_core/business_metrics.py",
        "gameplay/services/virtual_player_core/maintenance.py",
        "gameplay/services/virtual_player_core/maintenance_completion.py",
        "gameplay/services/virtual_player_core/recruitment.py",
        "tests/test_virtual_player_archetype_pacing.py",
        "tests/test_virtual_player_business_metrics.py",
        "tests/test_virtual_player_maintenance_cycle.py",
        "tests/test_virtual_player_recruitment.py",
    }
    assert current_runtime_files <= gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES
    assert current_runtime_files <= gate_evidence.GATE_E_REQUIRED_SOURCE_FILES


def test_runtime_preflight_binds_the_cross_app_migration_closure() -> None:
    required = runtime_preflight.REQUIRED_RUNTIME_MIGRATIONS

    assert len(required) == len(set(required))
    assert {app for app, _name in required} == {"gameplay", "guests"}
    assert required[-3:] == (
        ("gameplay", "0176_virtual_player_recruitment_due_and_cycle_budget"),
        ("gameplay", "0177_virtual_player_attempt_trigger_dimensions_index"),
        ("guests", "0071_guestrecruitment_virtual_source"),
    )


def test_gate_d1_evidence_sources_cover_all_canonical_suite_files() -> None:
    canonical_suite_files = set(recorder._read_makefile_paths(recorder.GATE_D1_CONTRACT_VARIABLE))
    canonical_suite_files.update(recorder._read_makefile_paths(recorder.GATE_D1_CORE_VARIABLE))
    canonical_suite_files.update(recorder._read_makefile_paths(recorder.GATE_D1_ADJACENT_VARIABLE))

    assert canonical_suite_files <= gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES


def test_gate_evidence_binds_virtual_player_health_and_arena_owner_files() -> None:
    common_runtime_files = {
        "config/settings/base.py",
        "gameplay/migrations/0145_alter_resourceevent_reason.py",
        "gameplay/migrations/0146_virtual_player_health_and_recovery.py",
        "gameplay/models/__init__.py",
        "gameplay/services/virtual_player_core/health.py",
        "gameplay/tasks/__init__.py",
        "gameplay/tasks/arena.py",
        "tests/test_arena_schedule.py",
        "tests/test_arena_tasks.py",
        "tests/test_virtual_player_health.py",
    }
    for required_files in (
        gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES,
        gate_evidence.GATE_E_REQUIRED_SOURCE_FILES,
    ):
        assert common_runtime_files <= required_files

    assert {
        "gameplay/admin/__init__.py",
        "gameplay/services/arena/coop_lifecycle.py",
        "gameplay/services/arena/registration_helpers.py",
        "gameplay/services/arena/virtual_reserve.py",
        "gameplay/services/arena/virtual_reserve_fill.py",
        "gameplay/services/arena/virtual_reserve_reconcile.py",
        "gameplay/services/arena/virtual_reserve_references.py",
        "gameplay/services/arena/virtual_reserve_scan.py",
    } <= gate_evidence.GATE_E_REQUIRED_SOURCE_FILES


def test_gate_e_evidence_binds_guest_status_and_resource_ledger_runtime_files() -> None:
    runtime_files = {
        "battle/deployment.py",
        "battle/execution.py",
        "battle/locking.py",
        "gameplay/migrations/0147_backfill_grain_warehouse_ledger.py",
        "gameplay/migrations/0149_botruntimeroutingstate_paused_from_maintenance_mode_and_more.py",
        "gameplay/migrations/0150_botarenashortagebaseline_expires_at_and_more.py",
        "gameplay/management/commands/cleanup_expired_virtual_player_arena_baselines.py",
        "gameplay/management/commands/resume_virtual_player_gate_e_cutover.py",
        "gameplay/services/inventory/guest_items.py",
        "gameplay/services/inventory/guest_reset_helpers.py",
        "gameplay/services/manor/bootstrap.py",
        "gameplay/services/manor/treasury.py",
        "gameplay/services/missions_impl/finalization_helpers.py",
        "gameplay/services/missions_impl/launch_command.py",
        "gameplay/services/raid/combat/capture.py",
        "gameplay/services/raid/combat/failure.py",
        "gameplay/services/raid/combat/finalize.py",
        "gameplay/services/raid/combat/loot.py",
        "gameplay/services/raid/combat/retreat.py",
        "gameplay/services/raid/combat/run_persistence.py",
        "gameplay/services/raid/utils.py",
        "gameplay/services/utils/messages.py",
        "gameplay/services/work.py",
        "gameplay/tasks/resources.py",
        "guests/constants.py",
        "guests/migrations/0067_guest_training_remaining_seconds.py",
        "guests/models.py",
        "guests/services/status.py",
    }

    assert runtime_files <= gate_evidence.GATE_E_REQUIRED_SOURCE_FILES


def test_recorder_parses_pytest_summaries_without_hardcoded_counts() -> None:
    summaries = recorder._parse_pytest_summaries("159 passed in 12.34s\n10 passed, 2 warnings in 103.01s (0:01:43)\n")

    assert [(row.passed, row.duration_seconds) for row in summaries] == [
        (159, 12.34),
        (10, 103.01),
    ]


def test_recorder_parses_d1_and_complete_gate_e_benchmark_metrics() -> None:
    d1 = recorder._parse_d1_benchmark(
        "........gate_d1_bootstrap_p95 planning_ms=98.739 materialization_ms=165.264 measured_runs=30\n"
    )
    gate_e_lines = "\n".join(
        ".gate_e_maintenance_benchmark "
        f"batch_size={batch_size} concurrency={concurrency} "
        "duration_p95_ms=10.100 duration_p99_ms=11.200 "
        "queries_max=20 write_queries_max=5 "
        "lock_wait_p95_ms=0.000 lock_wait_p99_ms=0.000 "
        "deadlocks=0 lock_timeouts=0 warmup_runs=5 measured_runs=30"
        for batch_size in (1, 10, 100)
        for concurrency in (1, 2)
    )

    assert d1 == {
        "planning_ms": 98.739,
        "materialization_ms": 165.264,
        "measured_runs": 30,
    }
    assert len(recorder._parse_gate_e_benchmarks(gate_e_lines)) == 6


def test_recorder_rejects_incomplete_gate_e_benchmark_matrix() -> None:
    with pytest.raises(recorder.EvidenceRecordingError, match="complete six-cell"):
        recorder._parse_gate_e_benchmarks(
            "gate_e_maintenance_benchmark "
            "batch_size=1 concurrency=1 duration_p95_ms=1 duration_p99_ms=1 "
            "queries_max=1 write_queries_max=1 lock_wait_p95_ms=0 lock_wait_p99_ms=0 "
            "deadlocks=0 lock_timeouts=0 warmup_runs=5 measured_runs=30"
        )


def test_recorder_parses_complete_gate_e_stage_metrics_and_fingerprints() -> None:
    fingerprint = (
        base64.urlsafe_b64encode(
            json.dumps([{"sql": "SELECT ? FROM `example`", "count": 3}], separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    stages = (
        "due_backlog_selection",
        "planning_snapshot_preload",
        "profile_plan_revalidation",
        "action_domain_writes",
        "cycle_attempt_receipt",
        "safety_task_wrapup",
    )
    output = "\n".join(
        "gate_e_maintenance_stage "
        f"batch_size={batch_size} concurrency={concurrency} stage={stage} observations=30 "
        "duration_p50_ms=1.000 duration_p95_ms=2.000 duration_p99_ms=3.000 "
        "queries_max=4 write_queries_max=2 "
        f"fingerprints_b64={fingerprint}"
        for batch_size in (1, 10, 100)
        for concurrency in (1, 2)
        for stage in stages
    )

    rows = recorder._parse_gate_e_stage_metrics(output)

    assert len(rows) == 36
    assert rows[0]["fingerprints"] == [{"sql": "SELECT ? FROM `example`", "count": 3}]


def test_recorder_rejects_incomplete_gate_e_stage_metric_matrix() -> None:
    with pytest.raises(recorder.EvidenceRecordingError, match="complete stage metric matrix"):
        recorder._parse_gate_e_stage_metrics(
            "gate_e_maintenance_stage "
            "batch_size=1 concurrency=1 stage=due_backlog_selection observations=1 "
            "duration_p50_ms=1 duration_p95_ms=1 duration_p99_ms=1 "
            "queries_max=1 write_queries_max=1 fingerprints_b64=-"
        )


def test_recorder_restores_replaced_and_new_artifacts_after_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(recorder, "DOCS_ROOT", tmp_path)
    existing_path = tmp_path / "existing.yaml"
    new_path = tmp_path / "new.yaml"
    existing_path.write_bytes(b"old evidence\n")
    state = recorder._capture_artifact_state((existing_path, new_path), replace=True)

    recorder._write_artifacts(
        {
            existing_path: b"replacement evidence\n",
            new_path: b"staged evidence\n",
        },
        replace=True,
    )
    recorder._restore_artifacts(state)

    assert existing_path.read_bytes() == b"old evidence\n"
    assert not new_path.exists()


def test_recorder_rejects_existing_artifact_before_running_gates(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(recorder, "DOCS_ROOT", tmp_path)
    existing_path = tmp_path / "existing.yaml"
    existing_path.write_bytes(b"old evidence\n")

    with pytest.raises(recorder.EvidenceRecordingError, match="refusing to overwrite"):
        recorder._capture_artifact_state((existing_path,), replace=False)


def test_recorder_failure_diagnostics_keep_stdout_and_stderr_tails(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["failing-command"],
        returncode=1,
        stdout="pytest failure summary\n",
        stderr="maintenance log tail\n",
    )
    monkeypatch.setattr(recorder.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(recorder.EvidenceRecordingError) as captured:
        recorder._run_command(
            ["failing-command"],
            env={},
            label="diagnostic test",
            timeout_seconds=1,
        )

    message = str(captured.value)
    assert "stdout tail:\npytest failure summary" in message
    assert "stderr tail:\nmaintenance log tail" in message
