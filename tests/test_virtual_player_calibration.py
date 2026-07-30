from __future__ import annotations

import math
from dataclasses import FrozenInstanceError, replace

import pytest

from gameplay.services.virtual_player_core.calibration import (
    CalibrationStatus,
    CalibrationUnit,
    DistributionEvidence,
    canonical_snapshot_digest,
    empirical_wasserstein_distance,
    evaluate_calibration_evidence,
    jensen_shannon_divergence_bits,
    nearest_rank_quantile,
    normalized_quantile_deviations,
    normalized_wasserstein_distance,
)

UNIT = CalibrationUnit(policy_version=7, reference_snapshot_version=3, prestige_band="junior")
SNAPSHOT_PAYLOAD = {
    "policy_version": 7,
    "prestige_band": "junior",
    "profiles": [{"guest_count": 4, "prestige": 900}],
    "reference_snapshot_version": 3,
}
SNAPSHOT_DIGEST = canonical_snapshot_digest(SNAPSHOT_PAYLOAD)


def _complete_evidence(**overrides: object) -> DistributionEvidence:
    values: dict[str, object] = {
        "unit": UNIT,
        "reference_snapshot_digest": SNAPSHOT_DIGEST,
        "reference_profile_count": 30,
        "candidate_profile_count": 30,
        "unclassified_profile_count": 0,
        "normalized_wasserstein": 0.25,
        "normalized_quantile_deviation_p10": 0.35,
        "normalized_quantile_deviation_p50": 0.25,
        "normalized_quantile_deviation_p90": 0.35,
        "js_divergence_bits": 0.10,
        "hard_constraint_violations": 0,
        "robust_joint_outlier_rate": 0.15,
        "robust_joint_outlier_rate_above_real_cohort": 0.05,
        "component_fingerprint_collision_rate_max": 0.35,
        "joint_fingerprint_collision_rate": 0.15,
        "fingerprint_collision_rate_above_v1_max": 0.0,
        "archetype_standardized_effect_min_absolute": 0.20,
        "archetype_standardized_effect_max_absolute": 0.80,
        "archetype_effect_direction_matches": True,
        "abandoned_rate_deviation_from_inactive_real": 0.10,
    }
    values.update(overrides)
    return DistributionEvidence(**values)  # type: ignore[arg-type]


def _evaluate(evidence: DistributionEvidence):
    return evaluate_calibration_evidence(UNIT, SNAPSHOT_DIGEST, evidence)


def test_canonical_snapshot_digest_is_order_independent_and_rejects_non_finite_values() -> None:
    reordered = {
        "reference_snapshot_version": 3,
        "profiles": [{"prestige": 900, "guest_count": 4}],
        "prestige_band": "junior",
        "policy_version": 7,
    }

    assert canonical_snapshot_digest(reordered) == SNAPSHOT_DIGEST
    assert len(SNAPSHOT_DIGEST) == 64
    with pytest.raises(ValueError):
        canonical_snapshot_digest({"invalid": math.inf})


def test_nearest_rank_quantile_uses_the_frozen_small_sample_semantics() -> None:
    values = [9, 1, 5, 3]

    assert nearest_rank_quantile(values, 0.25) == 1
    assert nearest_rank_quantile(values, 0.50) == 3
    assert nearest_rank_quantile(values, 0.75) == 5


@pytest.mark.parametrize("values", [[], [1, math.nan], [1, math.inf]])
def test_nearest_rank_quantile_rejects_empty_or_non_finite_samples(
    values: list[float],
) -> None:
    with pytest.raises(ValueError):
        nearest_rank_quantile(values, 0.50)


def test_empirical_wasserstein_supports_unequal_sample_sizes_and_real_iqr_normalization() -> None:
    assert empirical_wasserstein_distance([0, 2], [1]) == pytest.approx(1.0)
    assert normalized_wasserstein_distance([0, 2], [1]) == pytest.approx(0.5)
    assert normalized_wasserstein_distance([0, 2, 4, 6], [1, 3, 5, 7]) == pytest.approx(0.25)


def test_normalized_wasserstein_uses_a_one_unit_floor_for_zero_real_iqr() -> None:
    assert normalized_wasserstein_distance([5, 5, 5], [6, 6, 6]) == pytest.approx(1.0)


