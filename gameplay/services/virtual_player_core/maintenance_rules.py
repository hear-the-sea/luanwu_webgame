from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Final

from .contracts import (
    STRENGTH_BUDGET_MAX_ENTRIES,
    StrengthBudgetEntry,
    calculate_positive_growth_bps,
    consume_strength_budget,
    prune_strength_budget_entries,
    strength_budget_usage,
)
from .projection import (
    PRESTIGE_BANDS,
    DevelopmentIntent,
    ProjectionRuleError,
    StrengthSummary,
    safety_rule_for_sample_count,
)
from .random_context import RandomContext


class MaintenanceRuleError(ValueError):
    pass


class MaintenanceNoActionReason(str, Enum):
    DOMAIN_CONSTRAINT = "domain_constraint"
    STRENGTH_CAP = "strength_cap"
    BAND_SPACING = "band_spacing"
    BAND_ACTION_CAP = "band_action_cap"
    MULTI_BAND_TRANSITION = "multi_band_transition"


MAINTENANCE_NO_ACTION_REASON_PRIORITY: Final[tuple[MaintenanceNoActionReason, ...]] = (
    MaintenanceNoActionReason.DOMAIN_CONSTRAINT,
    MaintenanceNoActionReason.STRENGTH_CAP,
    MaintenanceNoActionReason.BAND_SPACING,
    MaintenanceNoActionReason.BAND_ACTION_CAP,
    MaintenanceNoActionReason.MULTI_BAND_TRANSITION,
)


_GROWTH_FIELDS: Final[frozenset[str]] = frozenset(
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
_PROFILE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "bootstrap_history_age_days",
        "preferred_strength_check_interval_hours",
        "minimum_positive_strength_action_spacing_hours",
        "composite_growth_bps_per_controlled_action_max",
    }
)
_EXPECTED_GROWTH_LITERALS: Final[Mapping[str, object]] = MappingProxyType(
    {
        "effective_limit_rule": "strictest_of_sample_tier_band_profile_and_domain_constraints",
        "direct_prestige_grant_by_maintenance_allowed": False,
        "last_strength_increase_at_required": True,
        "arena_acceleration_may_bypass_band_spacing": False,
        "admin_may_bypass_band_spacing": False,
        "configured_boundaries_crossed_per_controlled_action_max": 1,
        "cross_band_uses_stricter_source_or_destination_limit": True,
        "external_domain_result_may_be_rejected_by_bot_growth_policy": False,
        "bootstrap_fake_per_action_history_records": False,
    }
)


@dataclass(frozen=True, slots=True)
class BandGrowthCadence:
    prestige_band: str
    bootstrap_history_age_days: tuple[int, int]
    preferred_strength_check_interval: tuple[timedelta, timedelta]
    minimum_positive_strength_action_spacing: timedelta
    composite_growth_bps_per_controlled_action_max: int


@dataclass(frozen=True, slots=True)
class PrestigeBandGrowthPolicy:
    profiles: Mapping[str, BandGrowthCadence]

    def cadence_for(self, prestige_band: str) -> BandGrowthCadence:
        try:
            return self.profiles[prestige_band]
        except KeyError as exc:
            raise MaintenanceRuleError(f"unknown prestige band: {prestige_band!r}") from exc


@dataclass(frozen=True, slots=True)
class EffectiveGrowthLimits:
    minimum_positive_strength_action_spacing: timedelta
    composite_growth_bps_per_controlled_action_max: int
    strength_increasing_actions_per_24h_max: int
    composite_growth_bps_per_24h_max: int


@dataclass(frozen=True, slots=True)
class ControlledActionDecision:
    allowed: bool
    strength_increasing: bool
    controlled_growth_bps: int
    skipped_action_reasons: tuple[MaintenanceNoActionReason, ...]
    budget_entries_after: tuple[StrengthBudgetEntry, ...]
    last_strength_increase_at_after: datetime | None
    band_spacing_deadline: datetime | None
    effective_limits: EffectiveGrowthLimits | None

    @property
    def reason(self) -> MaintenanceNoActionReason | None:
        return self.skipped_action_reasons[0] if self.skipped_action_reasons else None


