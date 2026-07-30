from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any

from .random_context import canonical_json_bytes

MIN_PROFILES_PER_COHORT = 30
MAX_PROFILES_PER_COHORT = 1000
D2_PRESTIGE_BANDS = frozenset({"newbie", "junior", "middle", "senior", "veteran", "elite", "legend", "mythic"})

NORMALIZED_WASSERSTEIN_MAX = 0.25
NORMALIZED_QUANTILE_DEVIATION_P10_MAX = 0.35
NORMALIZED_QUANTILE_DEVIATION_P50_MAX = 0.25
NORMALIZED_QUANTILE_DEVIATION_P90_MAX = 0.35
JS_DIVERGENCE_MAX_BITS = 0.10
HARD_CONSTRAINT_VIOLATIONS_MAX = 0
ROBUST_JOINT_OUTLIER_RATE_MAX = 0.15
ROBUST_JOINT_OUTLIER_RATE_ABOVE_REAL_MAX = 0.05
COMPONENT_FINGERPRINT_COLLISION_RATE_MAX = 0.35
JOINT_FINGERPRINT_COLLISION_RATE_MAX = 0.15
FINGERPRINT_COLLISION_RATE_ABOVE_V1_MAX = 0.0
ARCHETYPE_STANDARDIZED_EFFECT_MIN_ABSOLUTE = 0.20
ARCHETYPE_STANDARDIZED_EFFECT_MAX_ABSOLUTE = 0.80
ABANDONED_RATE_DEVIATION_MAX = 0.10
CALIBRATION_THRESHOLD_FIELDS = frozenset(
    {
        "normalized_wasserstein_max",
        "normalized_quantile_deviation_p10_max",
        "normalized_quantile_deviation_p50_max",
        "normalized_quantile_deviation_p90_max",
        "js_divergence_max_bits",
        "hard_constraint_violations_max",
        "robust_joint_outlier_rate_max",
        "robust_joint_outlier_rate_above_real_max",
        "component_fingerprint_collision_rate_max",
        "joint_fingerprint_collision_rate_max",
        "fingerprint_collision_rate_above_v1_max",
        "archetype_standardized_effect_min_absolute",
        "archetype_standardized_effect_max_absolute",
        "archetype_effect_direction_must_match",
        "abandoned_rate_deviation_max",
    }
)


class CalibrationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class CalibrationUnit:
    policy_version: int
    reference_snapshot_version: int
    prestige_band: str


@dataclass(frozen=True, slots=True)
class CalibrationThresholds:
    normalized_wasserstein_max: float
    normalized_quantile_deviation_p10_max: float
    normalized_quantile_deviation_p50_max: float
    normalized_quantile_deviation_p90_max: float
    js_divergence_max_bits: float
    hard_constraint_violations_max: int
    robust_joint_outlier_rate_max: float
    robust_joint_outlier_rate_above_real_max: float
    component_fingerprint_collision_rate_max: float
    joint_fingerprint_collision_rate_max: float
    fingerprint_collision_rate_above_v1_max: float
    archetype_standardized_effect_min_absolute: float
    archetype_standardized_effect_max_absolute: float
    archetype_effect_direction_must_match: bool
    abandoned_rate_deviation_max: float


DEFAULT_CALIBRATION_THRESHOLDS = CalibrationThresholds(
    normalized_wasserstein_max=NORMALIZED_WASSERSTEIN_MAX,
    normalized_quantile_deviation_p10_max=(NORMALIZED_QUANTILE_DEVIATION_P10_MAX),
    normalized_quantile_deviation_p50_max=(NORMALIZED_QUANTILE_DEVIATION_P50_MAX),
    normalized_quantile_deviation_p90_max=(NORMALIZED_QUANTILE_DEVIATION_P90_MAX),
    js_divergence_max_bits=JS_DIVERGENCE_MAX_BITS,
    hard_constraint_violations_max=HARD_CONSTRAINT_VIOLATIONS_MAX,
    robust_joint_outlier_rate_max=ROBUST_JOINT_OUTLIER_RATE_MAX,
    robust_joint_outlier_rate_above_real_max=(ROBUST_JOINT_OUTLIER_RATE_ABOVE_REAL_MAX),
    component_fingerprint_collision_rate_max=(COMPONENT_FINGERPRINT_COLLISION_RATE_MAX),
    joint_fingerprint_collision_rate_max=JOINT_FINGERPRINT_COLLISION_RATE_MAX,
    fingerprint_collision_rate_above_v1_max=(FINGERPRINT_COLLISION_RATE_ABOVE_V1_MAX),
    archetype_standardized_effect_min_absolute=(ARCHETYPE_STANDARDIZED_EFFECT_MIN_ABSOLUTE),
    archetype_standardized_effect_max_absolute=(ARCHETYPE_STANDARDIZED_EFFECT_MAX_ABSOLUTE),
    archetype_effect_direction_must_match=True,
    abandoned_rate_deviation_max=ABANDONED_RATE_DEVIATION_MAX,
)


