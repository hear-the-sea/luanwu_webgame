from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DOCS_ROOT = PROJECT_ROOT / "docs"
MAKEFILE_PATH = PROJECT_ROOT / "Makefile"
ACCEPTANCE_PATH = DOCS_ROOT / "virtual_player_gate_a_acceptance_config_2026-07-27.yaml"
DEFAULT_MANIFEST_TEMPLATE = DOCS_ROOT / "virtual_player_gate_evidence_manifest_2026-07-28.yaml"
DEFAULT_D1_TEMPLATE = DOCS_ROOT / "virtual_player_gate_d1_evidence_2026-07-28.yaml"
DEFAULT_GATE_E_TEMPLATE = DOCS_ROOT / "virtual_player_gate_e_readiness_evidence_2026-07-28.yaml"

GATE_A_CONTRACT_VARIABLE = "VIRTUAL_PLAYER_GATE_A_CONTRACT_TESTS"
GATE_A_REAL_VARIABLE = "VIRTUAL_PLAYER_GATE_A_REAL_SERVICE_TESTS"
GATE_D1_CONTRACT_VARIABLE = "VIRTUAL_PLAYER_GATE_D1_CONTRACT_TESTS"
GATE_D1_CORE_VARIABLE = "VIRTUAL_PLAYER_GATE_D1_CORE_REAL_SERVICE_TESTS"
GATE_D1_ADJACENT_VARIABLE = "VIRTUAL_PLAYER_GATE_D1_ADJACENT_REAL_SERVICE_TESTS"
GATE_E_CONTRACT_VARIABLE = "VIRTUAL_PLAYER_GATE_E_CONTRACT_TESTS"
GATE_E_REAL_VARIABLE = "VIRTUAL_PLAYER_GATE_E_REAL_SERVICE_TESTS"

GATE_A_COMMAND = "DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-a"
GATE_D1_COMMAND = "DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-d1"
GATE_E_COMMAND = "DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-e"

_PYTEST_SUMMARY_PATTERN = re.compile(
    r"^(?P<outcomes>\d+ [a-z]+(?:, \d+ [a-z]+)*) in " r"(?P<seconds>\d+(?:\.\d+)?)s(?: \(\d+:\d{2}:\d{2}\))?$"
)
_MYPY_SOURCE_PATTERN = re.compile(r"Success: no issues found in (?P<count>\d+) source files")
_SHA256_INPUT = "sorted_pytest_nodeids_joined_with_lf_and_terminal_lf"
_FINAL_VERIFIER_FILES = (
    "tests/test_virtual_player_gate_evidence_manifest.py",
    "tests/test_virtual_player_gate_d1_evidence.py",
    "tests/test_virtual_player_gate_e_readiness_evidence.py",
    "tests/test_virtual_player_gate_activation_evidence.py",
)


class EvidenceRecordingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: tuple[str, ...]
    stdout: str
    stderr: str
    duration_seconds: float
    completed_at_utc: str


@dataclass(frozen=True, slots=True)
class PytestSummary:
    passed: int
    failed: int
    skipped: int
    deselected: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class SuiteCollection:
    files: tuple[str, ...]
    nodeids: tuple[str, ...]
    checksum: str
    collected_at_utc: str


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvidenceRecordingError(f"cannot read {path}") from exc
    if not isinstance(parsed, dict):
        raise EvidenceRecordingError(f"{path} must contain a YAML mapping")
    return parsed


def _read_makefile_paths(variable: str) -> tuple[str, ...]:
    lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    prefix = f"{variable} ?="
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        values: list[str] = []
        fragment = line.partition("=")[2].strip()
        while True:
            continued = fragment.endswith("\\")
            fragment = fragment.removesuffix("\\").strip()
            if fragment:
                values.extend(shlex.split(fragment))
            if not continued:
                if not values:
                    raise EvidenceRecordingError(f"Makefile variable {variable} is empty")
                return tuple(values)
            index += 1
            if index >= len(lines):
                raise EvidenceRecordingError(f"Makefile variable {variable} is unterminated")
            fragment = lines[index].strip()
    raise EvidenceRecordingError(f"Makefile variable {variable} is missing")


