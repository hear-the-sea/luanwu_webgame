from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
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

from gameplay.services.virtual_player_core.stage_metrics import (  # noqa: E402
    GATE_E_ALLOWED_STAGE_NAMES as _GATE_E_ALLOWED_STAGE_NAMES,
)
from gameplay.services.virtual_player_core.stage_metrics import (  # noqa: E402
    GATE_E_REQUIRED_STAGE_NAMES as _GATE_E_STAGE_NAMES,
)

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
_GIT_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}\Z")
_ARTIFACT_ID_PATTERN = re.compile(r"[A-Za-z0-9_]+\Z")
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


@dataclass(frozen=True, slots=True)
class GateD1Execution:
    gate_a_contract_files: tuple[str, ...]
    gate_a_real_files: tuple[str, ...]
    gate_a_contract_collection: SuiteCollection
    gate_a_real_collection: SuiteCollection
    gate_a_result: CommandResult
    gate_a_contract_summary: PytestSummary
    gate_a_real_summary: PytestSummary
    d1_collections: dict[str, SuiteCollection]
    d1_result: CommandResult
    d1_summaries: dict[str, PytestSummary]
    d1_benchmark: dict[str, float | int]
    migration: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _current_git_commit() -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvidenceRecordingError("cannot resolve the current Git commit") from exc
    if _GIT_COMMIT_PATTERN.fullmatch(commit) is None:
        raise EvidenceRecordingError("current Git commit is not a canonical hexadecimal object id")
    return commit


def _current_git_tree() -> str:
    try:
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvidenceRecordingError("cannot resolve the current Git tree") from exc
    if _GIT_COMMIT_PATTERN.fullmatch(tree) is None:
        raise EvidenceRecordingError("current Git tree is not a canonical hexadecimal object id")
    return tree


def _expected_git_commit(value: str | None) -> str:
    commit = value or _current_git_commit()
    if _GIT_COMMIT_PATTERN.fullmatch(commit) is None:
        raise EvidenceRecordingError("--expected-git-commit must be a canonical hexadecimal object id")
    return commit


def _assert_expected_source_commit(source_state: Mapping[str, Any], expected_commit: str | None) -> None:
    if expected_commit is None:
        return
    observed_commit = source_state.get("git_commit")
    if observed_commit != expected_commit:
        raise EvidenceRecordingError(
            "expected build commit does not match the current Git commit: "
            f"expected {expected_commit}, observed {observed_commit}"
        )


def _artifact_id_suffix(value: str) -> str:
    suffix = value.replace("-", "_")
    if not suffix or _ARTIFACT_ID_PATTERN.fullmatch(suffix) is None:
        raise EvidenceRecordingError("artifact id may contain only letters, digits, underscores, and hyphens")
    return suffix


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


def _decode_stage_fingerprints(value: str) -> list[dict[str, str | int]]:
    if value == "-":
        return []
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceRecordingError("Gate E stage fingerprint payload is not valid base64 JSON") from exc
    if not isinstance(decoded, list) or len(decoded) > 10:
        raise EvidenceRecordingError("Gate E stage fingerprint payload must contain at most ten rows")
    fingerprints: list[dict[str, str | int]] = []
    for item in decoded:
        if not isinstance(item, dict) or set(item) != {"sql", "count"}:
            raise EvidenceRecordingError("Gate E stage fingerprint rows are malformed")
        sql = item["sql"]
        count = item["count"]
        if not isinstance(sql, str) or not sql or isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise EvidenceRecordingError("Gate E stage fingerprint rows contain invalid values")
        fingerprints.append({"sql": sql, "count": count})
    return fingerprints