def calibration_thresholds_from_mapping(
    value: Mapping[str, Any],
) -> CalibrationThresholds:
    if not isinstance(value, Mapping):
        raise TypeError("reference calibration thresholds must be a mapping")
    fields = set(value)
    if fields != CALIBRATION_THRESHOLD_FIELDS:
        missing = sorted(CALIBRATION_THRESHOLD_FIELDS - fields)
        unknown = sorted(fields - CALIBRATION_THRESHOLD_FIELDS)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise ValueError("reference calibration thresholds have " + "; ".join(details))

    normalized: dict[str, float] = {}
    for field_name in CALIBRATION_THRESHOLD_FIELDS - {
        "hard_constraint_violations_max",
        "archetype_effect_direction_must_match",
    }:
        raw = value[field_name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise TypeError(f"reference calibration threshold {field_name} must be numeric")
        number = float(raw)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"reference calibration threshold {field_name} must be finite and non-negative")
        normalized[field_name] = number

    hard_max = value["hard_constraint_violations_max"]
    if isinstance(hard_max, bool) or not isinstance(hard_max, int) or hard_max < 0:
        raise ValueError(
            "reference calibration threshold hard_constraint_violations_max " "must be a non-negative integer"
        )
    direction_required = value["archetype_effect_direction_must_match"]
    if not isinstance(direction_required, bool):
        raise TypeError("reference calibration threshold archetype_effect_direction_must_match " "must be a boolean")
    if (
        normalized["archetype_standardized_effect_min_absolute"]
        > normalized["archetype_standardized_effect_max_absolute"]
    ):
        raise ValueError("reference calibration archetype effect minimum must not exceed maximum")
    return CalibrationThresholds(
        normalized_wasserstein_max=normalized["normalized_wasserstein_max"],
        normalized_quantile_deviation_p10_max=normalized["normalized_quantile_deviation_p10_max"],
        normalized_quantile_deviation_p50_max=normalized["normalized_quantile_deviation_p50_max"],
        normalized_quantile_deviation_p90_max=normalized["normalized_quantile_deviation_p90_max"],
        js_divergence_max_bits=normalized["js_divergence_max_bits"],
        hard_constraint_violations_max=hard_max,
        robust_joint_outlier_rate_max=normalized["robust_joint_outlier_rate_max"],
        robust_joint_outlier_rate_above_real_max=normalized["robust_joint_outlier_rate_above_real_max"],
        component_fingerprint_collision_rate_max=normalized["component_fingerprint_collision_rate_max"],
        joint_fingerprint_collision_rate_max=normalized["joint_fingerprint_collision_rate_max"],
        fingerprint_collision_rate_above_v1_max=normalized["fingerprint_collision_rate_above_v1_max"],
        archetype_standardized_effect_min_absolute=normalized["archetype_standardized_effect_min_absolute"],
        archetype_standardized_effect_max_absolute=normalized["archetype_standardized_effect_max_absolute"],
        archetype_effect_direction_must_match=direction_required,
        abandoned_rate_deviation_max=normalized["abandoned_rate_deviation_max"],
    )


@dataclass(frozen=True, slots=True)
class QuantileDeviations:
    p10: float
    p50: float
    p90: float