def _run_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    label: str,
    timeout_seconds: int,
) -> CommandResult:
    print(f"[{_utc_now()}] running {label}", flush=True)
    started = datetime.now(UTC)
    try:
        completed = subprocess.run(
            list(command),
            cwd=PROJECT_ROOT,
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceRecordingError(f"{label} could not complete: {exc}") from exc
    finished = datetime.now(UTC)
    duration_seconds = (finished - started).total_seconds()
    if completed.returncode != 0:
        output_tails: list[str] = []
        for stream_name, stream in (("stdout", completed.stdout), ("stderr", completed.stderr)):
            if stream.strip():
                tail = "\n".join(stream.strip().splitlines()[-80:])
                output_tails.append(f"{stream_name} tail:\n{tail}")
        details = "\n".join(output_tails)
        raise EvidenceRecordingError(f"{label} failed with exit code {completed.returncode}\n{details}")
    print(f"[{_utc_now()}] passed {label} in {duration_seconds:.2f}s", flush=True)
    return CommandResult(
        command=tuple(command),
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=duration_seconds,
        completed_at_utc=finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _parse_pytest_summaries(output: str) -> tuple[PytestSummary, ...]:
    summaries: list[PytestSummary] = []
    for raw_line in output.splitlines():
        line = raw_line.strip().strip("=").strip()
        match = _PYTEST_SUMMARY_PATTERN.fullmatch(line)
        if match is None:
            continue
        outcomes: dict[str, int] = {}
        for item in match.group("outcomes").split(", "):
            count_text, name = item.split(" ", 1)
            outcomes[name] = int(count_text)
        if "passed" not in outcomes:
            continue
        summaries.append(
            PytestSummary(
                passed=outcomes.get("passed", 0),
                failed=outcomes.get("failed", 0) + outcomes.get("error", 0) + outcomes.get("errors", 0),
                skipped=outcomes.get("skipped", 0),
                deselected=outcomes.get("deselected", 0),
                duration_seconds=float(match.group("seconds")),
            )
        )
    return tuple(summaries)


def _parse_metric_line(line: str, *, prefix: str) -> dict[str, str]:
    stripped = line.strip()
    if not stripped.startswith(prefix + " "):
        raise EvidenceRecordingError(f"metric line does not start with {prefix}")
    metrics: dict[str, str] = {}
    for token in stripped.removeprefix(prefix).strip().split():
        key, separator, value = token.partition("=")
        if not separator or not key or not value or key in metrics:
            raise EvidenceRecordingError(f"invalid {prefix} metric token: {token}")
        metrics[key] = value
    return metrics


def _extract_metric_records(output: str, *, prefix: str) -> tuple[str, ...]:
    marker = re.compile(rf"(?<![\w]){re.escape(prefix)} ")
    records: list[str] = []
    for line in output.splitlines():
        matches = tuple(marker.finditer(line))
        if len(matches) > 1:
            raise EvidenceRecordingError(f"multiple {prefix} records share one output line")
        if matches:
            records.append(line[matches[0].start() :].strip())
    return tuple(records)


def _parse_d1_benchmark(output: str) -> dict[str, float | int]:
    lines = _extract_metric_records(output, prefix="gate_d1_bootstrap_p95")
    if len(lines) != 1:
        raise EvidenceRecordingError("Gate D1 output must contain exactly one bootstrap P95 metric line")
    metrics = _parse_metric_line(lines[0], prefix="gate_d1_bootstrap_p95")
    expected = {"planning_ms", "materialization_ms", "measured_runs"}
    if set(metrics) != expected:
        raise EvidenceRecordingError("Gate D1 bootstrap metric fields are incomplete")
    return {
        "planning_ms": float(metrics["planning_ms"]),
        "materialization_ms": float(metrics["materialization_ms"]),
        "measured_runs": int(metrics["measured_runs"]),
    }


def _parse_gate_e_benchmarks(output: str) -> tuple[dict[str, float | int], ...]:
    rows: list[dict[str, float | int]] = []
    expected_fields = {
        "batch_size",
        "concurrency",
        "duration_p95_ms",
        "duration_p99_ms",
        "queries_max",
        "write_queries_max",
        "lock_wait_p95_ms",
        "lock_wait_p99_ms",
        "deadlocks",
        "lock_timeouts",
        "warmup_runs",
        "measured_runs",
    }
    for line in _extract_metric_records(output, prefix="gate_e_maintenance_benchmark"):
        metrics = _parse_metric_line(line, prefix="gate_e_maintenance_benchmark")
        if set(metrics) != expected_fields:
            raise EvidenceRecordingError("Gate E benchmark metric fields are incomplete")
        rows.append(
            {
                "batch_size": int(metrics["batch_size"]),
                "concurrency": int(metrics["concurrency"]),
                "duration_p95_ms": float(metrics["duration_p95_ms"]),
                "duration_p99_ms": float(metrics["duration_p99_ms"]),
                "queries_max": int(metrics["queries_max"]),
                "write_queries_max": int(metrics["write_queries_max"]),
                "lock_wait_p95_ms": float(metrics["lock_wait_p95_ms"]),
                "lock_wait_p99_ms": float(metrics["lock_wait_p99_ms"]),
                "deadlocks": int(metrics["deadlocks"]),
                "lock_timeouts": int(metrics["lock_timeouts"]),
                "warmup_runs": int(metrics["warmup_runs"]),
                "measured_runs": int(metrics["measured_runs"]),
            }
        )
    rows.sort(key=lambda row: (int(row["batch_size"]), int(row["concurrency"])))
    observed_cells = {(int(row["batch_size"]), int(row["concurrency"])) for row in rows}
    expected_cells = {(batch_size, concurrency) for batch_size in (1, 10, 100) for concurrency in (1, 2)}
    if len(rows) != 6 or observed_cells != expected_cells:
        raise EvidenceRecordingError("Gate E output does not contain the complete six-cell benchmark matrix")
    return tuple(rows)


def _hermetic_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "DJANGO_TEST_USE_ENV_SERVICES": "0",
            "PYTEST_ADDOPTS": "",
        }
    )
    return environment