def _mapping(value: object, *, field: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise MaintenanceRuleError(f"{field} must be a mapping")
    return value


def _require_exact_fields(
    value: Mapping[object, object],
    *,
    expected: frozenset[str],
    field: str,
) -> None:
    missing = sorted(item for item in expected if item not in value)
    unknown = sorted((item for item in value if item not in expected), key=repr)
    if missing or unknown:
        detail: list[str] = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if unknown:
            detail.append(f"unknown {', '.join(repr(item) for item in unknown)}")
        raise MaintenanceRuleError(f"{field} has invalid fields: {'; '.join(detail)}")


def _finite_non_negative_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaintenanceRuleError(f"{field} must be a finite non-negative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise MaintenanceRuleError(f"{field} must be a finite non-negative number")
    return normalized


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MaintenanceRuleError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    normalized = _non_negative_int(value, field=field)
    if normalized == 0:
        raise MaintenanceRuleError(f"{field} must be positive")
    return normalized


def _history_range(value: object, *, field: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise MaintenanceRuleError(f"{field} must be a two-item integer range")
    lower = _non_negative_int(value[0], field=f"{field}[0]")
    upper = _non_negative_int(value[1], field=f"{field}[1]")
    if lower > upper:
        raise MaintenanceRuleError(f"{field} lower bound must not exceed upper bound")
    return lower, upper


def _duration_range(value: object, *, field: str) -> tuple[timedelta, timedelta]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise MaintenanceRuleError(f"{field} must be a two-item numeric range")
    lower = _finite_non_negative_number(value[0], field=f"{field}[0]")
    upper = _finite_non_negative_number(value[1], field=f"{field}[1]")
    if lower > upper:
        raise MaintenanceRuleError(f"{field} lower bound must not exceed upper bound")
    return timedelta(hours=lower), timedelta(hours=upper)


def _validate_growth_literals(value: Mapping[object, object]) -> None:
    for field, expected in _EXPECTED_GROWTH_LITERALS.items():
        actual = value[field]
        if type(actual) is not type(expected) or actual != expected:
            raise MaintenanceRuleError(f"prestige_band_growth.{field} must equal {expected!r}")


def parse_prestige_band_growth_policy(value: object) -> PrestigeBandGrowthPolicy:
    growth = _mapping(value, field="prestige_band_growth")
    _require_exact_fields(growth, expected=_GROWTH_FIELDS, field="prestige_band_growth")
    _validate_growth_literals(growth)

    raw_profiles = _mapping(growth["profiles"], field="prestige_band_growth.profiles")
    profile_names = frozenset(raw_profiles)
    if profile_names != frozenset(PRESTIGE_BANDS):
        raise MaintenanceRuleError(f"prestige_band_growth.profiles names must equal {list(PRESTIGE_BANDS)}")

    parsed: dict[str, BandGrowthCadence] = {}
    previous: BandGrowthCadence | None = None
    for prestige_band in PRESTIGE_BANDS:
        field = f"prestige_band_growth.profiles.{prestige_band}"
        raw_profile = _mapping(raw_profiles[prestige_band], field=field)
        _require_exact_fields(raw_profile, expected=_PROFILE_FIELDS, field=field)
        history = _history_range(
            raw_profile["bootstrap_history_age_days"],
            field=f"{field}.bootstrap_history_age_days",
        )
        interval = _duration_range(
            raw_profile["preferred_strength_check_interval_hours"],
            field=f"{field}.preferred_strength_check_interval_hours",
        )
        spacing_hours = _finite_non_negative_number(
            raw_profile["minimum_positive_strength_action_spacing_hours"],
            field=f"{field}.minimum_positive_strength_action_spacing_hours",
        )
        cadence = BandGrowthCadence(
            prestige_band=prestige_band,
            bootstrap_history_age_days=history,
            preferred_strength_check_interval=interval,
            minimum_positive_strength_action_spacing=timedelta(hours=spacing_hours),
            composite_growth_bps_per_controlled_action_max=_non_negative_int(
                raw_profile["composite_growth_bps_per_controlled_action_max"],
                field=f"{field}.composite_growth_bps_per_controlled_action_max",
            ),
        )
        if previous is not None:
            if (
                cadence.bootstrap_history_age_days[0] < previous.bootstrap_history_age_days[0]
                or cadence.bootstrap_history_age_days[1] < previous.bootstrap_history_age_days[1]
            ):
                raise MaintenanceRuleError("bootstrap history age must not decrease across prestige bands")
            if (
                cadence.preferred_strength_check_interval[0] < previous.preferred_strength_check_interval[0]
                or cadence.preferred_strength_check_interval[1] < previous.preferred_strength_check_interval[1]
            ):
                raise MaintenanceRuleError("strength check interval must not decrease across prestige bands")
            if cadence.minimum_positive_strength_action_spacing < previous.minimum_positive_strength_action_spacing:
                raise MaintenanceRuleError("positive strength action spacing must not decrease across prestige bands")
            if (
                cadence.composite_growth_bps_per_controlled_action_max
                > previous.composite_growth_bps_per_controlled_action_max
            ):
                raise MaintenanceRuleError("controlled action growth cap must not increase across prestige bands")
        parsed[prestige_band] = cadence
        previous = cadence

    return PrestigeBandGrowthPolicy(profiles=MappingProxyType(parsed))


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MaintenanceRuleError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def bootstrap_historical_age_days(
    *,
    policy: PrestigeBandGrowthPolicy,
    prestige_band: str,
    context: RandomContext,
) -> int:
    cadence = policy.cadence_for(prestige_band)
    lower, upper = cadence.bootstrap_history_age_days
    rng = context.random(
        domain="bootstrap",
        discriminator={
            "purpose": "historical-age-days",
            "prestige_band": prestige_band,
        },
    )
    return rng.randint(lower, upper)


def next_normal_strength_check_at(
    *,
    policy: PrestigeBandGrowthPolicy,
    prestige_band: str,
    context: RandomContext,
    now: datetime,
) -> datetime:
    current_time = _aware_utc(now, field="now")
    cadence = policy.cadence_for(prestige_band)
    lower, upper = cadence.preferred_strength_check_interval
    rng = context.random(
        domain="schedule",
        discriminator={
            "purpose": "normal-strength-check",
            "prestige_band": prestige_band,
        },
    )
    interval_seconds = rng.uniform(lower.total_seconds(), upper.total_seconds())
    return current_time + timedelta(seconds=interval_seconds)


def _sample_limits(sample_count: int) -> tuple[int, int]:
    try:
        rule = safety_rule_for_sample_count(sample_count)
    except ProjectionRuleError as exc:
        raise MaintenanceRuleError(str(exc)) from exc
    return (
        rule.strength_increasing_actions_per_24h_max,
        rule.composite_growth_bps_per_24h_max,
    )


def effective_growth_limits(
    *,
    policy: PrestigeBandGrowthPolicy,
    source_prestige_band: str,
    target_prestige_band: str,
    source_sample_count: int,
    target_sample_count: int | None = None,
) -> EffectiveGrowthLimits:
    source = policy.cadence_for(source_prestige_band)
    target = policy.cadence_for(target_prestige_band)
    source_actions, source_growth = _sample_limits(source_sample_count)
    if source_prestige_band == target_prestige_band:
        if target_sample_count is not None and target_sample_count != source_sample_count:
            raise MaintenanceRuleError("same-band source and target sample counts must match")
        target_actions, target_growth = source_actions, source_growth
    else:
        if target_sample_count is None:
            raise MaintenanceRuleError("cross-band evaluation requires target_sample_count")
        target_actions, target_growth = _sample_limits(target_sample_count)
    return EffectiveGrowthLimits(
        minimum_positive_strength_action_spacing=max(
            source.minimum_positive_strength_action_spacing,
            target.minimum_positive_strength_action_spacing,
        ),
        composite_growth_bps_per_controlled_action_max=min(
            source.composite_growth_bps_per_controlled_action_max,
            target.composite_growth_bps_per_controlled_action_max,
        ),
        strength_increasing_actions_per_24h_max=min(source_actions, target_actions),
        composite_growth_bps_per_24h_max=min(source_growth, target_growth),
    )


def _transition_distance(source_band: str, target_band: str) -> int:
    try:
        source_index = PRESTIGE_BANDS.index(source_band)
        target_index = PRESTIGE_BANDS.index(target_band)
    except ValueError as exc:
        raise MaintenanceRuleError("controlled action references an unknown prestige band") from exc
    return abs(target_index - source_index)


def _strength_delta(intent: DevelopmentIntent) -> tuple[bool, int]:
    component_increase = any(
        intent.strength_after.components[key] > intent.strength_before.components[key]
        for key in intent.strength_before.components
    )
    composite_increase = intent.strength_after.composite > intent.strength_before.composite
    return composite_increase or component_increase, calculate_positive_growth_bps(
        pre_score=intent.strength_before.composite,
        post_score=intent.strength_after.composite,
        score_floor=1,
    )


def _validate_cap_shape(intent: DevelopmentIntent, cap: StrengthSummary, *, field: str) -> None:
    if not isinstance(cap, StrengthSummary):
        raise MaintenanceRuleError(f"{field} must be a StrengthSummary")
    if intent.strength_before.components.keys() != cap.components.keys():
        raise MaintenanceRuleError(f"{field} component keys must match the intent strength summary")


def _blocked_by_cap(intent: DevelopmentIntent, cap: StrengthSummary) -> bool:
    if intent.strength_before.composite >= cap.composite or intent.strength_after.composite > cap.composite:
        return True
    return any(
        intent.strength_before.components[key] >= cap.components[key]
        or intent.strength_after.components[key] > cap.components[key]
        for key in cap.components
    )


def _budget_entries(
    value: Sequence[StrengthBudgetEntry],
) -> tuple[StrengthBudgetEntry, ...]:
    entries = tuple(value)
    if len(entries) > STRENGTH_BUDGET_MAX_ENTRIES:
        raise MaintenanceRuleError(f"strength budget may contain at most {STRENGTH_BUDGET_MAX_ENTRIES} entries")
    if any(not isinstance(entry, StrengthBudgetEntry) for entry in entries):
        raise MaintenanceRuleError("strength budget entries must be StrengthBudgetEntry values")
    if any(current.applied_at < previous.applied_at for previous, current in zip(entries, entries[1:])):
        raise MaintenanceRuleError("strength budget entries must be sorted by applied_at ascending")
    return entries


def _decision(
    *,
    allowed: bool,
    strength_increasing: bool,
    controlled_growth_bps: int,
    reasons: tuple[MaintenanceNoActionReason, ...],
    entries: tuple[StrengthBudgetEntry, ...],
    last_strength_increase_at: datetime | None,
    band_spacing_deadline: datetime | None,
    limits: EffectiveGrowthLimits | None,
) -> ControlledActionDecision:
    return ControlledActionDecision(
        allowed=allowed,
        strength_increasing=strength_increasing,
        controlled_growth_bps=controlled_growth_bps,
        skipped_action_reasons=reasons,
        budget_entries_after=entries,
        last_strength_increase_at_after=last_strength_increase_at,
        band_spacing_deadline=band_spacing_deadline,
        effective_limits=limits,
    )


def _ordered_reasons(
    reasons: set[MaintenanceNoActionReason],
) -> tuple[MaintenanceNoActionReason, ...]:
    return tuple(reason for reason in MAINTENANCE_NO_ACTION_REASON_PRIORITY if reason in reasons)


def evaluate_controlled_action(
    *,
    policy: PrestigeBandGrowthPolicy,
    intent: DevelopmentIntent,
    now: datetime,
    last_strength_increase_at: datetime | None,
    budget_entries: Sequence[StrengthBudgetEntry],
    policy_version: int,
    source_sample_count: int,
    source_strength_cap: StrengthSummary,
    target_sample_count: int | None = None,
    target_strength_cap: StrengthSummary | None = None,
    allow_roster_expansion: bool = False,
) -> ControlledActionDecision:
    if not isinstance(policy, PrestigeBandGrowthPolicy):
        raise MaintenanceRuleError("policy must be a PrestigeBandGrowthPolicy")
    if not isinstance(intent, DevelopmentIntent):
        raise MaintenanceRuleError("intent must be a DevelopmentIntent")
    if type(allow_roster_expansion) is not bool:
        raise MaintenanceRuleError("allow_roster_expansion must be a boolean")
    current_time = _aware_utc(now, field="now")
    normalized_policy_version = _positive_int(policy_version, field="policy_version")
    entries = prune_strength_budget_entries(
        _budget_entries(budget_entries),
        now=current_time,
    )
    strength_increasing, controlled_growth_bps = _strength_delta(intent)
    normalized_last = (
        None
        if last_strength_increase_at is None
        else _aware_utc(last_strength_increase_at, field="last_strength_increase_at")
    )
    _sample_limits(source_sample_count)
    if strength_increasing and normalized_last is None:
        raise MaintenanceRuleError("strength-increasing actions require last_strength_increase_at")

    _validate_cap_shape(intent, source_strength_cap, field="source_strength_cap")
    transition_distance = _transition_distance(
        intent.source_prestige_band,
        intent.target_prestige_band,
    )
    if transition_distance > 1:
        if target_sample_count is not None:
            _sample_limits(target_sample_count)
        if target_strength_cap is not None:
            _validate_cap_shape(intent, target_strength_cap, field="target_strength_cap")
        multi_band_reasons = {MaintenanceNoActionReason.MULTI_BAND_TRANSITION}
        if intent.constraint_violations:
            multi_band_reasons.add(MaintenanceNoActionReason.DOMAIN_CONSTRAINT)
        return _decision(
            allowed=False,
            strength_increasing=strength_increasing,
            controlled_growth_bps=controlled_growth_bps,
            reasons=_ordered_reasons(multi_band_reasons),
            entries=entries,
            last_strength_increase_at=normalized_last,
            band_spacing_deadline=None,
            limits=None,
        )

    limits = effective_growth_limits(
        policy=policy,
        source_prestige_band=intent.source_prestige_band,
        target_prestige_band=intent.target_prestige_band,
        source_sample_count=source_sample_count,
        target_sample_count=target_sample_count,
    )
    caps = [source_strength_cap]
    if transition_distance == 1:
        if target_strength_cap is None:
            raise MaintenanceRuleError("cross-band evaluation requires target_strength_cap")
        _validate_cap_shape(intent, target_strength_cap, field="target_strength_cap")
        caps.append(target_strength_cap)

    if not strength_increasing:
        non_growth_reasons = (MaintenanceNoActionReason.DOMAIN_CONSTRAINT,) if intent.constraint_violations else ()
        return _decision(
            allowed=not non_growth_reasons,
            strength_increasing=False,
            controlled_growth_bps=0,
            reasons=non_growth_reasons,
            entries=entries,
            last_strength_increase_at=normalized_last,
            band_spacing_deadline=None,
            limits=limits,
        )

    assert normalized_last is not None
    spacing_deadline = normalized_last + limits.minimum_positive_strength_action_spacing
    usage = strength_budget_usage(entries)
    # Roster expansion is a bounded quantity-phase action: the action spec
    # limits each batch, while the reference component cap remains enforced.
    # Its quality-growth budget is intentionally separate so a low-level bot
    # is not permanently unable to reach the arena roster target.
    exceeds_strength_cap = any(_blocked_by_cap(intent, cap) for cap in caps) or (
        not allow_roster_expansion
        and (
            usage.action_count + 1 > limits.strength_increasing_actions_per_24h_max
            or usage.positive_growth_bps + controlled_growth_bps > limits.composite_growth_bps_per_24h_max
        )
    )
    blocked_reasons: set[MaintenanceNoActionReason] = set()
    if intent.constraint_violations:
        blocked_reasons.add(MaintenanceNoActionReason.DOMAIN_CONSTRAINT)
    if exceeds_strength_cap:
        blocked_reasons.add(MaintenanceNoActionReason.STRENGTH_CAP)
    if not allow_roster_expansion and current_time < spacing_deadline:
        blocked_reasons.add(MaintenanceNoActionReason.BAND_SPACING)
    if not allow_roster_expansion and controlled_growth_bps > limits.composite_growth_bps_per_controlled_action_max:
        blocked_reasons.add(MaintenanceNoActionReason.BAND_ACTION_CAP)
    if blocked_reasons:
        return _decision(
            allowed=False,
            strength_increasing=True,
            controlled_growth_bps=controlled_growth_bps,
            reasons=_ordered_reasons(blocked_reasons),
            entries=entries,
            last_strength_increase_at=normalized_last,
            band_spacing_deadline=spacing_deadline,
            limits=limits,
        )

    consumed = (
        entries
        if allow_roster_expansion
        else consume_strength_budget(
            entries,
            now=current_time,
            positive_growth_bps=controlled_growth_bps,
            policy_version=normalized_policy_version,
            max_actions=limits.strength_increasing_actions_per_24h_max,
            max_positive_growth_bps=limits.composite_growth_bps_per_24h_max,
        )
    )
    return _decision(
        allowed=True,
        strength_increasing=True,
        controlled_growth_bps=controlled_growth_bps,
        reasons=(),
        entries=consumed,
        last_strength_increase_at=current_time,
        band_spacing_deadline=current_time
        + policy.cadence_for(intent.target_prestige_band).minimum_positive_strength_action_spacing,
        limits=limits,
    )


__all__ = [
    "BandGrowthCadence",
    "ControlledActionDecision",
    "EffectiveGrowthLimits",
    "MAINTENANCE_NO_ACTION_REASON_PRIORITY",
    "MaintenanceNoActionReason",
    "MaintenanceRuleError",
    "PrestigeBandGrowthPolicy",
    "bootstrap_historical_age_days",
    "effective_growth_limits",
    "evaluate_controlled_action",
    "next_normal_strength_check_at",
    "parse_prestige_band_growth_policy",
]
