from __future__ import annotations

import hmac
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeVar, cast, overload

from django.conf import settings

from common.constants.virtual_players import VIRTUAL_PLAYER_ARCHETYPES

from .calibration import MAX_PROFILES_PER_COHORT, MIN_PROFILES_PER_COHORT, CalibrationUnit, canonical_snapshot_digest
from .config import V2_PRESTIGE_BAND_NAMES
from .reference_snapshot_catalog import (
    ReferenceSnapshotCatalogError,
    load_strict_json_document,
    resolve_project_data_json_path,
)

GATE_D2_CANDIDATE_ARTIFACT_SCHEMA_VERSION = 2
GATE_D2_METRIC_ALGORITHM_VERSION = 2
GATE_D2_GENERATOR_ID = "virtual_player_v2_bootstrap"
GATE_D2_GENERATOR_VERSION = 1
GATE_D2_GENERATOR_ENTRYPOINT = "gameplay.services.virtual_player_core.bootstrap.create_virtual_player_v2"
GATE_D2_CANDIDATE_ARTIFACT_DIRECTORY = "data/virtual_player_gate_d2_candidate_artifacts"
GATE_D2_SAMPLE_ORDER = "business_key_ascending"
GATE_D2_ATTESTATION_SCHEME = "hmac_sha256_v1"
GATE_D2_GENERATOR_SOURCE_FILES = (
    "common/constants/virtual_players.py",
    "gameplay/services/virtual_player_core/bootstrap.py",
    "gameplay/services/virtual_player_core/bootstrap_assets.py",
    "gameplay/services/virtual_player_core/bootstrap_catalog.py",
    "gameplay/services/virtual_player_core/bootstrap_materializer.py",
    "gameplay/services/virtual_player_core/calibration_runtime.py",
    "gameplay/services/virtual_player_core/config.py",
    "gameplay/services/virtual_player_core/contracts.py",
    "gameplay/services/virtual_player_core/gate_d2_candidate_artifact.py",
    "gameplay/services/virtual_player_core/gate_d2_metrics.py",
    "gameplay/services/virtual_player_core/identity.py",
    "gameplay/services/virtual_player_core/inventory_budget.py",
    "gameplay/services/virtual_player_core/lifecycle.py",
    "gameplay/services/virtual_player_core/maintenance_rules.py",
    "gameplay/services/virtual_player_core/projection.py",
    "gameplay/services/virtual_player_core/random_context.py",
    "gameplay/services/virtual_player_core/reference_snapshots.py",
    "gameplay/services/virtual_player_core/selectors.py",
    "gameplay/services/virtual_player_core/strategy.py",
)

_ARTIFACT_FIELDS = frozenset(
    {
        "schema_version",
        "metric_algorithm_version",
        "unit",
        "policy_checksum",
        "reference_snapshot_digest",
        "generator_provenance",
        "template_catalog",
        "reference_profiles",
        "candidate_profiles",
        "v1_profiles",
        "inactive_reference_profiles",
    }
)
_UNIT_FIELDS = frozenset({"policy_version", "reference_snapshot_version", "prestige_band"})
_PROVENANCE_FIELDS = frozenset(
    {
        "generator_id",
        "generator_version",
        "entrypoint",
        "engine_version",
        "rng_version",
        "plan_schema_version",
        "policy_checksum",
        "reference_snapshot_digest",
        "template_catalog_digest",
        "cohort_digests",
        "root_seed_digest",
        "sample_order",
        "source_state",
        "attestation",
    }
)
_ATTESTATION_FIELDS = frozenset({"scheme", "key_id", "payload_digest", "mac_sha256"})
_COHORT_DIGEST_FIELDS = frozenset(
    {
        "reference_profiles",
        "candidate_profiles",
        "v1_profiles",
        "inactive_reference_profiles",
    }
)
_SOURCE_STATE_FIELDS = frozenset({"algorithm", "bundle_digest", "files"})
_SOURCE_FILE_FIELDS = frozenset({"path", "sha256"})
_TEMPLATE_CATALOG_FIELDS = frozenset(
    {
        "guest_templates",
        "equipment_templates",
        "skill_templates",
        "guard_templates",
        "troop_templates",
        "building_templates",
        "resource_keys",
    }
)
_GUEST_TEMPLATE_FIELDS = frozenset({"key", "rarity", "archetype"})
_EQUIPMENT_TEMPLATE_FIELDS = frozenset({"key", "rarity", "slot"})
_SKILL_TEMPLATE_FIELDS = frozenset({"key", "kind", "rarity"})
_CLASS_TEMPLATE_FIELDS = frozenset({"key", "class"})
_BUILDING_TEMPLATE_FIELDS = frozenset({"key", "max_level"})
_PROFILE_FIELDS = frozenset(
    {
        "business_key",
        "prestige",
        "account_age_days",
        "days_since_last_strength_increase",
        "buildings",
        "guests",
        "guards",
        "troops",
        "resources",
    }
)
_CANDIDATE_PROFILE_FIELDS = frozenset({*_PROFILE_FIELDS, "archetype"})
_BUILDING_FIELDS = frozenset({"key", "level"})
_GUEST_FIELDS = frozenset(
    {
        "ordinal",
        "template",
        "level",
        "rarity",
        "archetype",
        "base_hp",
        "force",
        "intellect",
        "defense",
        "hp_bonus",
        "equipment",
        "skills",
    }
)
_EQUIPMENT_FIELDS = frozenset({"template", "level", "rarity", "slot"})
_SKILL_FIELDS = frozenset({"key", "kind", "rarity"})
_GUARD_FIELDS = frozenset({"template", "class", "level"})
_TROOP_FIELDS = frozenset({"template", "class", "count"})
_RESOURCE_FIELDS = frozenset({"key", "amount", "capacity"})
_ATTESTATION_KEY_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ATTESTATION_CONTEXT = b"virtual-player-gate-d2-candidate-artifact-v1\0"
_MINIMUM_ATTESTATION_KEY_BYTES = 32


