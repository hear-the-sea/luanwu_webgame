from __future__ import annotations

from dataclasses import replace

import pytest
import yaml
from django.db import connection

from gameplay.services.virtual_player_core import gate_evidence


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


def test_current_gate_activation_evidence_is_content_bound_to_the_worktree() -> None:
    d1 = gate_evidence.verify_gate_d1_readiness()
    gate_e = gate_evidence.verify_gate_e_readiness()

    assert d1.gate == "d1"
    assert gate_e.gate == "e"
    assert len(d1.evidence_digest) == len(gate_e.evidence_digest) == 64
    assert d1.evidence_digest != gate_e.evidence_digest

    d1_files = yaml.safe_load(gate_evidence.GATE_D1_EVIDENCE_PATH.read_text(encoding="utf-8"))["source_state"]["files"]
    gate_e_files = yaml.safe_load(gate_evidence.GATE_E_EVIDENCE_PATH.read_text(encoding="utf-8"))["source_state"][
        "files"
    ]
    assert gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES <= set(d1_files)
    assert gate_evidence.GATE_E_REQUIRED_SOURCE_FILES <= set(gate_e_files)


def test_gate_activation_evidence_rejects_source_digest_drift(
    tmp_path,
    monkeypatch,
) -> None:
    evidence = yaml.safe_load(gate_evidence.GATE_E_EVIDENCE_PATH.read_text())
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