def _real_service_environment() -> dict[str, str]:
    environment = dict(os.environ)
    mysql_host = str(environment.get("DJANGO_DB_HOST") or "127.0.0.1")
    mysql_port = str(environment.get("DJANGO_DB_PORT") or environment.get("REAL_SERVICES_MYSQL_PORT") or "13306")
    database_name = str(environment.get("DJANGO_DB_NAME") or "webgame")
    if database_name != "webgame":
        raise EvidenceRecordingError("evidence recording requires DJANGO_DB_NAME=webgame")
    redis_port = str(environment.get("REAL_SERVICES_REDIS_PORT") or "16379")
    redis_url = str(environment.get("REDIS_URL") or f"redis://127.0.0.1:{redis_port}")
    parsed_redis_url = urlparse(redis_url)
    redis_base_url = parsed_redis_url._replace(path="", query="", fragment="").geturl().rstrip("/")
    if not redis_base_url:
        raise EvidenceRecordingError("REDIS_URL must contain a valid Redis endpoint")
    test_user = str(environment.get("REAL_SERVICES_TEST_DB_USER") or "root")
    test_password = str(
        environment.get("REAL_SERVICES_TEST_DB_PASSWORD") or environment.get("DJANGO_DB_ROOT_PASSWORD") or "root"
    )
    environment.update(
        {
            "DJANGO_TEST_USE_ENV_SERVICES": "1",
            "DJANGO_DB_ENGINE": "django.db.backends.mysql",
            "DJANGO_DB_HOST": mysql_host,
            "DJANGO_DB_PORT": mysql_port,
            "DJANGO_DB_USER": test_user,
            "DJANGO_DB_PASSWORD": test_password,
            "DJANGO_DB_NAME": database_name,
            "REDIS_URL": redis_base_url,
            "REDIS_BROKER_URL": str(environment.get("REDIS_BROKER_URL") or f"{redis_base_url}/0"),
            "REDIS_RESULT_URL": str(environment.get("REDIS_RESULT_URL") or f"{redis_base_url}/0"),
            "REDIS_CHANNEL_URL": str(environment.get("REDIS_CHANNEL_URL") or f"{redis_base_url}/1"),
            "REDIS_CACHE_URL": str(environment.get("REDIS_CACHE_URL") or f"{redis_base_url}/2"),
            "REDIS_PASSWORD": str(environment.get("REDIS_PASSWORD") or ""),
            "PYTEST_ADDOPTS": "",
        }
    )
    return environment


def _collect_suite(files: Sequence[str], *, real_services: bool) -> SuiteCollection:
    environment = _real_service_environment() if real_services else _hermetic_environment()
    result = _run_command(
        [sys.executable, "-m", "pytest", *files, "--collect-only", "-q"],
        env=environment,
        label=f"collecting {len(files)} evidence test files",
        timeout_seconds=180,
    )
    nodeids = tuple(
        sorted(line.strip() for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line)
    )
    if not nodeids or len(nodeids) != len(set(nodeids)):
        raise EvidenceRecordingError("evidence collection is empty or contains duplicate nodeids")
    checksum_payload = "".join(f"{nodeid}\n" for nodeid in nodeids).encode("utf-8")
    return SuiteCollection(
        files=tuple(files),
        nodeids=nodeids,
        checksum=hashlib.sha256(checksum_payload).hexdigest(),
        collected_at_utc=result.completed_at_utc,
    )


def _require_clean_suite_result(
    summaries: Sequence[PytestSummary],
    *,
    expected_count: int,
    label: str,
) -> PytestSummary:
    if len(summaries) != 1:
        raise EvidenceRecordingError(f"{label} must produce exactly one pytest summary")
    summary = summaries[0]
    if summary.passed != expected_count or summary.failed != 0 or summary.skipped != 0 or summary.deselected != 0:
        raise EvidenceRecordingError(f"{label} execution does not match its frozen collection")
    return summary


def _source_state(
    required_files: frozenset[str],
    *,
    content_overrides: Mapping[str, bytes],
) -> dict[str, Any]:
    digests: dict[str, str] = {}
    for relative_path in sorted(required_files):
        payload = content_overrides.get(relative_path)
        if payload is None:
            path = PROJECT_ROOT / relative_path
            if not path.is_file():
                raise EvidenceRecordingError(f"required evidence source is missing: {relative_path}")
            payload = path.read_bytes()
        digests[relative_path] = hashlib.sha256(payload).hexdigest()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {
        "git_commit": commit,
        "worktree_clean": not bool(status.strip()),
        "evidence_applies_to_exact_file_hashes": True,
        "digest_algorithm": "sha256",
        "files": digests,
    }


