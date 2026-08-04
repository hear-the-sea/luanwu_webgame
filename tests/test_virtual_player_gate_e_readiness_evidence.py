from __future__ import annotations

from pathlib import Path

import yaml

from gameplay.services.virtual_player_core import gate_evidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = gate_evidence.GATE_E_EVIDENCE_PATH
MANIFEST_PATH = gate_evidence.GATE_A_MANIFEST_PATH
ACCEPTANCE_PATH = PROJECT_ROOT / "docs" / "virtual_player_gate_a_acceptance_config_2026-07-27.yaml"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_gate_e_evidence_is_readiness_only_and_keeps_runtime_disabled() -> None:
    evidence = _load_yaml(EVIDENCE_PATH)
    manifest = _load_yaml(MANIFEST_PATH)

    assert evidence["schema_version"] == 1
    assert (
        evidence["scope"]
        | {
            "environment": "test",
            "environment_class": "non_production",
            "production": False,
            "gate": "E",
            "evidence_kind": "readiness_only",
            "readiness_status": "passed",
            "gate_exit_executed": False,
            "authorizes_cutover": False,
            "authorizes_v2_active": False,
        }
        == evidence["scope"]
    )
    assert all(value is False for value in evidence["safeguards"].values())
    canonical = evidence["regression_evidence"]["canonical_gate_a"]
    manifest_execution = manifest["canonical_gate"]["execution"]
    expected_result = f'{manifest["collection"]["expected_nodeid_count"]} passed'
    assert canonical == {
        "status": "passed",
        "execution_timestamp_utc": manifest_execution["execution_timestamp_utc"],
        "result": expected_result,
        "detail": canonical["detail"],
    }
    assert manifest_execution["result_summary"] == f'{expected_result} ({canonical["detail"]})'


def test_gate_e_static_evidence_matches_repository_quality_gates() -> None:
    static_gates = _load_yaml(EVIDENCE_PATH)["static_gates"]

    assert (
        static_gates
        | {
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
        == static_gates
    )
    assert static_gates["full_mypy"]["status"] == "passed"
    assert "ruff_check" not in static_gates
    assert "ruff_format_touched_files" not in static_gates


def test_gate_e_benchmark_matrix_matches_frozen_acceptance_thresholds() -> None:
    evidence = _load_yaml(EVIDENCE_PATH)["maintenance_benchmark"]
    acceptance_config = _load_yaml(ACCEPTANCE_PATH)
    acceptance = acceptance_config["benchmark"]

    assert evidence["warmup_runs"] == acceptance["warmup_runs"] == 5
    assert evidence["measured_runs"] == acceptance["measured_runs"] == 30
    assert evidence["batch_sizes"] == acceptance["batch_sizes"] == [1, 10, 100]
    assert (
        evidence["worker_concurrency"]
        == acceptance["worker_concurrency"]
        == [
            1,
            2,
        ]
    )
    matrix = evidence["matrix"]
    assert {(cell["batch_size"], cell["concurrency"]) for cell in matrix} == {
        (batch_size, concurrency)
        for batch_size in acceptance["batch_sizes"]
        for concurrency in acceptance["worker_concurrency"]
    }
    assert len(matrix) == 6
    assert all(cell["status"] == "passed" for cell in matrix)
    assert evidence["all_six_cells_passed"] is True

    performance = acceptance_config["performance"]
    assert evidence["thresholds"] == {
        "single_profile": {
            "duration_p95_ms_max": performance["maintenance_single_profile"]["duration_p95_ms"],
            "duration_p99_ms_max": performance["maintenance_single_profile"]["duration_p99_ms"],
            "queries_max": performance["maintenance_single_profile"]["sql_queries_max"],
            "write_queries_max": performance["maintenance_single_profile"]["write_queries_max"],
        },
        "batch_100": {
            "duration_p95_ms_max": performance["maintenance_batch_100"]["duration_p95_ms"],
            "duration_p99_ms_max": performance["maintenance_batch_100"]["duration_p99_ms"],
            "queries_max": performance["maintenance_batch_100"]["sql_queries_max"],
            "write_queries_max": performance["maintenance_batch_100"]["write_queries_max"],
        },
        "lock_wait": {
            "p95_ms_max": performance["database_lock_wait"]["p95_ms"],
            "p99_ms_max": performance["database_lock_wait"]["p99_ms"],
        },
        "deadlocks_max": performance["deadlocks_max"],
        "lock_timeouts_max": performance["lock_timeouts_max"],
    }


def test_retained_batch_100_metrics_are_inside_frozen_limits() -> None:
    benchmark = _load_yaml(EVIDENCE_PATH)["maintenance_benchmark"]
    limits = benchmark["thresholds"]["batch_100"]
    retained = [cell for cell in benchmark["matrix"] if cell["batch_size"] == 100 and cell["exact_metrics_retained"]]

    assert len(retained) == 2
    for cell in retained:
        assert cell["duration_p95_ms"] <= limits["duration_p95_ms_max"]
        assert cell["duration_p99_ms"] <= limits["duration_p99_ms_max"]
        assert cell["queries_max"] <= limits["queries_max"]
        assert cell["write_queries_max"] <= limits["write_queries_max"]
        assert cell["deadlocks"] == benchmark["thresholds"]["deadlocks_max"] == 0
        assert cell["lock_timeouts"] == benchmark["thresholds"]["lock_timeouts_max"] == 0


def test_gate_e_evidence_uses_only_the_isolated_test_database() -> None:
    environment = _load_yaml(EVIDENCE_PATH)["environment"]

    assert environment["database_backend"] == "django.db.backends.mysql"
    assert environment["database_port"] == 13306
    assert environment["database_name"] == "test_webgame"
    assert environment["database_role"] == "isolated_disposable_test_database"
    assert environment["redis_port"] == 16379
    assert environment["services_restarted"] is False
    assert environment["business_database_contacted"] is False