class GateD2CandidateArtifactError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GateD2SourceFile:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class GateD2GeneratorAttestation:
    scheme: str
    key_id: str
    payload_digest: str
    mac_sha256: str


@dataclass(frozen=True, slots=True)
class GateD2GeneratorProvenance:
    generator_id: str
    generator_version: int
    entrypoint: str
    engine_version: int
    rng_version: int
    plan_schema_version: int
    policy_checksum: str
    reference_snapshot_digest: str
    template_catalog_digest: str
    cohort_digests: Mapping[str, str]
    root_seed_digest: str
    sample_order: str
    source_state: tuple[GateD2SourceFile, ...]
    source_bundle_digest: str
    attestation: GateD2GeneratorAttestation


@dataclass(frozen=True, slots=True)
class GateD2GuestTemplate:
    key: str
    rarity: str
    archetype: str


@dataclass(frozen=True, slots=True)
class GateD2EquipmentTemplate:
    key: str
    rarity: str
    slot: str


@dataclass(frozen=True, slots=True)
class GateD2SkillTemplate:
    key: str
    kind: str
    rarity: str


@dataclass(frozen=True, slots=True)
class GateD2ClassTemplate:
    key: str
    class_name: str


@dataclass(frozen=True, slots=True)
class GateD2BuildingTemplate:
    key: str
    max_level: int | None


@dataclass(frozen=True, slots=True)
class GateD2TemplateCatalog:
    guest_templates: Mapping[str, GateD2GuestTemplate]
    equipment_templates: Mapping[str, GateD2EquipmentTemplate]
    skill_templates: Mapping[str, GateD2SkillTemplate]
    guard_templates: Mapping[str, GateD2ClassTemplate]
    troop_templates: Mapping[str, GateD2ClassTemplate]
    building_templates: Mapping[str, GateD2BuildingTemplate]
    resource_keys: frozenset[str]
    digest: str


@dataclass(frozen=True, slots=True)
class GateD2RawEquipment:
    template: str
    level: int
    rarity: str
    slot: str


@dataclass(frozen=True, slots=True)
class GateD2RawSkill:
    key: str
    kind: str
    rarity: str


@dataclass(frozen=True, slots=True)
class GateD2RawGuest:
    ordinal: int
    template: str
    level: int
    rarity: str
    archetype: str
    base_hp: int
    force: int
    intellect: int
    defense: int
    hp_bonus: int
    equipment: tuple[GateD2RawEquipment, ...]
    skills: tuple[GateD2RawSkill, ...]


@dataclass(frozen=True, slots=True)
class GateD2RawGuard:
    template: str
    class_name: str
    level: int


@dataclass(frozen=True, slots=True)
class GateD2RawTroop:
    template: str
    class_name: str
    count: int


@dataclass(frozen=True, slots=True)
class GateD2RawBuilding:
    key: str
    level: int


@dataclass(frozen=True, slots=True)
class GateD2RawResource:
    key: str
    amount: int
    capacity: int


@dataclass(frozen=True, slots=True)
class GateD2RawProfile:
    business_key: str
    prestige: int
    account_age_days: int
    days_since_last_strength_increase: int
    buildings: tuple[GateD2RawBuilding, ...]
    guests: tuple[GateD2RawGuest, ...]
    guards: tuple[GateD2RawGuard, ...]
    troops: tuple[GateD2RawTroop, ...]
    resources: tuple[GateD2RawResource, ...]


@dataclass(frozen=True, slots=True)
class GateD2RawCandidateProfile:
    raw: GateD2RawProfile
    archetype: str


@dataclass(frozen=True, slots=True)
class GateD2CandidateArtifact:
    schema_version: int
    metric_algorithm_version: int
    artifact_path: str
    digest: str
    unit: CalibrationUnit
    policy_checksum: str
    reference_snapshot_digest: str
    generator_provenance: GateD2GeneratorProvenance
    template_catalog: GateD2TemplateCatalog
    reference_profiles: tuple[GateD2RawProfile, ...]
    candidate_profiles: tuple[GateD2RawCandidateProfile, ...]
    v1_profiles: tuple[GateD2RawProfile, ...]
    inactive_reference_profiles: tuple[GateD2RawProfile, ...]


