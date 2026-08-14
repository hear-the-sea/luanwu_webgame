from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Final

from .contracts import calculate_positive_growth_bps
from .projection import PRESTIGE_BANDS, DevelopmentIntent
from .random_context import RandomContext


class MaintenanceRuleError(ValueError):
    pass


GUEST_COUNT_TARGET_MAX = 12


def guest_count_target_for_profile(
    *,
    starter_guest_count: int,
    growth_stage: int,
    roster_focus: float,
) -> int:
    """Return a bounded ordinary-cultivation roster target."""

    if isinstance(starter_guest_count, bool) or not isinstance(starter_guest_count, int) or starter_guest_count < 0:
        raise MaintenanceRuleError("starter_guest_count must be a non-negative integer")
    if isinstance(growth_stage, bool) or not isinstance(growth_stage, int) or growth_stage < 1:
        raise MaintenanceRuleError("growth_stage must be a positive integer")
    if isinstance(roster_focus, bool) or not isinstance(roster_focus, (int, float)):
        raise MaintenanceRuleError("roster_focus must be a finite number between 0 and 1")
    normalized_focus = float(roster_focus)
    if not math.isfinite(normalized_focus) or not 0 <= normalized_focus <= 1:
        raise MaintenanceRuleError("roster_focus must be a finite number between 0 and 1")
    if starter_guest_count == 0:
        return 0
    focus_bonus = max(1, min(3, round(normalized_focus * 2)))
    stage_bonus = min(3, max(0, (growth_stage - 1) // 3))
    return min(GUEST_COUNT_TARGET_MAX, starter_guest_count + focus_bonus + stage_bonus)


class MaintenanceNoActionReason(str, Enum):
    DOMAIN_CONSTRAINT = "domain_constraint"
    MULTI_BAND_TRANSITION = "multi_band_transition"


MAINTENANCE_NO_ACTION_REASON_PRIORITY: Final[tuple[MaintenanceNoActionReason, ...]] = (
    MaintenanceNoActionReason.DOMAIN_CONSTRAINT,
    MaintenanceNoActionReason.MULTI_BAND_TRANSITION,
)


_GROWTH_BASE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "direct_prestige_grant_by_maintenance_allowed",
        "profiles",
        "configured_boundaries_crossed_per_controlled_action_max",
        "external_domain_result_may_be_rejected_by_bot_growth_policy",
        "bootstrap_fake_per_action_history_records",
    }
)
_GROWTH_FIELDS: Final[frozenset[str]] = frozenset({*_GROWTH_BASE_FIELDS, "arena_acceleration_bypass"})
_ARENA_BYPASS_FIELDS: Final[frozenset[str]] = frozenset({"due"})
_PROFILE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "bootstrap_history_age_days",
        "preferred_strength_check_interval_hours",
    }
)
_EXPECTED_GROWTH_LITERALS: Final[Mapping[str, object]] = MappingProxyType(
    {
        "direct_prestige_grant_by_maintenance_allowed": False,
        "configured_boundaries_crossed_per_controlled_action_max": 1,
        "external_domain_result_may_be_rejected_by_bot_growth_policy": False,
        "bootstrap_fake_per_action_history_records": False,
    }
)


@dataclass(frozen=True, slots=True)
class BandGrowthCadence:
    prestige_band: str
    bootstrap_history_age_days: tuple[int, int]
    preferred_strength_check_interval: tuple[timedelta, timedelta]


@dataclass(frozen=True, slots=True)
class ArenaAccelerationBypassPolicy:
    """Whether an arena event may trigger an immediate maintenance attempt."""

    due: bool


@dataclass(frozen=True, slots=True)
class PrestigeBandGrowthPolicy:
    profiles: Mapping[str, BandGrowthCadence]
    arena_acceleration_bypass: ArenaAccelerationBypassPolicy

    def cadence_for(self, prestige_band: str) -> BandGrowthCadence:
        try:
            return self.profiles[prestige_band]
        except KeyError as exc:
            raise MaintenanceRuleError(f"unknown prestige band: {prestige_band!r}") from exc


@dataclass(frozen=True, slots=True)
class ControlledActionDecision:
    allowed: bool
    strength_increasing: bool
    controlled_growth_bps: int
    skipped_action_reasons: tuple[MaintenanceNoActionReason, ...]
    last_strength_increase_at_after: datetime | None

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