@dataclass(frozen=True, slots=True)
class DistributionEvidence:
    unit: CalibrationUnit
    reference_snapshot_digest: str | None = None
    reference_profile_count: int | None = None
    candidate_profile_count: int | None = None
    unclassified_profile_count: int | None = None
    normalized_wasserstein: float | None = None
    normalized_quantile_deviation_p10: float | None = None
    normalized_quantile_deviation_p50: float | None = None
    normalized_quantile_deviation_p90: float | None = None
    js_divergence_bits: float | None = None
    hard_constraint_violations: int | None = None
    robust_joint_outlier_rate: float | None = None
    robust_joint_outlier_rate_above_real_cohort: float | None = None
    component_fingerprint_collision_rate_max: float | None = None
    joint_fingerprint_collision_rate: float | None = None
    fingerprint_collision_rate_above_v1_max: float | None = None
    archetype_standardized_effect_min_absolute: float | None = None
    archetype_standardized_effect_max_absolute: float | None = None
    archetype_effect_direction_matches: bool | None = None
    abandoned_rate_deviation_from_inactive_real: float | None = None


@dataclass(frozen=True, slots=True)
class CalibrationVerdict:
    unit: CalibrationUnit
    status: CalibrationStatus
    reason_codes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status is CalibrationStatus.PASSED


def canonical_snapshot_digest(payload: Any) -> str:
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _finite_values(values: Sequence[int | float], *, field: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} must be a numeric sequence")

    normalized: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field}[{index}] must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field}[{index}] must be finite")
        normalized.append(number)
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return tuple(normalized)


def nearest_rank_quantile(values: Sequence[int | float], quantile: float) -> float:
    normalized = _finite_values(values, field="values")
    if isinstance(quantile, bool) or not isinstance(quantile, (int, float)):
        raise TypeError("quantile must be numeric")
    percentile = float(quantile)
    if not math.isfinite(percentile) or not 0.0 <= percentile <= 1.0:
        raise ValueError("quantile must be finite and between 0 and 1")

    ordered = sorted(normalized)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _reference_iqr_floor(reference_values: Sequence[int | float]) -> float:
    lower = nearest_rank_quantile(reference_values, 0.25)
    upper = nearest_rank_quantile(reference_values, 0.75)
    return max(1.0, upper - lower)


def empirical_wasserstein_distance(
    reference_values: Sequence[int | float],
    candidate_values: Sequence[int | float],
) -> float:
    reference = sorted(_finite_values(reference_values, field="reference_values"))
    candidate = sorted(_finite_values(candidate_values, field="candidate_values"))
    support = sorted(set(reference).union(candidate))

    reference_index = 0
    candidate_index = 0
    reference_cdf = 0.0
    candidate_cdf = 0.0
    previous = support[0]
    distance = 0.0

    for point in support:
        distance += abs(reference_cdf - candidate_cdf) * (point - previous)
        while reference_index < len(reference) and reference[reference_index] <= point:
            reference_index += 1
        while candidate_index < len(candidate) and candidate[candidate_index] <= point:
            candidate_index += 1
        reference_cdf = reference_index / len(reference)
        candidate_cdf = candidate_index / len(candidate)
        previous = point

    return distance


def normalized_wasserstein_distance(
    reference_values: Sequence[int | float],
    candidate_values: Sequence[int | float],
) -> float:
    return empirical_wasserstein_distance(reference_values, candidate_values) / _reference_iqr_floor(reference_values)


def normalized_quantile_deviations(
    reference_values: Sequence[int | float],
    candidate_values: Sequence[int | float],
) -> QuantileDeviations:
    reference = _finite_values(reference_values, field="reference_values")
    candidate = _finite_values(candidate_values, field="candidate_values")
    denominator = _reference_iqr_floor(reference)

    def deviation(quantile: float) -> float:
        return (
            abs(nearest_rank_quantile(candidate, quantile) - nearest_rank_quantile(reference, quantile)) / denominator
        )

    return QuantileDeviations(
        p10=deviation(0.10),
        p50=deviation(0.50),
        p90=deviation(0.90),
    )


