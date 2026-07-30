from __future__ import annotations

from types import MappingProxyType

from gameplay.services.virtual_player_core.gate_d2_candidate_artifact import GateD2RawProfile
from gameplay.services.virtual_player_core.gate_d2_metrics import (
    _JOINT_FEATURE_FIELDS,
    _DerivedProfile,
    _fit_robust_joint_outlier_model,
    _joint_outlier_rate,
    _robust_joint_outlier_rates,
    _split_reference_fit_holdout,
)


def _derived_profile(
    index: int,
    *,
    candidate: bool = False,
    reverse_building_relationship: bool = False,
) -> _DerivedProfile:
    guest_level = float(index)
    building_level = float(29 - index if reverse_building_relationship else index)
    continuous = {field: 0.0 for field in _JOINT_FEATURE_FIELDS}
    continuous.update(
        {
            "mean_guest_level": guest_level,
            "mean_building_level": building_level,
            "core_building_level": building_level,
        }
    )
    prefix = "candidate-v1" if candidate else "human-ref-v3"
    raw = GateD2RawProfile(
        business_key=f"{prefix}:{index:064x}",
        prestige=0,
        account_age_days=0,
        days_since_last_strength_increase=0,
        buildings=(),
        guests=(),
        guards=(),
        troops=(),
        resources=(),
    )
    return _DerivedProfile(
        raw=raw,
        continuous=MappingProxyType(continuous),
        snapshot_values=MappingProxyType({}),
        categories=MappingProxyType({}),
        component_fingerprints=MappingProxyType({}),
        joint_fingerprint="",
        hard_constraint_violations=0,
        effect_metrics=MappingProxyType({}),
    )


def _reference_profiles() -> tuple[_DerivedProfile, ...]:
    return tuple(_derived_profile(index) for index in range(30))


def test_joint_outlier_reference_baseline_uses_a_disjoint_holdout() -> None:
    reference = _reference_profiles()

    fit, holdout = _split_reference_fit_holdout(reference)
    fit_keys = {profile.raw.business_key for profile in fit}
    holdout_keys = {profile.raw.business_key for profile in holdout}

    assert fit_keys.isdisjoint(holdout_keys)
    assert fit_keys | holdout_keys == {profile.raw.business_key for profile in reference}
    assert len(fit) == 24
    assert len(holdout) == 6

    reordered_fit, reordered_holdout = _split_reference_fit_holdout(tuple(reversed(reference)))
    assert {profile.raw.business_key for profile in reordered_fit} == fit_keys
    assert {profile.raw.business_key for profile in reordered_holdout} == holdout_keys

    model = _fit_robust_joint_outlier_model(fit)
    assert len(model.fit_vectors) == len(fit)
    assert _joint_outlier_rate(model, holdout) == 0.0


def test_joint_outlier_rejects_impossible_combinations_with_normal_marginals() -> None:
    reference = _reference_profiles()
    candidates = tuple(
        _derived_profile(
            index,
            candidate=True,
            reverse_building_relationship=True,
        )
        for index in range(30)
    )

    for field in _JOINT_FEATURE_FIELDS:
        assert sorted(profile.continuous[field] for profile in candidates) == sorted(
            profile.continuous[field] for profile in reference
        )

    candidate_rate, real_holdout_rate = _robust_joint_outlier_rates(
        reference,
        candidates,
    )

    assert real_holdout_rate == 0.0
    assert candidate_rate >= 0.3
