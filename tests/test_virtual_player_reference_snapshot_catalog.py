from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from gameplay.services.virtual_player_core import reference_snapshot_catalog as catalog_module
from gameplay.services.virtual_player_core.calibration import canonical_snapshot_digest
from gameplay.services.virtual_player_core.config import ReferenceSnapshotCatalogEntry, parse_bot_development_v2
from gameplay.services.virtual_player_core.reference_snapshot_catalog import (
    ReferenceSnapshotCatalogError,
    load_configured_reference_snapshot,
    load_reference_snapshot_artifact,
    load_strict_json_document,
    resolve_project_data_json_path,
)
from tests.yaml_schema_new_configs.virtual_players import _minimal_v2_config

BANDS = (
    "newbie",
    "junior",
    "middle",
    "senior",
    "veteran",
    "elite",
    "legend",
    "mythic",
)
BAND_PRESTIGE = {
    "newbie": 100,
    "junior": 1_000,
    "middle": 4_000,
    "senior": 12_000,
    "veteran": 40_000,
    "elite": 80_000,
    "legend": 160_000,
    "mythic": 280_000,
}


def _profile(*, version: int, band: str, index: int) -> dict[str, int | str]:
    digest = sha256(f"fixture:{version}:{band}:{index}".encode("ascii")).hexdigest()
    return {
        "business_key": f"human-ref-v{version}:{digest}",
        "prestige": BAND_PRESTIGE[band] + index,
        "core_building_level": index + 1,
        "guest_count": index % 9 + 1,
        "max_guest_level": index + 5,
        "arena_lineup_power": (index + 1) * 1_000,
        "troop_total": (index + 1) * 500,
    }


def _artifact(*, version: int = 3) -> dict:
    return {
        "schema_version": 1,
        "reference_snapshot_version": version,
        "bands": {
            band: {
                "profile_count": 1,
                "profiles": [_profile(version=version, band=band, index=index)],
            }
            for index, band in enumerate(BANDS)
        },
    }


def _write_artifact(project_root: Path, payload: dict) -> ReferenceSnapshotCatalogEntry:
    relative_path = "data/virtual_player_reference_snapshots/v3.json"
    path = project_root / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    return ReferenceSnapshotCatalogEntry(
        reference_snapshot_version=3,
        schema_version=1,
        digest=canonical_snapshot_digest(payload),
        artifact_path=relative_path,
    )


def test_reference_snapshot_loader_validates_identity_and_deeply_freezes_payload(
    tmp_path: Path,
) -> None:
    entry = _write_artifact(tmp_path, _artifact())

    snapshot = load_reference_snapshot_artifact(entry, project_root=tmp_path)

    assert snapshot.reference_snapshot_version == 3
    assert snapshot.digest == entry.digest
    assert tuple(snapshot.bands) == BANDS
    assert snapshot.band("elite").profile_count == 1
    assert snapshot.band("elite").profiles[0]["guest_count"] == 6
    with pytest.raises(TypeError):
        snapshot.bands["elite"] = snapshot.band("elite")  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.band("elite").profiles[0]["guest_count"] = 99  # type: ignore[index]


