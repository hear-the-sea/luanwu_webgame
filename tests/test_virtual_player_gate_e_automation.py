from __future__ import annotations

from pathlib import Path

import pytest

from gameplay.services.virtual_player_core import gate_evidence
from scripts import record_virtual_player_evidence as recorder

ROOT_DIR = Path(__file__).resolve().parents[1]


def test_makefile_exposes_commit_bound_gate_e_readiness_recording_and_verification() -> None:
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "gate-e-readiness-evidence:" in makefile_content
    assert "verify-gate-e-readiness-evidence:" in makefile_content
    assert "VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE" in makefile_content
    assert "GATE_E_EXPECTED_COMMIT" in makefile_content
    assert "--gate all" in makefile_content
    assert "--verify" in makefile_content
    assert "--artifact-date" in makefile_content
    assert "--expected-git-commit" in makefile_content
    assert "--replace" in makefile_content


def test_readiness_workflow_runs_only_on_release_tags_schedule_or_manual_dispatch() -> None:
    workflow_content = (ROOT_DIR / ".github" / "workflows" / "virtual_player_readiness.yml").read_text(encoding="utf-8")

    assert 'tags:\n      - "v*"' in workflow_content
    assert 'cron: "17 2 * * *"' in workflow_content
    assert "workflow_dispatch:" in workflow_content
    assert "concurrency:" in workflow_content
    assert "cancel-in-progress: false" in workflow_content
    assert "timeout-minutes: 60" in workflow_content
    assert "make gate-e-readiness-evidence" in workflow_content
    assert "date -u +%F" in workflow_content
    assert "env.VIRTUAL_PLAYER_EVIDENCE_ARTIFACT_DATE" in workflow_content
    assert 'GATE_E_EXPECTED_COMMIT="${GITHUB_SHA}"' in workflow_content
    assert "make verify-gate-e-readiness-evidence" in workflow_content
    assert "actions/upload-artifact@v4" in workflow_content
    assert "if-no-files-found: error" in workflow_content


def test_gate_evidence_binds_readiness_workflow_and_automation_contract() -> None:
    required_files = gate_evidence.GATE_E_REQUIRED_SOURCE_FILES

    assert ".github/workflows/virtual_player_readiness.yml" in required_files
    assert "gameplay/migrations/0147_backfill_grain_warehouse_ledger.py" in required_files
    assert "gameplay/migrations/0148_bot_runtime_safety_window_kind.py" in required_files
    assert "gameplay/migrations/0149_botruntimeroutingstate_paused_from_maintenance_mode_and_more.py" in required_files
    assert "gameplay/migrations/0150_botarenashortagebaseline_expires_at_and_more.py" in required_files
    assert "tests/test_virtual_player_gate_e_automation.py" in required_files


def test_full_evidence_recording_rejects_an_unexpected_build_commit() -> None:
    with pytest.raises(recorder.EvidenceRecordingError, match="expected build commit"):
        recorder._assert_expected_source_commit(
            {"git_commit": "a" * 40},
            "b" * 40,
        )