def _parse_gate_e_stage_metrics(output: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    expected_fields = {
        "batch_size",
        "concurrency",
        "stage",
        "observations",
        "duration_p50_ms",
        "duration_p95_ms",
        "duration_p99_ms",
        "queries_max",
        "write_queries_max",
        "fingerprints_b64",
    }
    for line in _extract_metric_records(output, prefix="gate_e_maintenance_stage"):
        metrics = _parse_metric_line(line, prefix="gate_e_maintenance_stage")
        if set(metrics) != expected_fields:
            raise EvidenceRecordingError("Gate E stage metric fields are incomplete")
        stage = metrics["stage"]
        if stage not in _GATE_E_ALLOWED_STAGE_NAMES:
            raise EvidenceRecordingError(f"Gate E output contains an unknown maintenance stage: {stage}")
        observations = int(metrics["observations"])
        p50 = float(metrics["duration_p50_ms"])
        p95 = float(metrics["duration_p95_ms"])
        p99 = float(metrics["duration_p99_ms"])
        if observations <= 0 or min(p50, p95, p99) < 0 or not p50 <= p95 <= p99:
            raise EvidenceRecordingError("Gate E stage duration metrics are invalid")
        rows.append(
            {
                "batch_size": int(metrics["batch_size"]),
                "concurrency": int(metrics["concurrency"]),
                "stage": stage,
                "observations": observations,
                "duration_p50_ms": p50,
                "duration_p95_ms": p95,
                "duration_p99_ms": p99,
                "queries_max": int(metrics["queries_max"]),
                "write_queries_max": int(metrics["write_queries_max"]),
                "fingerprints": _decode_stage_fingerprints(metrics["fingerprints_b64"]),
            }
        )
    rows.sort(key=lambda row: (int(row["batch_size"]), int(row["concurrency"]), str(row["stage"])))
    expected_keys = {
        (batch_size, concurrency, stage)
        for batch_size in (1, 10, 100)
        for concurrency in (1, 2)
        for stage in _GATE_E_STAGE_NAMES
    }
    observed_keys = {(int(row["batch_size"]), int(row["concurrency"]), str(row["stage"])) for row in rows}
    optional_keys = {
        (batch_size, concurrency, stage)
        for batch_size in (1, 10, 100)
        for concurrency in (1, 2)
        for stage in _GATE_E_ALLOWED_STAGE_NAMES
        if stage not in _GATE_E_STAGE_NAMES
    }
    permitted_keys = expected_keys | optional_keys
    if (
        len(rows) != len(observed_keys)
        or not expected_keys.issubset(observed_keys)
        or not observed_keys.issubset(permitted_keys)
    ):
        raise EvidenceRecordingError("Gate E output does not contain the complete stage metric matrix")
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
    environment.update(
        {
            "CELERY_BROKER_URL": environment["REDIS_BROKER_URL"],
            "CELERY_RESULT_BACKEND": environment["REDIS_RESULT_URL"],
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
    allowed_dirty_paths: frozenset[str] = frozenset(),
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
    normalized_allowed = {str(Path(relative_path)) for relative_path in allowed_dirty_paths}
    dirty_paths = _git_status_paths()
    worktree_clean = not bool(dirty_paths - normalized_allowed)
    return {
        "git_commit": _current_git_commit(),
        "git_tree": _current_git_tree(),
        "worktree_clean": worktree_clean,
        "allowed_dirty_paths": sorted(normalized_allowed),
        "evidence_applies_to_exact_file_hashes": True,
        "digest_algorithm": "sha256",
        "files": digests,
    }


def _git_status_paths() -> set[str]:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvidenceRecordingError("cannot inspect the current Git worktree") from exc
    paths: set[str] = set()
    for line in status.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry:
            paths.add(str(Path(entry)))
    return paths


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
    artifact_id: str | None = None,
    canonical_gate_a_execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = deepcopy(dict(template))
    date_id = _artifact_id_suffix(artifact_id or artifact_date)
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
    if canonical_gate_a_execution is not None:
        evidence["canonical_gate_a_execution"] = dict(canonical_gate_a_execution)
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
    stage_metric_rows: Sequence[Mapping[str, Any]],
    mypy_source_files: int,
    source_state: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = deepcopy(dict(template))
    date_id = artifact_date.replace("-", "_")
    matrix = [{**dict(row), "exact_metrics_retained": True, "status": "passed"} for row in benchmark_rows]
    required_stage_names = frozenset(_GATE_E_STAGE_NAMES)
    observed_stage_names = {str(row["stage"]) for row in stage_metric_rows}
    observed_optional_stage_names = [
        stage
        for stage in _GATE_E_ALLOWED_STAGE_NAMES
        if stage not in required_stage_names and stage in observed_stage_names
    ]
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
                "stage_metrics": {
                    "scope": (
                        "measured Gate E runs; SQL fingerprints are attributed to the innermost active stage; "
                        "duration_ms is exclusive of nested stages; nested stage durations are diagnostic and must not be summed; "
                        "inclusive_duration_ms is retained for tracing"
                    ),
                    "stage_names": list(_GATE_E_STAGE_NAMES),
                    "optional_stage_names": observed_optional_stage_names,
                    "rows": [dict(row) for row in stage_metric_rows],
                },
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
    artifact_date: str,
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
    configured_manifest_template = str(acceptance["evidence_manifest"]["path"])
    try:
        configured_manifest = PROJECT_ROOT / configured_manifest_template.format(artifact_date=artifact_date)
    except (KeyError, ValueError) as exc:
        raise EvidenceRecordingError("Gate A acceptance config has an invalid manifest path template") from exc
    if configured_manifest.resolve() != manifest_path.resolve():
        raise EvidenceRecordingError("Gate A acceptance config must point to the requested manifest output")
    source_references = {
        PROJECT_ROOT
        / "tests"
        / "test_virtual_player_gate_evidence_manifest.py": (
            manifest_path.name,
            "GATE_A_MANIFEST_PATH",
        ),
        PROJECT_ROOT
        / "tests"
        / "test_virtual_player_gate_d1_evidence.py": (
            d1_path.name,
            "GATE_D1_EVIDENCE_PATH",
        ),
        PROJECT_ROOT
        / "tests"
        / "test_virtual_player_gate_e_readiness_evidence.py": (
            gate_e_path.name,
            "GATE_E_EVIDENCE_PATH",
        ),
    }
    for source_path, (required_name, required_reference) in source_references.items():
        source_text = source_path.read_text(encoding="utf-8")
        if required_name not in source_text and required_reference not in source_text:
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


def _run_gate_a_and_d1(real_environment: Mapping[str, str]) -> GateD1Execution:
    gate_a_contract_files = _read_makefile_paths(GATE_A_CONTRACT_VARIABLE)
    gate_a_real_files = _read_makefile_paths(GATE_A_REAL_VARIABLE)
    d1_files = {
        "contract": _read_makefile_paths(GATE_D1_CONTRACT_VARIABLE),
        "core_real_service": _read_makefile_paths(GATE_D1_CORE_VARIABLE),
        "adjacent_real_service": _read_makefile_paths(GATE_D1_ADJACENT_VARIABLE),
    }

    gate_a_contract_collection = _collect_suite(gate_a_contract_files, real_services=True)
    gate_a_real_collection = _collect_suite(gate_a_real_files, real_services=True)
    d1_collections = {name: _collect_suite(files, real_services=name != "contract") for name, files in d1_files.items()}

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

    return GateD1Execution(
        gate_a_contract_files=gate_a_contract_files,
        gate_a_real_files=gate_a_real_files,
        gate_a_contract_collection=gate_a_contract_collection,
        gate_a_real_collection=gate_a_real_collection,
        gate_a_result=gate_a_result,
        gate_a_contract_summary=gate_a_contract_summary,
        gate_a_real_summary=gate_a_real_summary,
        d1_collections=d1_collections,
        d1_result=d1_result,
        d1_summaries=d1_summaries,
        d1_benchmark=d1_benchmark,
        migration=migration,
    )


def _gate_a_execution_payload(execution: GateD1Execution) -> dict[str, Any]:
    return {
        "command": GATE_A_COMMAND,
        "status": "passed",
        "execution_timestamp_utc": execution.gate_a_result.completed_at_utc,
        "contract_passed": execution.gate_a_contract_summary.passed,
        "real_service_passed": execution.gate_a_real_summary.passed,
        "duration_seconds": execution.gate_a_result.duration_seconds,
    }


def _project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _validate_generated_artifact_path(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise EvidenceRecordingError(f"refusing to write evidence outside the project: {path}") from exc
    if resolved.suffix != ".yaml":
        raise EvidenceRecordingError(f"generated Gate D1 evidence must use a .yaml path: {path}")
    return resolved


def _prepare_generated_artifact_path(path: Path, *, replace: bool) -> Path:
    destination = _validate_generated_artifact_path(path)
    if destination.exists() and not replace:
        raise EvidenceRecordingError(f"refusing to overwrite existing generated evidence: {destination}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvidenceRecordingError(f"cannot create generated evidence directory: {destination.parent}") from exc
    return destination


def _verify_gate_d1_artifact(
    *,
    evidence_path: Path,
    expected_git_commit: str,
    allowed_dirty_paths: tuple[str, ...] = (),
) -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from gameplay.services.virtual_player_core.gate_evidence import GateEvidenceError, verify_gate_d1_readiness

    try:
        verify_gate_d1_readiness(
            evidence_path=evidence_path,
            expected_git_commit=expected_git_commit,
            extra_allowed_dirty_paths=allowed_dirty_paths,
        )
    except GateEvidenceError as exc:
        raise EvidenceRecordingError(f"Gate D1 evidence verification failed: {exc}") from exc


def _write_verified_d1_artifact(
    *,
    destination: Path,
    payload: bytes,
    expected_git_commit: str,
    replace: bool,
) -> None:
    destination = _prepare_generated_artifact_path(destination, replace=replace)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        _verify_gate_d1_artifact(
            evidence_path=temporary_path,
            expected_git_commit=expected_git_commit,
            allowed_dirty_paths=(str(temporary_path.relative_to(PROJECT_ROOT)),),
        )
        os.replace(temporary_path, destination)
        temporary_path = None
    except OSError as exc:
        raise EvidenceRecordingError(f"cannot write generated Gate D1 evidence: {destination}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as exc:
                raise EvidenceRecordingError(f"cannot clean temporary Gate D1 evidence: {temporary_path}") from exc


def _record_gate_d1(args: argparse.Namespace) -> Path:
    if args.output is None:
        raise EvidenceRecordingError("--output is required when --gate d1 is selected")
    expected_commit = _expected_git_commit(args.expected_git_commit)
    destination = _prepare_generated_artifact_path(
        _project_path(args.output),
        replace=bool(args.replace),
    )
    artifact_id = args.artifact_id or f"commit_{expected_commit}"
    acceptance = _load_yaml(ACCEPTANCE_PATH)
    d1_template = _load_yaml(_project_path(args.d1_template))
    real_environment = _real_service_environment()

    _run_command(
        [sys.executable, "scripts/check_env_services_ready.py"],
        env=real_environment,
        label="real-service preflight",
        timeout_seconds=30,
    )
    execution = _run_gate_a_and_d1(real_environment)
    from gameplay.services.virtual_player_core.gate_evidence import GATE_D1_REQUIRED_SOURCE_FILES

    source_state = _source_state(
        GATE_D1_REQUIRED_SOURCE_FILES,
        content_overrides={},
        allowed_dirty_paths=frozenset({str(destination.relative_to(PROJECT_ROOT))}),
    )
    if source_state["git_commit"] != expected_commit:
        raise EvidenceRecordingError(
            "expected build commit does not match the current Git commit: "
            f"expected {expected_commit}, observed {source_state['git_commit']}"
        )
    evidence = _build_d1_evidence(
        template=d1_template,
        artifact_date=artifact_id,
        environment=real_environment,
        acceptance=acceptance,
        collections=execution.d1_collections,
        summaries=execution.d1_summaries,
        completed_at_utc=execution.d1_result.completed_at_utc,
        benchmark=execution.d1_benchmark,
        migration=execution.migration,
        artifact_id=artifact_id,
        canonical_gate_a_execution=_gate_a_execution_payload(execution),
        source_state=source_state,
    )
    _write_verified_d1_artifact(
        destination=destination,
        payload=_yaml_bytes(evidence),
        expected_git_commit=expected_commit,
        replace=bool(args.replace),
    )
    return destination


def _record(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    try:
        parsed_date = datetime.strptime(args.artifact_date, "%Y-%m-%d")
    except ValueError as exc:
        raise EvidenceRecordingError("--artifact-date must use YYYY-MM-DD") from exc
    if parsed_date.strftime("%Y-%m-%d") != args.artifact_date:
        raise EvidenceRecordingError("--artifact-date must be canonical")

    expected_commit = _expected_git_commit(args.expected_git_commit) if args.expected_git_commit else None
    manifest_path, d1_path, gate_e_path = _artifact_paths(args.artifact_date)
    acceptance = _load_yaml(ACCEPTANCE_PATH)
    _assert_active_artifact_paths(
        artifact_date=args.artifact_date,
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

    gate_e_contract_files = _read_makefile_paths(GATE_E_CONTRACT_VARIABLE)
    gate_e_real_files = _read_makefile_paths(GATE_E_REAL_VARIABLE)
    gate_e_contract_collection = _collect_suite(gate_e_contract_files, real_services=False)
    gate_e_real_collection = _collect_suite(gate_e_real_files, real_services=True)
    d1_execution = _run_gate_a_and_d1(real_environment)

    manifest = _build_manifest(
        template=manifest_template,
        artifact_date=args.artifact_date,
        environment=real_environment,
        contract_files=d1_execution.gate_a_contract_files,
        real_files=d1_execution.gate_a_real_files,
        contract_collection=d1_execution.gate_a_contract_collection,
        real_collection=d1_execution.gate_a_real_collection,
        contract_summary=d1_execution.gate_a_contract_summary,
        real_summary=d1_execution.gate_a_real_summary,
        completed_at_utc=d1_execution.gate_a_result.completed_at_utc,
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
        collections=d1_execution.d1_collections,
        summaries=d1_execution.d1_summaries,
        completed_at_utc=d1_execution.d1_result.completed_at_utc,
        benchmark=d1_execution.d1_benchmark,
        migration=d1_execution.migration,
        canonical_gate_a_execution=_gate_a_execution_payload(d1_execution),
        source_state=_source_state(
            GATE_D1_REQUIRED_SOURCE_FILES,
            content_overrides=source_overrides,
            allowed_dirty_paths=frozenset(
                str(path.relative_to(PROJECT_ROOT)) for path in (manifest_path, d1_path, gate_e_path)
            ),
        ),
    )
    _assert_expected_source_commit(d1_evidence["source_state"], expected_commit)
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
        gate_e_output = gate_e_result.stdout + "\n" + gate_e_result.stderr
        gate_e_benchmarks = _parse_gate_e_benchmarks(gate_e_output)
        gate_e_stage_metrics = _parse_gate_e_stage_metrics(gate_e_output)
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
            stage_metric_rows=gate_e_stage_metrics,
            mypy_source_files=mypy_source_files,
            source_state=_source_state(
                GATE_E_REQUIRED_SOURCE_FILES,
                content_overrides=source_overrides,
                allowed_dirty_paths=frozenset(
                    str(path.relative_to(PROJECT_ROOT)) for path in (manifest_path, d1_path, gate_e_path)
                ),
            ),
        )
        _assert_expected_source_commit(gate_e_evidence["source_state"], expected_commit)
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


def _verify_gate_all_artifacts(*, artifact_date: str, expected_git_commit: str) -> None:
    from gameplay.services.virtual_player_core import gate_evidence

    manifest_path, d1_path, gate_e_path = _artifact_paths(artifact_date)
    acceptance = _load_yaml(ACCEPTANCE_PATH)
    _assert_active_artifact_paths(
        artifact_date=artifact_date,
        manifest_path=manifest_path,
        d1_path=d1_path,
        gate_e_path=gate_e_path,
        acceptance=acceptance,
    )
    gate_evidence.verify_gate_d1_readiness(expected_git_commit=expected_git_commit)
    gate_evidence.verify_gate_e_readiness(expected_git_commit=expected_git_commit)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run canonical virtual-player gates and record content-bound readiness evidence."
    )
    parser.add_argument(
        "--gate",
        choices=("all", "d1"),
        default="all",
        help="Record or verify the complete historical bundle, or handle the commit-bound Gate D1 artifact.",
    )
    parser.add_argument("--artifact-date", help="Artifact filename date in YYYY-MM-DD format for --gate all.")
    parser.add_argument("--artifact-id", help="Stable suffix for a generated Gate D1 evidence_id.")
    parser.add_argument("--output", type=Path, help="Output path for a generated or verified Gate D1 artifact.")
    parser.add_argument("--expected-git-commit", help="Require generated evidence to bind to this Git commit.")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify existing evidence instead of running the test gates.",
    )
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
        if args.gate == "d1":
            if args.output is None:
                raise EvidenceRecordingError("--output is required when --gate d1 is selected")
            expected_commit = _expected_git_commit(args.expected_git_commit)
            output_path = _project_path(args.output)
            if args.verify:
                output_path = _validate_generated_artifact_path(output_path)
                _verify_gate_d1_artifact(
                    evidence_path=output_path,
                    expected_git_commit=expected_commit,
                )
                print(f"verified Gate D1 evidence: {output_path.relative_to(PROJECT_ROOT)}")
                return 0
            path = _record_gate_d1(args)
            print(f"recorded and verified Gate D1 evidence: {path.relative_to(PROJECT_ROOT)}")
            return 0
        if not args.artifact_date:
            raise EvidenceRecordingError("--artifact-date is required when --gate all is selected")
        if args.verify:
            expected_commit = _expected_git_commit(args.expected_git_commit)
            _verify_gate_all_artifacts(
                artifact_date=args.artifact_date,
                expected_git_commit=expected_commit,
            )
            print(f"verified Gate E readiness evidence: {args.artifact_date}")
            return 0
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