def _environment_evidence(environment: Mapping[str, str], *, gate: str) -> dict[str, Any]:
    redis = urlparse(environment["REDIS_URL"])
    common = {
        "database_backend": environment["DJANGO_DB_ENGINE"],
        "database_host": environment["DJANGO_DB_HOST"],
        "database_port": int(environment["DJANGO_DB_PORT"]),
        "database_name": "test_webgame",
        "redis_host": redis.hostname or "127.0.0.1",
        "redis_port": int(redis.port or 6379),
        "services_restarted": False,
    }
    if gate == "d1":
        return {
            "django_test_use_env_services": "1",
            **common,
            "database_role": "isolated_reused_test_database",
            "test_database_recreated": False,
            "credentials_recorded": False,
            "business_database_touched": False,
        }
    return {
        **common,
        "database_role": "isolated_disposable_test_database",
        "business_database_contacted": False,
    }


def _read_test_database_state(environment: Mapping[str, str]) -> dict[str, Any]:
    try:
        import MySQLdb
    except ImportError as exc:
        raise EvidenceRecordingError("mysqlclient is required to inspect test_webgame") from exc
    try:
        database = MySQLdb.connect(
            host=environment["DJANGO_DB_HOST"],
            port=int(environment["DJANGO_DB_PORT"]),
            user=environment["DJANGO_DB_USER"],
            passwd=environment["DJANGO_DB_PASSWORD"],
            db="test_webgame",
            charset="utf8mb4",
        )
        cursor = database.cursor()
        cursor.execute("START TRANSACTION READ ONLY")
        cursor.execute(
            "SELECT COUNT(*) FROM django_migrations WHERE app=%s AND name=%s",
            ("gameplay", "0140_bot_population_recompute_demand"),
        )
        migration_count = int(cursor.fetchone()[0])
        cursor.execute("SELECT COUNT(*) FROM gameplay_botpopulationrecomputedemand")
        demand_count = int(cursor.fetchone()[0])
        database.rollback()
    except Exception as exc:
        raise EvidenceRecordingError("cannot read the isolated test_webgame evidence state") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "database" in locals():
            database.close()
    if migration_count != 1 or demand_count != 0:
        raise EvidenceRecordingError("test_webgame migration or demand-table state is not canonical")
    return {
        "checked_at_utc": _utc_now(),
        "database": "test_webgame",
        "read_only_check": True,
        "migration_app": "gameplay",
        "migration_name": "0140_bot_population_recompute_demand",
        "matching_migration_records": migration_count,
        "demand_table_rows_after_suite": demand_count,
        "migration_executed_by_this_verification": False,
        "database_rebuilt_by_this_verification": False,
    }


def _suite_collection_payload(collection: SuiteCollection) -> dict[str, Any]:
    return {
        "expected_nodeid_count": len(collection.nodeids),
        "nodeid_checksum": collection.checksum,
        "files": list(collection.files),
    }


def _execution_payload(
    *,
    command: str,
    stage: str,
    summary: PytestSummary,
    completed_at_utc: str,
) -> dict[str, Any]:
    return {
        "command": command,
        "target_stage": stage,
        "status": "passed",
        "execution_timestamp_utc": completed_at_utc,
        "timestamp_source": "canonical_target_completion",
        "passed": summary.passed,
        "failed": summary.failed,
        "skipped": summary.skipped,
        "duration_seconds": summary.duration_seconds,
    }