def test_normalized_quantile_deviations_use_p10_p50_p90_and_real_iqr() -> None:
    reference = list(range(0, 100, 10))
    candidate = list(range(10, 110, 10))

    deviations = normalized_quantile_deviations(reference, candidate)

    assert deviations.p10 == pytest.approx(0.20)
    assert deviations.p50 == pytest.approx(0.20)
    assert deviations.p90 == pytest.approx(0.20)


def test_jensen_shannon_divergence_is_base_two_and_uses_the_union_of_categories() -> None:
    assert jensen_shannon_divergence_bits({"a": 1, "b": 1}, {"b": 1, "a": 1}) == pytest.approx(0.0)
    assert jensen_shannon_divergence_bits({"a": 1}, {"b": 1}) == pytest.approx(1.0)
    assert jensen_shannon_divergence_bits({"a": 1, "b": 0}, {"a": 1}) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "distribution",
    [
        {},
        {"a": -1},
        {"a": math.nan},
        {"a": math.inf},
        {"a": 0},
    ],
)
def test_jensen_shannon_divergence_rejects_invalid_distributions(
    distribution: dict[str, float],
) -> None:
    with pytest.raises(ValueError):
        jensen_shannon_divergence_bits(distribution, {"valid": 1})


def test_complete_evidence_passes_at_every_inclusive_threshold_and_is_immutable() -> None:
    evidence = _complete_evidence()

    verdict = _evaluate(evidence)

    assert verdict.status is CalibrationStatus.PASSED
    assert verdict.passed is True
    assert verdict.reason_codes == ()
    with pytest.raises(FrozenInstanceError):
        evidence.reference_profile_count = 31  # type: ignore[misc]


@pytest.mark.parametrize("field_name", ["reference_profile_count", "candidate_profile_count"])
def test_each_cohort_requires_thirty_profiles(field_name: str) -> None:
    failed = _evaluate(_complete_evidence(**{field_name: 29}))
    passed = _evaluate(_complete_evidence(**{field_name: 30}))

    assert failed.status is CalibrationStatus.FAILED
    assert failed.passed is False
    assert f"sample_below_minimum:{field_name}" in failed.reason_codes
    assert passed.status is CalibrationStatus.PASSED


def test_calibration_evaluator_accepts_a_policy_specific_sample_minimum() -> None:
    failed = evaluate_calibration_evidence(
        UNIT,
        SNAPSHOT_DIGEST,
        _complete_evidence(),
        minimum_profiles_per_cohort=31,
    )
    passed = evaluate_calibration_evidence(
        UNIT,
        SNAPSHOT_DIGEST,
        _complete_evidence(
            reference_profile_count=31,
            candidate_profile_count=31,
        ),
        minimum_profiles_per_cohort=31,
    )

    assert failed.status is CalibrationStatus.FAILED
    assert "sample_below_minimum:reference_profile_count" in failed.reason_codes
    assert "sample_below_minimum:candidate_profile_count" in failed.reason_codes
    assert passed.status is CalibrationStatus.PASSED


@pytest.mark.parametrize("field_name", ["reference_profile_count", "candidate_profile_count"])
def test_each_cohort_is_capped_at_one_thousand_profiles(field_name: str) -> None:
    assert _evaluate(_complete_evidence(**{field_name: 1000})).status is CalibrationStatus.PASSED

    failed = _evaluate(_complete_evidence(**{field_name: 1001}))

    assert failed.status is CalibrationStatus.FAILED
    assert f"sample_above_maximum:{field_name}" in failed.reason_codes


def test_unclassified_profiles_and_unclassified_units_cannot_pass() -> None:
    profiles_present = _evaluate(_complete_evidence(unclassified_profile_count=1))
    unclassified_unit = CalibrationUnit(
        policy_version=UNIT.policy_version,
        reference_snapshot_version=UNIT.reference_snapshot_version,
        prestige_band="unclassified",
    )
    invalid_unit = evaluate_calibration_evidence(
        unclassified_unit,
        SNAPSHOT_DIGEST,
        replace(_complete_evidence(), unit=unclassified_unit),
    )

    assert profiles_present.status is CalibrationStatus.FAILED
    assert "unclassified_profiles_present" in profiles_present.reason_codes
    assert invalid_unit.status is CalibrationStatus.INCOMPLETE
    assert invalid_unit.passed is False