def test_reference_snapshot_loader_rejects_digest_tampering(tmp_path: Path) -> None:
    entry = _write_artifact(tmp_path, _artifact())
    path = tmp_path / entry.artifact_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["bands"]["newbie"]["profiles"][0]["guest_count"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReferenceSnapshotCatalogError, match="digest"):
        load_reference_snapshot_artifact(entry, project_root=tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda payload: payload.update(schema_version=2), "schema"),
        (lambda payload: payload.update(schema_version=1.0), "schema version"),
        (
            lambda payload: payload.update(reference_snapshot_version=4),
            "version",
        ),
        (
            lambda payload: payload.update(reference_snapshot_version=3.0),
            "artifact version",
        ),
        (lambda payload: payload["bands"].pop("mythic"), "all V2 bands"),
        (
            lambda payload: payload["bands"].update(unknown={"profile_count": 0, "profiles": []}),
            "all V2 bands",
        ),
        (
            lambda payload: payload["bands"]["newbie"].update(profile_count=2),
            "does not match profiles",
        ),
        (
            lambda payload: payload["bands"]["newbie"]["profiles"][0].update(username="private"),
            "identifying fields",
        ),
        (
            lambda payload: payload["bands"]["newbie"]["profiles"][0].update(nested={"user_id": 7}),
            "identifying fields",
        ),
        (
            lambda payload: payload["bands"]["junior"]["profiles"][0].update(
                business_key=payload["bands"]["newbie"]["profiles"][0]["business_key"]
            ),
            "duplicate business_key",
        ),
        (
            lambda payload: payload["bands"]["newbie"]["profiles"][0].update(business_key="anonymous:newbie:1"),
            "anonymized snapshot HMAC",
        ),
        (
            lambda payload: payload["bands"]["newbie"]["profiles"][0].pop("troop_total"),
            "missing troop_total",
        ),
        (
            lambda payload: payload["bands"]["newbie"]["profiles"][0].update(displayName="private"),
            "unknown displayName",
        ),
    ),
)
def test_reference_snapshot_loader_rejects_invalid_schema_version_bands_and_profiles(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    payload = _artifact()
    mutation(payload)
    entry = _write_artifact(tmp_path, payload)
    if payload["schema_version"] != 1:
        entry = replace(entry, schema_version=2)
    if payload["reference_snapshot_version"] != 3:
        entry = replace(entry, reference_snapshot_version=3)

    with pytest.raises(ReferenceSnapshotCatalogError, match=message):
        load_reference_snapshot_artifact(entry, project_root=tmp_path)


@pytest.mark.parametrize(
    "artifact_path",
    (
        "/data/snapshot.json",
        "reports/snapshot.json",
        "data/../reports/snapshot.json",
        "data\\snapshot.json",
        "data//snapshot.json",
        "data/snapshot.yaml",
    ),
)
def test_reference_snapshot_paths_reject_noncanonical_or_out_of_scope_values(
    tmp_path: Path,
    artifact_path: str,
) -> None:
    (tmp_path / "data").mkdir()

    with pytest.raises(ReferenceSnapshotCatalogError, match="canonical"):
        resolve_project_data_json_path(artifact_path, project_root=tmp_path)


def test_reference_snapshot_path_rejects_a_symlink_escape(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    outside = tmp_path / "outside"
    data_root.mkdir()
    outside.mkdir()
    (outside / "snapshot.json").write_text("{}", encoding="utf-8")
    (data_root / "escape.json").symlink_to(outside / "snapshot.json")

    with pytest.raises(ReferenceSnapshotCatalogError, match="inside"):
        resolve_project_data_json_path(
            "data/escape.json",
            project_root=tmp_path,
        )


def test_reference_snapshot_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    entry = _write_artifact(tmp_path, _artifact())
    path = tmp_path / entry.artifact_path
    path.write_text(
        '{"schema_version":1,"schema_version":1,"reference_snapshot_version":3,"bands":{}}',
        encoding="utf-8",
    )
    entry = replace(entry, digest="0" * 64)

    with pytest.raises(ReferenceSnapshotCatalogError, match="duplicate key"):
        load_reference_snapshot_artifact(entry, project_root=tmp_path)


def test_configured_reference_snapshot_rejects_cross_band_prestige(
    tmp_path: Path,
) -> None:
    payload = _artifact()
    payload["bands"]["newbie"]["profiles"][0]["prestige"] = BAND_PRESTIGE["junior"]
    entry = _write_artifact(tmp_path, payload)
    raw_config = _minimal_v2_config()
    raw_config["reference_snapshot_catalog"] = {
        "3": {
            "schema_version": entry.schema_version,
            "digest": entry.digest,
            "artifact_path": entry.artifact_path,
        }
    }
    config = parse_bot_development_v2(raw_config)

    with pytest.raises(ReferenceSnapshotCatalogError, match="outside its band"):
        load_configured_reference_snapshot(3, config=config, project_root=tmp_path)


def test_strict_json_loader_rejects_an_oversized_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "oversized.json"
    path.write_text("{" + " " * 64 + "}", encoding="utf-8")
    monkeypatch.setattr(catalog_module, "STRICT_JSON_DOCUMENT_MAX_BYTES", 64)

    with pytest.raises(ReferenceSnapshotCatalogError, match="byte limit"):
        load_strict_json_document(path, label="oversized fixture")


def test_strict_json_loader_normalizes_python_integer_limit_errors(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oversized-integer.json"
    path.write_text('{"value":' + "9" * 5_000 + "}", encoding="utf-8")

    with pytest.raises(ReferenceSnapshotCatalogError, match="unable to load"):
        load_strict_json_document(path, label="integer-limit fixture")