def _normalized_distribution(values: Mapping[str, int | float], *, field: str) -> dict[str, float]:
    if not values:
        raise ValueError(f"{field} must not be empty")

    normalized: dict[str, float] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise TypeError(f"{field} keys must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field}[{key!r}] must be numeric")
        weight = float(value)
        if not math.isfinite(weight):
            raise ValueError(f"{field}[{key!r}] must be finite")
        if weight < 0:
            raise ValueError(f"{field}[{key!r}] must be non-negative")
        normalized[key] = weight

    total = sum(normalized.values())
    if total <= 0:
        raise ValueError(f"{field} must contain positive weight")
    return {key: weight / total for key, weight in normalized.items()}


def jensen_shannon_divergence_bits(
    reference_distribution: Mapping[str, int | float],
    candidate_distribution: Mapping[str, int | float],
) -> float:
    reference = _normalized_distribution(reference_distribution, field="reference_distribution")
    candidate = _normalized_distribution(candidate_distribution, field="candidate_distribution")
    categories = set(reference).union(candidate)

    divergence = 0.0
    for category in sorted(categories):
        reference_probability = reference.get(category, 0.0)
        candidate_probability = candidate.get(category, 0.0)
        midpoint = (reference_probability + candidate_probability) / 2.0
        if reference_probability > 0:
            divergence += 0.5 * reference_probability * math.log2(reference_probability / midpoint)
        if candidate_probability > 0:
            divergence += 0.5 * candidate_probability * math.log2(candidate_probability / midpoint)
    return min(1.0, max(0.0, divergence))


