"""Validator for virtual player runtime configuration."""

from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

from common.constants.virtual_players import (
    DEFAULT_VIRTUAL_PLAYER_PRESTIGE_BANDS,
    VIRTUAL_PLAYER_ARCHETYPES,
    VIRTUAL_PLAYER_BUILDING_TARGET_KEYS,
    VIRTUAL_PLAYER_INVENTORY_EFFECT_TYPES,
    VIRTUAL_PLAYER_TECHNOLOGY_TARGET_KEYS,
)

from .base import ValidationResult, _check_positive, _check_type

_SELECTION_SENTINELS = {"__all__", "__all_tradeable__"}
_RARITY_RANKS = {
    rarity: rank for rank, rarity in enumerate(("black", "gray", "green", "red", "blue", "purple", "orange"))
}
_GEAR_RARITIES = set(_RARITY_RANKS)
_COMBAT_PERSONAS = VIRTUAL_PLAYER_ARCHETYPES
_LIFECYCLE_PERSONAS = {"tourist", "casual", "committed", "veteran"}
_STRENGTH_QUANTILES = {"p25", "p50", "p75"}
_PERSONA_MULTIPLIERS = {
    "guest_level_multiplier",
    "guest_count_multiplier",
    "troop_multiplier",
}

_ROOT_FIELDS = frozenset(
    {
        "enabled",
        "population",
        "prestige_bands",
        "lifecycle",
        "growth",
        "resources",
        "projection",
        "combat_personas",
        "lifecycle_personas",
        "bot_development_v2",
    }
)
_V2_BAND_NAMES = (
    "newbie",
    "junior",
    "middle",
    "senior",
    "veteran",
    "elite",
    "legend",
    "mythic",
)
_V2_REQUIRED_ROOT_FIELDS = frozenset(
    {
        "environment_mode",
        "engine_version",
        "rng_version",
        "plan_schema_version",
        "prestige_segmentation",
        "routing",
        "policy_rollout",
        "policies",
    }
)
_V2_ROOT_FIELDS = frozenset({*_V2_REQUIRED_ROOT_FIELDS, "arena_training_policy", "growth_control"})
_V2_PRESTIGE_FIELDS = frozenset(
    {
        "band_schema_version",
        "boundary_semantics",
        "configured_band_count",
        "v2_bands",
        "first_high_band",
        "empty_high_band_target_supply",
        "high_band_activation_sources",
        "lower_band_supply_counts_for_higher_band",
        "cross_band_reactivation_allowed",
        "cross_band_instant_strength_promotion_allowed",
    }
)
_V2_ROUTING_FIELDS = frozenset({"activation_mode", "bootstrap_mode", "maintenance_mode"})
_V2_POLICY_ROLLOUT_FIELDS = frozenset({"target_version", "enabled", "rollout_percent"})
_V2_REFERENCE_SNAPSHOT_CATALOG_ENTRY_FIELDS = frozenset(
    {"schema_version", "digest", "artifact_path", "gate_d2_evidence"}
)
_V2_REFERENCE_SNAPSHOT_CATALOG_ENTRY_REQUIRED_FIELDS = frozenset({"schema_version", "digest", "artifact_path"})
_V2_GATE_D2_EVIDENCE_ENTRY_FIELDS = frozenset({"schema_version", "digest"})
_V2_POLICY_FIELDS = frozenset(
    {
        "checksum",
        "max_development_actions",
        "anchor_k",
        "prestige_band_growth",
        "starter_snapshots",
        "gear_upgrade_threshold",
        "roster_tiers",
        "troop_mix",
        "personas",
    }
)
_V2_REFERENCE_CALIBRATION_THRESHOLD_VALUES = {
    "normalized_wasserstein_max": 0.25,
    "normalized_quantile_deviation_p10_max": 0.35,
    "normalized_quantile_deviation_p50_max": 0.25,
    "normalized_quantile_deviation_p90_max": 0.35,
    "js_divergence_max_bits": 0.10,
    "hard_constraint_violations_max": 0,
    "robust_joint_outlier_rate_max": 0.15,
    "robust_joint_outlier_rate_above_real_max": 0.05,
    "component_fingerprint_collision_rate_max": 0.35,
    "joint_fingerprint_collision_rate_max": 0.15,
    "fingerprint_collision_rate_above_v1_max": 0.0,
    "archetype_standardized_effect_min_absolute": 0.20,
    "archetype_standardized_effect_max_absolute": 0.80,
    "archetype_effect_direction_must_match": True,
    "abandoned_rate_deviation_max": 0.10,
}
_V2_REFERENCE_CALIBRATION_THRESHOLD_FIELDS = frozenset(_V2_REFERENCE_CALIBRATION_THRESHOLD_VALUES)
_V2_REFERENCE_CALIBRATION_ARCHETYPE_EFFECTS = {
    "rich": ("mean_building_level", "higher"),
    "dojo": ("arena_lineup_power", "higher"),
    "guard": ("troop_total", "higher"),
    "abandoned": ("composite_strength", "lower"),
}
_V2_REFERENCE_CALIBRATION_ARCHETYPE_EFFECT_FIELDS = frozenset({"metric", "direction"})
_V2_REFERENCE_CALIBRATION_ABANDONED_FEATURES = {
    "underfilled_roster_guest_count_max": 2,
    "stale_gear_level_ratio_max": 0.50,
    "growth_gap_days_min": 30,
}
_V2_GROWTH_BASE_FIELDS = frozenset(
    {
        "direct_prestige_grant_by_maintenance_allowed",
        "profiles",
        "configured_boundaries_crossed_per_controlled_action_max",
        "external_domain_result_may_be_rejected_by_bot_growth_policy",
        "bootstrap_fake_per_action_history_records",
    }
)
_V2_GROWTH_FIELDS = frozenset({*_V2_GROWTH_BASE_FIELDS, "arena_acceleration_bypass"})
_V2_ARENA_ACCELERATION_BYPASS_FIELDS = frozenset({"due"})
_V2_GROWTH_PROFILE_FIELDS = frozenset(
    {
        "bootstrap_history_age_days",
        "preferred_strength_check_interval_hours",
    }
)
_V2_STARTER_SNAPSHOT_FIELDS = frozenset({"snapshot_version", "profiles"})
_V2_STARTER_PROFILE_FIELDS = frozenset(
    {
        "prestige",
        "core_building_level",
        "max_guest_level",
        "guest_count",
        "arena_lineup_power",
        "troop_total",
        "composite_strength",
    }
)
_V2_ARENA_TRAINING_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "version",
        "checksum",
        "envelopes",
    }
)
_V2_ARENA_TRAINING_ENVELOPE_FIELDS = frozenset(
    {
        "ready_power_range",
        "supply_prestige_band_priority",
    }
)
_V2_GROWTH_CONTROL_FIELDS = frozenset(
    {
        "minimum_sample_count",
        "smoothing_alpha",
        "maximum_daily_delta_bps",
        "active_sample_days",
        "ttl_days",
    }
)


