from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from django.db import connection

from gameplay.services.virtual_player_core import gate_evidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _current_source_files(required_files: frozenset[str]) -> dict[str, str]:
    return {
        relative_path: hashlib.sha256((PROJECT_ROOT / relative_path).read_bytes()).hexdigest()
        for relative_path in sorted(required_files)
    }


def _refresh_source_state(evidence: dict, required_files: frozenset[str]) -> dict:
    evidence["source_state"]["files"] = _current_source_files(required_files)
    evidence["source_state"]["worktree_clean"] = True
    evidence["source_state"]["allowed_dirty_paths"] = []
    return evidence


@pytest.mark.parametrize(
    ("gate", "relative_path"),
    [
        pytest.param("d1", relative_path, id=f"d1-{relative_path}")
        for relative_path in sorted(gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES)
    ]
    + [
        pytest.param("e", relative_path, id=f"e-{relative_path}")
        for relative_path in sorted(gate_evidence.GATE_E_REQUIRED_SOURCE_FILES)
    ],
)
def test_gate_activation_evidence_rejects_any_missing_required_source_file(
    gate: str,
    relative_path: str,
    tmp_path,
    monkeypatch,
) -> None:
    if gate == "d1":
        evidence_path = gate_evidence.GATE_D1_EVIDENCE_PATH
        verifier = gate_evidence.verify_gate_d1_readiness
        path_attribute = "GATE_D1_EVIDENCE_PATH"
    else:
        evidence_path = gate_evidence.GATE_E_EVIDENCE_PATH
        verifier = gate_evidence.verify_gate_e_readiness
        path_attribute = "GATE_E_EVIDENCE_PATH"

    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    evidence["source_state"]["files"].pop(relative_path, None)
    incomplete_path = tmp_path / f"{gate}-missing-source.yaml"
    incomplete_path.write_text(yaml.safe_dump(evidence), encoding="utf-8")
    monkeypatch.setattr(gate_evidence, path_attribute, incomplete_path)

    with pytest.raises(gate_evidence.GateEvidenceError, match="missing required files"):
        verifier()


def test_current_gate_activation_evidence_is_content_bound_to_the_worktree(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gate_evidence, "_current_unclean_paths", lambda _allowed: ())

    d1_evidence = yaml.safe_load(gate_evidence.GATE_D1_EVIDENCE_PATH.read_text(encoding="utf-8"))
    _refresh_source_state(d1_evidence, gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES)
    d1_path = tmp_path / "gate-d1-current.yaml"
    d1_path.write_text(yaml.safe_dump(d1_evidence), encoding="utf-8")
    monkeypatch.setattr(gate_evidence, "GATE_D1_EVIDENCE_PATH", d1_path)

    e_evidence = yaml.safe_load(gate_evidence.GATE_E_EVIDENCE_PATH.read_text(encoding="utf-8"))
    _refresh_source_state(e_evidence, gate_evidence.GATE_E_REQUIRED_SOURCE_FILES)
    e_path = tmp_path / "gate-e-current.yaml"
    e_path.write_text(yaml.safe_dump(e_evidence), encoding="utf-8")
    monkeypatch.setattr(gate_evidence, "GATE_E_EVIDENCE_PATH", e_path)

    d1 = gate_evidence.verify_gate_d1_readiness()
    gate_e = gate_evidence.verify_gate_e_readiness()

    assert d1.gate == "d1"
    assert gate_e.gate == "e"
    assert len(d1.evidence_digest) == len(gate_e.evidence_digest) == 64
    assert d1.evidence_digest != gate_e.evidence_digest

    d1_files = d1_evidence["source_state"]["files"]
    gate_e_files = e_evidence["source_state"]["files"]
    assert gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES <= set(d1_files)
    assert gate_evidence.GATE_E_REQUIRED_SOURCE_FILES <= set(gate_e_files)


def test_gate_evidence_binds_quality_gate_configuration_inputs() -> None:
    expected_configuration_files = {
        ".flake8",
        ".github/workflows/ci.yml",
        "package-lock.json",
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "requirements-dev.txt",
        "requirements.lock.txt",
        "requirements.txt",
        "scripts/check_env_services_ready.py",
    }

    assert expected_configuration_files <= gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES
    assert expected_configuration_files <= gate_evidence.GATE_E_REQUIRED_SOURCE_FILES


def test_gate_d1_verifier_accepts_a_commit_bound_external_artifact(tmp_path, monkeypatch) -> None:
    evidence_path = tmp_path / "gate-d1-commit-bound.yaml"
    evidence_path.write_bytes(gate_evidence.GATE_D1_EVIDENCE_PATH.read_bytes())
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    _refresh_source_state(evidence, gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES)
    evidence_path.write_text(yaml.safe_dump(evidence), encoding="utf-8")
    expected_commit = evidence["source_state"]["git_commit"]
    monkeypatch.setattr(gate_evidence, "_current_unclean_paths", lambda _allowed: ())

    proof = gate_evidence.verify_gate_d1_readiness(
        evidence_path=evidence_path,
        expected_git_commit=expected_commit,
    )

    assert proof.gate == "d1"
    with pytest.raises(gate_evidence.GateEvidenceError, match="expected build commit"):
        gate_evidence.verify_gate_d1_readiness(
            evidence_path=evidence_path,
            expected_git_commit="0" * 40,
        )


