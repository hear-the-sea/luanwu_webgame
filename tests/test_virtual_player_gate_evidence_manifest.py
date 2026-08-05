from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from gameplay.services.virtual_player_core import gate_evidence

pytestmark = pytest.mark.evidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = PROJECT_ROOT / "Makefile"
ACCEPTANCE_CONFIG_PATH = PROJECT_ROOT / "docs" / "virtual_player_gate_a_acceptance_config_2026-07-27.yaml"
MANIFEST_PATH = gate_evidence.GATE_A_MANIFEST_PATH
CANONICAL_COMMAND = "DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-a"
CONTRACT_TESTS_VARIABLE = "VIRTUAL_PLAYER_GATE_A_CONTRACT_TESTS"
REAL_SERVICE_TESTS_VARIABLE = "VIRTUAL_PLAYER_GATE_A_REAL_SERVICE_TESTS"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_makefile_paths(variable: str) -> list[str]:
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
                return values
            index += 1
            fragment = lines[index].strip()

    raise AssertionError(f"Makefile variable {variable} is missing")


def _assert_utc_timestamp(value: object) -> None:
    assert isinstance(value, str)
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    assert parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value


def test_manifest_identity_scope_and_canonical_command_match_acceptance() -> None:
    acceptance = _load_yaml(ACCEPTANCE_CONFIG_PATH)
    manifest = _load_yaml(MANIFEST_PATH)
    acceptance_manifest = acceptance["evidence_manifest"]

    assert PROJECT_ROOT / acceptance_manifest["path"].format(artifact_date=gate_evidence.ARTIFACT_DATE) == MANIFEST_PATH
    assert acceptance_manifest["schema_version"] == manifest["schema_version"] == 1
    assert acceptance_manifest["authorizes_gate_c_schema_or_runtime"] is False
    assert acceptance_manifest["checksum_algorithm"] == manifest["collection"]["nodeid_checksum"]["algorithm"]
    assert acceptance_manifest["checksum_input"] == manifest["collection"]["nodeid_checksum"]["input"]
    assert acceptance_manifest["exact_environment_required"] is True
    assert acceptance_manifest["exact_collection_count_required"] is True
    assert acceptance_manifest["exact_checksum_required"] is True
    assert acceptance_manifest["evidence_timestamp_required"] is True
    assert manifest["canonical_gate"]["command"] == CANONICAL_COMMAND
    assert (
        manifest["scope"]
        | {
            "environment": "test",
            "environment_class": "non_production",
            "production": False,
            "authorizes_production_rollout": False,
            "authorizes_gate_c_schema": False,
            "authorizes_gate_c_worker": False,
            "authorizes_gate_c_runtime": False,
            "authorizes_v2_runtime": False,
        }
        == manifest["scope"]
    )


def test_manifest_suite_selection_matches_makefile_exactly() -> None:
    selection = _load_yaml(MANIFEST_PATH)["suite_selection"]["make_variables"]

    assert selection == {
        CONTRACT_TESTS_VARIABLE: _read_makefile_paths(CONTRACT_TESTS_VARIABLE),
        REAL_SERVICE_TESTS_VARIABLE: _read_makefile_paths(REAL_SERVICE_TESTS_VARIABLE),
    }


def test_manifest_nodeid_count_and_checksum_match_current_collection() -> None:
    manifest = _load_yaml(MANIFEST_PATH)
    selection = manifest["suite_selection"]["make_variables"]
    selected_files = selection[CONTRACT_TESTS_VARIABLE] + selection[REAL_SERVICE_TESTS_VARIABLE]
    collection_environment = {
        **os.environ,
        "DJANGO_TEST_USE_ENV_SERVICES": "1",
        "PYTEST_ADDOPTS": "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *selected_files, "--collect-only", "-q"],
        cwd=PROJECT_ROOT,
        env=collection_environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, f"pytest collection failed:\n{result.stdout}\n{result.stderr}"

    nodeids = sorted(line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line)
    assert len(nodeids) == len(set(nodeids))
    checksum_payload = "".join(f"{nodeid}\n" for nodeid in nodeids).encode("utf-8")
    collection = manifest["collection"]

    assert len(nodeids) == collection["expected_nodeid_count"]
    assert hashlib.sha256(checksum_payload).hexdigest() == collection["nodeid_checksum"]["value"]
    assert (
        collection["nodeid_checksum"]
        | {
            "algorithm": "sha256",
            "input": "sorted_pytest_nodeids_joined_with_lf_and_terminal_lf",
            "encoding": "utf-8",
        }
        == collection["nodeid_checksum"]
    )


def test_manifest_timestamps_and_execution_status_are_honest() -> None:
    manifest = _load_yaml(MANIFEST_PATH)
    execution = manifest["canonical_gate"]["execution"]

    _assert_utc_timestamp(manifest["recorded_at_utc"])
    _assert_utc_timestamp(manifest["collection"]["collected_at_utc"])
    _assert_utc_timestamp(execution["execution_timestamp_utc"])
    assert manifest["collection"]["tests_executed"] is False
    assert execution["status"] == "passed"
    assert execution["timestamp_status"] == "captured"
    assert execution["reason"] == "canonical_target_completed_successfully"
    assert execution["result_summary"].startswith(f'{manifest["collection"]["expected_nodeid_count"]} passed (')
    assert execution["result_summary"].endswith(")")

    assert manifest["historical_evidence"]["may_satisfy_canonical_execution"] is False
    for evidence in manifest["historical_evidence"]["entries"]:
        assert evidence["execution_timestamp_utc"] is None
        assert evidence["timestamp_status"] == "not_captured_in_source_record"
