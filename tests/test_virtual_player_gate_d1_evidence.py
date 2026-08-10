from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from gameplay.services.virtual_player_core import gate_evidence

pytestmark = pytest.mark.evidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = gate_evidence.GATE_D1_EVIDENCE_PATH
ACCEPTANCE_PATH = PROJECT_ROOT / "docs" / "virtual_player_gate_a_acceptance_config_2026-07-27.yaml"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _assert_utc_timestamp(value: object) -> None:
    assert isinstance(value, str)
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    assert parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value


def _collected_nodeids(*, files: list[str], real_services: bool) -> list[str]:
    environment = {
        **os.environ,
        "DJANGO_TEST_USE_ENV_SERVICES": "1" if real_services else "0",
        "PYTEST_ADDOPTS": "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *files, "--collect-only", "-q"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, f"pytest collection failed:\n{result.stdout}\n{result.stderr}"
    return sorted(line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line)


def test_gate_d1_evidence_scope_does_not_authorize_runtime_changes() -> None:
    evidence = _load_yaml(EVIDENCE_PATH)

    _assert_utc_timestamp(evidence["recorded_at_utc"])
    assert evidence["schema_version"] == 1
    assert evidence["gate"] == "gate_d1_bootstrap_activation"
    assert evidence["verdict"] == {
        "required_implementation_and_test_evidence": "passed",
        "review_disposition": "ready_for_gate_exit_review",
        "gate_exit_performed": False,
        "runtime_activation_authorized": False,
        "runtime_routing_changed": False,
        "production_rollout_authorized": False,
    }
    assert all(
        evidence["scope"][field] is False
        for field in (
            "production",
            "representative_human_distribution_claimed",
            "authorizes_gate_d2_calibration",
            "authorizes_gate_e_maintenance",
            "authorizes_existing_data_rebuild",
            "authorizes_git_operations",
        )
    )
    assert (
        evidence["activation_preconditions_outside_this_evidence"]
        | {
            "canonical_gate_a_execution_status": "passed",
            "explicit_gate_exit_decision_status": "not_taken",
            "explicit_runtime_transition_authorization_status": "not_granted",
            "gate_d2_reference_calibration_required_for_d1": False,
        }
        == evidence["activation_preconditions_outside_this_evidence"]
    )


def test_gate_d1_evidence_source_state_has_governance_shape() -> None:
    source_state = _load_yaml(EVIDENCE_PATH)["source_state"]

    assert source_state["digest_algorithm"] == "sha256"
    assert source_state["evidence_applies_to_exact_file_hashes"] is True
    assert "worktree_clean" in source_state
    assert {"git_commit", "worktree_clean", "files"} <= set(source_state)
    assert {
        "gameplay/models/bots.py",
        "gameplay/services/runtime_configs.py",
    } <= set(source_state["files"])


@pytest.mark.parametrize(
    ("suite_name", "real_services"),
    (
        ("contract", False),
        ("core_real_service", True),
        ("adjacent_real_service", True),
    ),
)
def test_gate_d1_evidence_suite_collections_are_current(
    suite_name: str,
    real_services: bool,
) -> None:
    evidence = _load_yaml(EVIDENCE_PATH)
    collection = evidence["suite_collection"]
    suite = collection[suite_name]
    nodeids = _collected_nodeids(
        files=suite["files"],
        real_services=real_services,
    )
    checksum_payload = "".join(f"{nodeid}\n" for nodeid in nodeids).encode("utf-8")

    assert len(nodeids) == len(set(nodeids)) == suite["expected_nodeid_count"]
    assert hashlib.sha256(checksum_payload).hexdigest() == suite["nodeid_checksum"]
    assert (
        collection
        | {
            "checksum_algorithm": "sha256",
            "checksum_input": "sorted_pytest_nodeids_joined_with_lf_and_terminal_lf",
            "encoding": "utf-8",
        }
        == collection
    )


def test_gate_d1_evidence_performance_matches_frozen_acceptance() -> None:
    evidence = _load_yaml(EVIDENCE_PATH)
    acceptance = _load_yaml(ACCEPTANCE_PATH)
    performance = evidence["performance"]
    frozen = acceptance["performance"]["bootstrap_single_profile"]

    assert (
        performance["benchmark"]
        | {
            "warmup_runs": acceptance["benchmark"]["warmup_runs"],
            "measured_runs": acceptance["benchmark"]["measured_runs"],
            "percentile_method": "nearest_rank",
        }
        == performance["benchmark"]
    )
    for metric in (
        "planning_duration_p95_ms",
        "materialization_duration_p95_ms",
    ):
        result = performance[metric]
        assert result["passed"] is True
        assert result["observed"] <= result["threshold"]
    assert performance["planning_duration_p95_ms"]["threshold"] == frozen["planning_duration_p95_ms"]
    assert performance["materialization_duration_p95_ms"]["threshold"] == frozen["materialization_duration_p95_ms"]
    assert (
        performance["query_budget"]
        | {
            "sql_queries_max": frozen["sql_queries_max"],
            "write_queries_max": frozen["write_queries_max"],
            "exact_observed_counts_recorded": False,
            "contract_assertion_status": "passed",
        }
        == performance["query_budget"]
    )


def test_gate_d1_evidence_records_honest_execution_and_migration_results() -> None:
    evidence = _load_yaml(EVIDENCE_PATH)
    executions = evidence["executions"]
    migration = evidence["migration_verification"]

    assert set(executions) == {"contract", "core_real_service", "adjacent_real_service"}
    for name, result in executions.items():
        assert result["status"] == "passed"
        assert result["passed"] + result["skipped"] == evidence["suite_collection"][name]["expected_nodeid_count"]
        assert result["failed"] == 0
        _assert_utc_timestamp(result["execution_timestamp_utc"])
    _assert_utc_timestamp(migration["checked_at_utc"])
    assert (
        migration
        | {
            "database": "test_webgame",
            "read_only_check": True,
            "migration_app": "gameplay",
            "migration_name": "0140_bot_population_recompute_demand",
            "matching_migration_records": 1,
            "demand_table_rows_after_suite": 0,
            "migration_executed_by_this_verification": False,
            "database_rebuilt_by_this_verification": False,
        }
        == migration
    )
    assert all(
        result == "passed"
        for key, result in evidence["scenario_results"].items()
        if key not in {"deadlocks_observed", "lock_timeouts_observed"}
    )
    assert evidence["scenario_results"]["deadlocks_observed"] == 0
    assert evidence["scenario_results"]["lock_timeouts_observed"] == 0


def test_gate_d1_evidence_records_the_gate_a_prerequisite_execution() -> None:
    execution = _load_yaml(EVIDENCE_PATH)["canonical_gate_a_execution"]

    assert execution["command"] == "DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-a"
    assert execution["status"] == "passed"
    assert execution["contract_passed"] > 0
    assert execution["real_service_passed"] > 0
    _assert_utc_timestamp(execution["execution_timestamp_utc"])
    assert execution["duration_seconds"] >= 0