def _reject_unknown_fields(
    value: dict[Any, Any],
    allowed: frozenset[str],
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    for field_name in value:
        if not isinstance(field_name, str) or field_name not in allowed:
            result.add(file, f"{path}.{field_name}", "unknown field")


def _require_fields(
    value: dict[Any, Any],
    required: frozenset[str],
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    for field_name in sorted(required):
        if field_name not in value:
            result.add(file, path, f"missing required field '{field_name}'")


def _v2_mapping(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> dict[Any, Any] | None:
    if not isinstance(value, dict):
        result.add(file, path, "expected a mapping")
        return None
    return value


def _v2_int(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    minimum: int = 0,
    maximum: int | None = None,
    expected: int | None = None,
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        result.add(file, path, "expected an integer")
        return None
    if value < minimum:
        result.add(file, path, f"must be >= {minimum}")
    if maximum is not None and value > maximum:
        result.add(file, path, f"must be <= {maximum}")
    if expected is not None and value != expected:
        result.add(file, path, f"must equal {expected}")
    return value


def _v2_number(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        result.add(file, path, "expected a finite number")
        return None
    normalized = float(value)
    if not math.isfinite(normalized):
        result.add(file, path, "expected a finite number")
        return None
    if normalized < minimum:
        result.add(file, path, f"must be >= {minimum:g}")
    if maximum is not None and normalized > maximum:
        result.add(file, path, f"must be <= {maximum:g}")
    return normalized


def _v2_bool(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    expected: bool | None = None,
) -> bool | None:
    if not isinstance(value, bool):
        result.add(file, path, "expected a boolean")
        return None
    if expected is not None and value is not expected:
        result.add(file, path, f"must be {str(expected).lower()}")
    return value


def _v2_literal(
    value: Any,
    *,
    allowed: frozenset[str],
    result: ValidationResult,
    file: str,
    path: str,
) -> str | None:
    if not isinstance(value, str) or value not in allowed:
        result.add(file, path, f"expected one of {sorted(allowed)}")
        return None
    return value


def _v2_numeric_range(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    maximum: float | None = None,
) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) != 2:
        result.add(file, path, "expected a two-item numeric range")
        return None
    low = _v2_number(value[0], result=result, file=file, path=f"{path}[0]", maximum=maximum)
    high = _v2_number(value[1], result=result, file=file, path=f"{path}[1]", maximum=maximum)
    if low is None or high is None:
        return None
    if low > high:
        result.add(file, path, "range lower bound must be <= upper bound")
    return low, high


def _v2_integer_range(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    maximum: int | None = None,
) -> tuple[int, int] | None:
    if not isinstance(value, list) or len(value) != 2:
        result.add(file, path, "expected a two-item integer range")
        return None
    low = _v2_int(value[0], result=result, file=file, path=f"{path}[0]", maximum=maximum)
    high = _v2_int(value[1], result=result, file=file, path=f"{path}[1]", maximum=maximum)
    if low is None or high is None:
        return None
    if low > high:
        result.add(file, path, "range lower bound must be <= upper bound")
    return low, high


def _policy_checksum(payload: dict[Any, Any]) -> str | None:
    try:
        normalized = {key: value for key, value in payload.items() if key != "checksum"}
        encoded = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return sha256(encoded).hexdigest()


def _validate_v2_prestige_segmentation(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> tuple[tuple[str, int, int | None], ...]:
    segmentation = _v2_mapping(value, result=result, file=file, path=path)
    if segmentation is None:
        return ()
    _reject_unknown_fields(segmentation, _V2_PRESTIGE_FIELDS, result=result, file=file, path=path)
    _require_fields(segmentation, _V2_PRESTIGE_FIELDS, result=result, file=file, path=path)
    _v2_int(
        segmentation.get("band_schema_version"),
        result=result,
        file=file,
        path=f"{path}.band_schema_version",
        minimum=1,
        expected=2,
    )
    _v2_literal(
        segmentation.get("boundary_semantics"),
        allowed=frozenset({"lower_inclusive_upper_exclusive"}),
        result=result,
        file=file,
        path=f"{path}.boundary_semantics",
    )
    _v2_int(
        segmentation.get("configured_band_count"),
        result=result,
        file=file,
        path=f"{path}.configured_band_count",
        expected=len(_V2_BAND_NAMES),
    )

    bands: list[tuple[str, int, int | None]] = []
    raw_bands = _v2_mapping(segmentation.get("v2_bands"), result=result, file=file, path=f"{path}.v2_bands")
    if raw_bands is not None:
        expected_names = list(_V2_BAND_NAMES)
        actual_names = list(raw_bands)
        if actual_names != expected_names:
            result.add(
                file,
                f"{path}.v2_bands",
                f"band names and order must equal {expected_names}",
            )
        previous_high: int | None = None
        open_ended_count = 0
        for index, band_name in enumerate(_V2_BAND_NAMES):
            band_path = f"{path}.v2_bands.{band_name}"
            raw_range = raw_bands.get(band_name)
            if not isinstance(raw_range, list) or len(raw_range) != 2:
                result.add(file, band_path, "expected a two-item prestige range")
                continue
            low = _v2_int(
                raw_range[0],
                result=result,
                file=file,
                path=f"{band_path}[0]",
                minimum=0,
            )
            raw_high = raw_range[1]
            if raw_high is None:
                high = None
                open_ended_count += 1
                if index != len(_V2_BAND_NAMES) - 1:
                    result.add(
                        file,
                        f"{band_path}[1]",
                        "only the terminal band may be open ended",
                    )
            else:
                high = _v2_int(
                    raw_high,
                    result=result,
                    file=file,
                    path=f"{band_path}[1]",
                    minimum=1,
                )
            if low is not None and high is not None and low >= high:
                result.add(file, band_path, "prestige range lower bound must be < upper bound")
            if low is not None and previous_high is not None and low != previous_high:
                result.add(
                    file,
                    band_path,
                    "prestige bands must be gapless and non-overlapping",
                )
            if low is not None:
                bands.append((band_name, low, high))
            previous_high = high
        if open_ended_count != 1:
            result.add(
                file,
                f"{path}.v2_bands",
                "requires exactly one open-ended terminal band",
            )

    _v2_literal(
        segmentation.get("first_high_band"),
        allowed=frozenset({"veteran"}),
        result=result,
        file=file,
        path=f"{path}.first_high_band",
    )
    _v2_int(
        segmentation.get("empty_high_band_target_supply"),
        result=result,
        file=file,
        path=f"{path}.empty_high_band_target_supply",
        expected=0,
    )
    activation_sources = segmentation.get("high_band_activation_sources")
    expected_sources = {
        "active_real_player_presence",
        "explicit_map_search_demand",
        "explicit_arena_demand",
    }
    if not isinstance(activation_sources, list) or any(not isinstance(item, str) for item in activation_sources):
        result.add(
            file,
            f"{path}.high_band_activation_sources",
            "expected a list of activation sources",
        )
    elif len(activation_sources) != len(set(activation_sources)) or set(activation_sources) != expected_sources:
        result.add(
            file,
            f"{path}.high_band_activation_sources",
            "must contain exactly the frozen activation sources",
        )
    for field_name in (
        "lower_band_supply_counts_for_higher_band",
        "cross_band_reactivation_allowed",
        "cross_band_instant_strength_promotion_allowed",
    ):
        _v2_bool(
            segmentation.get(field_name),
            result=result,
            file=file,
            path=f"{path}.{field_name}",
            expected=False,
        )
    return tuple(bands)


def _validate_v2_routing(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    routing = _v2_mapping(value, result=result, file=file, path=path)
    if routing is None:
        return
    _reject_unknown_fields(routing, _V2_ROUTING_FIELDS, result=result, file=file, path=path)
    _require_fields(routing, _V2_ROUTING_FIELDS, result=result, file=file, path=path)
    _v2_literal(
        routing.get("activation_mode"),
        allowed=frozenset({"direct_after_gate"}),
        result=result,
        file=file,
        path=f"{path}.activation_mode",
    )
    bootstrap_mode = _v2_literal(
        routing.get("bootstrap_mode"),
        allowed=frozenset({"legacy_before_gate", "v2_active", "v2_paused"}),
        result=result,
        file=file,
        path=f"{path}.bootstrap_mode",
    )
    maintenance_mode = _v2_literal(
        routing.get("maintenance_mode"),
        allowed=frozenset({"legacy_before_gate", "v2_cutover", "v2_active", "v2_paused"}),
        result=result,
        file=file,
        path=f"{path}.maintenance_mode",
    )
    if bootstrap_mode not in (None, "v2_active"):
        result.add(file, f"{path}.bootstrap_mode", "single-policy runtime requires v2_active")
    if maintenance_mode not in (None, "v2_active"):
        result.add(file, f"{path}.maintenance_mode", "single-policy runtime requires v2_active")
    if bootstrap_mode == "legacy_before_gate" and maintenance_mode not in (
        None,
        "legacy_before_gate",
    ):
        result.add(
            file,
            path,
            "Maintenance cannot leave legacy mode before Bootstrap exits Gate D1",
        )


def _validate_v2_policy_rollout(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> int | None:
    rollout = _v2_mapping(value, result=result, file=file, path=path)
    if rollout is None:
        return None
    _reject_unknown_fields(rollout, _V2_POLICY_ROLLOUT_FIELDS, result=result, file=file, path=path)
    _require_fields(rollout, _V2_POLICY_ROLLOUT_FIELDS, result=result, file=file, path=path)
    target_version = _v2_int(
        rollout.get("target_version"),
        result=result,
        file=file,
        path=f"{path}.target_version",
        minimum=1,
    )
    if target_version not in (None, 2):
        result.add(file, f"{path}.target_version", "single-policy runtime requires target_version=2")
    enabled = _v2_bool(rollout.get("enabled"), result=result, file=file, path=f"{path}.enabled")
    if enabled is True:
        result.add(file, f"{path}.enabled", "multi-version policy rollout is retired")
    rollout_percent = _v2_int(
        rollout.get("rollout_percent"),
        result=result,
        file=file,
        path=f"{path}.rollout_percent",
        maximum=100,
    )
    if enabled is False and rollout_percent not in (None, 0):
        result.add(
            file,
            f"{path}.rollout_percent",
            "must be 0 while policy rollout is disabled",
        )
    if enabled is True and rollout_percent == 0:
        result.add(
            file,
            f"{path}.rollout_percent",
            "must be positive while policy rollout is enabled",
        )
    return target_version


def _validate_v2_reference_snapshot_catalog(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    catalog = _v2_mapping(value, result=result, file=file, path=path)
    if catalog is None:
        return
    artifact_paths: set[str] = set()
    for raw_version, raw_entry in catalog.items():
        entry_path = f"{path}.{raw_version}"
        if (
            not isinstance(raw_version, str)
            or not raw_version.isdigit()
            or str(int(raw_version)) != raw_version
            or int(raw_version) < 1
        ):
            result.add(
                file,
                entry_path,
                "catalog key must be a canonical positive integer string",
            )
            continue
        entry = _v2_mapping(
            raw_entry,
            result=result,
            file=file,
            path=entry_path,
        )
        if entry is None:
            continue
        _reject_unknown_fields(
            entry,
            _V2_REFERENCE_SNAPSHOT_CATALOG_ENTRY_FIELDS,
            result=result,
            file=file,
            path=entry_path,
        )
        _require_fields(
            entry,
            _V2_REFERENCE_SNAPSHOT_CATALOG_ENTRY_REQUIRED_FIELDS,
            result=result,
            file=file,
            path=entry_path,
        )
        _v2_int(
            entry.get("schema_version"),
            result=result,
            file=file,
            path=f"{entry_path}.schema_version",
            minimum=1,
        )
        digest = entry.get("digest")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            result.add(
                file,
                f"{entry_path}.digest",
                "expected a lowercase SHA-256 checksum",
            )
        artifact_path = entry.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path:
            result.add(
                file,
                f"{entry_path}.artifact_path",
                "expected a project-relative data JSON path",
            )
            continue
        normalized_path = PurePosixPath(artifact_path)
        if (
            normalized_path.is_absolute()
            or normalized_path.as_posix() != artifact_path
            or not normalized_path.parts
            or normalized_path.parts[0] != "data"
            or ".." in normalized_path.parts
            or normalized_path.suffix != ".json"
            or "\\" in artifact_path
        ):
            result.add(
                file,
                f"{entry_path}.artifact_path",
                "expected a canonical project-relative data JSON path",
            )
            continue
        if artifact_path in artifact_paths:
            result.add(
                file,
                f"{entry_path}.artifact_path",
                "artifact path must be unique across catalog entries",
            )
        artifact_paths.add(artifact_path)
        raw_evidence = entry.get("gate_d2_evidence")
        if raw_evidence is None:
            continue
        evidence_by_policy = _v2_mapping(
            raw_evidence,
            result=result,
            file=file,
            path=f"{entry_path}.gate_d2_evidence",
        )
        if evidence_by_policy is None:
            continue
        for raw_policy_version, raw_bands in evidence_by_policy.items():
            policy_path = f"{entry_path}.gate_d2_evidence.{raw_policy_version}"
            if (
                not isinstance(raw_policy_version, str)
                or not raw_policy_version.isdigit()
                or str(int(raw_policy_version)) != raw_policy_version
                or int(raw_policy_version) < 1
            ):
                result.add(
                    file,
                    policy_path,
                    "policy key must be a canonical positive integer string",
                )
                continue
            evidence_bands = _v2_mapping(
                raw_bands,
                result=result,
                file=file,
                path=policy_path,
            )
            if evidence_bands is None:
                continue
            for prestige_band, raw_evidence_entry in evidence_bands.items():
                evidence_path = f"{policy_path}.{prestige_band}"
                if prestige_band not in _V2_BAND_NAMES:
                    result.add(file, evidence_path, "unknown V2 prestige band")
                    continue
                evidence_entry = _v2_mapping(
                    raw_evidence_entry,
                    result=result,
                    file=file,
                    path=evidence_path,
                )
                if evidence_entry is None:
                    continue
                _reject_unknown_fields(
                    evidence_entry,
                    _V2_GATE_D2_EVIDENCE_ENTRY_FIELDS,
                    result=result,
                    file=file,
                    path=evidence_path,
                )
                _require_fields(
                    evidence_entry,
                    _V2_GATE_D2_EVIDENCE_ENTRY_FIELDS,
                    result=result,
                    file=file,
                    path=evidence_path,
                )
                _v2_int(
                    evidence_entry.get("schema_version"),
                    result=result,
                    file=file,
                    path=f"{evidence_path}.schema_version",
                    expected=3,
                )
                evidence_digest = evidence_entry.get("digest")
                if (
                    not isinstance(evidence_digest, str)
                    or len(evidence_digest) != 64
                    or any(character not in "0123456789abcdef" for character in evidence_digest)
                ):
                    result.add(
                        file,
                        f"{evidence_path}.digest",
                        "expected a lowercase SHA-256 checksum",
                    )


def _validate_v2_growth(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    growth = _v2_mapping(value, result=result, file=file, path=path)
    if growth is None:
        return
    _reject_unknown_fields(growth, _V2_GROWTH_FIELDS, result=result, file=file, path=path)
    _require_fields(growth, _V2_GROWTH_FIELDS, result=result, file=file, path=path)
    false_fields: tuple[str, ...] = (
        "direct_prestige_grant_by_maintenance_allowed",
        "external_domain_result_may_be_rejected_by_bot_growth_policy",
        "bootstrap_fake_per_action_history_records",
    )
    for field_name in false_fields:
        _v2_bool(
            growth.get(field_name),
            result=result,
            file=file,
            path=f"{path}.{field_name}",
            expected=False,
        )
    arena_bypass_path = f"{path}.arena_acceleration_bypass"
    arena_bypass = _v2_mapping(
        growth.get("arena_acceleration_bypass"),
        result=result,
        file=file,
        path=arena_bypass_path,
    )
    if arena_bypass is not None:
        _reject_unknown_fields(
            arena_bypass,
            _V2_ARENA_ACCELERATION_BYPASS_FIELDS,
            result=result,
            file=file,
            path=arena_bypass_path,
        )
        _require_fields(
            arena_bypass,
            _V2_ARENA_ACCELERATION_BYPASS_FIELDS,
            result=result,
            file=file,
            path=arena_bypass_path,
        )
        _v2_bool(
            arena_bypass.get("due"),
            result=result,
            file=file,
            path=f"{arena_bypass_path}.due",
            expected=True,
        )
    _v2_int(
        growth.get("configured_boundaries_crossed_per_controlled_action_max"),
        result=result,
        file=file,
        path=f"{path}.configured_boundaries_crossed_per_controlled_action_max",
        expected=1,
    )

    profiles = _v2_mapping(growth.get("profiles"), result=result, file=file, path=f"{path}.profiles")
    if profiles is None:
        return
    actual_names = list(profiles)
    if actual_names != list(_V2_BAND_NAMES):
        result.add(
            file,
            f"{path}.profiles",
            f"profile names and order must equal {list(_V2_BAND_NAMES)}",
        )
    previous_history: tuple[float, float] | None = None
    previous_interval: tuple[float, float] | None = None
    for band_name in _V2_BAND_NAMES:
        profile_path = f"{path}.profiles.{band_name}"
        profile = _v2_mapping(profiles.get(band_name), result=result, file=file, path=profile_path)
        if profile is None:
            continue
        _reject_unknown_fields(
            profile,
            _V2_GROWTH_PROFILE_FIELDS,
            result=result,
            file=file,
            path=profile_path,
        )
        _require_fields(
            profile,
            _V2_GROWTH_PROFILE_FIELDS,
            result=result,
            file=file,
            path=profile_path,
        )
        history = _v2_integer_range(
            profile.get("bootstrap_history_age_days"),
            result=result,
            file=file,
            path=f"{profile_path}.bootstrap_history_age_days",
        )
        interval = _v2_numeric_range(
            profile.get("preferred_strength_check_interval_hours"),
            result=result,
            file=file,
            path=f"{profile_path}.preferred_strength_check_interval_hours",
        )
        if (
            history is not None
            and previous_history is not None
            and (history[0] < previous_history[0] or history[1] < previous_history[1])
        ):
            result.add(
                file,
                f"{profile_path}.bootstrap_history_age_days",
                "must not decrease across prestige bands",
            )
        if (
            interval is not None
            and previous_interval is not None
            and (interval[0] < previous_interval[0] or interval[1] < previous_interval[1])
        ):
            result.add(
                file,
                f"{profile_path}.preferred_strength_check_interval_hours",
                "must not decrease across prestige bands",
            )
        previous_history = history or previous_history
        previous_interval = interval or previous_interval


def _validate_v2_starter_snapshots(
    value: Any,
    *,
    bands: tuple[tuple[str, int, int | None], ...],
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    snapshots = _v2_mapping(value, result=result, file=file, path=path)
    if snapshots is None:
        return
    _reject_unknown_fields(snapshots, _V2_STARTER_SNAPSHOT_FIELDS, result=result, file=file, path=path)
    _require_fields(snapshots, _V2_STARTER_SNAPSHOT_FIELDS, result=result, file=file, path=path)
    _v2_int(
        snapshots.get("snapshot_version"),
        result=result,
        file=file,
        path=f"{path}.snapshot_version",
        minimum=1,
    )
    profiles = _v2_mapping(snapshots.get("profiles"), result=result, file=file, path=f"{path}.profiles")
    if profiles is None:
        return
    if list(profiles) != list(_V2_BAND_NAMES):
        result.add(
            file,
            f"{path}.profiles",
            f"snapshot names and order must equal {list(_V2_BAND_NAMES)}",
        )
    band_bounds = {name: (low, high) for name, low, high in bands}
    for band_name in _V2_BAND_NAMES:
        profile_path = f"{path}.profiles.{band_name}"
        profile = _v2_mapping(profiles.get(band_name), result=result, file=file, path=profile_path)
        if profile is None:
            continue
        _reject_unknown_fields(
            profile,
            _V2_STARTER_PROFILE_FIELDS,
            result=result,
            file=file,
            path=profile_path,
        )
        _require_fields(
            profile,
            _V2_STARTER_PROFILE_FIELDS,
            result=result,
            file=file,
            path=profile_path,
        )
        values: dict[str, int] = {}
        for field_name in _V2_STARTER_PROFILE_FIELDS:
            normalized = _v2_int(
                profile.get(field_name),
                result=result,
                file=file,
                path=f"{profile_path}.{field_name}",
                minimum=0,
            )
            if normalized is not None:
                values[field_name] = normalized
        prestige = values.get("prestige")
        bounds = band_bounds.get(band_name)
        if prestige is not None and bounds is not None:
            post_ratio_prestige = math.floor(prestige * 0.90)
            low, high = bounds
            if post_ratio_prestige < low or (high is not None and post_ratio_prestige >= high):
                result.add(
                    file,
                    f"{profile_path}.prestige",
                    "90 percent starter prestige must remain inside its configured band",
                )


def _validate_v2_ratio_groups(
    policy: dict[Any, Any],
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    gear_range = _v2_numeric_range(
        policy.get("gear_upgrade_threshold"),
        result=result,
        file=file,
        path=f"{path}.gear_upgrade_threshold",
        maximum=1.0,
    )
    if gear_range is not None and gear_range[0] <= 0:
        result.add(file, f"{path}.gear_upgrade_threshold", "lower bound must be positive")

    roster = _v2_mapping(
        policy.get("roster_tiers"),
        result=result,
        file=file,
        path=f"{path}.roster_tiers",
    )
    roster_names = ("core", "secondary", "bench")
    if roster is not None:
        allowed = frozenset(roster_names)
        _reject_unknown_fields(roster, allowed, result=result, file=file, path=f"{path}.roster_tiers")
        _require_fields(roster, allowed, result=result, file=file, path=f"{path}.roster_tiers")
        ranges = [
            _v2_numeric_range(
                roster.get(name),
                result=result,
                file=file,
                path=f"{path}.roster_tiers.{name}",
                maximum=1.0,
            )
            for name in roster_names
        ]
        if all(item is not None for item in ranges):
            core, secondary, bench = ranges
            assert core is not None and secondary is not None and bench is not None
            if not (bench[1] <= secondary[0] and secondary[1] <= core[0]):
                result.add(
                    file,
                    f"{path}.roster_tiers",
                    "tier ranges must be ordered bench, secondary, core",
                )

    troop_mix = _v2_mapping(policy.get("troop_mix"), result=result, file=file, path=f"{path}.troop_mix")
    troop_names = ("primary", "secondary", "scout")
    if troop_mix is not None:
        allowed = frozenset(troop_names)
        _reject_unknown_fields(troop_mix, allowed, result=result, file=file, path=f"{path}.troop_mix")
        _require_fields(troop_mix, allowed, result=result, file=file, path=f"{path}.troop_mix")
        ranges = [
            _v2_numeric_range(
                troop_mix.get(name),
                result=result,
                file=file,
                path=f"{path}.troop_mix.{name}",
                maximum=1.0,
            )
            for name in troop_names
        ]
        if all(item is not None for item in ranges):
            typed_ranges = [item for item in ranges if item is not None]
            if sum(item[0] for item in typed_ranges) > 1 or sum(item[1] for item in typed_ranges) < 1:
                result.add(
                    file,
                    f"{path}.troop_mix",
                    "ratio ranges cannot be normalized to a total of 1",
                )

    personas = _v2_mapping(policy.get("personas"), result=result, file=file, path=f"{path}.personas")
    if personas is not None:
        expected = frozenset(VIRTUAL_PLAYER_ARCHETYPES)
        _reject_unknown_fields(personas, expected, result=result, file=file, path=f"{path}.personas")
        _require_fields(personas, expected, result=result, file=file, path=f"{path}.personas")
        for archetype in VIRTUAL_PLAYER_ARCHETYPES:
            persona_path = f"{path}.personas.{archetype}"
            persona = _v2_mapping(personas.get(archetype), result=result, file=file, path=persona_path)
            if persona:
                _reject_unknown_fields(persona, frozenset(), result=result, file=file, path=persona_path)


def _validate_v2_policy(
    policy: dict[Any, Any],
    *,
    version: int,
    bands: tuple[tuple[str, int, int | None], ...],
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    _reject_unknown_fields(policy, _V2_POLICY_FIELDS, result=result, file=file, path=path)
    _require_fields(policy, _V2_POLICY_FIELDS, result=result, file=file, path=path)
    declared_checksum = policy.get("checksum")
    if (
        not isinstance(declared_checksum, str)
        or len(declared_checksum) != 64
        or any(character not in "0123456789abcdef" for character in declared_checksum)
    ):
        result.add(file, f"{path}.checksum", "expected a lowercase SHA-256 checksum")
    calculated_checksum = _policy_checksum(policy)
    if (
        isinstance(declared_checksum, str)
        and calculated_checksum is not None
        and declared_checksum != calculated_checksum
    ):
        result.add(file, f"{path}.checksum", "does not match the normalized policy payload")
    _v2_int(
        policy.get("max_development_actions"),
        result=result,
        file=file,
        path=f"{path}.max_development_actions",
        expected=(16 if int(version) == 2 else 1),
    )
    _v2_int(
        policy.get("anchor_k"),
        result=result,
        file=file,
        path=f"{path}.anchor_k",
        minimum=1,
    )
    _validate_v2_growth(
        policy.get("prestige_band_growth"),
        result=result,
        file=file,
        path=f"{path}.prestige_band_growth",
    )
    _validate_v2_starter_snapshots(
        policy.get("starter_snapshots"),
        bands=bands,
        result=result,
        file=file,
        path=f"{path}.starter_snapshots",
    )
    _validate_v2_ratio_groups(policy, result=result, file=file, path=path)


def _validate_v2_arena_training_policy(
    value: Any,
    *,
    bands: tuple[tuple[str, int, int | None], ...],
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    policy = _v2_mapping(value, result=result, file=file, path=path)
    if policy is None:
        return
    _reject_unknown_fields(policy, _V2_ARENA_TRAINING_POLICY_FIELDS, result=result, file=file, path=path)
    _require_fields(policy, _V2_ARENA_TRAINING_POLICY_FIELDS, result=result, file=file, path=path)
    _v2_int(
        policy.get("schema_version"),
        result=result,
        file=file,
        path=f"{path}.schema_version",
        expected=2,
    )
    _v2_int(
        policy.get("version"),
        result=result,
        file=file,
        path=f"{path}.version",
        minimum=1,
    )
    declared_checksum = policy.get("checksum")
    if (
        not isinstance(declared_checksum, str)
        or len(declared_checksum) != 64
        or any(character not in "0123456789abcdef" for character in declared_checksum)
    ):
        result.add(file, f"{path}.checksum", "expected a lowercase SHA-256 checksum")
    calculated_checksum = _policy_checksum(policy)
    if (
        isinstance(declared_checksum, str)
        and calculated_checksum is not None
        and declared_checksum != calculated_checksum
    ):
        result.add(file, f"{path}.checksum", "does not match the normalized arena training payload")

    envelopes = _v2_mapping(
        policy.get("envelopes"),
        result=result,
        file=file,
        path=f"{path}.envelopes",
    )
    if not envelopes:
        result.add(file, f"{path}.envelopes", "requires at least one strength envelope")
        return
    configured_bands = {name for name, _low, _high in bands}
    ranges: list[tuple[int, int | None, str]] = []
    for raw_segment, raw_envelope in envelopes.items():
        segment = str(raw_segment).strip() if isinstance(raw_segment, str) else ""
        envelope_path = f"{path}.envelopes.{raw_segment}"
        if not segment:
            result.add(file, envelope_path, "segment key must be a non-empty string")
            continue
        envelope = _v2_mapping(raw_envelope, result=result, file=file, path=envelope_path)
        if envelope is None:
            continue
        _reject_unknown_fields(
            envelope,
            _V2_ARENA_TRAINING_ENVELOPE_FIELDS,
            result=result,
            file=file,
            path=envelope_path,
        )
        _require_fields(
            envelope,
            _V2_ARENA_TRAINING_ENVELOPE_FIELDS,
            result=result,
            file=file,
            path=envelope_path,
        )
        raw_range = envelope.get("ready_power_range")
        range_path = f"{envelope_path}.ready_power_range"
        if not isinstance(raw_range, list) or len(raw_range) != 2:
            result.add(file, range_path, "expected a two-item ready-power range")
            continue
        lower = _v2_int(raw_range[0], result=result, file=file, path=f"{range_path}[0]", minimum=0)
        if raw_range[1] is None:
            upper = None
        else:
            upper = _v2_int(raw_range[1], result=result, file=file, path=f"{range_path}[1]", minimum=0)
        if lower is not None and upper is not None and upper < lower:
            result.add(file, range_path, "range upper bound must be >= lower bound")
        if lower is not None:
            ranges.append((lower, upper, segment))
        supply_priority = envelope.get("supply_prestige_band_priority")
        priority_path = f"{envelope_path}.supply_prestige_band_priority"
        if not isinstance(supply_priority, list) or not supply_priority:
            result.add(file, priority_path, "must be a non-empty ordered V2 prestige-band list")
            continue
        if any(not isinstance(band, str) or band not in configured_bands for band in supply_priority):
            result.add(file, priority_path, "must only contain configured V2 prestige bands")
        elif len(set(supply_priority)) != len(supply_priority):
            result.add(file, priority_path, "must not repeat a V2 prestige band")

    previous_upper: int | None = None
    for index, (lower, upper, _segment) in enumerate(
        sorted(ranges, key=lambda item: (item[0], item[1] is None, 0 if item[1] is None else item[1], item[2]))
    ):
        if previous_upper is None and index > 0:
            result.add(file, f"{path}.envelopes", "only the final strength envelope may be open ended")
            break
        if previous_upper is not None and lower <= previous_upper:
            result.add(file, f"{path}.envelopes", "strength envelope ranges must not overlap")
            break
        previous_upper = upper


def _validate_v2_growth_control(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    config = _v2_mapping(value, result=result, file=file, path=path)
    if config is None:
        return
    _reject_unknown_fields(config, _V2_GROWTH_CONTROL_FIELDS, result=result, file=file, path=path)
    _require_fields(config, _V2_GROWTH_CONTROL_FIELDS, result=result, file=file, path=path)
    _v2_int(
        config.get("minimum_sample_count"),
        result=result,
        file=file,
        path=f"{path}.minimum_sample_count",
        minimum=1,
    )
    smoothing_alpha = _v2_number(
        config.get("smoothing_alpha"),
        result=result,
        file=file,
        path=f"{path}.smoothing_alpha",
        minimum=0.0,
        maximum=1.0,
    )
    if smoothing_alpha is not None and smoothing_alpha <= 0:
        result.add(file, f"{path}.smoothing_alpha", "must be > 0")
    _v2_int(
        config.get("maximum_daily_delta_bps"),
        result=result,
        file=file,
        path=f"{path}.maximum_daily_delta_bps",
        minimum=0,
    )
    _v2_int(
        config.get("active_sample_days"),
        result=result,
        file=file,
        path=f"{path}.active_sample_days",
        minimum=1,
    )
    _v2_int(
        config.get("ttl_days"),
        result=result,
        file=file,
        path=f"{path}.ttl_days",
        minimum=1,
    )


def _validate_bot_development_v2(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str = "bot_development_v2",
) -> None:
    config = _v2_mapping(value, result=result, file=file, path=path)
    if config is None:
        return
    _reject_unknown_fields(config, _V2_ROOT_FIELDS, result=result, file=file, path=path)
    _require_fields(config, _V2_REQUIRED_ROOT_FIELDS, result=result, file=file, path=path)
    _v2_literal(
        config.get("environment_mode"),
        allowed=frozenset({"test"}),
        result=result,
        file=file,
        path=f"{path}.environment_mode",
    )
    _v2_int(
        config.get("engine_version"),
        result=result,
        file=file,
        path=f"{path}.engine_version",
        expected=2,
    )
    _v2_int(
        config.get("rng_version"),
        result=result,
        file=file,
        path=f"{path}.rng_version",
        expected=1,
    )
    _v2_int(
        config.get("plan_schema_version"),
        result=result,
        file=file,
        path=f"{path}.plan_schema_version",
        expected=1,
    )
    bands = _validate_v2_prestige_segmentation(
        config.get("prestige_segmentation"),
        result=result,
        file=file,
        path=f"{path}.prestige_segmentation",
    )
    if "arena_training_policy" in config:
        _validate_v2_arena_training_policy(
            config.get("arena_training_policy"),
            bands=bands,
            result=result,
            file=file,
            path=f"{path}.arena_training_policy",
        )
    if "growth_control" in config:
        _validate_v2_growth_control(
            config.get("growth_control"),
            result=result,
            file=file,
            path=f"{path}.growth_control",
        )
    _validate_v2_routing(config.get("routing"), result=result, file=file, path=f"{path}.routing")
    target_version = _validate_v2_policy_rollout(
        config.get("policy_rollout"),
        result=result,
        file=file,
        path=f"{path}.policy_rollout",
    )
    policies = _v2_mapping(config.get("policies"), result=result, file=file, path=f"{path}.policies")
    normalized_versions: set[int] = set()
    if policies is not None:
        if not policies:
            result.add(file, f"{path}.policies", "requires at least one policy release input")
        for raw_version, raw_policy in policies.items():
            policy_path = f"{path}.policies.{raw_version}"
            if (
                not isinstance(raw_version, str)
                or not raw_version.isdigit()
                or str(int(raw_version)) != raw_version
                or int(raw_version) < 1
            ):
                result.add(
                    file,
                    policy_path,
                    "policy key must be a canonical positive integer string",
                )
                continue
            version = int(raw_version)
            normalized_versions.add(version)
            policy = _v2_mapping(raw_policy, result=result, file=file, path=policy_path)
            if policy is not None:
                _validate_v2_policy(
                    policy,
                    version=version,
                    bands=bands,
                    result=result,
                    file=file,
                    path=policy_path,
                )
    if normalized_versions != {2}:
        result.add(file, f"{path}.policies", "single-policy runtime requires exactly policy 2")
    if target_version is not None and target_version not in normalized_versions:
        result.add(
            file,
            f"{path}.policy_rollout.target_version",
            "target policy is missing from policies",
        )


def _validate_int(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    minimum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        result.add(file, path, "expected an integer")
        return
    if value < minimum:
        result.add(file, path, f"must be >= {minimum}")


def _validate_int_range(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    allow_open_high: bool = False,
    min_value: int | None = None,
) -> None:
    if not isinstance(value, list) or len(value) != 2:
        result.add(file, path, "expected a two-item list range")
        return

    low, high = value
    if not isinstance(low, int):
        result.add(file, path, "range lower bound must be an integer")
    if high is not None or not allow_open_high:
        if not isinstance(high, int):
            result.add(file, path, "range upper bound must be an integer")
            return
    if isinstance(low, int) and isinstance(high, int) and high < low:
        result.add(file, path, "range upper bound must be >= lower bound")
    if min_value is not None:
        if isinstance(low, int) and low < min_value:
            result.add(file, path, f"range lower bound must be >= {min_value}")
        if isinstance(high, int) and high < min_value:
            result.add(file, path, f"range upper bound must be >= {min_value}")


def _validate_ratio_range(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    if not isinstance(value, list) or len(value) != 2:
        result.add(file, path, "expected a two-item ratio range")
        return
    low, high = value
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        result.add(file, path, "ratio bounds must be numbers")
        return
    if low < 0 or high > 1:
        result.add(file, path, "ratio bounds must be between 0 and 1")
    if high < low:
        result.add(file, path, "ratio upper bound must be >= lower bound")


def _validate_ratio(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        result.add(file, path, "expected a number between 0 and 1")
        return
    if value < 0 or value > 1:
        result.add(file, path, "must be between 0 and 1")


def _validate_rarity_stage_mapping(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    if not isinstance(value, dict):
        result.add(file, path, "expected a mapping")
        return

    valid_entries: list[tuple[int, int, str]] = []
    for raw_stage, rarity in value.items():
        entry_path = f"{path}.{raw_stage}"
        valid_stage = isinstance(raw_stage, int) and not isinstance(raw_stage, bool) and raw_stage > 0
        if not valid_stage:
            result.add(file, entry_path, "stage must be a positive integer")
        valid_rarity = isinstance(rarity, str) and rarity in _GEAR_RARITIES
        if not valid_rarity:
            result.add(file, entry_path, "expected a supported rarity")
        if valid_stage and valid_rarity:
            valid_entries.append((raw_stage, _RARITY_RANKS[rarity], entry_path))

    highest_rank = -1
    for _, rarity_rank, entry_path in sorted(valid_entries):
        if rarity_rank < highest_rank:
            result.add(file, entry_path, "rarity must not decrease as stage increases")
        highest_rank = max(highest_rank, rarity_rank)


def _validate_string_list(value: Any, *, result: ValidationResult, file: str, path: str, field_name: str) -> None:
    if isinstance(value, str):
        allowed_sentinels = {"__all__"}
        if field_name in {"item_template_keys", "loot_item_template_keys"}:
            allowed_sentinels = _SELECTION_SENTINELS
        if value not in allowed_sentinels:
            result.add(file, path, f"field '{field_name}' expected list or supported selector")
        return
    if not isinstance(value, list):
        result.add(
            file,
            path,
            f"field '{field_name}' expected list, got {type(value).__name__}",
        )
        return
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            result.add(file, f"{path}.{field_name}[{idx}]", "expected string")


def validate_virtual_players(data: dict, *, file: str = "virtual_players.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    _reject_unknown_fields(data, _ROOT_FIELDS, result=result, file=file, path="<root>")

    enabled = data.get("enabled")
    if enabled is not None:
        _check_type(enabled, bool, result=result, file=file, path="<root>", field_name="enabled")

    population = data.get("population")
    if population is not None:
        if not isinstance(population, dict):
            result.add(file, "population", "expected a mapping")
        else:
            for field_name in (
                "active_player_multiplier",
                "min_per_region",
                "min_attackable_per_band",
                "hard_cap",
            ):
                value = population.get(field_name)
                if value is None:
                    continue
                _check_type(
                    value,
                    int,
                    result=result,
                    file=file,
                    path="population",
                    field_name=field_name,
                )
                _check_positive(
                    value,
                    result=result,
                    file=file,
                    path="population",
                    field_name=field_name,
                )
            for field_name, minimum in (
                ("active_window_days", 1),
                ("cell_floor", 0),
                ("cell_active_multiplier", 0),
                ("region_floor", 0),
                ("region_active_multiplier", 0),
                ("global_floor", 0),
                ("global_active_multiplier", 0),
                ("exploration_supply", 0),
            ):
                if field_name in population:
                    _validate_int(
                        population[field_name],
                        result=result,
                        file=file,
                        path=f"population.{field_name}",
                        minimum=minimum,
                    )
            if "rolling_batch_size" in population:
                _validate_int_range(
                    population["rolling_batch_size"],
                    result=result,
                    file=file,
                    path="population.rolling_batch_size",
                )
            if "retired_reactivation_chance" in population:
                _validate_ratio(
                    population["retired_reactivation_chance"],
                    result=result,
                    file=file,
                    path="population.retired_reactivation_chance",
                )

    prestige_bands = data.get("prestige_bands")
    if prestige_bands is not None:
        if not isinstance(prestige_bands, dict):
            result.add(file, "prestige_bands", "expected a mapping")
        else:
            for band, value in prestige_bands.items():
                _validate_int_range(
                    value,
                    result=result,
                    file=file,
                    path=f"prestige_bands.{band}",
                    allow_open_high=True,
                )

    lifecycle = data.get("lifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, dict):
            result.add(file, "lifecycle", "expected a mapping")
        else:
            for field_name in ("active_days", "abandoned_days", "next_growth_hours"):
                if field_name in lifecycle:
                    _validate_int_range(
                        lifecycle[field_name],
                        result=result,
                        file=file,
                        path=f"lifecycle.{field_name}",
                    )
            for field_name in (
                "empty_hit_stale_threshold",
                "empty_hit_window_hours",
                "stale_no_interaction_days",
            ):
                value = lifecycle.get(field_name)
                if value is None:
                    continue
                _check_type(
                    value,
                    int,
                    result=result,
                    file=file,
                    path="lifecycle",
                    field_name=field_name,
                )
                _check_positive(
                    value,
                    result=result,
                    file=file,
                    path="lifecycle",
                    field_name=field_name,
                )

    growth = data.get("growth")
    if growth is not None:
        if not isinstance(growth, dict):
            result.add(file, "growth", "expected a mapping")
        else:
            stage_caps = growth.get("stage_caps")
            if stage_caps is not None:
                if not isinstance(stage_caps, dict):
                    result.add(file, "growth.stage_caps", "expected a mapping")
                else:
                    supported_bands = (
                        set(prestige_bands)
                        if isinstance(prestige_bands, dict) and prestige_bands
                        else DEFAULT_VIRTUAL_PLAYER_PRESTIGE_BANDS
                    )
                    for band, value in stage_caps.items():
                        if band not in supported_bands:
                            result.add(
                                file,
                                f"growth.stage_caps.{band}",
                                "expected a configured prestige band",
                            )
                        _check_type(
                            value,
                            int,
                            result=result,
                            file=file,
                            path="growth.stage_caps",
                            field_name=str(band),
                        )
                        _check_positive(
                            value,
                            result=result,
                            file=file,
                            path="growth.stage_caps",
                            field_name=str(band),
                        )
            for field_name in ("catch_up_ratio", "slowing_ratio_multiplier"):
                if field_name in growth:
                    _validate_ratio(
                        growth[field_name],
                        result=result,
                        file=file,
                        path=f"growth.{field_name}",
                    )
            for field_name in (
                "max_building_step",
                "max_guest_level_step",
                "max_prestige_step",
            ):
                if field_name in growth:
                    _validate_int(
                        growth[field_name],
                        result=result,
                        file=file,
                        path=f"growth.{field_name}",
                        minimum=1,
                    )

    resources = data.get("resources")
    if resources is not None:
        if not isinstance(resources, dict):
            result.add(file, "resources", "expected a mapping")
        else:
            for archetype, value in resources.items():
                _validate_ratio_range(value, result=result, file=file, path=f"resources.{archetype}")

    projection = data.get("projection")
    if projection is not None:
        if not isinstance(projection, dict):
            result.add(file, "projection", "expected a mapping")
        else:
            for field_name in (
                "guest_template_keys",
                "gear_template_keys",
                "troop_template_keys",
                "technology_keys",
                "extra_skill_keys",
                "high_tier_skill_keys",
                "item_template_keys",
                "loot_item_template_keys",
            ):
                if field_name in projection:
                    _validate_string_list(
                        projection[field_name],
                        result=result,
                        file=file,
                        path="projection",
                        field_name=field_name,
                    )
            for field_name in (
                "powerful_item_daily_global_cap",
                "powerful_item_min_price",
                "powerful_item_min_growth_stage",
                "powerful_item_prestige_chance",
                "low_stage_powerful_item_chance",
            ):
                if field_name in projection:
                    result.add(
                        file,
                        f"projection.{field_name}",
                        "field is retired; inventory projection uses rarity, stage, control and component caps",
                    )
            if "extra_skills_per_guest" in projection:
                _validate_int_range(
                    projection["extra_skills_per_guest"],
                    result=result,
                    file=file,
                    path="projection.extra_skills_per_guest",
                )
            if "early_stage_skill_count" in projection:
                _validate_int_range(
                    projection["early_stage_skill_count"],
                    result=result,
                    file=file,
                    path="projection.early_stage_skill_count",
                    min_value=0,
                )
                values = projection["early_stage_skill_count"]
                if isinstance(values, list) and len(values) == 2:
                    for index, value in enumerate(values):
                        if isinstance(value, int) and value > 1:
                            result.add(
                                file,
                                f"projection.early_stage_skill_count[{index}]",
                                "must be <= 1",
                            )
            if "high_tier_skill_chance" in projection:
                _validate_ratio(
                    projection["high_tier_skill_chance"],
                    result=result,
                    file=file,
                    path="projection.high_tier_skill_chance",
                )
            if "multi_skill_passive_focus_chance" in projection:
                _validate_ratio(
                    projection["multi_skill_passive_focus_chance"],
                    result=result,
                    file=file,
                    path="projection.multi_skill_passive_focus_chance",
                )
            if "high_tier_skills_per_guest" in projection:
                _validate_int_range(
                    projection["high_tier_skills_per_guest"],
                    result=result,
                    file=file,
                    path="projection.high_tier_skills_per_guest",
                    min_value=0,
                )
            if "loot_item_quantity" in projection:
                _validate_int_range(
                    projection["loot_item_quantity"],
                    result=result,
                    file=file,
                    path="projection.loot_item_quantity",
                    min_value=0,
                )
            for field_name in (
                "real_projection_sample_size",
                "real_projection_jitter_bps",
            ):
                value = projection.get(field_name)
                if value is None:
                    continue
                _check_type(
                    value,
                    int,
                    result=result,
                    file=file,
                    path="projection",
                    field_name=field_name,
                )
                _check_positive(
                    value,
                    result=result,
                    file=file,
                    path="projection",
                    field_name=field_name,
                )
            for field_name in ("active_sample_days", "regional_min_sample_size"):
                if field_name in projection:
                    _validate_int(
                        projection[field_name],
                        result=result,
                        file=file,
                        path=f"projection.{field_name}",
                        minimum=1,
                    )
            quantile_weights = projection.get("strength_quantile_weights")
            if quantile_weights is not None:
                if not isinstance(quantile_weights, dict):
                    result.add(
                        file,
                        "projection.strength_quantile_weights",
                        "expected a mapping",
                    )
                else:
                    for key, value in quantile_weights.items():
                        path = f"projection.strength_quantile_weights.{key}"
                        if key not in _STRENGTH_QUANTILES:
                            result.add(file, path, "expected one of p25, p50, p75")
                        _validate_int(value, result=result, file=file, path=path, minimum=0)
                    if not any(
                        isinstance(value, int) and not isinstance(value, bool) and value > 0
                        for value in quantile_weights.values()
                    ):
                        result.add(
                            file,
                            "projection.strength_quantile_weights",
                            "requires at least one positive weight",
                        )
            if "early_stage_skill_max" in projection:
                value = projection["early_stage_skill_max"]
                _check_type(
                    value,
                    int,
                    result=result,
                    file=file,
                    path="projection",
                    field_name="early_stage_skill_max",
                )
                if isinstance(value, int) and value < 0:
                    result.add(file, "projection.early_stage_skill_max", "must be >= 0")
            for rarity_mapping_name in (
                "guest_max_rarity_by_stage",
                "gear_max_rarity_by_stage",
                "inventory_max_rarity_by_stage",
            ):
                rarity_mapping = projection.get(rarity_mapping_name)
                if rarity_mapping is None:
                    continue
                mapping_path = f"projection.{rarity_mapping_name}"
                _validate_rarity_stage_mapping(
                    rarity_mapping,
                    result=result,
                    file=file,
                    path=mapping_path,
                )
            for mapping_name, value_type in (
                ("gear_slots_by_archetype", int),
                ("inventory_quantity_multipliers", (int, float)),
                ("inventory_template_slots_by_archetype", int),
            ):
                mapping = projection.get(mapping_name)
                if mapping is None:
                    continue
                if not isinstance(mapping, dict):
                    result.add(file, f"projection.{mapping_name}", "expected a mapping")
                    continue
                for key, value in mapping.items():
                    path = f"projection.{mapping_name}.{key}"
                    if mapping_name == "inventory_template_slots_by_archetype" and key not in _COMBAT_PERSONAS:
                        result.add(file, path, "expected a supported combat archetype")
                    if not isinstance(value, value_type):
                        result.add(file, path, "expected a numeric value")
                        continue
                    if mapping_name == "inventory_template_slots_by_archetype" and value <= 0:
                        result.add(file, path, "must be > 0")
                    elif value < 0:
                        result.add(file, path, "must be >= 0")
            effect_weights = projection.get("inventory_effect_type_weights")
            if effect_weights is not None:
                if not isinstance(effect_weights, dict):
                    result.add(
                        file,
                        "projection.inventory_effect_type_weights",
                        "expected a mapping",
                    )
                else:
                    for archetype, weights in effect_weights.items():
                        weights_path = f"projection.inventory_effect_type_weights.{archetype}"
                        if archetype not in _COMBAT_PERSONAS:
                            result.add(
                                file,
                                weights_path,
                                "expected a supported combat archetype",
                            )
                        if not isinstance(weights, dict):
                            result.add(file, weights_path, "expected a mapping")
                            continue
                        for effect_type, weight in weights.items():
                            weight_path = f"{weights_path}.{effect_type}"
                            if effect_type not in VIRTUAL_PLAYER_INVENTORY_EFFECT_TYPES:
                                result.add(
                                    file,
                                    weight_path,
                                    "expected a supported inventory effect type",
                                )
                            if not isinstance(weight, int) or weight <= 0:
                                result.add(file, weight_path, "expected a positive integer")
            rare_colors = projection.get("inventory_rare_color_set")
            if rare_colors is not None:
                if not isinstance(rare_colors, list) or not rare_colors:
                    result.add(file, "projection.inventory_rare_color_set", "expected a non-empty list")
                else:
                    seen_colors: set[str] = set()
                    for index, raw_color in enumerate(rare_colors):
                        color = str(raw_color).strip().lower() if isinstance(raw_color, str) else ""
                        path = f"projection.inventory_rare_color_set[{index}]"
                        if color not in {"red", "purple", "orange"}:
                            result.add(file, path, "expected red, purple or orange")
                        if color in seen_colors:
                            result.add(file, path, "must not repeat")
                        seen_colors.add(color)
            color_weights = projection.get("inventory_color_weights_by_prestige_band")
            if color_weights is not None:
                if not isinstance(color_weights, dict):
                    result.add(file, "projection.inventory_color_weights_by_prestige_band", "expected a mapping")
                else:
                    for band, weights in color_weights.items():
                        band_path = f"projection.inventory_color_weights_by_prestige_band.{band}"
                        if band not in _V2_BAND_NAMES:
                            result.add(file, band_path, "expected a configured V2 prestige band")
                        if not isinstance(weights, dict):
                            result.add(file, band_path, "expected a mapping")
                            continue
                        for color, weight in weights.items():
                            color_path = f"{band_path}.{color}"
                            if color not in _RARITY_RANKS:
                                result.add(file, color_path, "expected a supported rarity")
                            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
                                result.add(file, color_path, "expected a non-negative number")
            batch_limit = projection.get("inventory_batch_max_per_cycle")
            if batch_limit is not None:
                _validate_int(
                    batch_limit,
                    result=result,
                    file=file,
                    path="projection.inventory_batch_max_per_cycle",
                    minimum=1,
                )
            loot_budget = projection.get("loot_budget_daily")
            if loot_budget is not None:
                _check_type(
                    loot_budget,
                    int,
                    result=result,
                    file=file,
                    path="projection",
                    field_name="loot_budget_daily",
                )
                _check_positive(
                    loot_budget,
                    result=result,
                    file=file,
                    path="projection",
                    field_name="loot_budget_daily",
                )
            loot_limits = projection.get("loot_limits")
            if loot_limits is not None:
                if not isinstance(loot_limits, dict):
                    result.add(file, "projection.loot_limits", "expected a mapping")
                else:
                    real_attacker_cap = loot_limits.get("real_attacker_daily_resource_cap")
                    if real_attacker_cap is not None:
                        _check_type(
                            real_attacker_cap,
                            int,
                            result=result,
                            file=file,
                            path="projection.loot_limits",
                            field_name="real_attacker_daily_resource_cap",
                        )
                        _check_positive(
                            real_attacker_cap,
                            result=result,
                            file=file,
                            path="projection.loot_limits",
                            field_name="real_attacker_daily_resource_cap",
                        )
            for field_name in ("rare_item_daily_global_cap",):
                value = projection.get(field_name)
                if value is None:
                    continue
                _check_type(
                    value,
                    int,
                    result=result,
                    file=file,
                    path="projection",
                    field_name=field_name,
                )
                _check_positive(
                    value,
                    result=result,
                    file=file,
                    path="projection",
                    field_name=field_name,
                )
            archetype_pacing = projection.get("archetype_pacing")
            if archetype_pacing is not None:
                if not isinstance(archetype_pacing, dict):
                    result.add(file, "projection.archetype_pacing", "expected a mapping")
                else:
                    pacing_fields = {
                        "schema_version",
                        "slot_interval_minutes",
                        "max_parallel_training",
                        "building_targets",
                        "technology_targets",
                        "recruitment_pool_weights",
                    }
                    for archetype, values in archetype_pacing.items():
                        pacing_path = f"projection.archetype_pacing.{archetype}"
                        if archetype not in _COMBAT_PERSONAS:
                            result.add(file, pacing_path, "expected a supported combat archetype")
                        if not isinstance(values, dict):
                            result.add(file, pacing_path, "expected a mapping")
                            continue
                        for field_name in set(values) - pacing_fields:
                            result.add(file, f"{pacing_path}.{field_name}", "unknown field")
                        _validate_int(
                            values.get("schema_version", 1),
                            result=result,
                            file=file,
                            path=f"{pacing_path}.schema_version",
                            minimum=1,
                        )
                        interval = values.get("slot_interval_minutes")
                        _validate_int_range(
                            interval,
                            result=result,
                            file=file,
                            path=f"{pacing_path}.slot_interval_minutes",
                            min_value=10,
                        )
                        if isinstance(interval, list) and len(interval) == 2:
                            if isinstance(interval[1], int) and interval[1] > 15:
                                result.add(file, f"{pacing_path}.slot_interval_minutes[1]", "must be <= 15")
                        for field_name, minimum, maximum in (("max_parallel_training", 0, 8),):
                            _validate_int(
                                values.get(field_name),
                                result=result,
                                file=file,
                                path=f"{pacing_path}.{field_name}",
                                minimum=minimum,
                            )
                            raw_value = values.get(field_name)
                            if isinstance(raw_value, int) and raw_value > maximum:
                                result.add(file, f"{pacing_path}.{field_name}", f"must be <= {maximum}")
                        for field_name in ("building_targets", "technology_targets"):
                            _validate_string_list(
                                values.get(field_name),
                                result=result,
                                file=file,
                                path=pacing_path,
                                field_name=field_name,
                            )
                            supported_targets = {
                                "building_targets": set(VIRTUAL_PLAYER_BUILDING_TARGET_KEYS),
                                "technology_targets": set(VIRTUAL_PLAYER_TECHNOLOGY_TARGET_KEYS),
                            }[field_name]
                            raw_targets = values.get(field_name)
                            if isinstance(raw_targets, list):
                                for target in raw_targets:
                                    if isinstance(target, str) and target not in supported_targets:
                                        result.add(
                                            file,
                                            f"{pacing_path}.{field_name}",
                                            f"unknown target key: {target}",
                                        )
                        pool_weights = values.get("recruitment_pool_weights")
                        if not isinstance(pool_weights, dict):
                            result.add(file, f"{pacing_path}.recruitment_pool_weights", "expected a mapping")
                        else:
                            expected_pools = {"dianshi", "xiangshi", "cunmu"}
                            for pool_key in sorted(expected_pools - set(pool_weights)):
                                result.add(
                                    file,
                                    f"{pacing_path}.recruitment_pool_weights.{pool_key}",
                                    "missing required pool",
                                )
                            for pool_key in sorted(set(pool_weights) - expected_pools):
                                result.add(
                                    file,
                                    f"{pacing_path}.recruitment_pool_weights.{pool_key}",
                                    "expected a supported recruitment pool",
                                )
                            for pool_key, raw_weight in pool_weights.items():
                                _validate_int(
                                    raw_weight,
                                    result=result,
                                    file=file,
                                    path=f"{pacing_path}.recruitment_pool_weights.{pool_key}",
                                    minimum=1,
                                )
                                if isinstance(raw_weight, int) and raw_weight > 100:
                                    result.add(
                                        file,
                                        f"{pacing_path}.recruitment_pool_weights.{pool_key}",
                                        "must be <= 100",
                                    )

    combat_personas = data.get("combat_personas")
    if combat_personas is not None:
        if not isinstance(combat_personas, dict):
            result.add(file, "combat_personas", "expected a mapping")
        else:
            for persona, values in combat_personas.items():
                persona_path = f"combat_personas.{persona}"
                if persona not in _COMBAT_PERSONAS:
                    result.add(file, persona_path, "expected a supported combat persona")
                if not isinstance(values, dict):
                    result.add(file, persona_path, "expected a mapping")
                    continue
                for field_name, value in values.items():
                    field_path = f"{persona_path}.{field_name}"
                    if field_name not in _PERSONA_MULTIPLIERS:
                        result.add(file, field_path, "expected a supported persona multiplier")
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        result.add(file, field_path, "expected a positive number")
                    elif value <= 0:
                        result.add(file, field_path, "must be > 0")

    lifecycle_personas = data.get("lifecycle_personas")
    if lifecycle_personas is not None:
        if not isinstance(lifecycle_personas, dict):
            result.add(file, "lifecycle_personas", "expected a mapping")
        else:
            positive_weight = False
            for persona, values in lifecycle_personas.items():
                persona_path = f"lifecycle_personas.{persona}"
                if persona not in _LIFECYCLE_PERSONAS:
                    result.add(file, persona_path, "expected a supported lifecycle persona")
                if not isinstance(values, dict):
                    result.add(file, persona_path, "expected a mapping")
                    continue
                weight = values.get("weight")
                _validate_int(
                    weight,
                    result=result,
                    file=file,
                    path=f"{persona_path}.weight",
                    minimum=0,
                )
                positive_weight = positive_weight or (
                    isinstance(weight, int) and not isinstance(weight, bool) and weight > 0
                )
                for field_name in ("active_days", "abandoned_days"):
                    _validate_int_range(
                        values.get(field_name),
                        result=result,
                        file=file,
                        path=f"{persona_path}.{field_name}",
                        min_value=0,
                    )
            if not positive_weight:
                result.add(
                    file,
                    "lifecycle_personas",
                    "requires at least one positive lifecycle weight",
                )

    if "bot_development_v2" in data:
        _validate_bot_development_v2(data["bot_development_v2"], result=result, file=file)

    return result
