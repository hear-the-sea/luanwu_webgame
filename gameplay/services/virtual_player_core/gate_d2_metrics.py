from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean, median, variance
from types import MappingProxyType
from typing import Any

from core.config import GUEST

from .calibration import (
    CalibrationUnit,
    DistributionEvidence,
    canonical_snapshot_digest,
    jensen_shannon_divergence_bits,
    nearest_rank_quantile,
    normalized_quantile_deviations,
    normalized_wasserstein_distance,
)
from .config import VirtualPlayerConfigError, VirtualPlayerV2Config
from .gate_d2_candidate_artifact import (
    GateD2CandidateArtifact,
    GateD2CandidateArtifactError,
    GateD2RawCandidateProfile,
    GateD2RawProfile,
)
from .projection import calculate_guest_arena_power
from .reference_snapshot_catalog import ReferenceSnapshotBand
from .reference_snapshots import CORE_BUILDING_KEYS, build_strength_summary

_CONTINUOUS_FIELDS = (
    "guest_count",
    "mean_guest_level",
    "guest_level_gap",
    "gear_count",
    "skill_count",
    "troop_total",
    "troop_concentration",
    "mean_building_level",
)
_JOINT_FEATURE_FIELDS = (
    *_CONTINUOUS_FIELDS,
    "mean_gear_level",
    "guard_count",
    "mean_guard_level",
    "core_building_level",
)
_CATEGORY_FIELDS = (
    "guest_rarity",
    "guest_archetype",
    "skill_kind",
    "guard_class",
)
_COMPONENT_FINGERPRINT_FIELDS = (
    "roster",
    "equipment",
    "skills",
    "guard",
    "buildings",
)
_EFFECT_METRIC_FIELDS = frozenset(
    {
        "prestige",
        "core_building_level",
        "max_guest_level",
        "arena_lineup_power",
        "troop_total",
        "mean_building_level",
        "composite_strength",
    }
)
_EFFECT_ARCHETYPES = frozenset({"rich", "dojo", "guard", "abandoned"})
_EFFECT_SPEC_FIELDS = frozenset({"metric", "direction"})
_ABANDONED_SPEC_FIELDS = frozenset(
    {
        "underfilled_roster_guest_count_max",
        "stale_gear_level_ratio_max",
        "growth_gap_days_min",
    }
)
_REFERENCE_HOLDOUT_STRIDE = 5
_JOINT_DISTANCE_ROBUST_Z_THRESHOLD = 3.5
_MAD_NORMALIZATION_FACTOR = 1.4826


@dataclass(frozen=True, slots=True)
class _DerivedProfile:
    raw: GateD2RawProfile
    continuous: Mapping[str, float]
    snapshot_values: Mapping[str, int]
    categories: Mapping[str, tuple[str, ...]]
    component_fingerprints: Mapping[str, str]
    joint_fingerprint: str
    hard_constraint_violations: int
    effect_metrics: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class _RobustJointOutlierModel:
    centers: Mapping[str, float]
    scales: Mapping[str, float]
    fit_vectors: tuple[tuple[float, ...], ...]
    distance_threshold: float


