from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

FORCED_SETTLEMENT_CYCLE_CAP_NUMERATOR = 1
FORCED_SETTLEMENT_CYCLE_CAP_DENOMINATOR = 10
FORCED_SETTLEMENT_DAILY_CAP_NUMERATOR = 1
FORCED_SETTLEMENT_DAILY_CAP_DENOMINATOR = 2
FORCED_SETTLEMENT_COMBINED_DAILY_CAP = 2_000_000

_BUDGET_FIELDS = frozenset(
    {
        "utc_date",
        "silver_units",
        "grain_units",
        "combined_units",
        "silver_capacity_snapshot",
        "grain_capacity_snapshot",
    }
)


class ForcedSettlementBudgetError(ValueError):
    pass


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ForcedSettlementBudgetError(f"{field} must be a non-negative integer")
    return value


def _utc_date(value: datetime) -> date:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ForcedSettlementBudgetError("now must be a timezone-aware datetime")
    return value.astimezone(UTC).date()


@dataclass(frozen=True, slots=True)
class ForcedSettlementBudget:
    utc_date: date
    silver_units: int
    grain_units: int
    combined_units: int
    silver_capacity_snapshot: int
    grain_capacity_snapshot: int

    def __post_init__(self) -> None:
        if not isinstance(self.utc_date, date) or isinstance(self.utc_date, datetime):
            raise ForcedSettlementBudgetError("utc_date must be a date")
        for field in (
            "silver_units",
            "grain_units",
            "combined_units",
            "silver_capacity_snapshot",
            "grain_capacity_snapshot",
        ):
            object.__setattr__(self, field, _non_negative_int(getattr(self, field), field=field))
        if self.combined_units != self.silver_units + self.grain_units:
            raise ForcedSettlementBudgetError("combined_units must equal silver_units plus grain_units")
        silver_daily_cap = (
            self.silver_capacity_snapshot
            * FORCED_SETTLEMENT_DAILY_CAP_NUMERATOR
            // FORCED_SETTLEMENT_DAILY_CAP_DENOMINATOR
        )
        grain_daily_cap = (
            self.grain_capacity_snapshot
            * FORCED_SETTLEMENT_DAILY_CAP_NUMERATOR
            // FORCED_SETTLEMENT_DAILY_CAP_DENOMINATOR
        )
        if self.silver_units > silver_daily_cap:
            raise ForcedSettlementBudgetError("silver_units exceeds the frozen daily capacity cap")
        if self.grain_units > grain_daily_cap:
            raise ForcedSettlementBudgetError("grain_units exceeds the frozen daily capacity cap")
        if self.combined_units > FORCED_SETTLEMENT_COMBINED_DAILY_CAP:
            raise ForcedSettlementBudgetError("combined_units exceeds the absolute daily cap")

    def to_payload(self) -> dict[str, int | str]:
        return {
            "utc_date": self.utc_date.isoformat(),
            "silver_units": self.silver_units,
            "grain_units": self.grain_units,
            "combined_units": self.combined_units,
            "silver_capacity_snapshot": self.silver_capacity_snapshot,
            "grain_capacity_snapshot": self.grain_capacity_snapshot,
        }


@dataclass(frozen=True, slots=True)
class ForcedSettlementDecision:
    silver_units: int
    grain_units: int
    budget_before: ForcedSettlementBudget | None
    budget_after: ForcedSettlementBudget | None

    @property
    def combined_units(self) -> int:
        return self.silver_units + self.grain_units


def parse_forced_settlement_budget(value: object) -> ForcedSettlementBudget | None:
    if value == {}:
        return None
    if not isinstance(value, dict):
        raise ForcedSettlementBudgetError("forced_settlement_daily_budget must be a mapping")
    fields = set(value)
    if fields != _BUDGET_FIELDS:
        missing = sorted(_BUDGET_FIELDS - fields)
        unknown = sorted(fields - _BUDGET_FIELDS)
        detail: list[str] = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if unknown:
            detail.append(f"unknown {', '.join(str(item) for item in unknown)}")
        raise ForcedSettlementBudgetError(f"forced_settlement_daily_budget has invalid fields: {'; '.join(detail)}")
    raw_date = value["utc_date"]
    if not isinstance(raw_date, str):
        raise ForcedSettlementBudgetError("utc_date must be an ISO-8601 date")
    try:
        parsed_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ForcedSettlementBudgetError("utc_date must be an ISO-8601 date") from exc
    if parsed_date.isoformat() != raw_date:
        raise ForcedSettlementBudgetError("utc_date must use canonical ISO-8601 date format")
    return ForcedSettlementBudget(
        utc_date=parsed_date,
        silver_units=value["silver_units"],
        grain_units=value["grain_units"],
        combined_units=value["combined_units"],
        silver_capacity_snapshot=value["silver_capacity_snapshot"],
        grain_capacity_snapshot=value["grain_capacity_snapshot"],
    )