def _valid_unit(unit: CalibrationUnit) -> bool:
    return (
        isinstance(unit.policy_version, int)
        and not isinstance(unit.policy_version, bool)
        and unit.policy_version > 0
        and isinstance(unit.reference_snapshot_version, int)
        and not isinstance(unit.reference_snapshot_version, bool)
        and unit.reference_snapshot_version > 0
        and isinstance(unit.prestige_band, str)
        and unit.prestige_band in D2_PRESTIGE_BANDS
    )


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def evaluate_calibration_evidence(
    expected_unit: CalibrationUnit,
    expected_reference_snapshot_digest: str,
    evidence: DistributionEvidence,
    *,
    minimum_profiles_per_cohort: int = MIN_PROFILES_PER_COHORT,
    thresholds: CalibrationThresholds = DEFAULT_CALIBRATION_THRESHOLDS,
) -> CalibrationVerdict:
    if isinstance(minimum_profiles_per_cohort, bool) or not isinstance(minimum_profiles_per_cohort, int):
        raise TypeError("minimum_profiles_per_cohort must be an integer")
    if not MIN_PROFILES_PER_COHORT <= minimum_profiles_per_cohort <= MAX_PROFILES_PER_COHORT:
        raise ValueError(
            "minimum_profiles_per_cohort must be between " f"{MIN_PROFILES_PER_COHORT} and {MAX_PROFILES_PER_COHORT}"
        )
    if not isinstance(thresholds, CalibrationThresholds):
        raise TypeError("thresholds must be CalibrationThresholds")
    incomplete: list[str] = []
    failed: list[str] = []

    if not _valid_unit(expected_unit):
        incomplete.append("invalid_expected_unit")
    if not _valid_unit(evidence.unit):
        incomplete.append("invalid_evidence_unit")
    if evidence.unit != expected_unit:
        failed.append("identity_mismatch")

    if not _valid_digest(expected_reference_snapshot_digest):
        incomplete.append("invalid_expected_reference_snapshot_digest")
    if not _valid_digest(evidence.reference_snapshot_digest):
        incomplete.append("invalid_evidence_reference_snapshot_digest")
    elif evidence.reference_snapshot_digest != expected_reference_snapshot_digest:
        failed.append("reference_snapshot_digest_mismatch")

    count_fields = (
        ("reference_profile_count", evidence.reference_profile_count),
        ("candidate_profile_count", evidence.candidate_profile_count),
    )
    for field_name, count_value in count_fields:
        if count_value is None:
            incomplete.append(f"missing_metric:{field_name}")
        elif isinstance(count_value, bool) or not isinstance(count_value, int) or count_value < 0:
            incomplete.append(f"invalid_metric:{field_name}")
        else:
            if count_value < minimum_profiles_per_cohort:
                failed.append(f"sample_below_minimum:{field_name}")
            if count_value > MAX_PROFILES_PER_COHORT:
                failed.append(f"sample_above_maximum:{field_name}")

    unclassified = evidence.unclassified_profile_count
    if unclassified is None:
        incomplete.append("missing_metric:unclassified_profile_count")
    elif isinstance(unclassified, bool) or not isinstance(unclassified, int) or unclassified < 0:
        incomplete.append("invalid_metric:unclassified_profile_count")
    elif unclassified > 0:
        failed.append("unclassified_profiles_present")

    scalar_fields = {
        "normalized_wasserstein": evidence.normalized_wasserstein,
        "normalized_quantile_deviation_p10": evidence.normalized_quantile_deviation_p10,
        "normalized_quantile_deviation_p50": evidence.normalized_quantile_deviation_p50,
        "normalized_quantile_deviation_p90": evidence.normalized_quantile_deviation_p90,
        "js_divergence_bits": evidence.js_divergence_bits,
        "robust_joint_outlier_rate": evidence.robust_joint_outlier_rate,
        "robust_joint_outlier_rate_above_real_cohort": evidence.robust_joint_outlier_rate_above_real_cohort,
        "component_fingerprint_collision_rate_max": evidence.component_fingerprint_collision_rate_max,
        "joint_fingerprint_collision_rate": evidence.joint_fingerprint_collision_rate,
        "fingerprint_collision_rate_above_v1_max": evidence.fingerprint_collision_rate_above_v1_max,
        "archetype_standardized_effect_min_absolute": evidence.archetype_standardized_effect_min_absolute,
        "archetype_standardized_effect_max_absolute": evidence.archetype_standardized_effect_max_absolute,
        "abandoned_rate_deviation_from_inactive_real": evidence.abandoned_rate_deviation_from_inactive_real,
    }
    normalized_scalars: dict[str, float] = {}
    for field_name, scalar_value in scalar_fields.items():
        if scalar_value is None:
            incomplete.append(f"missing_metric:{field_name}")
            continue
        if (
            isinstance(scalar_value, bool)
            or not isinstance(scalar_value, (int, float))
            or not math.isfinite(float(scalar_value))
        ):
            incomplete.append(f"invalid_metric:{field_name}")
            continue
        normalized_scalars[field_name] = float(scalar_value)

    non_negative_fields = (
        "normalized_wasserstein",
        "normalized_quantile_deviation_p10",
        "normalized_quantile_deviation_p50",
        "normalized_quantile_deviation_p90",
        "js_divergence_bits",
        "robust_joint_outlier_rate",
        "component_fingerprint_collision_rate_max",
        "joint_fingerprint_collision_rate",
        "archetype_standardized_effect_min_absolute",
        "archetype_standardized_effect_max_absolute",
        "abandoned_rate_deviation_from_inactive_real",
    )
    for field_name in non_negative_fields:
        metric_value = normalized_scalars.get(field_name)
        if metric_value is not None and metric_value < 0:
            incomplete.append(f"invalid_metric:{field_name}")

    unit_interval_fields = (
        "js_divergence_bits",
        "robust_joint_outlier_rate",
        "component_fingerprint_collision_rate_max",
        "joint_fingerprint_collision_rate",
        "abandoned_rate_deviation_from_inactive_real",
    )
    for field_name in unit_interval_fields:
        metric_value = normalized_scalars.get(field_name)
        if metric_value is not None and metric_value > 1:
            incomplete.append(f"invalid_metric:{field_name}")

    for field_name in (
        "robust_joint_outlier_rate_above_real_cohort",
        "fingerprint_collision_rate_above_v1_max",
    ):
        metric_value = normalized_scalars.get(field_name)
        if metric_value is not None and not -1 <= metric_value <= 1:
            incomplete.append(f"invalid_metric:{field_name}")

    minimum_effect = normalized_scalars.get("archetype_standardized_effect_min_absolute")
    maximum_effect = normalized_scalars.get("archetype_standardized_effect_max_absolute")
    if minimum_effect is not None and maximum_effect is not None and minimum_effect > maximum_effect:
        incomplete.append("invalid_metric:archetype_standardized_effect_range")

    hard_violations = evidence.hard_constraint_violations
    if hard_violations is None:
        incomplete.append("missing_metric:hard_constraint_violations")
    elif isinstance(hard_violations, bool) or not isinstance(hard_violations, int) or hard_violations < 0:
        incomplete.append("invalid_metric:hard_constraint_violations")
    elif hard_violations > thresholds.hard_constraint_violations_max:
        failed.append("threshold_exceeded:hard_constraint_violations")

    direction_matches = evidence.archetype_effect_direction_matches
    if direction_matches is None:
        incomplete.append("missing_metric:archetype_effect_direction_matches")
    elif not isinstance(direction_matches, bool):
        incomplete.append("invalid_metric:archetype_effect_direction_matches")
    elif thresholds.archetype_effect_direction_must_match and not direction_matches:
        failed.append("threshold_exceeded:archetype_effect_direction")

    maximum_thresholds = {
        "normalized_wasserstein": thresholds.normalized_wasserstein_max,
        "normalized_quantile_deviation_p10": (thresholds.normalized_quantile_deviation_p10_max),
        "normalized_quantile_deviation_p50": (thresholds.normalized_quantile_deviation_p50_max),
        "normalized_quantile_deviation_p90": (thresholds.normalized_quantile_deviation_p90_max),
        "js_divergence_bits": thresholds.js_divergence_max_bits,
        "robust_joint_outlier_rate": thresholds.robust_joint_outlier_rate_max,
        "robust_joint_outlier_rate_above_real_cohort": (thresholds.robust_joint_outlier_rate_above_real_max),
        "component_fingerprint_collision_rate_max": (thresholds.component_fingerprint_collision_rate_max),
        "joint_fingerprint_collision_rate": (thresholds.joint_fingerprint_collision_rate_max),
        "fingerprint_collision_rate_above_v1_max": (thresholds.fingerprint_collision_rate_above_v1_max),
        "archetype_standardized_effect_max_absolute": (thresholds.archetype_standardized_effect_max_absolute),
        "abandoned_rate_deviation_from_inactive_real": (thresholds.abandoned_rate_deviation_max),
    }
    invalid_metrics = {
        reason.partition(":")[2] for reason in incomplete if reason.startswith(("missing_metric:", "invalid_metric:"))
    }
    for field_name, maximum in maximum_thresholds.items():
        metric_value = normalized_scalars.get(field_name)
        if metric_value is not None and field_name not in invalid_metrics and metric_value > maximum:
            failed.append(f"threshold_exceeded:{field_name}")

    if (
        minimum_effect is not None
        and "archetype_standardized_effect_min_absolute" not in invalid_metrics
        and minimum_effect < thresholds.archetype_standardized_effect_min_absolute
    ):
        failed.append("threshold_not_met:archetype_standardized_effect_min_absolute")

    reason_codes = tuple(dict.fromkeys((*incomplete, *failed)))
    if incomplete:
        status = CalibrationStatus.INCOMPLETE
    elif failed:
        status = CalibrationStatus.FAILED
    else:
        status = CalibrationStatus.PASSED
    return CalibrationVerdict(unit=expected_unit, status=status, reason_codes=reason_codes)


__all__ = [
    "CALIBRATION_THRESHOLD_FIELDS",
    "DEFAULT_CALIBRATION_THRESHOLDS",
    "CalibrationThresholds",
    "CalibrationStatus",
    "CalibrationUnit",
    "CalibrationVerdict",
    "DistributionEvidence",
    "QuantileDeviations",
    "calibration_thresholds_from_mapping",
    "canonical_snapshot_digest",
    "empirical_wasserstein_distance",
    "evaluate_calibration_evidence",
    "jensen_shannon_divergence_bits",
    "nearest_rank_quantile",
    "normalized_quantile_deviations",
    "normalized_wasserstein_distance",
]