def _strict_mapping_fields(
    value: Any,
    expected: frozenset[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GateD2CandidateArtifactError(f"{label} must be a mapping")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise GateD2CandidateArtifactError(f"{label} has {'; '.join(details)}")
    return value


def _troop_concentration(profile: GateD2RawProfile) -> float:
    counts = [max(0, troop.count) for troop in profile.troops]
    total = sum(counts)
    if total <= 0:
        return 0.0
    return sum((count / total) ** 2 for count in counts)


def _guest_arena_power(profile: GateD2RawProfile) -> int:
    return sum(
        calculate_guest_arena_power(
            force=guest.force,
            intellect=guest.intellect,
            defense=guest.defense,
            # Gate D2 is a retired static artifact schema and does not carry
            # the live guest agility field; current runtime paths pass it.
            agility=0,
            hp_bonus=guest.hp_bonus,
            archetype=guest.archetype,
            base_hp=guest.base_hp,
        )
        for guest in profile.guests
    )


def _hard_constraint_violations(
    profile: GateD2RawProfile,
    artifact: GateD2CandidateArtifact,
) -> int:
    catalog = artifact.template_catalog
    violations = int(profile.prestige < 0)
    for guest in profile.guests:
        template = catalog.guest_templates.get(guest.template)
        violations += int(template is None or template.rarity != guest.rarity or template.archetype != guest.archetype)
        violations += int(guest.level <= 0)
        violations += sum(
            int(value < 0)
            for value in (
                guest.force,
                guest.intellect,
                guest.defense,
                guest.hp_bonus,
            )
        )
        violations += int(guest.base_hp <= 0)
        slot_counts = Counter(item.slot for item in guest.equipment)
        violations += sum(max(0, count - 1) for count in slot_counts.values())
        for item in guest.equipment:
            item_template = catalog.equipment_templates.get(item.template)
            violations += int(
                item_template is None or item_template.rarity != item.rarity or item_template.slot != item.slot
            )
            violations += int(item.level <= 0)
        violations += max(0, len(guest.skills) - int(GUEST.MAX_SKILL_SLOTS))
        for skill in guest.skills:
            skill_template = catalog.skill_templates.get(skill.key)
            violations += int(
                skill_template is None or skill_template.kind != skill.kind or skill_template.rarity != skill.rarity
            )
    for guard in profile.guards:
        guard_template = catalog.guard_templates.get(guard.template)
        violations += int(guard_template is None or guard_template.class_name != guard.class_name)
        violations += int(guard.level <= 0)
    for troop in profile.troops:
        troop_template = catalog.troop_templates.get(troop.template)
        violations += int(troop_template is None or troop_template.class_name != troop.class_name)
        violations += int(troop.count < 0)
    for building in profile.buildings:
        building_template = catalog.building_templates.get(building.key)
        violations += int(building_template is None or building.level < 1)
        if (
            building_template is not None
            and building_template.max_level is not None
            and building.level > building_template.max_level
        ):
            violations += 1
    for resource in profile.resources:
        violations += int(resource.key not in catalog.resource_keys)
        violations += int(resource.capacity < 0 or resource.amount < 0 or resource.amount > resource.capacity)
    return violations


def _profile_fingerprints(
    profile: GateD2RawProfile,
) -> tuple[Mapping[str, str], str]:
    roster = [
        [
            guest.ordinal,
            guest.template,
            guest.level,
            guest.rarity,
            guest.archetype,
            guest.base_hp,
            guest.force,
            guest.intellect,
            guest.defense,
            guest.hp_bonus,
        ]
        for guest in profile.guests
    ]
    equipment = [
        [
            guest.ordinal,
            item.template,
            item.level,
            item.rarity,
            item.slot,
        ]
        for guest in profile.guests
        for item in guest.equipment
    ]
    skills = [
        [guest.ordinal, skill.key, skill.kind, skill.rarity] for guest in profile.guests for skill in guest.skills
    ]
    guards = [[guard.template, guard.class_name, guard.level] for guard in profile.guards]
    buildings = [[item.key, item.level] for item in profile.buildings]
    troops = [[troop.template, troop.class_name, troop.count] for troop in profile.troops]
    resources = [[resource.key, resource.amount, resource.capacity] for resource in profile.resources]
    payloads = {
        "roster": roster,
        "equipment": equipment,
        "skills": skills,
        "guard": guards,
        "buildings": buildings,
    }
    fingerprints = MappingProxyType(
        {field: canonical_snapshot_digest(payloads[field]) for field in _COMPONENT_FINGERPRINT_FIELDS}
    )
    return fingerprints, canonical_snapshot_digest([roster, equipment, skills, guards, buildings, troops, resources])


def _derive_profile(
    profile: GateD2RawProfile,
    artifact: GateD2CandidateArtifact,
) -> _DerivedProfile:
    guest_levels = [guest.level for guest in profile.guests]
    gear_levels = [item.level for guest in profile.guests for item in guest.equipment]
    guard_levels = [guard.level for guard in profile.guards]
    building_levels = [building.level for building in profile.buildings]
    troop_total = sum(max(0, troop.count) for troop in profile.troops)
    core_building_level = max(
        (building.level for building in profile.buildings if building.key in CORE_BUILDING_KEYS),
        default=0,
    )
    arena_power = _guest_arena_power(profile)
    max_guest_level = max(guest_levels, default=0)
    snapshot_values = MappingProxyType(
        {
            "prestige": profile.prestige,
            "core_building_level": core_building_level,
            "guest_count": len(profile.guests),
            "max_guest_level": max_guest_level,
            "arena_lineup_power": arena_power,
            "troop_total": troop_total,
        }
    )
    strength = build_strength_summary(**snapshot_values)
    continuous = MappingProxyType(
        {
            "guest_count": float(len(profile.guests)),
            "mean_guest_level": (sum(guest_levels) / len(guest_levels) if guest_levels else 0.0),
            "guest_level_gap": float(max(guest_levels) - min(guest_levels) if guest_levels else 0),
            "gear_count": float(sum(len(guest.equipment) for guest in profile.guests)),
            "skill_count": float(sum(len(guest.skills) for guest in profile.guests)),
            "troop_total": float(troop_total),
            "troop_concentration": _troop_concentration(profile),
            "mean_building_level": (sum(building_levels) / len(building_levels) if building_levels else 0.0),
            "mean_gear_level": (sum(gear_levels) / len(gear_levels) if gear_levels else 0.0),
            "guard_count": float(len(profile.guards)),
            "mean_guard_level": (sum(guard_levels) / len(guard_levels) if guard_levels else 0.0),
            "core_building_level": float(core_building_level),
        }
    )
    categories = MappingProxyType(
        {
            "guest_rarity": tuple(guest.rarity for guest in profile.guests),
            "guest_archetype": tuple(guest.archetype for guest in profile.guests),
            "skill_kind": tuple(skill.kind for guest in profile.guests for skill in guest.skills),
            "guard_class": tuple(guard.class_name for guard in profile.guards),
        }
    )
    fingerprints, joint_fingerprint = _profile_fingerprints(profile)
    return _DerivedProfile(
        raw=profile,
        continuous=continuous,
        snapshot_values=snapshot_values,
        categories=categories,
        component_fingerprints=fingerprints,
        joint_fingerprint=joint_fingerprint,
        hard_constraint_violations=_hard_constraint_violations(profile, artifact),
        effect_metrics=MappingProxyType(
            {
                "prestige": float(profile.prestige),
                "core_building_level": float(core_building_level),
                "max_guest_level": float(max_guest_level),
                "arena_lineup_power": float(arena_power),
                "troop_total": float(troop_total),
                "mean_building_level": continuous["mean_building_level"],
                "composite_strength": float(strength.composite),
            }
        ),
    )


def _validate_reference_snapshot(
    derived: Sequence[_DerivedProfile],
    reference_band: ReferenceSnapshotBand,
) -> None:
    expected = {str(profile["business_key"]): profile for profile in reference_band.profiles}
    actual = {profile.raw.business_key: profile for profile in derived}
    if set(actual) != set(expected):
        raise GateD2CandidateArtifactError("Gate D2 reference raw profiles do not match the frozen snapshot keys")
    for business_key, profile in actual.items():
        for field, value in profile.snapshot_values.items():
            if value != expected[business_key][field]:
                raise GateD2CandidateArtifactError(
                    "Gate D2 reference raw profile does not reproduce the frozen " f"snapshot: {business_key}:{field}"
                )


def _category_distribution(profiles: Sequence[_DerivedProfile], *, field: str) -> Mapping[str, int]:
    counts = Counter(value for profile in profiles for value in profile.categories[field])
    if not counts:
        raise GateD2CandidateArtifactError(f"Gate D2 raw cohort has no observations for {field}")
    return counts


def _split_reference_fit_holdout(
    reference: Sequence[_DerivedProfile],
) -> tuple[tuple[_DerivedProfile, ...], tuple[_DerivedProfile, ...]]:
    ordered = tuple(sorted(reference, key=lambda profile: profile.raw.business_key))
    holdout = tuple(profile for index, profile in enumerate(ordered) if index % _REFERENCE_HOLDOUT_STRIDE == 0)
    fit = tuple(profile for index, profile in enumerate(ordered) if index % _REFERENCE_HOLDOUT_STRIDE != 0)
    if len(fit) < 2 or len(holdout) < 2:
        raise GateD2CandidateArtifactError("Gate D2 joint outlier evaluation requires disjoint fit and holdout cohorts")
    return fit, holdout


def _joint_feature_vector(
    profile: _DerivedProfile,
    *,
    centers: Mapping[str, float],
    scales: Mapping[str, float],
) -> tuple[float, ...]:
    vector = tuple((profile.continuous[field] - centers[field]) / scales[field] for field in _JOINT_FEATURE_FIELDS)
    if not all(math.isfinite(value) for value in vector):
        raise GateD2CandidateArtifactError("Gate D2 joint outlier features must be finite")
    return vector


def _joint_distance(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    return math.sqrt(
        sum((left_value - right_value) ** 2 for left_value, right_value in zip(left, right, strict=True))
        / len(_JOINT_FEATURE_FIELDS)
    )


def _nearest_joint_distance(
    vector: tuple[float, ...],
    anchors: Sequence[tuple[float, ...]],
    *,
    excluded_index: int | None = None,
) -> float:
    distances = (_joint_distance(vector, anchor) for index, anchor in enumerate(anchors) if index != excluded_index)
    try:
        return min(distances)
    except ValueError as exc:
        raise GateD2CandidateArtifactError("Gate D2 joint outlier model requires at least two fit profiles") from exc


def _fit_robust_joint_outlier_model(
    fit: Sequence[_DerivedProfile],
) -> _RobustJointOutlierModel:
    centers: dict[str, float] = {}
    scales: dict[str, float] = {}
    for field in _JOINT_FEATURE_FIELDS:
        values = [profile.continuous[field] for profile in fit]
        centers[field] = float(median(values))
        scales[field] = max(
            1.0,
            nearest_rank_quantile(values, 0.75) - nearest_rank_quantile(values, 0.25),
        )
    frozen_centers = MappingProxyType(centers)
    frozen_scales = MappingProxyType(scales)
    fit_vectors = tuple(
        _joint_feature_vector(
            profile,
            centers=frozen_centers,
            scales=frozen_scales,
        )
        for profile in fit
    )
    fit_distances = tuple(
        _nearest_joint_distance(vector, fit_vectors, excluded_index=index) for index, vector in enumerate(fit_vectors)
    )
    distance_center = float(median(fit_distances))
    distance_mad = float(median(abs(distance - distance_center) for distance in fit_distances))
    numerical_tolerance = max(1e-12, abs(distance_center) * 1e-12)
    robust_distance_threshold = (
        distance_center
        + _JOINT_DISTANCE_ROBUST_Z_THRESHOLD * _MAD_NORMALIZATION_FACTOR * distance_mad
        + numerical_tolerance
    )
    # Sparse fit cohorts must not turn an otherwise legal sub-IQR perturbation
    # in one feature into a joint anomaly merely because local spacing is tiny.
    minimum_joint_tolerance = 1.0 / math.sqrt(len(_JOINT_FEATURE_FIELDS))
    distance_threshold = max(robust_distance_threshold, minimum_joint_tolerance)
    return _RobustJointOutlierModel(
        centers=frozen_centers,
        scales=frozen_scales,
        fit_vectors=fit_vectors,
        distance_threshold=distance_threshold,
    )


def _joint_outlier_rate(
    model: _RobustJointOutlierModel,
    evaluated: Sequence[_DerivedProfile],
) -> float:
    if not evaluated:
        raise GateD2CandidateArtifactError("Gate D2 joint outlier evaluation cohort must not be empty")
    outliers = 0
    for profile in evaluated:
        vector = _joint_feature_vector(
            profile,
            centers=model.centers,
            scales=model.scales,
        )
        distance = _nearest_joint_distance(vector, model.fit_vectors)
        outliers += int(distance > model.distance_threshold)
    return outliers / len(evaluated)


def _robust_joint_outlier_rates(
    reference: Sequence[_DerivedProfile],
    evaluated: Sequence[_DerivedProfile],
) -> tuple[float, float]:
    fit, holdout = _split_reference_fit_holdout(reference)
    model = _fit_robust_joint_outlier_model(fit)
    return (
        _joint_outlier_rate(model, evaluated),
        _joint_outlier_rate(model, holdout),
    )


def _collision_rate(values: Sequence[str]) -> float:
    return (len(values) - len(set(values))) / len(values)


def _standardized_mean_difference(treatment: Sequence[float], control: Sequence[float]) -> float:
    if len(treatment) < 2 or len(control) < 2:
        raise GateD2CandidateArtifactError("Gate D2 archetype effects require at least two profiles per cohort")
    pooled_variance = ((len(treatment) - 1) * variance(treatment) + (len(control) - 1) * variance(control)) / (
        len(treatment) + len(control) - 2
    )
    if pooled_variance <= 0:
        raise GateD2CandidateArtifactError("Gate D2 archetype effects require non-zero pooled variance")
    return (mean(treatment) - mean(control)) / math.sqrt(pooled_variance)


def _archetype_effects(
    candidates: Sequence[tuple[GateD2RawCandidateProfile, _DerivedProfile]],
    policy_payload: Mapping[str, Any],
) -> tuple[float, float, bool]:
    specs = _strict_mapping_fields(
        policy_payload.get("reference_calibration_archetype_effects"),
        _EFFECT_ARCHETYPES,
        label="policy reference_calibration_archetype_effects",
    )
    balanced = [derived for candidate, derived in candidates if candidate.archetype == "balanced"]
    effects: list[float] = []
    directions_match = True
    for archetype in sorted(_EFFECT_ARCHETYPES):
        raw_spec = _strict_mapping_fields(
            specs[archetype],
            _EFFECT_SPEC_FIELDS,
            label=f"policy reference_calibration_archetype_effects.{archetype}",
        )
        metric = raw_spec["metric"]
        direction = raw_spec["direction"]
        if metric not in _EFFECT_METRIC_FIELDS:
            raise GateD2CandidateArtifactError(f"Gate D2 archetype metric is unsupported: {metric!r}")
        if direction not in {"higher", "lower"}:
            raise GateD2CandidateArtifactError(f"Gate D2 archetype direction is unsupported: {direction!r}")
        treatment = [
            derived.effect_metrics[str(metric)] for candidate, derived in candidates if candidate.archetype == archetype
        ]
        control = [profile.effect_metrics[str(metric)] for profile in balanced]
        effect = _standardized_mean_difference(treatment, control)
        effects.append(effect)
        directions_match = directions_match and (effect > 0 if direction == "higher" else effect < 0)
    absolute = [abs(effect) for effect in effects]
    return min(absolute), max(absolute), directions_match


def _abandoned_features(
    profile: GateD2RawProfile,
    *,
    roster_max: int,
    stale_ratio_max: float,
    growth_gap_days: int,
) -> tuple[bool, bool, bool]:
    maximum_guest_level = max((guest.level for guest in profile.guests), default=0)
    maximum_gear_level = max(
        (item.level for guest in profile.guests for item in guest.equipment),
        default=0,
    )
    gear_ratio = maximum_gear_level / maximum_guest_level if maximum_guest_level > 0 else 0.0
    return (
        len(profile.guests) <= roster_max,
        gear_ratio <= stale_ratio_max,
        profile.days_since_last_strength_increase >= growth_gap_days,
    )


def _abandoned_rate_deviation(
    candidates: Sequence[GateD2RawCandidateProfile],
    inactive_reference: Sequence[GateD2RawProfile],
    policy_payload: Mapping[str, Any],
) -> float:
    spec = _strict_mapping_fields(
        policy_payload.get("reference_calibration_abandoned_features"),
        _ABANDONED_SPEC_FIELDS,
        label="policy reference_calibration_abandoned_features",
    )
    roster_max = spec["underfilled_roster_guest_count_max"]
    growth_gap_days = spec["growth_gap_days_min"]
    stale_ratio = spec["stale_gear_level_ratio_max"]
    if isinstance(roster_max, bool) or not isinstance(roster_max, int) or roster_max < 0:
        raise GateD2CandidateArtifactError("Gate D2 abandoned roster threshold must be a non-negative integer")
    if isinstance(growth_gap_days, bool) or not isinstance(growth_gap_days, int) or growth_gap_days < 1:
        raise GateD2CandidateArtifactError("Gate D2 abandoned growth gap must be a positive integer")
    if (
        isinstance(stale_ratio, bool)
        or not isinstance(stale_ratio, (int, float))
        or not math.isfinite(float(stale_ratio))
        or not 0 <= float(stale_ratio) <= 1
    ):
        raise GateD2CandidateArtifactError("Gate D2 abandoned stale gear ratio must be between zero and one")
    abandoned = [item.raw for item in candidates if item.archetype == "abandoned"]
    if len(abandoned) < 2:
        raise GateD2CandidateArtifactError("Gate D2 abandoned candidate cohort requires at least two profiles")
    candidate_flags = [
        _abandoned_features(
            profile,
            roster_max=roster_max,
            stale_ratio_max=float(stale_ratio),
            growth_gap_days=growth_gap_days,
        )
        for profile in abandoned
    ]
    reference_flags = [
        _abandoned_features(
            profile,
            roster_max=roster_max,
            stale_ratio_max=float(stale_ratio),
            growth_gap_days=growth_gap_days,
        )
        for profile in inactive_reference
    ]
    deviations = []
    for index in range(3):
        candidate_rate = sum(flags[index] for flags in candidate_flags) / len(candidate_flags)
        reference_rate = sum(flags[index] for flags in reference_flags) / len(reference_flags)
        deviations.append(abs(candidate_rate - reference_rate))
    return max(deviations)


def _rounded(value: float) -> float:
    result = round(float(value), 12)
    return 0.0 if result == 0.0 else result


def recompute_gate_d2_candidate_evidence(
    artifact: GateD2CandidateArtifact,
    *,
    expected_unit: CalibrationUnit,
    config: VirtualPlayerV2Config,
    reference_band: ReferenceSnapshotBand,
    expected_reference_snapshot_digest: str,
) -> DistributionEvidence:
    if artifact.unit != expected_unit:
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact unit does not match the activation unit")
    policy = config.policy(expected_unit.policy_version)
    if artifact.policy_checksum != policy.checksum:
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact policy checksum does not match configuration")
    if artifact.reference_snapshot_digest != expected_reference_snapshot_digest:
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact reference digest does not match configuration")
    provenance = artifact.generator_provenance
    if provenance.engine_version != config.engine_version:
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact engine version does not match configuration")
    if provenance.rng_version != config.rng_version:
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact RNG version does not match configuration")
    if provenance.plan_schema_version != config.plan_schema_version:
        raise GateD2CandidateArtifactError("Gate D2 candidate artifact plan schema does not match configuration")
    reference = tuple(_derive_profile(profile, artifact) for profile in artifact.reference_profiles)
    candidates = tuple(
        (candidate, _derive_profile(candidate.raw, artifact)) for candidate in artifact.candidate_profiles
    )
    candidate_derived = tuple(item[1] for item in candidates)
    v1 = tuple(_derive_profile(profile, artifact) for profile in artifact.v1_profiles)
    _validate_reference_snapshot(reference, reference_band)
    wasserstein = max(
        normalized_wasserstein_distance(
            [profile.continuous[field] for profile in reference],
            [profile.continuous[field] for profile in candidate_derived],
        )
        for field in _CONTINUOUS_FIELDS
    )
    quantiles = [
        normalized_quantile_deviations(
            [profile.continuous[field] for profile in reference],
            [profile.continuous[field] for profile in candidate_derived],
        )
        for field in _CONTINUOUS_FIELDS
    ]
    js_divergence = max(
        jensen_shannon_divergence_bits(
            _category_distribution(reference, field=field),
            _category_distribution(candidate_derived, field=field),
        )
        for field in _CATEGORY_FIELDS
    )
    candidate_outlier_rate, reference_outlier_rate = _robust_joint_outlier_rates(
        reference,
        candidate_derived,
    )
    component_collision_rates = {
        field: _collision_rate([profile.component_fingerprints[field] for profile in candidate_derived])
        for field in _COMPONENT_FINGERPRINT_FIELDS
    }
    joint_collision_rate = _collision_rate([profile.joint_fingerprint for profile in candidate_derived])
    v1_deltas = [
        component_collision_rates[field] - _collision_rate([profile.component_fingerprints[field] for profile in v1])
        for field in _COMPONENT_FINGERPRINT_FIELDS
    ]
    v1_deltas.append(joint_collision_rate - _collision_rate([profile.joint_fingerprint for profile in v1]))
    minimum_effect, maximum_effect, directions_match = _archetype_effects(candidates, policy.payload)
    unclassified = 0
    for profile in candidate_derived:
        try:
            band = config.band_for_prestige(profile.raw.prestige)
        except VirtualPlayerConfigError:
            unclassified += 1
            continue
        if band.name != expected_unit.prestige_band:
            unclassified += 1
    return DistributionEvidence(
        unit=expected_unit,
        reference_snapshot_digest=expected_reference_snapshot_digest,
        reference_profile_count=len(reference),
        candidate_profile_count=len(candidates),
        unclassified_profile_count=unclassified,
        normalized_wasserstein=_rounded(wasserstein),
        normalized_quantile_deviation_p10=_rounded(max(item.p10 for item in quantiles)),
        normalized_quantile_deviation_p50=_rounded(max(item.p50 for item in quantiles)),
        normalized_quantile_deviation_p90=_rounded(max(item.p90 for item in quantiles)),
        js_divergence_bits=_rounded(js_divergence),
        hard_constraint_violations=sum(profile.hard_constraint_violations for profile in candidate_derived),
        robust_joint_outlier_rate=_rounded(candidate_outlier_rate),
        robust_joint_outlier_rate_above_real_cohort=_rounded(candidate_outlier_rate - reference_outlier_rate),
        component_fingerprint_collision_rate_max=_rounded(max(component_collision_rates.values())),
        joint_fingerprint_collision_rate=_rounded(joint_collision_rate),
        fingerprint_collision_rate_above_v1_max=_rounded(max(v1_deltas)),
        archetype_standardized_effect_min_absolute=_rounded(minimum_effect),
        archetype_standardized_effect_max_absolute=_rounded(maximum_effect),
        archetype_effect_direction_matches=directions_match,
        abandoned_rate_deviation_from_inactive_real=_rounded(
            _abandoned_rate_deviation(
                artifact.candidate_profiles,
                artifact.inactive_reference_profiles,
                policy.payload,
            )
        ),
    )


__all__ = ["recompute_gate_d2_candidate_evidence"]
