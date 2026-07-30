from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from django.conf import settings

from .calibration import MAX_PROFILES_PER_COHORT, canonical_snapshot_digest
from .config import (
    V2_PRESTIGE_BAND_NAMES,
    ReferenceSnapshotCatalogEntry,
    VirtualPlayerV2Config,
    load_virtual_player_v2_config,
)

REFERENCE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION = 1
STRICT_JSON_DOCUMENT_MAX_BYTES = 8 * 1024 * 1024
STRICT_JSON_DOCUMENT_MAX_DEPTH = 32
STRICT_JSON_DOCUMENT_MAX_NODES = 200_000
_ARTIFACT_FIELDS = frozenset({"schema_version", "reference_snapshot_version", "bands"})
_BAND_FIELDS = frozenset({"profile_count", "profiles"})
_PROFILE_FIELDS = frozenset(
    {
        "business_key",
        "prestige",
        "core_building_level",
        "guest_count",
        "max_guest_level",
        "arena_lineup_power",
        "troop_total",
    }
)
_FORBIDDEN_PROFILE_FIELDS = frozenset(
    {
        "address",
        "display_name",
        "email",
        "ip_address",
        "last_login",
        "manor_id",
        "manor_name",
        "password",
        "phone",
        "session",
        "token",
        "user_id",
        "username",
    }
)


class ReferenceSnapshotCatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReferenceSnapshotBand:
    prestige_band: str
    profile_count: int
    profiles: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ReferenceSnapshotArtifact:
    schema_version: int
    reference_snapshot_version: int
    digest: str
    artifact_path: str
    bands: Mapping[str, ReferenceSnapshotBand]

    def band(self, prestige_band: str) -> ReferenceSnapshotBand:
        try:
            return self.bands[prestige_band]
        except KeyError as exc:
            raise ReferenceSnapshotCatalogError(f"reference snapshot has no band {prestige_band!r}") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReferenceSnapshotCatalogError(f"reference snapshot JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ReferenceSnapshotCatalogError(f"reference snapshot JSON contains non-finite value {value}")


def load_strict_json_document(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        if path.stat().st_size > STRICT_JSON_DOCUMENT_MAX_BYTES:
            raise ReferenceSnapshotCatalogError(f"{label} exceeds the {STRICT_JSON_DOCUMENT_MAX_BYTES}-byte limit")
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ReferenceSnapshotCatalogError:
        raise
    except (
        OSError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise ReferenceSnapshotCatalogError(f"unable to load {label} from {path}") from exc
    if not isinstance(payload, Mapping):
        raise ReferenceSnapshotCatalogError(f"{label} root must be a mapping")
    stack: list[tuple[Any, int]] = [(payload, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > STRICT_JSON_DOCUMENT_MAX_DEPTH:
            raise ReferenceSnapshotCatalogError(f"{label} exceeds the maximum JSON depth")
        if nodes > STRICT_JSON_DOCUMENT_MAX_NODES:
            raise ReferenceSnapshotCatalogError(f"{label} exceeds the maximum JSON node count")
        if isinstance(value, Mapping):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    return payload


def resolve_project_data_json_path(
    artifact_path: str,
    *,
    project_root: Path | None = None,
) -> Path:
    if not isinstance(artifact_path, str) or not artifact_path:
        raise ReferenceSnapshotCatalogError("artifact path must be a project-relative data JSON path")
    relative = PurePosixPath(artifact_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != artifact_path
        or not relative.parts
        or relative.parts[0] != "data"
        or ".." in relative.parts
        or relative.suffix != ".json"
        or "\\" in artifact_path
    ):
        raise ReferenceSnapshotCatalogError("artifact path must be a canonical project-relative data JSON path")
    root = Path(settings.BASE_DIR if project_root is None else project_root).resolve()
    data_root = (root / "data").resolve()
    try:
        resolved = (root / Path(*relative.parts)).resolve(strict=True)
    except OSError as exc:
        raise ReferenceSnapshotCatalogError(f"artifact path does not exist: {artifact_path}") from exc
    if not resolved.is_relative_to(data_root) or not resolved.is_file():
        raise ReferenceSnapshotCatalogError("artifact path must resolve to a file inside the project data directory")
    return resolved


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _forbidden_profile_fields(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        forbidden = set(value).intersection(_FORBIDDEN_PROFILE_FIELDS)
        for item in value.values():
            forbidden.update(_forbidden_profile_fields(item))
        return forbidden
    if isinstance(value, list):
        nested_forbidden: set[str] = set()
        for item in value:
            nested_forbidden.update(_forbidden_profile_fields(item))
        return nested_forbidden
    return set()


def _strict_fields(value: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    fields = set(value)
    if fields == expected:
        return
    missing = sorted(expected - fields)
    unknown = sorted(fields - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unknown:
        details.append(f"unknown {', '.join(unknown)}")
    raise ReferenceSnapshotCatalogError(f"{label} has {'; '.join(details)}")


def _positive_or_zero_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReferenceSnapshotCatalogError(f"{label} must be a non-negative integer")
    return value


def _parse_band(
    prestige_band: str,
    value: Any,
    *,
    reference_snapshot_version: int,
) -> ReferenceSnapshotBand:
    label = f"reference snapshot band {prestige_band!r}"
    if not isinstance(value, Mapping):
        raise ReferenceSnapshotCatalogError(f"{label} must be a mapping")
    _strict_fields(value, _BAND_FIELDS, label=label)
    profile_count = _positive_or_zero_int(
        value["profile_count"],
        label=f"{label}.profile_count",
    )
    if profile_count > MAX_PROFILES_PER_COHORT:
        raise ReferenceSnapshotCatalogError(f"{label}.profile_count exceeds {MAX_PROFILES_PER_COHORT}")
    raw_profiles = value["profiles"]
    if not isinstance(raw_profiles, list):
        raise ReferenceSnapshotCatalogError(f"{label}.profiles must be a list")
    if len(raw_profiles) != profile_count:
        raise ReferenceSnapshotCatalogError(f"{label}.profile_count does not match profiles")
    profiles: list[Mapping[str, Any]] = []
    business_keys: set[str] = set()
    for index, raw_profile in enumerate(raw_profiles):
        profile_label = f"{label}.profiles[{index}]"
        if not isinstance(raw_profile, Mapping):
            raise ReferenceSnapshotCatalogError(f"{profile_label} must be a mapping")
        forbidden = sorted(_forbidden_profile_fields(raw_profile))
        if forbidden:
            raise ReferenceSnapshotCatalogError(f"{profile_label} contains identifying fields: {', '.join(forbidden)}")
        _strict_fields(raw_profile, _PROFILE_FIELDS, label=profile_label)
        business_key = raw_profile.get("business_key")
        expected_prefix = f"human-ref-v{reference_snapshot_version}:"
        if (
            not isinstance(business_key, str)
            or not business_key.startswith(expected_prefix)
            or len(business_key) != len(expected_prefix) + 64
            or any(character not in "0123456789abcdef" for character in business_key[len(expected_prefix) :])
        ):
            raise ReferenceSnapshotCatalogError(f"{profile_label}.business_key must be an anonymized snapshot HMAC")
        if business_key in business_keys:
            raise ReferenceSnapshotCatalogError(f"{label} contains duplicate business_key {business_key!r}")
        business_keys.add(business_key)
        _positive_or_zero_int(
            raw_profile["prestige"],
            label=f"{profile_label}.prestige",
        )
        core_building_level = _positive_or_zero_int(
            raw_profile["core_building_level"],
            label=f"{profile_label}.core_building_level",
        )
        guest_count = _positive_or_zero_int(
            raw_profile["guest_count"],
            label=f"{profile_label}.guest_count",
        )
        max_guest_level = _positive_or_zero_int(
            raw_profile["max_guest_level"],
            label=f"{profile_label}.max_guest_level",
        )
        _positive_or_zero_int(
            raw_profile["arena_lineup_power"],
            label=f"{profile_label}.arena_lineup_power",
        )
        _positive_or_zero_int(
            raw_profile["troop_total"],
            label=f"{profile_label}.troop_total",
        )
        if core_building_level < 1:
            raise ReferenceSnapshotCatalogError(f"{profile_label}.core_building_level must be positive")
        if guest_count < 1 or max_guest_level < 1:
            raise ReferenceSnapshotCatalogError(f"{profile_label} must contain a non-empty valid guest roster")
        profiles.append(_freeze_json_value(raw_profile))
    return ReferenceSnapshotBand(
        prestige_band=prestige_band,
        profile_count=profile_count,
        profiles=tuple(profiles),
    )


def load_reference_snapshot_artifact(
    entry: ReferenceSnapshotCatalogEntry,
    *,
    project_root: Path | None = None,
) -> ReferenceSnapshotArtifact:
    if (
        isinstance(entry.schema_version, bool)
        or not isinstance(entry.schema_version, int)
        or entry.schema_version != REFERENCE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION
    ):
        raise ReferenceSnapshotCatalogError(f"unsupported reference snapshot schema version {entry.schema_version}")
    if (
        isinstance(entry.reference_snapshot_version, bool)
        or not isinstance(entry.reference_snapshot_version, int)
        or entry.reference_snapshot_version < 1
    ):
        raise ReferenceSnapshotCatalogError("reference snapshot catalog version must be a positive integer")
    resolved = resolve_project_data_json_path(
        entry.artifact_path,
        project_root=project_root,
    )
    raw = load_strict_json_document(resolved, label="reference snapshot artifact")
    _strict_fields(raw, _ARTIFACT_FIELDS, label="reference snapshot artifact")
    try:
        digest = canonical_snapshot_digest(raw)
    except (TypeError, ValueError) as exc:
        raise ReferenceSnapshotCatalogError("reference snapshot artifact is not canonical JSON data") from exc
    if digest != entry.digest:
        raise ReferenceSnapshotCatalogError("reference snapshot artifact digest does not match the catalog")
    raw_schema_version = raw["schema_version"]
    if isinstance(raw_schema_version, bool) or not isinstance(raw_schema_version, int):
        raise ReferenceSnapshotCatalogError("reference snapshot artifact schema version must be an integer")
    if raw_schema_version != entry.schema_version:
        raise ReferenceSnapshotCatalogError("reference snapshot artifact schema does not match the catalog")
    raw_snapshot_version = raw["reference_snapshot_version"]
    if isinstance(raw_snapshot_version, bool) or not isinstance(raw_snapshot_version, int):
        raise ReferenceSnapshotCatalogError("reference snapshot artifact version must be an integer")
    if raw_snapshot_version != entry.reference_snapshot_version:
        raise ReferenceSnapshotCatalogError("reference snapshot artifact version does not match the catalog")
    raw_bands = raw["bands"]
    if not isinstance(raw_bands, Mapping):
        raise ReferenceSnapshotCatalogError("reference snapshot artifact bands must be a mapping")
    if set(raw_bands) != set(V2_PRESTIGE_BAND_NAMES):
        raise ReferenceSnapshotCatalogError("reference snapshot artifact bands must contain exactly all V2 bands")
    bands = {
        prestige_band: _parse_band(
            prestige_band,
            raw_bands[prestige_band],
            reference_snapshot_version=entry.reference_snapshot_version,
        )
        for prestige_band in V2_PRESTIGE_BAND_NAMES
    }
    business_key_bands: dict[str, str] = {}
    for prestige_band, band in bands.items():
        for profile in band.profiles:
            business_key = str(profile["business_key"])
            previous_band = business_key_bands.get(business_key)
            if previous_band is not None:
                raise ReferenceSnapshotCatalogError(
                    "reference snapshot contains duplicate business_key "
                    f"{business_key!r} across bands {previous_band!r} and "
                    f"{prestige_band!r}"
                )
            business_key_bands[business_key] = prestige_band
    return ReferenceSnapshotArtifact(
        schema_version=entry.schema_version,
        reference_snapshot_version=entry.reference_snapshot_version,
        digest=digest,
        artifact_path=entry.artifact_path,
        bands=MappingProxyType(bands),
    )


def load_configured_reference_snapshot(
    reference_snapshot_version: int,
    *,
    config: VirtualPlayerV2Config | None = None,
    project_root: Path | None = None,
) -> ReferenceSnapshotArtifact:
    resolved_config = config or load_virtual_player_v2_config()
    if resolved_config is None:
        raise ReferenceSnapshotCatalogError("virtual-player V2 configuration is unavailable")
    try:
        entry = resolved_config.reference_snapshot_catalog[int(reference_snapshot_version)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ReferenceSnapshotCatalogError(
            f"reference snapshot version {reference_snapshot_version!r} is not cataloged"
        ) from exc
    artifact = load_reference_snapshot_artifact(entry, project_root=project_root)
    configured_bands = {band.name: band for band in resolved_config.bands}
    for prestige_band, snapshot_band in artifact.bands.items():
        configured_band = configured_bands[prestige_band]
        for index, profile in enumerate(snapshot_band.profiles):
            prestige = int(profile["prestige"])
            if not configured_band.contains(prestige):
                raise ReferenceSnapshotCatalogError(
                    "reference snapshot profile prestige is outside its band: " f"{prestige_band}.profiles[{index}]"
                )
    return artifact


__all__ = [
    "REFERENCE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION",
    "STRICT_JSON_DOCUMENT_MAX_BYTES",
    "STRICT_JSON_DOCUMENT_MAX_DEPTH",
    "STRICT_JSON_DOCUMENT_MAX_NODES",
    "ReferenceSnapshotArtifact",
    "ReferenceSnapshotBand",
    "ReferenceSnapshotCatalogError",
    "load_configured_reference_snapshot",
    "load_reference_snapshot_artifact",
    "load_strict_json_document",
    "resolve_project_data_json_path",
]
