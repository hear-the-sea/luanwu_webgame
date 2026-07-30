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
    VIRTUAL_PLAYER_INVENTORY_EFFECT_TYPES,
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
_V2_ROOT_FIELDS = frozenset(
    {
        "environment_mode",
        "engine_version",
        "rng_version",
        "plan_schema_version",
        "prestige_segmentation",
        "routing",
        "policy_rollout",
        "reference_snapshot_catalog",
        "policies",
    }
)
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
        "reference_calibration_min_profiles_per_band",
        "reference_calibration_thresholds",
        "reference_calibration_archetype_effects",
        "reference_calibration_abandoned_features",
        "use_local_reference_when_profiles_gte",
        "borrowed_global_reference_discount_ratio",
        "borrowed_global_reference_usage",
        "borrowed_global_may_raise_sample_tier",
        "borrowed_global_may_raise_strength_cap",
        "starter_snapshot_scope",
        "starter_snapshot_requires_live_player_data",
        "zero_local_sample_cap_strategy",
        "anchor_k",
        "strength_safety",
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
_V2_STRENGTH_TIERS = (
    "no_reference",
    "sparse_1_4",
    "limited_5_29",
    "sufficient_30_plus",
)
_V2_STRENGTH_COMMON_FIELDS = frozenset(
    {
        "positive_jitter_bps_max",
        "actions_per_24h_max",
        "growth_bps_per_24h_max",
    }
)
_V2_STRENGTH_FIELDS = frozenset(
    {
        *_V2_STRENGTH_TIERS,
        "arena_acceleration_may_bypass",
        "admin_may_bypass",
    }
)
_V2_GROWTH_FIELDS = frozenset(
    {
        "effective_limit_rule",
        "direct_prestige_grant_by_maintenance_allowed",
        "profiles",
        "last_strength_increase_at_required",
        "arena_acceleration_may_bypass_band_spacing",
        "admin_may_bypass_band_spacing",
        "configured_boundaries_crossed_per_controlled_action_max",
        "cross_band_uses_stricter_source_or_destination_limit",
        "external_domain_result_may_be_rejected_by_bot_growth_policy",
        "bootstrap_fake_per_action_history_records",
    }
)
_V2_GROWTH_PROFILE_FIELDS = frozenset(
    {
        "bootstrap_history_age_days",
        "preferred_strength_check_interval_hours",
        "minimum_positive_strength_action_spacing_hours",
        "composite_growth_bps_per_controlled_action_max",
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
    enabled = _v2_bool(rollout.get("enabled"), result=result, file=file, path=f"{path}.enabled")
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


def _validate_v2_strength_safety(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    strength = _v2_mapping(value, result=result, file=file, path=path)
    if strength is None:
        return
    _reject_unknown_fields(strength, _V2_STRENGTH_FIELDS, result=result, file=file, path=path)
    _require_fields(strength, _V2_STRENGTH_FIELDS, result=result, file=file, path=path)
    expected_tiers: dict[str, dict[str, str | int | float]] = {
        "no_reference": {
            "starter_snapshot_ratio": 0.90,
            "positive_jitter_bps_max": 0,
            "actions_per_24h_max": 0,
            "growth_bps_per_24h_max": 0,
        },
        "sparse_1_4": {
            "cap_quantile": "p50",
            "composite_cap_ratio": 1.05,
            "component_cap_ratio": 1.10,
            "positive_jitter_bps_max": 0,
            "actions_per_24h_max": 1,
            "growth_bps_per_24h_max": 300,
        },
        "limited_5_29": {
            "cap_quantile": "p75",
            "composite_cap_ratio": 1.10,
            "component_cap_ratio": 1.15,
            "positive_jitter_bps_max": 200,
            "actions_per_24h_max": 2,
            "growth_bps_per_24h_max": 500,
        },
        "sufficient_30_plus": {
            "cap_quantile": "p95",
            "composite_cap_ratio": 1.15,
            "component_cap_ratio": 1.20,
            "positive_jitter_bps_max": 500,
            "actions_per_24h_max": 4,
            "growth_bps_per_24h_max": 1000,
        },
    }
    for tier_name, expected in expected_tiers.items():
        tier_path = f"{path}.{tier_name}"
        tier = _v2_mapping(strength.get(tier_name), result=result, file=file, path=tier_path)
        if tier is None:
            continue
        allowed = frozenset(expected)
        _reject_unknown_fields(tier, allowed, result=result, file=file, path=tier_path)
        _require_fields(tier, allowed, result=result, file=file, path=tier_path)
        for field_name, expected_value in expected.items():
            field_path = f"{tier_path}.{field_name}"
            actual = tier.get(field_name)
            if isinstance(expected_value, str):
                _v2_literal(
                    actual,
                    allowed=frozenset({expected_value}),
                    result=result,
                    file=file,
                    path=field_path,
                )
            elif isinstance(expected_value, int):
                _v2_int(
                    actual,
                    result=result,
                    file=file,
                    path=field_path,
                    expected=expected_value,
                )
            else:
                normalized = _v2_number(actual, result=result, file=file, path=field_path)
                if normalized is not None and not math.isclose(normalized, expected_value):
                    result.add(file, field_path, f"must equal {expected_value:g}")
    _v2_bool(
        strength.get("arena_acceleration_may_bypass"),
        result=result,
        file=file,
        path=f"{path}.arena_acceleration_may_bypass",
        expected=False,
    )
    _v2_bool(
        strength.get("admin_may_bypass"),
        result=result,
        file=file,
        path=f"{path}.admin_may_bypass",
        expected=False,
    )


def _validate_v2_growth(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    growth = _v2_mapping(value, result=result, file=file, path=path)
    if growth is None:
        return
    _reject_unknown_fields(growth, _V2_GROWTH_FIELDS, result=result, file=file, path=path)
    _require_fields(growth, _V2_GROWTH_FIELDS, result=result, file=file, path=path)
    _v2_literal(
        growth.get("effective_limit_rule"),
        allowed=frozenset({"strictest_of_sample_tier_band_profile_and_domain_constraints"}),
        result=result,
        file=file,
        path=f"{path}.effective_limit_rule",
    )
    false_fields = (
        "direct_prestige_grant_by_maintenance_allowed",
        "arena_acceleration_may_bypass_band_spacing",
        "admin_may_bypass_band_spacing",
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
    for field_name in (
        "last_strength_increase_at_required",
        "cross_band_uses_stricter_source_or_destination_limit",
    ):
        _v2_bool(
            growth.get(field_name),
            result=result,
            file=file,
            path=f"{path}.{field_name}",
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
    previous_spacing: float | None = None
    previous_action_cap: int | None = None
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
        spacing = _v2_number(
            profile.get("minimum_positive_strength_action_spacing_hours"),
            result=result,
            file=file,
            path=f"{profile_path}.minimum_positive_strength_action_spacing_hours",
        )
        action_cap = _v2_int(
            profile.get("composite_growth_bps_per_controlled_action_max"),
            result=result,
            file=file,
            path=f"{profile_path}.composite_growth_bps_per_controlled_action_max",
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
        if spacing is not None and previous_spacing is not None and spacing < previous_spacing:
            result.add(
                file,
                f"{profile_path}.minimum_positive_strength_action_spacing_hours",
                "must not decrease across prestige bands",
            )
        if action_cap is not None and previous_action_cap is not None and action_cap > previous_action_cap:
            result.add(
                file,
                f"{profile_path}.composite_growth_bps_per_controlled_action_max",
                "must not increase across prestige bands",
            )
        previous_history = history or previous_history
        previous_interval = interval or previous_interval
        previous_spacing = spacing if spacing is not None else previous_spacing
        previous_action_cap = action_cap if action_cap is not None else previous_action_cap


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
        expected=1,
    )
    _v2_int(
        policy.get("reference_calibration_min_profiles_per_band"),
        result=result,
        file=file,
        path=f"{path}.reference_calibration_min_profiles_per_band",
        minimum=30,
        maximum=1000,
    )
    calibration_thresholds = _v2_mapping(
        policy.get("reference_calibration_thresholds"),
        result=result,
        file=file,
        path=f"{path}.reference_calibration_thresholds",
    )
    if calibration_thresholds is not None:
        threshold_path = f"{path}.reference_calibration_thresholds"
        _reject_unknown_fields(
            calibration_thresholds,
            _V2_REFERENCE_CALIBRATION_THRESHOLD_FIELDS,
            result=result,
            file=file,
            path=threshold_path,
        )
        _require_fields(
            calibration_thresholds,
            _V2_REFERENCE_CALIBRATION_THRESHOLD_FIELDS,
            result=result,
            file=file,
            path=threshold_path,
        )
        normalized_thresholds: dict[str, float] = {}
        for field_name, expected in _V2_REFERENCE_CALIBRATION_THRESHOLD_VALUES.items():
            field_path = f"{threshold_path}.{field_name}"
            raw_value = calibration_thresholds.get(field_name)
            if isinstance(expected, bool):
                _v2_bool(
                    raw_value,
                    result=result,
                    file=file,
                    path=field_path,
                    expected=expected,
                )
            elif isinstance(expected, int):
                _v2_int(
                    raw_value,
                    result=result,
                    file=file,
                    path=field_path,
                    expected=expected,
                )
            else:
                normalized = _v2_number(
                    raw_value,
                    result=result,
                    file=file,
                    path=field_path,
                    maximum=1.0,
                )
                if normalized is None:
                    continue
                normalized_thresholds[field_name] = normalized
                if field_name == "archetype_standardized_effect_min_absolute":
                    if normalized < expected:
                        result.add(file, field_path, f"must be >= {expected:g}")
                elif normalized > expected:
                    result.add(file, field_path, f"must be <= {expected:g}")
        minimum_effect = normalized_thresholds.get("archetype_standardized_effect_min_absolute")
        maximum_effect = normalized_thresholds.get("archetype_standardized_effect_max_absolute")
        if minimum_effect is not None and maximum_effect is not None and minimum_effect > maximum_effect:
            result.add(
                file,
                f"{threshold_path}.archetype_standardized_effect_max_absolute",
                "must be >= archetype_standardized_effect_min_absolute",
            )
    archetype_effects = _v2_mapping(
        policy.get("reference_calibration_archetype_effects"),
        result=result,
        file=file,
        path=f"{path}.reference_calibration_archetype_effects",
    )
    if archetype_effects is not None:
        effects_path = f"{path}.reference_calibration_archetype_effects"
        expected_archetypes = frozenset(_V2_REFERENCE_CALIBRATION_ARCHETYPE_EFFECTS)
        _reject_unknown_fields(
            archetype_effects,
            expected_archetypes,
            result=result,
            file=file,
            path=effects_path,
        )
        _require_fields(
            archetype_effects,
            expected_archetypes,
            result=result,
            file=file,
            path=effects_path,
        )
        for archetype, (expected_metric, expected_direction) in _V2_REFERENCE_CALIBRATION_ARCHETYPE_EFFECTS.items():
            effect_path = f"{effects_path}.{archetype}"
            effect = _v2_mapping(
                archetype_effects.get(archetype),
                result=result,
                file=file,
                path=effect_path,
            )
            if effect is None:
                continue
            _reject_unknown_fields(
                effect,
                _V2_REFERENCE_CALIBRATION_ARCHETYPE_EFFECT_FIELDS,
                result=result,
                file=file,
                path=effect_path,
            )
            _require_fields(
                effect,
                _V2_REFERENCE_CALIBRATION_ARCHETYPE_EFFECT_FIELDS,
                result=result,
                file=file,
                path=effect_path,
            )
            if effect.get("metric") != expected_metric:
                result.add(
                    file,
                    f"{effect_path}.metric",
                    f"must equal {expected_metric!r}",
                )
            if effect.get("direction") != expected_direction:
                result.add(
                    file,
                    f"{effect_path}.direction",
                    f"must equal {expected_direction!r}",
                )
    abandoned_features = _v2_mapping(
        policy.get("reference_calibration_abandoned_features"),
        result=result,
        file=file,
        path=f"{path}.reference_calibration_abandoned_features",
    )
    if abandoned_features is not None:
        abandoned_path = f"{path}.reference_calibration_abandoned_features"
        expected_fields = frozenset(_V2_REFERENCE_CALIBRATION_ABANDONED_FEATURES)
        _reject_unknown_fields(
            abandoned_features,
            expected_fields,
            result=result,
            file=file,
            path=abandoned_path,
        )
        _require_fields(
            abandoned_features,
            expected_fields,
            result=result,
            file=file,
            path=abandoned_path,
        )
        _v2_int(
            abandoned_features.get("underfilled_roster_guest_count_max"),
            result=result,
            file=file,
            path=f"{abandoned_path}.underfilled_roster_guest_count_max",
            expected=2,
        )
        stale_ratio = _v2_number(
            abandoned_features.get("stale_gear_level_ratio_max"),
            result=result,
            file=file,
            path=f"{abandoned_path}.stale_gear_level_ratio_max",
            maximum=1.0,
        )
        if stale_ratio is not None and not math.isclose(stale_ratio, 0.50):
            result.add(
                file,
                f"{abandoned_path}.stale_gear_level_ratio_max",
                "must equal 0.5",
            )
        _v2_int(
            abandoned_features.get("growth_gap_days_min"),
            result=result,
            file=file,
            path=f"{abandoned_path}.growth_gap_days_min",
            expected=30,
        )
    _v2_int(
        policy.get("use_local_reference_when_profiles_gte"),
        result=result,
        file=file,
        path=f"{path}.use_local_reference_when_profiles_gte",
        expected=1,
    )
    discount = _v2_number(
        policy.get("borrowed_global_reference_discount_ratio"),
        result=result,
        file=file,
        path=f"{path}.borrowed_global_reference_discount_ratio",
        maximum=1.0,
    )
    if discount is not None and not math.isclose(discount, 0.90):
        result.add(file, f"{path}.borrowed_global_reference_discount_ratio", "must equal 0.9")
    literal_fields = {
        "borrowed_global_reference_usage": "composition_anchor_only",
        "starter_snapshot_scope": "per_prestige_band_conservative_entry_fixture",
        "zero_local_sample_cap_strategy": "stricter_of_starter_90_percent_and_discounted_global",
    }
    for field_name, expected_literal in literal_fields.items():
        _v2_literal(
            policy.get(field_name),
            allowed=frozenset({expected_literal}),
            result=result,
            file=file,
            path=f"{path}.{field_name}",
        )
    for field_name in (
        "borrowed_global_may_raise_sample_tier",
        "borrowed_global_may_raise_strength_cap",
        "starter_snapshot_requires_live_player_data",
    ):
        _v2_bool(
            policy.get(field_name),
            result=result,
            file=file,
            path=f"{path}.{field_name}",
            expected=False,
        )
    _v2_int(
        policy.get("anchor_k"),
        result=result,
        file=file,
        path=f"{path}.anchor_k",
        minimum=1,
    )
    _validate_v2_strength_safety(
        policy.get("strength_safety"),
        result=result,
        file=file,
        path=f"{path}.strength_safety",
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
    _require_fields(config, _V2_ROOT_FIELDS, result=result, file=file, path=path)
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
    _validate_v2_routing(config.get("routing"), result=result, file=file, path=f"{path}.routing")
    target_version = _validate_v2_policy_rollout(
        config.get("policy_rollout"),
        result=result,
        file=file,
        path=f"{path}.policy_rollout",
    )
    _validate_v2_reference_snapshot_catalog(
        config.get("reference_snapshot_catalog"),
        result=result,
        file=file,
        path=f"{path}.reference_snapshot_catalog",
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
                _validate_v2_policy(policy, bands=bands, result=result, file=file, path=policy_path)
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


def _validate_prestige_chance_table(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    if not isinstance(value, list):
        result.add(
            file,
            path,
            "powerful_item_prestige_chance expected a list of prestige chance entries",
        )
        return
    for idx, row in enumerate(value):
        row_path = f"{path}[{idx}]"
        if not isinstance(row, dict):
            result.add(file, row_path, "expected a mapping")
            continue
        min_prestige = row.get("min_prestige")
        if not isinstance(min_prestige, int) or min_prestige < 0:
            result.add(file, f"{row_path}.min_prestige", "expected a non-negative integer")
        _validate_ratio(row.get("chance"), result=result, file=file, path=f"{row_path}.chance")


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
            if "low_stage_powerful_item_chance" in projection:
                _validate_ratio(
                    projection["low_stage_powerful_item_chance"],
                    result=result,
                    file=file,
                    path="projection.low_stage_powerful_item_chance",
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
            if "powerful_item_prestige_chance" in projection:
                _validate_prestige_chance_table(
                    projection["powerful_item_prestige_chance"],
                    result=result,
                    file=file,
                    path="projection.powerful_item_prestige_chance",
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
            for field_name in (
                "rare_item_daily_global_cap",
                "powerful_item_daily_global_cap",
                "powerful_item_min_price",
                "powerful_item_min_growth_stage",
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
                if field_name == "powerful_item_min_growth_stage":
                    if isinstance(value, int) and value < 0:
                        result.add(file, "projection", f"field '{field_name}' must be >= 0")
                else:
                    _check_positive(
                        value,
                        result=result,
                        file=file,
                        path="projection",
                        field_name=field_name,
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