def test_gate_e_verifier_checks_an_expected_build_commit(tmp_path, monkeypatch) -> None:
    evidence = yaml.safe_load(gate_evidence.GATE_E_EVIDENCE_PATH.read_text(encoding="utf-8"))
    _refresh_source_state(evidence, gate_evidence.GATE_E_REQUIRED_SOURCE_FILES)
    evidence_path = tmp_path / "gate-e-commit-bound.yaml"
    evidence_path.write_text(yaml.safe_dump(evidence), encoding="utf-8")
    monkeypatch.setattr(gate_evidence, "GATE_E_EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(gate_evidence, "_current_unclean_paths", lambda _allowed: ())
    expected_commit = evidence["source_state"]["git_commit"]

    proof = gate_evidence.verify_gate_e_readiness(expected_git_commit=expected_commit)

    assert proof.gate == "e"
    with pytest.raises(gate_evidence.GateEvidenceError, match="expected build commit"):
        gate_evidence.verify_gate_e_readiness(expected_git_commit="0" * 40)


def test_gate_evidence_requires_a_clean_worktree(tmp_path, monkeypatch) -> None:
    evidence = yaml.safe_load(gate_evidence.GATE_D1_EVIDENCE_PATH.read_text(encoding="utf-8"))
    _refresh_source_state(evidence, gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES)
    evidence_path = tmp_path / "gate-d1-dirty.yaml"
    evidence_path.write_text(yaml.safe_dump(evidence), encoding="utf-8")
    monkeypatch.setattr(
        gate_evidence,
        "_current_unclean_paths",
        lambda _allowed: ("gameplay/services/inventory/core.py",),
    )

    with pytest.raises(gate_evidence.GateEvidenceError, match="dirty outside allowed"):
        gate_evidence.verify_gate_d1_readiness(evidence_path=evidence_path)


def test_gate_evidence_allows_only_declared_generated_artifacts(tmp_path, monkeypatch) -> None:
    evidence = yaml.safe_load(gate_evidence.GATE_D1_EVIDENCE_PATH.read_text(encoding="utf-8"))
    _refresh_source_state(evidence, gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES)
    evidence["source_state"]["allowed_dirty_paths"] = ["docs/generated-evidence.yaml"]
    evidence_path = tmp_path / "gate-d1-generated.yaml"
    evidence_path.write_text(yaml.safe_dump(evidence), encoding="utf-8")
    monkeypatch.setattr(
        gate_evidence,
        "_current_unclean_paths",
        lambda allowed: tuple(path for path in ("docs/generated-evidence.yaml",) if path not in allowed),
    )

    proof = gate_evidence.verify_gate_d1_readiness(evidence_path=evidence_path)

    assert proof.gate == "d1"


def test_gate_activation_evidence_rejects_source_digest_drift(
    tmp_path,
    monkeypatch,
) -> None:
    evidence = yaml.safe_load(gate_evidence.GATE_E_EVIDENCE_PATH.read_text())
    _refresh_source_state(evidence, gate_evidence.GATE_E_REQUIRED_SOURCE_FILES)
    first_path = next(iter(evidence["source_state"]["files"]))
    evidence["source_state"]["files"][first_path] = "0" * 64
    drifted_path = tmp_path / "gate-e-drifted.yaml"
    drifted_path.write_text(yaml.safe_dump(evidence), encoding="utf-8")
    monkeypatch.setattr(gate_evidence, "GATE_E_EVIDENCE_PATH", drifted_path)

    try:
        gate_evidence.verify_gate_e_readiness()
    except gate_evidence.GateEvidenceError as exc:
        assert "source digest changed" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("drifted source evidence must fail closed")


def test_gate_activation_apply_environment_must_match_current_database() -> None:
    settings = connection.settings_dict
    proof = gate_evidence.GateReadinessProof(
        gate="e",
        evidence_id="environment-binding-test",
        evidence_digest="a" * 64,
        recorded_at_utc="2026-07-30T00:00:00Z",
        database_backend=str(settings.get("ENGINE") or ""),
        database_host=str(settings.get("HOST") or ""),
        database_port=int(settings.get("PORT") or 0) or None,
        database_name=str(settings.get("NAME") or ""),
    )

    gate_evidence.assert_current_evidence_environment(proof)

    with pytest.raises(
        gate_evidence.GateEvidenceError,
        match="database_name",
    ):
        gate_evidence.assert_current_evidence_environment(replace(proof, database_name="another_database"))
