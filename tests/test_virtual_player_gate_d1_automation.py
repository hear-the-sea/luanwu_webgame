from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import record_virtual_player_evidence as recorder

ROOT_DIR = Path(__file__).resolve().parents[1]


def test_recorder_d1_mode_requires_an_output_path(capsys) -> None:
    assert recorder.main(["--gate", "d1"]) == 2
    assert "--output is required when --gate d1 is selected" in capsys.readouterr().err


def test_recorder_writes_generated_d1_artifact_atomically_without_overwrite(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(recorder, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(recorder, "_verify_gate_d1_artifact", lambda **_kwargs: None)
    output_path = tmp_path / "test-results" / "gate-d1" / "evidence.yaml"

    recorder._write_verified_d1_artifact(
        destination=output_path,
        payload=b"schema_version: 1\n",
        expected_git_commit="a" * 40,
        replace=False,
    )

    assert output_path.read_bytes() == b"schema_version: 1\n"
    with pytest.raises(recorder.EvidenceRecordingError, match="refusing to overwrite"):
        recorder._write_verified_d1_artifact(
            destination=output_path,
            payload=b"replacement\n",
            expected_git_commit="a" * 40,
            replace=False,
        )
    assert output_path.read_bytes() == b"schema_version: 1\n"


def test_make_and_ci_expose_commit_bound_gate_d1_artifact_flow() -> None:
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")
    workflow_content = (ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "gate-d1-evidence:" in makefile_content
    assert "verify-gate-d1-evidence:" in makefile_content
    assert "GATE_D1_EXPECTED_COMMIT" in makefile_content
    assert "Generate commit-bound Gate D1 evidence" in workflow_content
    assert 'GATE_D1_EXPECTED_COMMIT="${GITHUB_SHA}"' in workflow_content
    assert "gate-d1-evidence-${{ github.sha }}" in workflow_content


def test_ci_runs_gate_d1_in_a_visible_job_after_integration() -> None:
    workflow = yaml.safe_load((ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    gate_d1 = workflow["jobs"]["gate-d1"]

    assert gate_d1["if"] == "always() && github.event_name == 'push'"
    assert gate_d1["needs"] == "integration-tests"
    assert gate_d1["timeout-minutes"] == 60
    assert any(step.get("name") == "Install service probe CLI tools" for step in gate_d1["steps"])
    assert any(step.get("name") == "Generate commit-bound Gate D1 evidence" for step in gate_d1["steps"])
    assert any(step.get("name") == "Upload Gate D1 Diagnostics" for step in gate_d1["steps"])