def _strict_fields(value: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unknown:
        details.append(f"unknown {', '.join(unknown)}")
    raise GateD2CandidateArtifactError(f"{label} has {'; '.join(details)}")


def _integer(value: Any, *, label: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GateD2CandidateArtifactError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise GateD2CandidateArtifactError(f"{label} must be at least {minimum}")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    return _integer(value, label=label, minimum=1)


def _string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise GateD2CandidateArtifactError(f"{label} must be a non-empty string")
    return value


def _lower_sha256(value: Any, *, label: str) -> str:
    if not (
        isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
    ):
        raise GateD2CandidateArtifactError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _attestation_key_id(value: Any, *, label: str) -> str:
    key_id = _string(value, label=label)
    if _ATTESTATION_KEY_ID_PATTERN.fullmatch(key_id) is None:
        raise GateD2CandidateArtifactError(f"{label} must be a canonical lowercase key identifier")
    return key_id


def _attestation_key_bytes(value: Any, *, label: str) -> bytes:
    if isinstance(value, str):
        key = value.encode("utf-8")
    elif isinstance(value, bytes):
        key = value
    else:
        raise GateD2CandidateArtifactError(f"{label} must be text or bytes")
    if len(key) < _MINIMUM_ATTESTATION_KEY_BYTES:
        raise GateD2CandidateArtifactError(f"{label} must contain at least {_MINIMUM_ATTESTATION_KEY_BYTES} bytes")
    return key


def gate_d2_attestation_payload_digest(artifact: Mapping[str, Any]) -> str:
    provenance = artifact.get("generator_provenance")
    if not isinstance(provenance, Mapping):
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact generator_provenance must be a mapping")
    unsigned_provenance = {str(key): value for key, value in provenance.items() if key != "attestation"}
    unsigned_artifact = {str(key): value for key, value in artifact.items() if key != "generator_provenance"}
    unsigned_artifact["generator_provenance"] = unsigned_provenance
    try:
        return canonical_snapshot_digest(unsigned_artifact)
    except (OverflowError, TypeError, ValueError) as exc:
        raise GateD2CandidateArtifactError("Gate D2 candidate attestation payload is not canonical JSON data") from exc


def build_gate_d2_generator_attestation(
    artifact: Mapping[str, Any],
    *,
    key_id: str,
    key: str | bytes,
) -> dict[str, str]:
    normalized_key_id = _attestation_key_id(
        key_id,
        label="Gate D2 generator attestation key_id",
    )
    secret = _attestation_key_bytes(
        key,
        label="Gate D2 generator attestation key",
    )
    payload_digest = gate_d2_attestation_payload_digest(artifact)
    mac = hmac.new(
        secret,
        _ATTESTATION_CONTEXT + payload_digest.encode("ascii"),
        sha256,
    ).hexdigest()
    return {
        "scheme": GATE_D2_ATTESTATION_SCHEME,
        "key_id": normalized_key_id,
        "payload_digest": payload_digest,
        "mac_sha256": mac,
    }


def _parse_generator_attestation(
    value: Any,
    *,
    artifact: Mapping[str, Any],
) -> GateD2GeneratorAttestation:
    label = "Gate D2 generator attestation"
    if not isinstance(value, Mapping):
        raise GateD2CandidateArtifactError(f"{label} must be a mapping")
    _strict_fields(value, _ATTESTATION_FIELDS, label=label)
    if value["scheme"] != GATE_D2_ATTESTATION_SCHEME:
        raise GateD2CandidateArtifactError(f"{label}.scheme is unsupported")
    key_id = _attestation_key_id(value["key_id"], label=f"{label}.key_id")
    payload_digest = _lower_sha256(
        value["payload_digest"],
        label=f"{label}.payload_digest",
    )
    expected_payload_digest = gate_d2_attestation_payload_digest(artifact)
    if payload_digest != expected_payload_digest:
        raise GateD2CandidateArtifactError(f"{label}.payload_digest does not match the candidate artifact")
    trusted_keys = getattr(settings, "VIRTUAL_PLAYER_GATE_D2_ATTESTATION_KEYS", {})
    if not isinstance(trusted_keys, Mapping):
        raise GateD2CandidateArtifactError("Gate D2 trusted generator attestation keys are misconfigured")
    try:
        raw_key = trusted_keys[key_id]
    except KeyError as exc:
        raise GateD2CandidateArtifactError(f"{label}.key_id is not trusted in this environment") from exc
    key = _attestation_key_bytes(raw_key, label=f"trusted Gate D2 key {key_id!r}")
    mac = _lower_sha256(value["mac_sha256"], label=f"{label}.mac_sha256")
    expected_mac = hmac.new(
        key,
        _ATTESTATION_CONTEXT + payload_digest.encode("ascii"),
        sha256,
    ).hexdigest()
    if not hmac.compare_digest(mac, expected_mac):
        raise GateD2CandidateArtifactError(f"{label}.mac_sha256 is not valid for the trusted key")
    return GateD2GeneratorAttestation(
        scheme=GATE_D2_ATTESTATION_SCHEME,
        key_id=key_id,
        payload_digest=payload_digest,
        mac_sha256=mac,
    )


def _business_key(value: Any, *, prefix: str, label: str) -> str:
    normalized = _string(value, label=label)
    if not normalized.startswith(prefix):
        raise GateD2CandidateArtifactError(f"{label} must start with {prefix!r}")
    _lower_sha256(normalized[len(prefix) :], label=label)
    return normalized


def _parse_unit(value: Any) -> CalibrationUnit:
    label = "Gate D2 candidate artifact unit"
    if not isinstance(value, Mapping):
        raise GateD2CandidateArtifactError(f"{label} must be a mapping")
    _strict_fields(value, _UNIT_FIELDS, label=label)
    return CalibrationUnit(
        policy_version=_positive_int(value["policy_version"], label=f"{label}.policy_version"),
        reference_snapshot_version=_positive_int(
            value["reference_snapshot_version"],
            label=f"{label}.reference_snapshot_version",
        ),
        prestige_band=_string(value["prestige_band"], label=f"{label}.prestige_band"),
    )


def gate_d2_candidate_artifact_path(unit: CalibrationUnit) -> str:
    if not isinstance(unit, CalibrationUnit):
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact unit must be a CalibrationUnit")
    _positive_int(unit.policy_version, label="Gate D2 policy_version")
    _positive_int(
        unit.reference_snapshot_version,
        label="Gate D2 reference_snapshot_version",
    )
    prestige_band = _string(unit.prestige_band, label="Gate D2 prestige_band")
    if prestige_band not in V2_PRESTIGE_BAND_NAMES:
        raise GateD2CandidateArtifactError("Gate D2 prestige_band is invalid")
    return (
        f"{GATE_D2_CANDIDATE_ARTIFACT_DIRECTORY}/"
        f"policy-{unit.policy_version}/"
        f"snapshot-{unit.reference_snapshot_version}/"
        f"{prestige_band}.json"
    )


def current_gate_d2_generator_source_state(*, project_root: Path) -> tuple[GateD2SourceFile, ...]:
    root = Path(project_root).resolve()
    files: list[GateD2SourceFile] = []
    for relative_path in GATE_D2_GENERATOR_SOURCE_FILES:
        try:
            resolved = (root / relative_path).resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise OSError(relative_path)
            digest = sha256(resolved.read_bytes()).hexdigest()
        except OSError as exc:
            raise GateD2CandidateArtifactError(f"Gate D2 provenance source is unavailable: {relative_path}") from exc
        files.append(GateD2SourceFile(relative_path, digest))
    return tuple(files)


def gate_d2_source_bundle_digest(files: tuple[GateD2SourceFile, ...]) -> str:
    return canonical_snapshot_digest([{"path": item.path, "sha256": item.sha256} for item in files])


def _parse_source_state(value: Any, *, project_root: Path) -> tuple[tuple[GateD2SourceFile, ...], str]:
    label = "Gate D2 generator source_state"
    if not isinstance(value, Mapping):
        raise GateD2CandidateArtifactError(f"{label} must be a mapping")
    _strict_fields(value, _SOURCE_STATE_FIELDS, label=label)
    if value["algorithm"] != "sha256_canonical_manifest":
        raise GateD2CandidateArtifactError(f"{label}.algorithm must be sha256_canonical_manifest")
    raw_files = value["files"]
    if not isinstance(raw_files, list):
        raise GateD2CandidateArtifactError(f"{label}.files must be a list")
    files: list[GateD2SourceFile] = []
    for index, raw_file in enumerate(raw_files):
        item_label = f"{label}.files[{index}]"
        if not isinstance(raw_file, Mapping):
            raise GateD2CandidateArtifactError(f"{item_label} must be a mapping")
        _strict_fields(raw_file, _SOURCE_FILE_FIELDS, label=item_label)
        files.append(
            GateD2SourceFile(
                _string(raw_file["path"], label=f"{item_label}.path"),
                _lower_sha256(raw_file["sha256"], label=f"{item_label}.sha256"),
            )
        )
    normalized = tuple(files)
    if tuple(item.path for item in normalized) != GATE_D2_GENERATOR_SOURCE_FILES:
        raise GateD2CandidateArtifactError(f"{label}.files must contain the exact canonical source manifest")
    current = current_gate_d2_generator_source_state(project_root=project_root)
    if normalized != current:
        raise GateD2CandidateArtifactError("Gate D2 generator source_state does not match the current source")
    bundle_digest = gate_d2_source_bundle_digest(normalized)
    if _lower_sha256(value["bundle_digest"], label=f"{label}.bundle_digest") != bundle_digest:
        raise GateD2CandidateArtifactError("Gate D2 generator source bundle digest does not match its manifest")
    return normalized, bundle_digest


T = TypeVar("T")


def _parse_sorted_records(
    value: Any,
    *,
    label: str,
    parser: Callable[[Mapping[str, Any], str], T],
    key: Callable[[T], Any],
    minimum_count: int = 0,
    maximum_count: int | None = None,
) -> tuple[T, ...]:
    if not isinstance(value, list):
        raise GateD2CandidateArtifactError(f"{label} must be a list")
    if len(value) < minimum_count:
        raise GateD2CandidateArtifactError(f"{label} must contain at least {minimum_count} records")
    if maximum_count is not None and len(value) > maximum_count:
        raise GateD2CandidateArtifactError(f"{label} exceeds {maximum_count} records")
    parsed: list[T] = []
    for index, raw_item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(raw_item, Mapping):
            raise GateD2CandidateArtifactError(f"{item_label} must be a mapping")
        parsed.append(parser(raw_item, item_label))
    keys = [key(item) for item in parsed]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise GateD2CandidateArtifactError(f"{label} must be uniquely sorted in canonical order")
    return tuple(parsed)


def _parse_guest_template(value: Mapping[str, Any], label: str) -> GateD2GuestTemplate:
    _strict_fields(value, _GUEST_TEMPLATE_FIELDS, label=label)
    return GateD2GuestTemplate(
        key=_string(value["key"], label=f"{label}.key"),
        rarity=_string(value["rarity"], label=f"{label}.rarity"),
        archetype=_string(value["archetype"], label=f"{label}.archetype"),
    )


def _parse_equipment_template(value: Mapping[str, Any], label: str) -> GateD2EquipmentTemplate:
    _strict_fields(value, _EQUIPMENT_TEMPLATE_FIELDS, label=label)
    return GateD2EquipmentTemplate(
        key=_string(value["key"], label=f"{label}.key"),
        rarity=_string(value["rarity"], label=f"{label}.rarity"),
        slot=_string(value["slot"], label=f"{label}.slot"),
    )


def _parse_skill_template(value: Mapping[str, Any], label: str) -> GateD2SkillTemplate:
    _strict_fields(value, _SKILL_TEMPLATE_FIELDS, label=label)
    return GateD2SkillTemplate(
        key=_string(value["key"], label=f"{label}.key"),
        kind=_string(value["kind"], label=f"{label}.kind"),
        rarity=_string(value["rarity"], label=f"{label}.rarity"),
    )


def _parse_class_template(value: Mapping[str, Any], label: str) -> GateD2ClassTemplate:
    _strict_fields(value, _CLASS_TEMPLATE_FIELDS, label=label)
    return GateD2ClassTemplate(
        key=_string(value["key"], label=f"{label}.key"),
        class_name=_string(value["class"], label=f"{label}.class"),
    )


def _parse_building_template(value: Mapping[str, Any], label: str) -> GateD2BuildingTemplate:
    _strict_fields(value, _BUILDING_TEMPLATE_FIELDS, label=label)
    raw_maximum = value["max_level"]
    maximum = None if raw_maximum is None else _positive_int(raw_maximum, label=f"{label}.max_level")
    return GateD2BuildingTemplate(
        key=_string(value["key"], label=f"{label}.key"),
        max_level=maximum,
    )


def _parse_template_catalog(value: Any) -> GateD2TemplateCatalog:
    label = "Gate D2 candidate artifact template_catalog"
    if not isinstance(value, Mapping):
        raise GateD2CandidateArtifactError(f"{label} must be a mapping")
    _strict_fields(value, _TEMPLATE_CATALOG_FIELDS, label=label)
    guests = _parse_sorted_records(
        value["guest_templates"],
        label=f"{label}.guest_templates",
        parser=_parse_guest_template,
        key=lambda item: item.key,
        minimum_count=1,
    )
    equipment = _parse_sorted_records(
        value["equipment_templates"],
        label=f"{label}.equipment_templates",
        parser=_parse_equipment_template,
        key=lambda item: item.key,
        minimum_count=1,
    )
    skills = _parse_sorted_records(
        value["skill_templates"],
        label=f"{label}.skill_templates",
        parser=_parse_skill_template,
        key=lambda item: item.key,
        minimum_count=1,
    )
    guards = _parse_sorted_records(
        value["guard_templates"],
        label=f"{label}.guard_templates",
        parser=_parse_class_template,
        key=lambda item: item.key,
        minimum_count=1,
    )
    troops = _parse_sorted_records(
        value["troop_templates"],
        label=f"{label}.troop_templates",
        parser=_parse_class_template,
        key=lambda item: item.key,
        minimum_count=1,
    )
    buildings = _parse_sorted_records(
        value["building_templates"],
        label=f"{label}.building_templates",
        parser=_parse_building_template,
        key=lambda item: item.key,
        minimum_count=1,
    )
    raw_resource_keys = value["resource_keys"]
    if not isinstance(raw_resource_keys, list):
        raise GateD2CandidateArtifactError(f"{label}.resource_keys must be a list")
    resource_keys = [
        _string(item, label=f"{label}.resource_keys[{index}]") for index, item in enumerate(raw_resource_keys)
    ]
    if not resource_keys or resource_keys != sorted(resource_keys) or len(resource_keys) != len(set(resource_keys)):
        raise GateD2CandidateArtifactError(f"{label}.resource_keys must be non-empty, unique, and sorted")
    try:
        digest = canonical_snapshot_digest(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise GateD2CandidateArtifactError("Gate D2 template catalog is not canonical JSON data") from exc
    return GateD2TemplateCatalog(
        guest_templates=MappingProxyType({item.key: item for item in guests}),
        equipment_templates=MappingProxyType({item.key: item for item in equipment}),
        skill_templates=MappingProxyType({item.key: item for item in skills}),
        guard_templates=MappingProxyType({item.key: item for item in guards}),
        troop_templates=MappingProxyType({item.key: item for item in troops}),
        building_templates=MappingProxyType({item.key: item for item in buildings}),
        resource_keys=frozenset(resource_keys),
        digest=digest,
    )


def _parse_equipment(value: Mapping[str, Any], label: str) -> GateD2RawEquipment:
    _strict_fields(value, _EQUIPMENT_FIELDS, label=label)
    return GateD2RawEquipment(
        template=_string(value["template"], label=f"{label}.template"),
        level=_integer(value["level"], label=f"{label}.level"),
        rarity=_string(value["rarity"], label=f"{label}.rarity"),
        slot=_string(value["slot"], label=f"{label}.slot"),
    )


def _parse_skill(value: Mapping[str, Any], label: str) -> GateD2RawSkill:
    _strict_fields(value, _SKILL_FIELDS, label=label)
    return GateD2RawSkill(
        key=_string(value["key"], label=f"{label}.key"),
        kind=_string(value["kind"], label=f"{label}.kind"),
        rarity=_string(value["rarity"], label=f"{label}.rarity"),
    )


def _parse_guest(value: Mapping[str, Any], label: str) -> GateD2RawGuest:
    _strict_fields(value, _GUEST_FIELDS, label=label)
    equipment = _parse_sorted_records(
        value["equipment"],
        label=f"{label}.equipment",
        parser=_parse_equipment,
        key=lambda item: (item.slot, item.template, item.level, item.rarity),
    )
    skills = _parse_sorted_records(
        value["skills"],
        label=f"{label}.skills",
        parser=_parse_skill,
        key=lambda item: (item.key, item.kind, item.rarity),
    )
    return GateD2RawGuest(
        ordinal=_integer(value["ordinal"], label=f"{label}.ordinal", minimum=0),
        template=_string(value["template"], label=f"{label}.template"),
        level=_integer(value["level"], label=f"{label}.level"),
        rarity=_string(value["rarity"], label=f"{label}.rarity"),
        archetype=_string(value["archetype"], label=f"{label}.archetype"),
        base_hp=_integer(value["base_hp"], label=f"{label}.base_hp"),
        force=_integer(value["force"], label=f"{label}.force"),
        intellect=_integer(value["intellect"], label=f"{label}.intellect"),
        defense=_integer(value["defense"], label=f"{label}.defense"),
        hp_bonus=_integer(value["hp_bonus"], label=f"{label}.hp_bonus"),
        equipment=equipment,
        skills=skills,
    )


def _parse_guard(value: Mapping[str, Any], label: str) -> GateD2RawGuard:
    _strict_fields(value, _GUARD_FIELDS, label=label)
    return GateD2RawGuard(
        template=_string(value["template"], label=f"{label}.template"),
        class_name=_string(value["class"], label=f"{label}.class"),
        level=_integer(value["level"], label=f"{label}.level"),
    )


def _parse_troop(value: Mapping[str, Any], label: str) -> GateD2RawTroop:
    _strict_fields(value, _TROOP_FIELDS, label=label)
    return GateD2RawTroop(
        template=_string(value["template"], label=f"{label}.template"),
        class_name=_string(value["class"], label=f"{label}.class"),
        count=_integer(value["count"], label=f"{label}.count"),
    )


def _parse_building(value: Mapping[str, Any], label: str) -> GateD2RawBuilding:
    _strict_fields(value, _BUILDING_FIELDS, label=label)
    return GateD2RawBuilding(
        key=_string(value["key"], label=f"{label}.key"),
        level=_integer(value["level"], label=f"{label}.level"),
    )


def _parse_resource(value: Mapping[str, Any], label: str) -> GateD2RawResource:
    _strict_fields(value, _RESOURCE_FIELDS, label=label)
    return GateD2RawResource(
        key=_string(value["key"], label=f"{label}.key"),
        amount=_integer(value["amount"], label=f"{label}.amount"),
        capacity=_integer(value["capacity"], label=f"{label}.capacity"),
    )


def _parse_raw_profile(
    value: Mapping[str, Any],
    *,
    label: str,
    business_key_prefix: str,
    candidate: bool,
) -> GateD2RawProfile | GateD2RawCandidateProfile:
    _strict_fields(
        value,
        _CANDIDATE_PROFILE_FIELDS if candidate else _PROFILE_FIELDS,
        label=label,
    )
    buildings = _parse_sorted_records(
        value["buildings"],
        label=f"{label}.buildings",
        parser=_parse_building,
        key=lambda item: item.key,
    )
    guests = _parse_sorted_records(
        value["guests"],
        label=f"{label}.guests",
        parser=_parse_guest,
        key=lambda item: item.ordinal,
    )
    if tuple(guest.ordinal for guest in guests) != tuple(range(len(guests))):
        raise GateD2CandidateArtifactError(f"{label}.guests ordinals must be contiguous from zero")
    guards = _parse_sorted_records(
        value["guards"],
        label=f"{label}.guards",
        parser=_parse_guard,
        key=lambda item: (item.class_name, item.template, item.level),
    )
    troops = _parse_sorted_records(
        value["troops"],
        label=f"{label}.troops",
        parser=_parse_troop,
        key=lambda item: item.template,
    )
    resources = _parse_sorted_records(
        value["resources"],
        label=f"{label}.resources",
        parser=_parse_resource,
        key=lambda item: item.key,
    )
    raw = GateD2RawProfile(
        business_key=_business_key(
            value["business_key"],
            prefix=business_key_prefix,
            label=f"{label}.business_key",
        ),
        prestige=_integer(value["prestige"], label=f"{label}.prestige"),
        account_age_days=_integer(
            value["account_age_days"],
            label=f"{label}.account_age_days",
            minimum=0,
        ),
        days_since_last_strength_increase=_integer(
            value["days_since_last_strength_increase"],
            label=f"{label}.days_since_last_strength_increase",
            minimum=0,
        ),
        buildings=buildings,
        guests=guests,
        guards=guards,
        troops=troops,
        resources=resources,
    )
    if not candidate:
        return raw
    archetype = _string(value["archetype"], label=f"{label}.archetype")
    if archetype not in VIRTUAL_PLAYER_ARCHETYPES:
        raise GateD2CandidateArtifactError(f"{label}.archetype is invalid")
    return GateD2RawCandidateProfile(raw=raw, archetype=archetype)


@overload
def _parse_profile_cohort(
    value: Any,
    *,
    label: str,
    business_key_prefix: str,
    candidate: Literal[False],
) -> tuple[GateD2RawProfile, ...]: ...


@overload
def _parse_profile_cohort(
    value: Any,
    *,
    label: str,
    business_key_prefix: str,
    candidate: Literal[True],
) -> tuple[GateD2RawCandidateProfile, ...]: ...


def _parse_profile_cohort(
    value: Any,
    *,
    label: str,
    business_key_prefix: str,
    candidate: bool,
) -> tuple[GateD2RawProfile, ...] | tuple[GateD2RawCandidateProfile, ...]:
    parsed = _parse_sorted_records(
        value,
        label=label,
        parser=lambda item, item_label: _parse_raw_profile(
            item,
            label=item_label,
            business_key_prefix=business_key_prefix,
            candidate=candidate,
        ),
        key=lambda item: (item.raw.business_key if isinstance(item, GateD2RawCandidateProfile) else item.business_key),
        minimum_count=MIN_PROFILES_PER_COHORT,
        maximum_count=MAX_PROFILES_PER_COHORT,
    )
    if candidate:
        counts = Counter(item.archetype for item in parsed if isinstance(item, GateD2RawCandidateProfile))
        missing = sorted(archetype for archetype in VIRTUAL_PLAYER_ARCHETYPES if counts[archetype] < 2)
        if missing:
            raise GateD2CandidateArtifactError(
                "Gate D2 candidate archetype cohorts require at least two profiles: " + ", ".join(missing)
            )
    if candidate:
        return tuple(cast(GateD2RawCandidateProfile, item) for item in parsed)
    return tuple(cast(GateD2RawProfile, item) for item in parsed)


def _parse_provenance(
    value: Any,
    *,
    artifact: Mapping[str, Any],
    project_root: Path,
    policy_checksum: str,
    reference_snapshot_digest: str,
    template_catalog_digest: str,
    raw_cohorts: Mapping[str, Any],
) -> GateD2GeneratorProvenance:
    label = "Gate D2 generator provenance"
    if not isinstance(value, Mapping):
        raise GateD2CandidateArtifactError(f"{label} must be a mapping")
    _strict_fields(value, _PROVENANCE_FIELDS, label=label)
    if value["generator_id"] != GATE_D2_GENERATOR_ID:
        raise GateD2CandidateArtifactError(f"{label}.generator_id is unsupported")
    generator_version = _positive_int(value["generator_version"], label=f"{label}.generator_version")
    if generator_version != GATE_D2_GENERATOR_VERSION:
        raise GateD2CandidateArtifactError(f"{label}.generator_version is unsupported")
    if value["entrypoint"] != GATE_D2_GENERATOR_ENTRYPOINT:
        raise GateD2CandidateArtifactError(f"{label}.entrypoint is unsupported")
    if value["sample_order"] != GATE_D2_SAMPLE_ORDER:
        raise GateD2CandidateArtifactError(f"{label}.sample_order must be {GATE_D2_SAMPLE_ORDER}")
    if value["policy_checksum"] != policy_checksum:
        raise GateD2CandidateArtifactError(f"{label}.policy_checksum does not match the artifact")
    if value["reference_snapshot_digest"] != reference_snapshot_digest:
        raise GateD2CandidateArtifactError(f"{label}.reference_snapshot_digest does not match the artifact")
    if value["template_catalog_digest"] != template_catalog_digest:
        raise GateD2CandidateArtifactError(f"{label}.template_catalog_digest does not match the artifact")
    raw_digests = value["cohort_digests"]
    if not isinstance(raw_digests, Mapping):
        raise GateD2CandidateArtifactError(f"{label}.cohort_digests must be a mapping")
    _strict_fields(raw_digests, _COHORT_DIGEST_FIELDS, label=f"{label}.cohort_digests")
    cohort_digests: dict[str, str] = {}
    for cohort_name in sorted(_COHORT_DIGEST_FIELDS):
        reported = _lower_sha256(
            raw_digests[cohort_name],
            label=f"{label}.cohort_digests.{cohort_name}",
        )
        calculated = canonical_snapshot_digest(raw_cohorts[cohort_name])
        if reported != calculated:
            raise GateD2CandidateArtifactError(f"{label}.cohort_digests.{cohort_name} does not match the raw cohort")
        cohort_digests[cohort_name] = reported
    source_state, source_bundle_digest = _parse_source_state(value["source_state"], project_root=project_root)
    attestation = _parse_generator_attestation(
        value["attestation"],
        artifact=artifact,
    )
    return GateD2GeneratorProvenance(
        generator_id=GATE_D2_GENERATOR_ID,
        generator_version=generator_version,
        entrypoint=GATE_D2_GENERATOR_ENTRYPOINT,
        engine_version=_positive_int(value["engine_version"], label=f"{label}.engine_version"),
        rng_version=_positive_int(value["rng_version"], label=f"{label}.rng_version"),
        plan_schema_version=_positive_int(
            value["plan_schema_version"],
            label=f"{label}.plan_schema_version",
        ),
        policy_checksum=policy_checksum,
        reference_snapshot_digest=reference_snapshot_digest,
        template_catalog_digest=template_catalog_digest,
        cohort_digests=MappingProxyType(cohort_digests),
        root_seed_digest=_lower_sha256(value["root_seed_digest"], label=f"{label}.root_seed_digest"),
        sample_order=GATE_D2_SAMPLE_ORDER,
        source_state=source_state,
        source_bundle_digest=source_bundle_digest,
        attestation=attestation,
    )


def load_gate_d2_candidate_artifact(
    unit: CalibrationUnit, *, project_root: Path | None = None
) -> GateD2CandidateArtifact:
    artifact_path = gate_d2_candidate_artifact_path(unit)
    root = Path(settings.BASE_DIR if project_root is None else project_root)
    try:
        resolved = resolve_project_data_json_path(artifact_path, project_root=root)
        raw = load_strict_json_document(resolved, label="Gate D2 candidate artifact")
    except ReferenceSnapshotCatalogError as exc:
        raise GateD2CandidateArtifactError(str(exc)) from exc
    _strict_fields(raw, _ARTIFACT_FIELDS, label="Gate D2 candidate artifact")
    schema_version = raw["schema_version"]
    if schema_version != GATE_D2_CANDIDATE_ARTIFACT_SCHEMA_VERSION or isinstance(schema_version, bool):
        raise GateD2CandidateArtifactError(f"unsupported Gate D2 candidate artifact schema version {schema_version!r}")
    metric_version = raw["metric_algorithm_version"]
    if metric_version != GATE_D2_METRIC_ALGORITHM_VERSION or isinstance(metric_version, bool):
        raise GateD2CandidateArtifactError(f"unsupported Gate D2 metric algorithm version {metric_version!r}")
    artifact_unit = _parse_unit(raw["unit"])
    if artifact_unit != unit:
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact unit does not match its derived path")
    policy_checksum = _lower_sha256(
        raw["policy_checksum"],
        label="Gate D2 candidate artifact policy_checksum",
    )
    reference_digest = _lower_sha256(
        raw["reference_snapshot_digest"],
        label="Gate D2 candidate artifact reference_snapshot_digest",
    )
    catalog = _parse_template_catalog(raw["template_catalog"])
    raw_cohorts = {name: raw[name] for name in sorted(_COHORT_DIGEST_FIELDS)}
    reference_profiles = _parse_profile_cohort(
        raw["reference_profiles"],
        label="Gate D2 candidate artifact reference_profiles",
        business_key_prefix=f"human-ref-v{unit.reference_snapshot_version}:",
        candidate=False,
    )
    candidate_profiles = _parse_profile_cohort(
        raw["candidate_profiles"],
        label="Gate D2 candidate artifact candidate_profiles",
        business_key_prefix=f"candidate-v{GATE_D2_GENERATOR_VERSION}:",
        candidate=True,
    )
    v1_profiles = _parse_profile_cohort(
        raw["v1_profiles"],
        label="Gate D2 candidate artifact v1_profiles",
        business_key_prefix="v1-baseline:",
        candidate=False,
    )
    inactive_profiles = _parse_profile_cohort(
        raw["inactive_reference_profiles"],
        label="Gate D2 candidate artifact inactive_reference_profiles",
        business_key_prefix="inactive-human-ref:",
        candidate=False,
    )
    if not all(isinstance(item, GateD2RawProfile) for item in reference_profiles):
        raise GateD2CandidateArtifactError("invalid Gate D2 reference cohort")
    if not all(isinstance(item, GateD2RawCandidateProfile) for item in candidate_profiles):
        raise GateD2CandidateArtifactError("invalid Gate D2 candidate cohort")
    if not all(isinstance(item, GateD2RawProfile) for item in v1_profiles):
        raise GateD2CandidateArtifactError("invalid Gate D2 V1 cohort")
    if not all(isinstance(item, GateD2RawProfile) for item in inactive_profiles):
        raise GateD2CandidateArtifactError("invalid Gate D2 inactive cohort")
    provenance = _parse_provenance(
        raw["generator_provenance"],
        artifact=raw,
        project_root=root,
        policy_checksum=policy_checksum,
        reference_snapshot_digest=reference_digest,
        template_catalog_digest=catalog.digest,
        raw_cohorts=raw_cohorts,
    )
    try:
        digest = canonical_snapshot_digest(raw)
    except (OverflowError, TypeError, ValueError) as exc:
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact is not canonical JSON data") from exc
    return GateD2CandidateArtifact(
        schema_version=schema_version,
        metric_algorithm_version=metric_version,
        artifact_path=artifact_path,
        digest=digest,
        unit=artifact_unit,
        policy_checksum=policy_checksum,
        reference_snapshot_digest=reference_digest,
        generator_provenance=provenance,
        template_catalog=catalog,
        reference_profiles=tuple(reference_profiles),
        candidate_profiles=tuple(candidate_profiles),
        v1_profiles=tuple(v1_profiles),
        inactive_reference_profiles=tuple(inactive_profiles),
    )


__all__ = [
    "GATE_D2_ATTESTATION_SCHEME",
    "GATE_D2_CANDIDATE_ARTIFACT_DIRECTORY",
    "GATE_D2_CANDIDATE_ARTIFACT_SCHEMA_VERSION",
    "GATE_D2_GENERATOR_ENTRYPOINT",
    "GATE_D2_GENERATOR_ID",
    "GATE_D2_GENERATOR_SOURCE_FILES",
    "GATE_D2_GENERATOR_VERSION",
    "GATE_D2_METRIC_ALGORITHM_VERSION",
    "GATE_D2_SAMPLE_ORDER",
    "GateD2CandidateArtifact",
    "GateD2CandidateArtifactError",
    "GateD2GeneratorAttestation",
    "GateD2RawCandidateProfile",
    "GateD2RawProfile",
    "GateD2SourceFile",
    "build_gate_d2_generator_attestation",
    "current_gate_d2_generator_source_state",
    "gate_d2_attestation_payload_digest",
    "gate_d2_candidate_artifact_path",
    "gate_d2_source_bundle_digest",
    "load_gate_d2_candidate_artifact",
]