@pytest.mark.parametrize(
    "other_unit",
    [
        CalibrationUnit(policy_version=8, reference_snapshot_version=3, prestige_band="junior"),
        CalibrationUnit(policy_version=7, reference_snapshot_version=4, prestige_band="junior"),
        CalibrationUnit(policy_version=7, reference_snapshot_version=3, prestige_band="middle"),
    ],
)
def test_each_calibration_unit_identity_mismatch_cannot_pass(
    other_unit: CalibrationUnit,
) -> None:
    unit_mismatch = evaluate_calibration_evidence(other_unit, SNAPSHOT_DIGEST, _complete_evidence())

    assert unit_mismatch.status is CalibrationStatus.FAILED
    assert "identity_mismatch" in unit_mismatch.reason_codes


def test_snapshot_digest_identity_mismatch_cannot_pass() -> None:
    digest_mismatch = _evaluate(
        _complete_evidence(reference_snapshot_digest=canonical_snapshot_digest({"different": True}))
    )

    assert digest_mismatch.status is CalibrationStatus.FAILED
    assert "reference_snapshot_digest_mismatch" in digest_mismatch.reason_codes


@pytest.mark.parametrize(
    "field_name",
    [
        "reference_snapshot_digest",
        "reference_profile_count",
        "candidate_profile_count",
        "unclassified_profile_count",
        "normalized_wasserstein",
        "normalized_quantile_deviation_p10",
        "normalized_quantile_deviation_p50",
        "normalized_quantile_deviation_p90",
        "js_divergence_bits",
        "hard_constraint_violations",
        "robust_joint_outlier_rate",
        "robust_joint_outlier_rate_above_real_cohort",
        "component_fingerprint_collision_rate_max",
        "joint_fingerprint_collision_rate",
        "fingerprint_collision_rate_above_v1_max",
        "archetype_standardized_effect_min_absolute",
        "archetype_standardized_effect_max_absolute",
        "archetype_effect_direction_matches",
        "abandoned_rate_deviation_from_inactive_real",
    ],
)
def test_missing_evidence_is_incomplete_and_never_passes(field_name: str) -> None:
    verdict = _evaluate(_complete_evidence(**{field_name: None}))

    assert verdict.status is CalibrationStatus.INCOMPLETE
    assert verdict.passed is False


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_non_finite_evidence_is_incomplete_and_never_passes(non_finite: float) -> None:
    verdict = _evaluate(_complete_evidence(normalized_wasserstein=non_finite))

    assert verdict.status is CalibrationStatus.INCOMPLETE
    assert "invalid_metric:normalized_wasserstein" in verdict.reason_codes


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("normalized_wasserstein", 0.250001),
        ("normalized_quantile_deviation_p10", 0.350001),
        ("normalized_quantile_deviation_p50", 0.250001),
        ("normalized_quantile_deviation_p90", 0.350001),
        ("js_divergence_bits", 0.100001),
        ("hard_constraint_violations", 1),
        ("robust_joint_outlier_rate", 0.150001),
        ("robust_joint_outlier_rate_above_real_cohort", 0.050001),
        ("component_fingerprint_collision_rate_max", 0.350001),
        ("joint_fingerprint_collision_rate", 0.150001),
        ("fingerprint_collision_rate_above_v1_max", 0.000001),
        ("archetype_standardized_effect_min_absolute", 0.199999),
        ("archetype_standardized_effect_max_absolute", 0.800001),
        ("archetype_effect_direction_matches", False),
        ("abandoned_rate_deviation_from_inactive_real", 0.100001),
    ],
)
def test_each_frozen_distribution_threshold_fails_closed(field_name: str, value: object) -> None:
    verdict = _evaluate(_complete_evidence(**{field_name: value}))

    assert verdict.status is CalibrationStatus.FAILED
    assert verdict.passed is False


def test_invalid_scalar_ranges_are_incomplete_instead_of_being_treated_as_measurements() -> None:
    negative_rate = _evaluate(_complete_evidence(js_divergence_bits=-0.01))
    inverted_effect_range = _evaluate(
        _complete_evidence(
            archetype_standardized_effect_min_absolute=0.7,
            archetype_standardized_effect_max_absolute=0.3,
        )
    )

    assert negative_rate.status is CalibrationStatus.INCOMPLETE
    assert "invalid_metric:js_divergence_bits" in negative_rate.reason_codes
    assert inverted_effect_range.status is CalibrationStatus.INCOMPLETE
    assert "invalid_metric:archetype_standardized_effect_range" in inverted_effect_range.reason_codes