def _strict_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise MaintenanceRuleError(f"{field} must be a boolean")
    return value


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
    _require_exact_fields(
        growth,
        expected=_GROWTH_FIELDS,
        field="prestige_band_growth",
    )
    _validate_growth_literals(growth)

    raw_bypass = _mapping(
        growth["arena_acceleration_bypass"],
        field="prestige_band_growth.arena_acceleration_bypass",
    )
    _require_exact_fields(
        raw_bypass,
        expected=_ARENA_BYPASS_FIELDS,
        field="prestige_band_growth.arena_acceleration_bypass",
    )
    bypass = ArenaAccelerationBypassPolicy(
        due=_strict_bool(
            raw_bypass["due"],
            field="prestige_band_growth.arena_acceleration_bypass.due",
        )
    )

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
        cadence = BandGrowthCadence(
            prestige_band=prestige_band,
            bootstrap_history_age_days=history,
            preferred_strength_check_interval=interval,
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
        parsed[prestige_band] = cadence
        previous = cadence

    return PrestigeBandGrowthPolicy(
        profiles=MappingProxyType(parsed),
        arena_acceleration_bypass=bypass,
    )


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


def _decision(
    *,
    allowed: bool,
    strength_increasing: bool,
    controlled_growth_bps: int,
    reasons: tuple[MaintenanceNoActionReason, ...],
    last_strength_increase_at: datetime | None,
) -> ControlledActionDecision:
    return ControlledActionDecision(
        allowed=allowed,
        strength_increasing=strength_increasing,
        controlled_growth_bps=controlled_growth_bps,
        skipped_action_reasons=reasons,
        last_strength_increase_at_after=last_strength_increase_at,
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
) -> ControlledActionDecision:
    if not isinstance(policy, PrestigeBandGrowthPolicy):
        raise MaintenanceRuleError("policy must be a PrestigeBandGrowthPolicy")
    if not isinstance(intent, DevelopmentIntent):
        raise MaintenanceRuleError("intent must be a DevelopmentIntent")
    current_time = _aware_utc(now, field="now")
    strength_increasing, controlled_growth_bps = _strength_delta(intent)
    normalized_last = (
        None
        if last_strength_increase_at is None
        else _aware_utc(last_strength_increase_at, field="last_strength_increase_at")
    )
    transition_distance = _transition_distance(
        intent.source_prestige_band,
        intent.target_prestige_band,
    )
    if transition_distance > 1:
        multi_band_reasons = {MaintenanceNoActionReason.MULTI_BAND_TRANSITION}
        if intent.constraint_violations:
            multi_band_reasons.add(MaintenanceNoActionReason.DOMAIN_CONSTRAINT)
        return _decision(
            allowed=False,
            strength_increasing=strength_increasing,
            controlled_growth_bps=controlled_growth_bps,
            reasons=_ordered_reasons(multi_band_reasons),
            last_strength_increase_at=normalized_last,
        )

    if not strength_increasing:
        non_growth_reasons = (MaintenanceNoActionReason.DOMAIN_CONSTRAINT,) if intent.constraint_violations else ()
        return _decision(
            allowed=not non_growth_reasons,
            strength_increasing=False,
            controlled_growth_bps=0,
            reasons=non_growth_reasons,
            last_strength_increase_at=normalized_last,
        )

    if intent.constraint_violations:
        return _decision(
            allowed=False,
            strength_increasing=True,
            controlled_growth_bps=controlled_growth_bps,
            reasons=(MaintenanceNoActionReason.DOMAIN_CONSTRAINT,),
            last_strength_increase_at=normalized_last,
        )

    # Strength growth is intentionally unbounded here.  Reference snapshots,
    # daily budgets, per-action growth limits, and cadence guards are not
    # execution constraints; the scheduler still controls when this evaluator
    # is called, while domain services remain responsible for legal execution.
    return _decision(
        allowed=True,
        strength_increasing=True,
        controlled_growth_bps=controlled_growth_bps,
        reasons=(),
        last_strength_increase_at=current_time,
    )


__all__ = [
    "ArenaAccelerationBypassPolicy",
    "BandGrowthCadence",
    "ControlledActionDecision",
    "GUEST_COUNT_TARGET_MAX",
    "MAINTENANCE_NO_ACTION_REASON_PRIORITY",
    "MaintenanceNoActionReason",
    "MaintenanceRuleError",
    "PrestigeBandGrowthPolicy",
    "bootstrap_historical_age_days",
    "evaluate_controlled_action",
    "guest_count_target_for_profile",
    "next_normal_strength_check_at",
    "parse_prestige_band_growth_policy",
]