def serialize_forced_settlement_budget(budget: ForcedSettlementBudget | None) -> dict[str, int | str]:
    return {} if budget is None else budget.to_payload()


def _cap_by_combined_remaining(silver: int, grain: int, combined_remaining: int) -> tuple[int, int]:
    total = silver + grain
    if total <= combined_remaining:
        return silver, grain
    if total == 0 or combined_remaining == 0:
        return 0, 0

    silver_numerator = silver * combined_remaining
    grain_numerator = grain * combined_remaining
    allocated = {
        "silver": silver_numerator // total,
        "grain": grain_numerator // total,
    }
    remainder = combined_remaining - allocated["silver"] - allocated["grain"]
    if remainder:
        fractions = sorted(
            (
                (silver_numerator % total, "silver"),
                (grain_numerator % total, "grain"),
            ),
            key=lambda item: (-item[0], item[1]),
        )
        for _fraction, resource in fractions[:remainder]:
            allocated[resource] += 1
    return allocated["silver"], allocated["grain"]


def plan_forced_settlement(
    budget: ForcedSettlementBudget | None,
    *,
    now: datetime,
    silver_capacity: int,
    grain_capacity: int,
    requested_silver: int,
    requested_grain: int,
) -> ForcedSettlementDecision:
    current_date = _utc_date(now)
    current_silver_capacity = _non_negative_int(silver_capacity, field="silver_capacity")
    current_grain_capacity = _non_negative_int(grain_capacity, field="grain_capacity")
    desired_silver = _non_negative_int(requested_silver, field="requested_silver")
    desired_grain = _non_negative_int(requested_grain, field="requested_grain")

    active_budget = budget if budget is not None and budget.utc_date == current_date else None
    silver_snapshot = active_budget.silver_capacity_snapshot if active_budget is not None else current_silver_capacity
    grain_snapshot = active_budget.grain_capacity_snapshot if active_budget is not None else current_grain_capacity
    used_silver = active_budget.silver_units if active_budget is not None else 0
    used_grain = active_budget.grain_units if active_budget is not None else 0
    used_combined = active_budget.combined_units if active_budget is not None else 0

    silver_cycle_cap = (
        current_silver_capacity * FORCED_SETTLEMENT_CYCLE_CAP_NUMERATOR // FORCED_SETTLEMENT_CYCLE_CAP_DENOMINATOR
    )
    grain_cycle_cap = (
        current_grain_capacity * FORCED_SETTLEMENT_CYCLE_CAP_NUMERATOR // FORCED_SETTLEMENT_CYCLE_CAP_DENOMINATOR
    )
    silver_daily_remaining = max(
        0,
        silver_snapshot * FORCED_SETTLEMENT_DAILY_CAP_NUMERATOR // FORCED_SETTLEMENT_DAILY_CAP_DENOMINATOR
        - used_silver,
    )
    grain_daily_remaining = max(
        0,
        grain_snapshot * FORCED_SETTLEMENT_DAILY_CAP_NUMERATOR // FORCED_SETTLEMENT_DAILY_CAP_DENOMINATOR - used_grain,
    )
    combined_remaining = max(0, FORCED_SETTLEMENT_COMBINED_DAILY_CAP - used_combined)
    silver = min(desired_silver, silver_cycle_cap, silver_daily_remaining)
    grain = min(desired_grain, grain_cycle_cap, grain_daily_remaining)
    silver, grain = _cap_by_combined_remaining(silver, grain, combined_remaining)

    if silver + grain == 0:
        return ForcedSettlementDecision(
            silver_units=0,
            grain_units=0,
            budget_before=budget,
            budget_after=budget,
        )

    budget_after = ForcedSettlementBudget(
        utc_date=current_date,
        silver_units=used_silver + silver,
        grain_units=used_grain + grain,
        combined_units=used_combined + silver + grain,
        silver_capacity_snapshot=silver_snapshot,
        grain_capacity_snapshot=grain_snapshot,
    )
    return ForcedSettlementDecision(
        silver_units=silver,
        grain_units=grain,
        budget_before=budget,
        budget_after=budget_after,
    )


__all__ = [
    "FORCED_SETTLEMENT_COMBINED_DAILY_CAP",
    "FORCED_SETTLEMENT_CYCLE_CAP_DENOMINATOR",
    "FORCED_SETTLEMENT_CYCLE_CAP_NUMERATOR",
    "FORCED_SETTLEMENT_DAILY_CAP_DENOMINATOR",
    "FORCED_SETTLEMENT_DAILY_CAP_NUMERATOR",
    "ForcedSettlementBudget",
    "ForcedSettlementBudgetError",
    "ForcedSettlementDecision",
    "parse_forced_settlement_budget",
    "plan_forced_settlement",
    "serialize_forced_settlement_budget",
]
