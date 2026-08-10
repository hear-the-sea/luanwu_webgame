from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class BotProjectionConfig:
    prestige: int
    building_level: int
    guest_count: int
    guest_level: int
    troop_count: int = 50


class PopulationMutationStatus(str, Enum):
    CREATED = "created"
    REACTIVATED = "reactivated"
    CAP_REACHED = "cap_reached"
    UNAVAILABLE = "unavailable"


class AcceleratedGrowthOutcome(str, Enum):
    GROWN = "grown"
    BUSY = "busy"
    INELIGIBLE = "ineligible"
    NO_ACTION = "no_action"
    PAUSED = "paused"


class MaintenanceTrigger(str, Enum):
    SCHEDULED = "scheduled"
    ARENA_ACCELERATION = "arena_acceleration"
    ADMIN = "admin"


class MaintenanceScheduleDisposition(str, Enum):
    ADVANCE_NORMAL_SCHEDULE = "advance_normal_schedule"
    PRESERVE_NORMAL_SCHEDULE = "preserve_normal_schedule"


class MaintenanceOutcome(str, Enum):
    APPLIED = "applied"
    NO_ACTION = "no_action"
    BUSY = "busy"
    PAUSED = "paused"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class ArenaGrowthObjective:
    """Pure arena target consumed by maintenance without arena ORM access."""

    critical_guest_count: int
    preferred_guest_count: int
    selected_power_lower_bound: int
    selected_power_upper_bound: int
    selected_power_before: int
    target_team_power: int
    lineup_mode: str
    lineup_event_id: int
    lineup_max_size: int
    minimum_guest_level: int
    recruitment_rarity_cap: str | None
    max_guest_level_step: int

    def __post_init__(self) -> None:
        for field_name in (
            "critical_guest_count",
            "preferred_guest_count",
            "selected_power_lower_bound",
            "selected_power_upper_bound",
            "selected_power_before",
            "target_team_power",
            "lineup_event_id",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        for field_name in ("lineup_max_size", "minimum_guest_level", "max_guest_level_step"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.preferred_guest_count < self.critical_guest_count:
            raise ValueError("preferred_guest_count must not be below critical_guest_count")
        if self.selected_power_upper_bound < self.selected_power_lower_bound:
            raise ValueError("selected power upper bound must not be below the lower bound")
        if self.target_team_power < 1:
            raise ValueError("target_team_power must be positive")
        if self.lineup_mode not in {"tournament", "coop"}:
            raise ValueError("lineup_mode must be tournament or coop")
        if self.lineup_event_id < 1:
            raise ValueError("lineup_event_id must be positive")
        if self.recruitment_rarity_cap is not None and (
            not isinstance(self.recruitment_rarity_cap, str) or not self.recruitment_rarity_cap.strip()
        ):
            raise ValueError("recruitment_rarity_cap must be a non-empty string or None")

    @property
    def selected_lineup_gap(self) -> int:
        return max(0, self.selected_power_lower_bound - self.selected_power_before)

    def to_payload(self) -> dict[str, int | str | None]:
        return {
            "critical_guest_count": self.critical_guest_count,
            "preferred_guest_count": self.preferred_guest_count,
            "selected_power_lower_bound": self.selected_power_lower_bound,
            "selected_power_upper_bound": self.selected_power_upper_bound,
            "selected_power_before": self.selected_power_before,
            "target_team_power": self.target_team_power,
            "lineup_mode": self.lineup_mode,
            "lineup_event_id": self.lineup_event_id,
            "lineup_max_size": self.lineup_max_size,
            "minimum_guest_level": self.minimum_guest_level,
            "recruitment_rarity_cap": self.recruitment_rarity_cap,
            "max_guest_level_step": self.max_guest_level_step,
        }

    @classmethod
    def from_payload(cls, value: object) -> "ArenaGrowthObjective":
        """Parse the exact persisted arena-growth request schema."""

        expected_fields = {
            "critical_guest_count",
            "preferred_guest_count",
            "selected_power_lower_bound",
            "selected_power_upper_bound",
            "selected_power_before",
            "target_team_power",
            "lineup_mode",
            "lineup_event_id",
            "lineup_max_size",
            "minimum_guest_level",
            "recruitment_rarity_cap",
            "max_guest_level_step",
        }
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise ValueError("arena growth objective payload must contain exactly the objective fields")
        return cls(**value)


STRENGTH_BUDGET_WINDOW = timedelta(hours=24)
STRENGTH_BUDGET_MAX_ENTRIES = 4
STRENGTH_BUDGET_MAX_FUTURE_SKEW = timedelta(minutes=5)


class StrengthBudgetError(ValueError):
    pass


class InvalidStrengthBudgetError(StrengthBudgetError):
    pass


class StrengthBudgetExceededError(StrengthBudgetError):
    pass


def _finite_non_negative_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise InvalidStrengthBudgetError(f"{field} must be a finite non-negative number")
    try:
        normalized = Decimal(str(value))
    except InvalidOperation as exc:
        raise InvalidStrengthBudgetError(f"{field} must be a finite non-negative number") from exc
    if not normalized.is_finite() or normalized < 0:
        raise InvalidStrengthBudgetError(f"{field} must be a finite non-negative number")
    return normalized


def calculate_positive_growth_bps(
    *,
    pre_score: object,
    post_score: object,
    score_floor: object = 1,
) -> int:
    """Return the positive composite delta in basis points, rounded up."""

    before = _finite_non_negative_decimal(pre_score, field="pre_score")
    after = _finite_non_negative_decimal(post_score, field="post_score")
    floor = _finite_non_negative_decimal(score_floor, field="score_floor")
    if floor <= 0:
        raise InvalidStrengthBudgetError("score_floor must be positive")
    if after <= before:
        return 0
    growth_bps = ((after - before) / max(before, floor)) * Decimal(10_000)
    return int(growth_bps.to_integral_value(rounding=ROUND_CEILING))


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidStrengthBudgetError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class StrengthBudgetEntry:
    applied_at: datetime
    positive_growth_bps: int
    policy_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "applied_at", _aware_utc(self.applied_at, field="applied_at"))
        if isinstance(self.positive_growth_bps, bool) or not isinstance(self.positive_growth_bps, int):
            raise InvalidStrengthBudgetError("positive_growth_bps must be an integer")
        if self.positive_growth_bps < 0:
            raise InvalidStrengthBudgetError("positive_growth_bps must be non-negative")
        if isinstance(self.policy_version, bool) or not isinstance(self.policy_version, int):
            raise InvalidStrengthBudgetError("policy_version must be an integer")
        if self.policy_version < 1:
            raise InvalidStrengthBudgetError("policy_version must be positive")

    def to_payload(self) -> dict[str, int | str]:
        return {
            "applied_at": self.applied_at.isoformat().replace("+00:00", "Z"),
            "positive_growth_bps": self.positive_growth_bps,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class StrengthBudgetUsage:
    action_count: int
    positive_growth_bps: int


def _parse_budget_datetime(value: object, *, index: int) -> datetime:
    if not isinstance(value, str) or not value:
        raise InvalidStrengthBudgetError(f"strength_budget_entries[{index}].applied_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidStrengthBudgetError(
            f"strength_budget_entries[{index}].applied_at must be an ISO-8601 datetime"
        ) from exc
    return _aware_utc(parsed, field=f"strength_budget_entries[{index}].applied_at")


def parse_strength_budget_entries(
    value: object,
    *,
    now: datetime,
    max_future_skew: timedelta = STRENGTH_BUDGET_MAX_FUTURE_SKEW,
) -> tuple[StrengthBudgetEntry, ...]:
    current_time = _aware_utc(now, field="now")
    if max_future_skew < timedelta(0):
        raise InvalidStrengthBudgetError("max_future_skew must be non-negative")
    if not isinstance(value, list):
        raise InvalidStrengthBudgetError("strength_budget_entries must be a list")
    if len(value) > STRENGTH_BUDGET_MAX_ENTRIES:
        raise InvalidStrengthBudgetError(
            f"strength_budget_entries may contain at most {STRENGTH_BUDGET_MAX_ENTRIES} entries"
        )
    entries: list[StrengthBudgetEntry] = []
    expected_fields = {"applied_at", "positive_growth_bps", "policy_version"}
    for index, raw_entry in enumerate(value):
        if not isinstance(raw_entry, dict):
            raise InvalidStrengthBudgetError(f"strength_budget_entries[{index}] must be a mapping")
        fields = set(raw_entry)
        if fields != expected_fields:
            missing = sorted(expected_fields - fields)
            unknown = sorted(fields - expected_fields)
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if unknown:
                detail.append(f"unknown {', '.join(str(item) for item in unknown)}")
            raise InvalidStrengthBudgetError(
                f"strength_budget_entries[{index}] has invalid fields: {'; '.join(detail)}"
            )
        entry = StrengthBudgetEntry(
            applied_at=_parse_budget_datetime(raw_entry["applied_at"], index=index),
            positive_growth_bps=raw_entry["positive_growth_bps"],
            policy_version=raw_entry["policy_version"],
        )
        if entries and entry.applied_at < entries[-1].applied_at:
            raise InvalidStrengthBudgetError("strength_budget_entries must be sorted by applied_at ascending")
        if entry.applied_at > current_time + max_future_skew:
            raise InvalidStrengthBudgetError("strength_budget_entries contains an applied_at beyond allowed clock skew")
        entries.append(entry)
    return tuple(entries)


def serialize_strength_budget_entries(
    entries: tuple[StrengthBudgetEntry, ...],
) -> list[dict[str, int | str]]:
    return [entry.to_payload() for entry in entries]


def prune_strength_budget_entries(
    entries: tuple[StrengthBudgetEntry, ...],
    *,
    now: datetime,
    window: timedelta = STRENGTH_BUDGET_WINDOW,
) -> tuple[StrengthBudgetEntry, ...]:
    current_time = _aware_utc(now, field="now")
    if window <= timedelta(0):
        raise InvalidStrengthBudgetError("strength budget window must be positive")
    cutoff = current_time - window
    return tuple(entry for entry in entries if entry.applied_at > cutoff)


def strength_budget_usage(
    entries: tuple[StrengthBudgetEntry, ...],
) -> StrengthBudgetUsage:
    return StrengthBudgetUsage(
        action_count=len(entries),
        positive_growth_bps=sum(entry.positive_growth_bps for entry in entries),
    )


def consume_strength_budget(
    entries: tuple[StrengthBudgetEntry, ...],
    *,
    now: datetime,
    positive_growth_bps: int,
    policy_version: int,
    max_actions: int,
    max_positive_growth_bps: int,
) -> tuple[StrengthBudgetEntry, ...]:
    if isinstance(max_actions, bool) or not isinstance(max_actions, int) or not 0 <= max_actions <= 4:
        raise InvalidStrengthBudgetError("max_actions must be an integer between 0 and 4")
    if (
        isinstance(max_positive_growth_bps, bool)
        or not isinstance(max_positive_growth_bps, int)
        or max_positive_growth_bps < 0
    ):
        raise InvalidStrengthBudgetError("max_positive_growth_bps must be a non-negative integer")
    candidate = StrengthBudgetEntry(
        applied_at=now,
        positive_growth_bps=positive_growth_bps,
        policy_version=policy_version,
    )
    active_entries = prune_strength_budget_entries(entries, now=now)
    usage = strength_budget_usage(active_entries)
    if usage.action_count + 1 > max_actions:
        raise StrengthBudgetExceededError("strength action count budget exceeded")
    if usage.positive_growth_bps + candidate.positive_growth_bps > max_positive_growth_bps:
        raise StrengthBudgetExceededError("positive strength growth budget exceeded")
    if active_entries and candidate.applied_at < active_entries[-1].applied_at:
        raise InvalidStrengthBudgetError("new strength budget entry predates the latest active entry")
    return (*active_entries, candidate)


@dataclass(frozen=True, slots=True)
class MaintenanceTriggerPolicy:
    trigger: MaintenanceTrigger
    requires_due: bool
    schedule_disposition: MaintenanceScheduleDisposition
    sequence_advancing_outcomes: frozenset[MaintenanceOutcome] = frozenset(
        {MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION}
    )

    def __post_init__(self) -> None:
        trigger = MaintenanceTrigger(self.trigger)
        disposition = MaintenanceScheduleDisposition(self.schedule_disposition)
        if type(self.requires_due) is not bool:
            raise ValueError("requires_due must be a boolean")
        outcomes = frozenset(MaintenanceOutcome(outcome) for outcome in self.sequence_advancing_outcomes)
        expected_outcomes = frozenset({MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION})
        if outcomes != expected_outcomes:
            raise ValueError("only APPLIED and NO_ACTION may advance maintenance sequence")
        if trigger is MaintenanceTrigger.SCHEDULED and (
            not self.requires_due or disposition is not MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE
        ):
            raise ValueError("scheduled maintenance must require due and advance the normal schedule")
        if trigger is MaintenanceTrigger.ARENA_ACCELERATION and (
            self.requires_due or disposition is not MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE
        ):
            raise ValueError("arena acceleration must ignore due and preserve the normal schedule")
        object.__setattr__(self, "trigger", trigger)
        object.__setattr__(self, "schedule_disposition", disposition)
        object.__setattr__(self, "sequence_advancing_outcomes", outcomes)

    def is_due(
        self,
        *,
        next_growth_at: datetime | None,
        now: datetime,
        arena_bypass_due: bool | None = None,
    ) -> bool:
        if arena_bypass_due is not None:
            if self.trigger is not MaintenanceTrigger.ARENA_ACCELERATION:
                raise ValueError("arena_bypass_due is only valid for arena acceleration")
            if type(arena_bypass_due) is not bool:
                raise ValueError("arena_bypass_due must be a boolean")
            if arena_bypass_due:
                return True
            return next_growth_at is not None and next_growth_at <= now
        if not self.requires_due:
            return True
        return next_growth_at is not None and next_growth_at <= now

    def advances_sequence(self, outcome: MaintenanceOutcome) -> bool:
        return MaintenanceOutcome(outcome) in self.sequence_advancing_outcomes


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    outcome: MaintenanceOutcome
    trigger: MaintenanceTrigger
    profile_id: int
    sequence_before: int
    sequence_after: int
    schedule_disposition: MaintenanceScheduleDisposition
    next_growth_at_before: datetime | None
    next_growth_at_after: datetime | None
    action_kind: str = ""
    reason: str = ""
    shadow_cost: Mapping[str, int] = field(default_factory=dict)
    target_id: int | None = None
    scheduled_cycle_slot_due: bool = False

    def __post_init__(self) -> None:
        outcome = MaintenanceOutcome(self.outcome)
        trigger = MaintenanceTrigger(self.trigger)
        disposition = MaintenanceScheduleDisposition(self.schedule_disposition)
        if isinstance(self.profile_id, bool) or not isinstance(self.profile_id, int) or self.profile_id < 1:
            raise ValueError("profile_id must be a positive integer")
        for field_name in ("sequence_before", "sequence_after"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        before = (
            None
            if self.next_growth_at_before is None
            else _aware_utc(self.next_growth_at_before, field="next_growth_at_before")
        )
        after = (
            None
            if self.next_growth_at_after is None
            else _aware_utc(self.next_growth_at_after, field="next_growth_at_after")
        )
        committed = outcome in {
            MaintenanceOutcome.APPLIED,
            MaintenanceOutcome.NO_ACTION,
        }
        if type(self.scheduled_cycle_slot_due) is not bool:
            raise ValueError("scheduled_cycle_slot_due must be a boolean")
        expected_sequence_after = self.sequence_before + int(committed)
        if self.sequence_after != expected_sequence_after:
            raise ValueError("maintenance sequence must advance exactly once only for APPLIED or NO_ACTION")
        if outcome is MaintenanceOutcome.BUSY and after != before:
            raise ValueError("BUSY maintenance must retain next_growth_at exactly")
        if committed:
            if trigger is MaintenanceTrigger.SCHEDULED:
                if disposition is not MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE:
                    raise ValueError("scheduled committed maintenance must use advance_normal_schedule")
                if before is None or after is None or (not self.scheduled_cycle_slot_due and after <= before):
                    raise ValueError(
                        "scheduled committed maintenance must move next_growth_at "
                        "forward from a non-null due deadline"
                    )
            elif trigger is MaintenanceTrigger.ARENA_ACCELERATION:
                if disposition is not MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE:
                    raise ValueError("arena committed maintenance must use preserve_normal_schedule")
                if after != before:
                    raise ValueError("preserve_normal_schedule must retain next_growth_at exactly")
            elif disposition is MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE:
                if after != before:
                    raise ValueError("preserve_normal_schedule must retain next_growth_at exactly")
            elif after is None or after == before:
                raise ValueError(
                    "admin advance_normal_schedule must replace next_growth_at " "with a non-null different value"
                )
        if not isinstance(self.action_kind, str) or not isinstance(self.reason, str):
            raise ValueError("action_kind and reason must be strings")
        if self.target_id is not None and (
            isinstance(self.target_id, bool) or not isinstance(self.target_id, int) or self.target_id < 1
        ):
            raise ValueError("target_id must be a positive integer or None")
        if not isinstance(self.shadow_cost, Mapping):
            raise ValueError("shadow_cost must be a mapping")
        normalized_shadow_cost: dict[str, int] = {}
        for raw_key, raw_value in self.shadow_cost.items():
            key = str(raw_key).strip()
            if not key or isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
                raise ValueError("shadow_cost must contain non-negative integer values")
            normalized_shadow_cost[key] = int(raw_value)
        if outcome is MaintenanceOutcome.APPLIED:
            if not self.action_kind.strip():
                raise ValueError("APPLIED maintenance requires a non-empty action_kind")
            if self.reason:
                raise ValueError("APPLIED maintenance must not include a reason")
        elif outcome is MaintenanceOutcome.NO_ACTION:
            if not self.reason.strip():
                raise ValueError("NO_ACTION maintenance requires a non-empty reason")
        else:
            if self.action_kind:
                raise ValueError("uncommitted maintenance must not include an action_kind")
            if not self.reason.strip():
                raise ValueError("non-APPLIED maintenance requires a non-empty reason")
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "trigger", trigger)
        object.__setattr__(self, "schedule_disposition", disposition)
        object.__setattr__(self, "next_growth_at_before", before)
        object.__setattr__(self, "next_growth_at_after", after)
        object.__setattr__(self, "shadow_cost", MappingProxyType(normalized_shadow_cost))


@dataclass(frozen=True, slots=True)
class BotLootClampDecision:
    """Read-only loot result plus an explicit post-commit lifecycle suggestion."""

    resources: Mapping[str, int]
    bot_profile_id: int | None = None
    bot_budget_exhausted: bool = False
    retirement_recommended: bool = False

    def __post_init__(self) -> None:
        normalized: dict[str, int] = {}
        for key, value in self.resources.items():
            amount = max(0, int(value or 0))
            if amount > 0:
                normalized[str(key)] = amount
        object.__setattr__(self, "resources", MappingProxyType(normalized))

        profile_id = self.bot_profile_id
        if profile_id is not None:
            object.__setattr__(self, "bot_profile_id", int(profile_id))
        if self.retirement_recommended and profile_id is None:
            raise ValueError("retirement recommendation requires a BotProfile id")


def maintenance_trigger_policy(
    trigger: MaintenanceTrigger,
    *,
    admin_requires_due: bool | None = None,
    admin_schedule_disposition: MaintenanceScheduleDisposition | None = None,
) -> MaintenanceTriggerPolicy:
    normalized_trigger = MaintenanceTrigger(trigger)
    if normalized_trigger is MaintenanceTrigger.SCHEDULED:
        if admin_requires_due is not None or admin_schedule_disposition is not None:
            raise ValueError("admin trigger semantics are not valid for scheduled maintenance")
        return MaintenanceTriggerPolicy(
            trigger=normalized_trigger,
            requires_due=True,
            schedule_disposition=MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE,
        )
    if normalized_trigger is MaintenanceTrigger.ARENA_ACCELERATION:
        if admin_requires_due is not None or admin_schedule_disposition is not None:
            raise ValueError("admin trigger semantics are not valid for arena acceleration")
        return MaintenanceTriggerPolicy(
            trigger=normalized_trigger,
            requires_due=False,
            schedule_disposition=MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
        )
    if admin_requires_due is None or admin_schedule_disposition is None:
        raise ValueError("admin maintenance requires explicit due and schedule disposition semantics")
    if type(admin_requires_due) is not bool:
        raise ValueError("admin maintenance requires_due must be a boolean")
    return MaintenanceTriggerPolicy(
        trigger=normalized_trigger,
        requires_due=admin_requires_due,
        schedule_disposition=MaintenanceScheduleDisposition(admin_schedule_disposition),
    )


__all__ = [
    "AcceleratedGrowthOutcome",
    "ArenaGrowthObjective",
    "BotLootClampDecision",
    "BotProjectionConfig",
    "MaintenanceOutcome",
    "MaintenanceResult",
    "MaintenanceScheduleDisposition",
    "MaintenanceTrigger",
    "MaintenanceTriggerPolicy",
    "PopulationMutationStatus",
    "STRENGTH_BUDGET_MAX_ENTRIES",
    "STRENGTH_BUDGET_MAX_FUTURE_SKEW",
    "STRENGTH_BUDGET_WINDOW",
    "InvalidStrengthBudgetError",
    "StrengthBudgetEntry",
    "StrengthBudgetError",
    "StrengthBudgetExceededError",
    "StrengthBudgetUsage",
    "calculate_positive_growth_bps",
    "consume_strength_budget",
    "maintenance_trigger_policy",
    "parse_strength_budget_entries",
    "prune_strength_budget_entries",
    "serialize_strength_budget_entries",
    "strength_budget_usage",
]
