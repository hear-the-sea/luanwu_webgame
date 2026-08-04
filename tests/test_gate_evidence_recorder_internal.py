from __future__ import annotations

from scripts import record_virtual_player_evidence as recorder


def test_recorder_allows_temporary_d1_artifact_during_verification(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(recorder, "PROJECT_ROOT", tmp_path)
    calls: list[dict] = []

    def fake_verify(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(recorder, "_verify_gate_d1_artifact", fake_verify)
    output_path = tmp_path / "gate-d1" / "evidence.yaml"

    recorder._write_verified_d1_artifact(
        destination=output_path,
        payload=b"schema_version: 1\n",
        expected_git_commit="a" * 40,
        replace=False,
    )

    assert len(calls) == 1
    allowed_dirty_paths = calls[0]["allowed_dirty_paths"]
    assert len(allowed_dirty_paths) == 1
    assert allowed_dirty_paths[0].startswith("gate-d1/.evidence.yaml.")