def _yaml_bytes(payload: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(
        dict(payload),
        allow_unicode=False,
        sort_keys=False,
        width=120,
    ).encode("utf-8")


def _build_manifest(
    *,
    template: Mapping[str, Any],
    artifact_date: str,
    environment: Mapping[str, str],
    contract_files: tuple[str, ...],
    real_files: tuple[str, ...],
    contract_collection: SuiteCollection,
    real_collection: SuiteCollection,
    contract_summary: PytestSummary,
    real_summary: PytestSummary,
    completed_at_utc: str,
) -> dict[str, Any]:
    manifest = deepcopy(dict(template))
    date_id = artifact_date.replace("-", "_")
    all_nodeids = tuple(sorted(contract_collection.nodeids + real_collection.nodeids))
    if len(all_nodeids) != len(set(all_nodeids)):
        raise EvidenceRecordingError("Gate A contract and real-service collections overlap")
    checksum_payload = "".join(f"{nodeid}\n" for nodeid in all_nodeids).encode("utf-8")
    detail = (
        f"{contract_summary.passed} contract in {contract_summary.duration_seconds:.2f}s; "
        f"{real_summary.passed} real-service in {real_summary.duration_seconds:.2f}s"
    )
    manifest.update(
        {
            "schema_version": 1,
            "manifest_id": f"virtual_player_gate_a_evidence_{date_id}",
            "recorded_at_utc": completed_at_utc,
            "canonical_gate": {
                "command": GATE_A_COMMAND,
                "make_target": "test-virtual-player-gate-a",
                "environment": {
                    **_environment_evidence(environment, gate="e"),
                    "database_role": "isolated_test_database",
                    "credentials_recorded": False,
                },
                "execution": {
                    "status": "passed",
                    "execution_timestamp_utc": completed_at_utc,
                    "timestamp_status": "captured",
                    "result_summary": f"{len(all_nodeids)} passed ({detail})",
                    "reason": "canonical_target_completed_successfully",
                },
            },
            "suite_selection": {
                "source": "Makefile",
                "make_variables": {
                    GATE_A_CONTRACT_VARIABLE: list(contract_files),
                    GATE_A_REAL_VARIABLE: list(real_files),
                },
                "execution_order": ["contract_tests", "real_service_preflight", "real_service_tests"],
            },
            "collection": {
                "status": "collected",
                "tests_executed": False,
                "collected_at_utc": max(
                    contract_collection.collected_at_utc,
                    real_collection.collected_at_utc,
                ),
                "selection": "union_of_makefile_suite_variables",
                "pytest_arguments": ["--collect-only", "-q"],
                "environment_overrides": {"DJANGO_TEST_USE_ENV_SERVICES": "1"},
                "service_preflight_run": False,
                "external_services_contacted": False,
                "expected_nodeid_count": len(all_nodeids),
                "nodeid_checksum": {
                    "algorithm": "sha256",
                    "value": hashlib.sha256(checksum_payload).hexdigest(),
                    "input": _SHA256_INPUT,
                    "encoding": "utf-8",
                },
            },
        }
    )
    return manifest


def _build_d1_evidence(
    *,
    template: Mapping[str, Any],
    artifact_date: str,
    environment: Mapping[str, str],
    acceptance: Mapping[str, Any],
    collections: Mapping[str, SuiteCollection],
    summaries: Mapping[str, PytestSummary],
    completed_at_utc: str,
    benchmark: Mapping[str, float | int],
    migration: Mapping[str, Any],
    source_state: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = deepcopy(dict(template))
    date_id = artifact_date.replace("-", "_")
    frozen = acceptance["performance"]["bootstrap_single_profile"]
    evidence.update(
        {
            "schema_version": 1,
            "evidence_id": f"virtual_player_gate_d1_evidence_{date_id}",
            "recorded_at_utc": completed_at_utc,
            "environment": _environment_evidence(environment, gate="d1"),
            "source_state": dict(source_state),
            "suite_collection": {
                "checksum_algorithm": "sha256",
                "checksum_input": _SHA256_INPUT,
                "encoding": "utf-8",
                **{name: _suite_collection_payload(collection) for name, collection in collections.items()},
            },
            "executions": {
                name: _execution_payload(
                    command=GATE_D1_COMMAND,
                    stage=name,
                    summary=summaries[name],
                    completed_at_utc=completed_at_utc,
                )
                for name in collections
            },
            "migration_verification": dict(migration),
            "performance": {
                "acceptance_source": str(ACCEPTANCE_PATH.relative_to(PROJECT_ROOT)),
                "benchmark": {
                    "warmup_runs": acceptance["benchmark"]["warmup_runs"],
                    "measured_runs": benchmark["measured_runs"],
                    "percentile_method": "nearest_rank",
                    "profile_band": "mythic",
                },
                "planning_duration_p95_ms": {
                    "threshold": frozen["planning_duration_p95_ms"],
                    "observed": benchmark["planning_ms"],
                    "passed": True,
                },
                "materialization_duration_p95_ms": {
                    "threshold": frozen["materialization_duration_p95_ms"],
                    "observed": benchmark["materialization_ms"],
                    "passed": True,
                },
                "query_budget": {
                    "sql_queries_max": frozen["sql_queries_max"],
                    "write_queries_max": frozen["write_queries_max"],
                    "exact_observed_counts_recorded": False,
                    "contract_assertion_status": "passed",
                },
            },
        }
    )
    evidence["activation_preconditions_outside_this_evidence"]["canonical_gate_a_execution_status"] = "passed"
    evidence["scenario_results"]["deadlocks_observed"] = 0
    evidence["scenario_results"]["lock_timeouts_observed"] = 0
    return evidence


def _gate_e_thresholds(acceptance: Mapping[str, Any]) -> dict[str, Any]:
    performance = acceptance["performance"]
    return {
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


def _build_gate_e_evidence(
    *,
    template: Mapping[str, Any],
    artifact_date: str,
    environment: Mapping[str, str],
    acceptance: Mapping[str, Any],
    manifest: Mapping[str, Any],
    contract_summary: PytestSummary,
    real_summary: PytestSummary,
    completed_at_utc: str,
    benchmark_rows: Sequence[Mapping[str, float | int]],
    mypy_source_files: int,
    source_state: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = deepcopy(dict(template))
    date_id = artifact_date.replace("-", "_")
    matrix = [{**dict(row), "exact_metrics_retained": True, "status": "passed"} for row in benchmark_rows]
    manifest_execution = manifest["canonical_gate"]["execution"]
    result_summary = str(manifest_execution["result_summary"])
    result, separator, detail = result_summary.partition(" (")
    if not separator or not detail.endswith(")"):
        raise EvidenceRecordingError("Gate A manifest result_summary is not canonical")
    evidence.update(
        {
            "schema_version": 1,
            "evidence_id": f"virtual_player_gate_e_readiness_{date_id}",
            "recorded_at_utc": completed_at_utc,
            "environment": _environment_evidence(environment, gate="e"),
            "source_state": dict(source_state),
            "maintenance_benchmark": {
                "test": (
                    "tests/test_virtual_player_maintenance_concurrency_integration.py::"
                    "test_v2_maintenance_meets_frozen_mysql_benchmark_matrix"
                ),
                "warmup_runs": acceptance["benchmark"]["warmup_runs"],
                "measured_runs": acceptance["benchmark"]["measured_runs"],
                "batch_sizes": acceptance["benchmark"]["batch_sizes"],
                "worker_concurrency": acceptance["benchmark"]["worker_concurrency"],
                "thresholds": _gate_e_thresholds(acceptance),
                "matrix": matrix,
                "canonical_execution": {
                    "command": GATE_E_COMMAND,
                    "execution_timestamp_utc": completed_at_utc,
                    "result_summary": f"{real_summary.passed} passed in {real_summary.duration_seconds:.2f}s",
                },
                "all_six_cells_passed": True,
            },
            "regression_evidence": {
                "gate_e_contract": {
                    "command": GATE_E_COMMAND,
                    "result": f"{contract_summary.passed} passed in {contract_summary.duration_seconds:.2f}s",
                },
                "gate_e_real_service": {
                    "command": GATE_E_COMMAND,
                    "result": f"{real_summary.passed} passed in {real_summary.duration_seconds:.2f}s",
                },
                "canonical_gate_a": {
                    "status": "passed",
                    "execution_timestamp_utc": manifest_execution["execution_timestamp_utc"],
                    "result": result,
                    "detail": detail[:-1],
                },
            },
            "static_gates": {
                "full_mypy": {"status": "passed", "source_files": mypy_source_files},
                "black_check": "passed",
                "isort_check": "passed",
                "flake8": "passed",
                "javascript_check": "passed",
                "javascript_tests": "passed",
                "django_check": "passed",
                "makemigrations_check_dry_run": "no_changes_detected",
                "compileall": "passed",
                "git_diff_check": "passed",
            },
        }
    )
    return evidence


def _artifact_paths(artifact_date: str) -> tuple[Path, Path, Path]:
    return (
        DOCS_ROOT / f"virtual_player_gate_evidence_manifest_{artifact_date}.yaml",
        DOCS_ROOT / f"virtual_player_gate_d1_evidence_{artifact_date}.yaml",
        DOCS_ROOT / f"virtual_player_gate_e_readiness_evidence_{artifact_date}.yaml",
    )


def _assert_active_artifact_paths(
    *,
    manifest_path: Path,
    d1_path: Path,
    gate_e_path: Path,
    acceptance: Mapping[str, Any],
) -> None:
    from gameplay.services.virtual_player_core import gate_evidence

    expected = {
        "GATE_A_MANIFEST_PATH": manifest_path,
        "GATE_D1_EVIDENCE_PATH": d1_path,
        "GATE_E_EVIDENCE_PATH": gate_e_path,
    }
    for attribute, output_path in expected.items():
        if Path(getattr(gate_evidence, attribute)).resolve() != output_path.resolve():
            raise EvidenceRecordingError(f"{attribute} must point to {output_path.name} before recording")
    configured_manifest = PROJECT_ROOT / acceptance["evidence_manifest"]["path"]
    if configured_manifest.resolve() != manifest_path.resolve():
        raise EvidenceRecordingError("Gate A acceptance config must point to the requested manifest output")
    source_references = {
        PROJECT_ROOT / "tests" / "test_virtual_player_gate_evidence_manifest.py": manifest_path.name,
        PROJECT_ROOT / "tests" / "test_virtual_player_gate_d1_evidence.py": d1_path.name,
        PROJECT_ROOT / "tests" / "test_virtual_player_gate_e_readiness_evidence.py": gate_e_path.name,
    }
    for source_path, required_name in source_references.items():
        if required_name not in source_path.read_text(encoding="utf-8"):
            raise EvidenceRecordingError(f"{source_path.name} must point to {required_name} before recording")


def _validate_artifact_path(path: Path) -> None:
    if path.resolve().parent != DOCS_ROOT.resolve() or path.suffix != ".yaml":
        raise EvidenceRecordingError(f"refusing to write evidence outside docs: {path}")


def _capture_artifact_state(paths: Sequence[Path], *, replace: bool) -> dict[Path, bytes | None]:
    state: dict[Path, bytes | None] = {}
    for path in paths:
        _validate_artifact_path(path)
        if path.exists() and not replace:
            raise EvidenceRecordingError(f"refusing to overwrite existing evidence without --replace: {path.name}")
        try:
            state[path] = path.read_bytes() if path.exists() else None
        except OSError as exc:
            raise EvidenceRecordingError(f"cannot snapshot existing evidence: {path.name}") from exc
    return state


def _write_artifacts(payloads: Mapping[Path, bytes], *, replace: bool) -> None:
    for path in payloads:
        _validate_artifact_path(path)
        if path.exists() and not replace:
            raise EvidenceRecordingError(f"refusing to overwrite existing evidence without --replace: {path.name}")
    temporary_paths: dict[Path, Path] = {}
    try:
        for destination, payload in payloads.items():
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_paths[destination] = Path(temporary.name)
        for destination, temporary_path in temporary_paths.items():
            os.replace(temporary_path, destination)
    finally:
        for temporary_path in temporary_paths.values():
            if temporary_path.exists():
                temporary_path.unlink()


def _restore_artifacts(state: Mapping[Path, bytes | None]) -> None:
    previous_payloads = {path: payload for path, payload in state.items() if payload is not None}
    if previous_payloads:
        _write_artifacts(previous_payloads, replace=True)
    for path, payload in state.items():
        if payload is not None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise EvidenceRecordingError(f"cannot remove staged evidence: {path.name}") from exc


def _record(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    try:
        parsed_date = datetime.strptime(args.artifact_date, "%Y-%m-%d")
    except ValueError as exc:
        raise EvidenceRecordingError("--artifact-date must use YYYY-MM-DD") from exc
    if parsed_date.strftime("%Y-%m-%d") != args.artifact_date:
        raise EvidenceRecordingError("--artifact-date must be canonical")

    manifest_path, d1_path, gate_e_path = _artifact_paths(args.artifact_date)
    acceptance = _load_yaml(ACCEPTANCE_PATH)
    _assert_active_artifact_paths(
        manifest_path=manifest_path,
        d1_path=d1_path,
        gate_e_path=gate_e_path,
        acceptance=acceptance,
    )
    artifact_state = _capture_artifact_state(
        (manifest_path, d1_path, gate_e_path),
        replace=bool(args.replace),
    )
    manifest_template = _load_yaml(Path(args.manifest_template))
    d1_template = _load_yaml(Path(args.d1_template))
    gate_e_template = _load_yaml(Path(args.gate_e_template))
    real_environment = _real_service_environment()

    _run_command(
        [sys.executable, "scripts/check_env_services_ready.py"],
        env=real_environment,
        label="real-service preflight",
        timeout_seconds=30,
    )
    static_result = _run_command(
        ["make", "static-check"],
        env=_hermetic_environment(),
        label="canonical static checks",
        timeout_seconds=1800,
    )
    mypy_matches = _MYPY_SOURCE_PATTERN.findall(static_result.stdout + "\n" + static_result.stderr)
    if len(mypy_matches) != 1:
        raise EvidenceRecordingError("static-check output must contain one full mypy source count")
    mypy_source_files = int(mypy_matches[0])

    gate_a_contract_files = _read_makefile_paths(GATE_A_CONTRACT_VARIABLE)
    gate_a_real_files = _read_makefile_paths(GATE_A_REAL_VARIABLE)
    d1_files = {
        "contract": _read_makefile_paths(GATE_D1_CONTRACT_VARIABLE),
        "core_real_service": _read_makefile_paths(GATE_D1_CORE_VARIABLE),
        "adjacent_real_service": _read_makefile_paths(GATE_D1_ADJACENT_VARIABLE),
    }
    gate_e_contract_files = _read_makefile_paths(GATE_E_CONTRACT_VARIABLE)
    gate_e_real_files = _read_makefile_paths(GATE_E_REAL_VARIABLE)

    gate_a_contract_collection = _collect_suite(gate_a_contract_files, real_services=True)
    gate_a_real_collection = _collect_suite(gate_a_real_files, real_services=True)
    d1_collections = {name: _collect_suite(files, real_services=name != "contract") for name, files in d1_files.items()}
    gate_e_contract_collection = _collect_suite(gate_e_contract_files, real_services=False)
    gate_e_real_collection = _collect_suite(gate_e_real_files, real_services=True)

    gate_a_result = _run_command(
        ["make", "test-virtual-player-gate-a"],
        env=real_environment,
        label="canonical Gate A",
        timeout_seconds=1800,
    )
    gate_a_summaries = _parse_pytest_summaries(gate_a_result.stdout + "\n" + gate_a_result.stderr)
    if len(gate_a_summaries) != 2:
        raise EvidenceRecordingError("Gate A target must produce two pytest summaries")
    gate_a_contract_summary = _require_clean_suite_result(
        gate_a_summaries[:1],
        expected_count=len(gate_a_contract_collection.nodeids),
        label="Gate A contract",
    )
    gate_a_real_summary = _require_clean_suite_result(
        gate_a_summaries[1:],
        expected_count=len(gate_a_real_collection.nodeids),
        label="Gate A real-service",
    )

    d1_result = _run_command(
        ["make", "test-virtual-player-gate-d1"],
        env=real_environment,
        label="canonical Gate D1",
        timeout_seconds=3600,
    )
    d1_summary_rows = _parse_pytest_summaries(d1_result.stdout + "\n" + d1_result.stderr)
    if len(d1_summary_rows) != 3:
        raise EvidenceRecordingError("Gate D1 target must produce three pytest summaries")
    d1_summaries = {
        name: _require_clean_suite_result(
            d1_summary_rows[index : index + 1],
            expected_count=len(d1_collections[name].nodeids),
            label=f"Gate D1 {name}",
        )
        for index, name in enumerate(d1_collections)
    }
    d1_benchmark = _parse_d1_benchmark(d1_result.stdout + "\n" + d1_result.stderr)

    migration = _read_test_database_state(real_environment)

    manifest = _build_manifest(
        template=manifest_template,
        artifact_date=args.artifact_date,
        environment=real_environment,
        contract_files=gate_a_contract_files,
        real_files=gate_a_real_files,
        contract_collection=gate_a_contract_collection,
        real_collection=gate_a_real_collection,
        contract_summary=gate_a_contract_summary,
        real_summary=gate_a_real_summary,
        completed_at_utc=gate_a_result.completed_at_utc,
    )
    manifest_bytes = _yaml_bytes(manifest)
    manifest_relative_path = str(manifest_path.relative_to(PROJECT_ROOT))
    source_overrides = {manifest_relative_path: manifest_bytes}

    from gameplay.services.virtual_player_core.gate_evidence import (
        GATE_D1_REQUIRED_SOURCE_FILES,
        GATE_E_REQUIRED_SOURCE_FILES,
    )

    d1_evidence = _build_d1_evidence(
        template=d1_template,
        artifact_date=args.artifact_date,
        environment=real_environment,
        acceptance=acceptance,
        collections=d1_collections,
        summaries=d1_summaries,
        completed_at_utc=d1_result.completed_at_utc,
        benchmark=d1_benchmark,
        migration=migration,
        source_state=_source_state(
            GATE_D1_REQUIRED_SOURCE_FILES,
            content_overrides=source_overrides,
        ),
    )
    try:
        _write_artifacts(
            {
                manifest_path: manifest_bytes,
                d1_path: _yaml_bytes(d1_evidence),
            },
            replace=True,
        )
        gate_e_path.unlink(missing_ok=True)

        gate_e_result = _run_command(
            ["make", "test-virtual-player-gate-e"],
            env=real_environment,
            label="canonical Gate E",
            timeout_seconds=7200,
        )
        gate_e_summaries = _parse_pytest_summaries(gate_e_result.stdout + "\n" + gate_e_result.stderr)
        if len(gate_e_summaries) != 2:
            raise EvidenceRecordingError("Gate E target must produce two pytest summaries")
        gate_e_contract_summary = _require_clean_suite_result(
            gate_e_summaries[:1],
            expected_count=len(gate_e_contract_collection.nodeids),
            label="Gate E contract",
        )
        gate_e_real_summary = _require_clean_suite_result(
            gate_e_summaries[1:],
            expected_count=len(gate_e_real_collection.nodeids),
            label="Gate E real-service",
        )
        gate_e_benchmarks = _parse_gate_e_benchmarks(gate_e_result.stdout + "\n" + gate_e_result.stderr)
        gate_e_evidence = _build_gate_e_evidence(
            template=gate_e_template,
            artifact_date=args.artifact_date,
            environment=real_environment,
            acceptance=acceptance,
            manifest=manifest,
            contract_summary=gate_e_contract_summary,
            real_summary=gate_e_real_summary,
            completed_at_utc=gate_e_result.completed_at_utc,
            benchmark_rows=gate_e_benchmarks,
            mypy_source_files=mypy_source_files,
            source_state=_source_state(
                GATE_E_REQUIRED_SOURCE_FILES,
                content_overrides=source_overrides,
            ),
        )
        _write_artifacts(
            {gate_e_path: _yaml_bytes(gate_e_evidence)},
            replace=True,
        )
        _run_command(
            [sys.executable, "-m", "pytest", *_FINAL_VERIFIER_FILES, "-q"],
            env=real_environment,
            label="final evidence verifier",
            timeout_seconds=600,
        )
    except BaseException as exc:
        try:
            _restore_artifacts(artifact_state)
        except EvidenceRecordingError as rollback_error:
            raise EvidenceRecordingError(
                f"evidence recording failed and artifact rollback failed: {rollback_error}"
            ) from exc
        raise
    return manifest_path, d1_path, gate_e_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run canonical virtual-player gates and record content-bound readiness evidence."
    )
    parser.add_argument("--artifact-date", required=True, help="Artifact filename date in YYYY-MM-DD format.")
    parser.add_argument("--manifest-template", type=Path, default=DEFAULT_MANIFEST_TEMPLATE)
    parser.add_argument("--d1-template", type=Path, default=DEFAULT_D1_TEMPLATE)
    parser.add_argument("--gate-e-template", type=Path, default=DEFAULT_GATE_E_TEMPLATE)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly allow replacement when the requested dated artifacts already exist.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = _record(args)
    except EvidenceRecordingError as exc:
        print(f"evidence recording failed: {exc}", file=sys.stderr)
        return 2
    print("recorded and verified evidence:")
    for path in paths:
        print(f"  - {path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
