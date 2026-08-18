from __future__ import annotations

import math
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from core.utils.time_scale import get_time_scale
from gameplay.models import Manor, ResourceType
from gameplay.services.resources import ResourceProductionBasis, preview_resource_production
from guests.models import Guest
from guests.services.salary import DEFAULT_SALARY_SCALE, SalaryBatchQuote, SalaryScale, quote_all_salaries

from .economy import ForcedSettlementBudget, ForcedSettlementDecision, plan_forced_settlement


class ResourcePlanningError(ValueError):
    pass


_SUPPORTED_RESOURCES = frozenset({ResourceType.SILVER, ResourceType.GRAIN})
_OPERATING_BUFFER_SALARY_NUMERATOR = 1
_OPERATING_BUFFER_SALARY_DENOMINATOR = 10
_SILVER_FORECAST_HORIZON_24_HOURS = 24
_SILVER_FORECAST_HORIZON_72_HOURS = 72
VIRTUAL_PLAYER_SALARY_SCALE: SalaryScale = (1, 2)


def salary_runway_commitment(additional_daily_salary: int) -> int:
    """Return one next-day salary plus the configured operating buffer."""

    if isinstance(additional_daily_salary, bool) or not isinstance(additional_daily_salary, int):
        raise ResourcePlanningError("additional_daily_salary must be an integer")
    if additional_daily_salary < 0:
        raise ResourcePlanningError("additional_daily_salary must be non-negative")
    operating_buffer = (
        additional_daily_salary * _OPERATING_BUFFER_SALARY_NUMERATOR + _OPERATING_BUFFER_SALARY_DENOMINATOR - 1
    ) // _OPERATING_BUFFER_SALARY_DENOMINATOR
    return additional_daily_salary + operating_buffer


def _canonical_resources(
    values: Mapping[str, int] | Sequence[tuple[str, int]],
    *,
    field: str,
    allow_negative: bool = False,
) -> tuple[tuple[str, int], ...]:
    rows = values.items() if isinstance(values, Mapping) else values
    normalized: dict[str, int] = {}
    for resource, amount in rows:
        key = str(resource).strip()
        if key not in _SUPPORTED_RESOURCES:
            raise ResourcePlanningError(f"{field} contains an unsupported resource: {key!r}")
        if key in normalized:
            raise ResourcePlanningError(f"{field} contains a duplicate resource: {key}")
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise ResourcePlanningError(f"{field}[{key!r}] must be an integer")
        if amount < 0 and not allow_negative:
            raise ResourcePlanningError(f"{field}[{key!r}] must be non-negative")
        normalized[key] = amount
    return tuple(sorted(normalized.items()))


def _canonical_production_rates(
    values: Mapping[str, float] | Sequence[tuple[str, float]],
) -> tuple[tuple[str, float], ...]:
    rows = values.items() if isinstance(values, Mapping) else values
    normalized: dict[str, float] = {}
    for resource, amount in rows:
        key = str(resource).strip()
        if key not in _SUPPORTED_RESOURCES:
            raise ResourcePlanningError(f"production_rates contains an unsupported resource: {key!r}")
        if key in normalized:
            raise ResourcePlanningError(f"production_rates contains a duplicate resource: {key}")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ResourcePlanningError(f"production_rates[{key!r}] must be a number")
        normalized_amount = float(amount)
        if not math.isfinite(normalized_amount):
            raise ResourcePlanningError(f"production_rates[{key!r}] must be finite")
        normalized[key] = normalized_amount
    return tuple(sorted(normalized.items()))


def _forecast_resource(
    *,
    current: int,
    capacity: int,
    hourly_rate: float,
    horizon_hours: int,
    obligations: int = 0,
) -> int:
    # Rates are net rates (for example, grain production minus personnel
    # upkeep), so a negative rate must be allowed to drain the forecast.
    projected = current + math.floor(hourly_rate * horizon_hours)
    return max(0, min(max(0, capacity), projected) - max(0, obligations))


@dataclass(frozen=True, slots=True)
class ResourcePlanningSnapshot:
    """Frozen resource ledger used by every maintenance candidate."""

    current_resources: tuple[tuple[str, int], ...]
    production_deltas: tuple[tuple[str, int], ...]
    post_settlement_resources: tuple[tuple[str, int], ...]
    current_salary_quote: SalaryBatchQuote
    current_salary_payable: bool
    post_salary_resources: tuple[tuple[str, int], ...]
    next_day_salary_quote: SalaryBatchQuote
    operating_buffer: tuple[tuple[str, int], ...]
    protected_resources: tuple[tuple[str, int], ...]
    spendable_resources: tuple[tuple[str, int], ...]
    production_rates: tuple[tuple[str, float], ...] = ()
    silver_forecast_24h: int = 0
    silver_forecast_72h: int = 0
    grain_forecast_24h: int = 0
    grain_forecast_72h: int = 0
    recurring_silver_outflow_daily: int = 0
    salary_enabled: bool = True

    def __post_init__(self) -> None:
        for field in (
            "current_resources",
            "post_settlement_resources",
            "post_salary_resources",
            "operating_buffer",
            "protected_resources",
            "spendable_resources",
        ):
            value = getattr(self, field)
            if _canonical_resources(value, field=field) != value:
                raise ResourcePlanningError(f"{field} must use canonical resource order")
        if (
            _canonical_resources(
                self.production_deltas,
                field="production_deltas",
                allow_negative=True,
            )
            != self.production_deltas
        ):
            raise ResourcePlanningError("production_deltas must use canonical resource order")
        if _canonical_production_rates(self.production_rates) != self.production_rates:
            raise ResourcePlanningError("production_rates must use canonical resource order")
        for field in (
            "silver_forecast_24h",
            "silver_forecast_72h",
            "grain_forecast_24h",
            "grain_forecast_72h",
            "recurring_silver_outflow_daily",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ResourcePlanningError(f"{field} must be a non-negative integer")
        if not isinstance(self.current_salary_quote, SalaryBatchQuote):
            raise ResourcePlanningError("current_salary_quote must be a SalaryBatchQuote")
        if not isinstance(self.next_day_salary_quote, SalaryBatchQuote):
            raise ResourcePlanningError("next_day_salary_quote must be a SalaryBatchQuote")
        if not isinstance(self.current_salary_payable, bool):
            raise ResourcePlanningError("current_salary_payable must be a boolean")
        if not isinstance(self.salary_enabled, bool):
            raise ResourcePlanningError("salary_enabled must be a boolean")
        if self.next_day_salary_quote.for_date <= self.current_salary_quote.for_date:
            raise ResourcePlanningError("next-day salary quote must follow the current salary date")

    @property
    def salary_shortfall(self) -> bool:
        return bool(self.current_salary_quote.unpaid_guest_ids and not self.current_salary_payable)

    @property
    def silver_liquidity_state(self) -> str:
        """Classify post-obligation cash flow without imposing a hard ceiling.

        The forecasts already subtract the current/next salary and recurring
        recruitment outflow.  The combined floor is therefore a safety buffer
        for the following cycle, not another spendable-resource reservation.
        """

        next_salary = int(self.next_day_salary_quote.total_amount)
        daily_outflow = int(self.recurring_silver_outflow_daily)
        # Salary and recruitment outflow consume the same Manor.silver
        # balance.  The liquidity floor therefore covers both obligations.
        daily_cash_floor = next_salary + daily_outflow
        if daily_cash_floor <= 0:
            return "healthy"
        if self.silver_forecast_24h < daily_cash_floor:
            return "critical"
        if self.silver_forecast_72h < daily_cash_floor * 3:
            return "tight"
        return "healthy"

    @property
    def net_grain_rate_per_hour(self) -> float:
        """Return the frozen net grain rate after personnel upkeep."""

        return float(dict(self.production_rates).get(ResourceType.GRAIN, 0.0))

    @property
    def grain_rate_budget_24h(self) -> int:
        """Return the maximum grain outflow supported by one frozen day of net production."""

        return max(0, math.floor(self.net_grain_rate_per_hour * 24.0))

    def next_affordable_at(
        self,
        costs: Mapping[str, int] | Sequence[tuple[str, int]],
        *,
        now: datetime,
    ) -> datetime | None:
        """Estimate when a currently blocked cost becomes payable.

        This is advisory scheduling data only.  The execution path still
        re-quotes resources under lock before spending anything.
        """

        if timezone.is_naive(now):
            raise ResourcePlanningError("now must be timezone-aware")
        resource_costs = _canonical_resources(costs, field="candidate resource costs")
        available = dict(self.spendable_resources)
        rates = dict(self.production_rates)
        max_game_seconds = 0.0
        for resource, amount in resource_costs:
            gap = max(0, int(amount) - int(available.get(resource, 0)))
            if gap == 0:
                continue
            hourly_rate = float(rates.get(resource, 0.0))
            if resource == ResourceType.SILVER and self.recurring_silver_outflow_daily:
                # Treat the recurring daily outflow as a smoothed advisory
                # rate.  Current affordability still uses the untouched
                # spendable balance, so this cannot reserve or pre-spend cash.
                hourly_rate -= float(self.recurring_silver_outflow_daily) / 24.0
            if hourly_rate <= 0:
                return None
            max_game_seconds = max(max_game_seconds, gap / hourly_rate * 3600.0)
        if max_game_seconds <= 0:
            return now
        multiplier = max(0.000001, float(get_time_scale().multiplier))
        return now + timedelta(seconds=max(1, math.ceil(max_game_seconds / multiplier)))

    def assess_costs(
        self,
        costs: Mapping[str, int] | Sequence[tuple[str, int]],
    ) -> tuple[
        tuple[tuple[str, int], ...],
        tuple[tuple[str, int], ...],
        tuple[tuple[str, int], ...],
        tuple[str, ...],
    ]:
        resource_costs = _canonical_resources(costs, field="candidate resource costs")
        spendable = dict(self.spendable_resources)
        physical = dict(self.post_salary_resources)
        reasons: list[str] = []
        resources_after = dict(spendable)
        for resource, amount in resource_costs:
            available = int(spendable.get(resource, 0))
            physical_available = int(physical.get(resource, 0))
            resources_after[resource] = max(0, available - amount)
            if amount <= available:
                continue
            reason = (
                "salary_runway_protected"
                if resource == ResourceType.SILVER and amount <= physical_available
                else "insufficient_resource"
            )
            if reason not in reasons:
                reasons.append(reason)
        return (
            resource_costs,
            self.spendable_resources,
            tuple(sorted(resources_after.items())),
            tuple(reasons),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "current_resources": dict(self.current_resources),
            "production_deltas": dict(self.production_deltas),
            "post_settlement_resources": dict(self.post_settlement_resources),
            "salary_enabled": self.salary_enabled,
            "current_salary": {
                "for_date": self.current_salary_quote.for_date.isoformat(),
                "total_amount": self.current_salary_quote.total_amount,
                "unpaid_guest_ids": list(self.current_salary_quote.unpaid_guest_ids),
            },
            "current_salary_payable": self.current_salary_payable,
            "post_salary_resources": dict(self.post_salary_resources),
            "next_day_salary": {
                "for_date": self.next_day_salary_quote.for_date.isoformat(),
                "total_amount": self.next_day_salary_quote.total_amount,
                "guest_ids": list(self.next_day_salary_quote.guest_ids),
            },
            "operating_buffer": dict(self.operating_buffer),
            "protected_resources": dict(self.protected_resources),
            "spendable_resources": dict(self.spendable_resources),
            "production_rates": dict(self.production_rates),
            "silver_forecast_24h": self.silver_forecast_24h,
            "silver_forecast_72h": self.silver_forecast_72h,
            "grain_forecast_24h": self.grain_forecast_24h,
            "grain_forecast_72h": self.grain_forecast_72h,
            "net_grain_rate_per_hour": self.net_grain_rate_per_hour,
            "grain_rate_budget_24h": self.grain_rate_budget_24h,
            "recurring_silver_outflow_daily": self.recurring_silver_outflow_daily,
            "silver_liquidity_state": self.silver_liquidity_state,
        }


def build_resource_planning_snapshot(
    *,
    manor: Manor,
    guests: Sequence[Guest],
    paid_guest_ids: Set[int] | None,
    planned_at,
    production_basis: ResourceProductionBasis,
    forced_settlement_budget: ForcedSettlementBudget | None,
    current_grain: int,
    protect_salary_runway: bool = True,
    recurring_silver_outflow_daily: int = 0,
    salary_scale: SalaryScale = DEFAULT_SALARY_SCALE,
    salary_enabled: bool = True,
) -> tuple[ResourcePlanningSnapshot, ForcedSettlementDecision]:
    if (
        isinstance(recurring_silver_outflow_daily, bool)
        or not isinstance(recurring_silver_outflow_daily, int)
        or recurring_silver_outflow_daily < 0
    ):
        raise ResourcePlanningError("recurring_silver_outflow_daily must be a non-negative integer")
    if not isinstance(salary_enabled, bool):
        raise ResourcePlanningError("salary_enabled must be a boolean")
    current_resources = _canonical_resources(
        {
            ResourceType.SILVER: max(0, int(manor.silver or 0)),
            ResourceType.GRAIN: max(0, int(current_grain)),
        },
        field="current_resources",
    )
    production_deltas = _canonical_resources(
        preview_resource_production(
            manor,
            now=planned_at,
            production_basis=production_basis,
            current_resources=dict(current_resources),
        ),
        field="production_deltas",
        allow_negative=True,
    )
    delta_by_resource = dict(production_deltas)
    production_rates_by_resource = dict(production_basis.hourly_rates)
    production_rates_by_resource[ResourceType.GRAIN] = float(
        production_rates_by_resource.get(ResourceType.GRAIN, 0.0)
    ) - float(production_basis.personnel_grain_cost_per_hour)
    production_rates = _canonical_production_rates(production_rates_by_resource)
    settlement = plan_forced_settlement(
        forced_settlement_budget,
        now=planned_at,
        silver_capacity=int(manor.silver_capacity or 0),
        grain_capacity=int(manor.grain_capacity or 0),
        requested_silver=max(0, delta_by_resource.get(ResourceType.SILVER, 0)),
        requested_grain=max(0, delta_by_resource.get(ResourceType.GRAIN, 0)),
    )
    settlement_limits: dict[str, int] = {
        str(ResourceType.SILVER): settlement.silver_units,
        str(ResourceType.GRAIN): settlement.grain_units,
    }
    post_settlement: dict[str, int] = {}
    for resource, current in current_resources:
        projected_delta = int(delta_by_resource.get(resource, 0))
        applied_delta = min(projected_delta, settlement_limits[resource]) if projected_delta > 0 else projected_delta
        post_settlement[resource] = max(0, current + applied_delta)

    salary_date = timezone.localdate(planned_at)
    guest_ids = tuple(sorted(int(guest.id) for guest in guests))
    current_salary = (
        quote_all_salaries(
            manor,
            for_date=salary_date,
            guests=guests,
            paid_guest_ids=(None if paid_guest_ids is None else set(paid_guest_ids)),
            salary_scale=salary_scale,
        )
        if salary_enabled
        else SalaryBatchQuote(
            for_date=salary_date,
            guest_ids=guest_ids,
            unpaid_guest_ids=(),
            total_amount=0,
            salary_scale=salary_scale,
        )
    )
    current_salary_payable = post_settlement[ResourceType.SILVER] >= current_salary.total_amount
    post_salary = dict(post_settlement)
    if current_salary_payable:
        post_salary[ResourceType.SILVER] -= current_salary.total_amount

    next_day_salary = (
        quote_all_salaries(
            manor,
            for_date=salary_date + timedelta(days=1),
            guests=guests,
            paid_guest_ids=set(),
            salary_scale=salary_scale,
        )
        if salary_enabled
        else SalaryBatchQuote(
            for_date=salary_date + timedelta(days=1),
            guest_ids=guest_ids,
            unpaid_guest_ids=(),
            total_amount=0,
            salary_scale=salary_scale,
        )
    )
    operating_silver = (
        salary_runway_commitment(next_day_salary.total_amount) - next_day_salary.total_amount
        if salary_enabled and protect_salary_runway
        else 0
    )
    operating_buffer: dict[str, int] = {
        str(ResourceType.SILVER): operating_silver,
        str(ResourceType.GRAIN): 0,
    }
    protected_resources: dict[str, int] = {
        str(ResourceType.SILVER): (
            next_day_salary.total_amount + operating_silver if salary_enabled and protect_salary_runway else 0
        ),
        str(ResourceType.GRAIN): 0,
    }
    spendable_resources: dict[str, int] = {
        str(ResourceType.SILVER): (
            post_salary[ResourceType.SILVER]
            if not salary_enabled
            else (
                max(0, post_salary[ResourceType.SILVER] - protected_resources[ResourceType.SILVER])
                if protect_salary_runway and current_salary_payable
                else (0 if protect_salary_runway else post_salary[ResourceType.SILVER])
            )
        ),
        str(ResourceType.GRAIN): post_salary[ResourceType.GRAIN],
    }
    rate_by_resource = dict(production_rates)
    current_salary_obligation = 0 if current_salary_payable else int(current_salary.total_amount)
    silver_forecast_24h = _forecast_resource(
        current=post_salary[ResourceType.SILVER],
        capacity=int(manor.silver_capacity or 0),
        hourly_rate=float(rate_by_resource.get(ResourceType.SILVER, 0.0)),
        horizon_hours=_SILVER_FORECAST_HORIZON_24_HOURS,
        obligations=(
            current_salary_obligation
            + (int(next_day_salary.total_amount) if salary_enabled else 0)
            + recurring_silver_outflow_daily
        ),
    )
    silver_forecast_72h = _forecast_resource(
        current=post_salary[ResourceType.SILVER],
        capacity=int(manor.silver_capacity or 0),
        hourly_rate=float(rate_by_resource.get(ResourceType.SILVER, 0.0)),
        horizon_hours=_SILVER_FORECAST_HORIZON_72_HOURS,
        obligations=(
            current_salary_obligation
            + (int(next_day_salary.total_amount) * 3 if salary_enabled else 0)
            + recurring_silver_outflow_daily * 3
        ),
    )
    grain_forecast_24h = _forecast_resource(
        current=post_salary[ResourceType.GRAIN],
        capacity=int(manor.grain_capacity or 0),
        hourly_rate=float(rate_by_resource.get(ResourceType.GRAIN, 0.0)),
        horizon_hours=_SILVER_FORECAST_HORIZON_24_HOURS,
    )
    grain_forecast_72h = _forecast_resource(
        current=post_salary[ResourceType.GRAIN],
        capacity=int(manor.grain_capacity or 0),
        hourly_rate=float(rate_by_resource.get(ResourceType.GRAIN, 0.0)),
        horizon_hours=_SILVER_FORECAST_HORIZON_72_HOURS,
    )
    snapshot = ResourcePlanningSnapshot(
        current_resources=current_resources,
        production_deltas=production_deltas,
        post_settlement_resources=_canonical_resources(
            post_settlement,
            field="post_settlement_resources",
        ),
        current_salary_quote=current_salary,
        current_salary_payable=current_salary_payable,
        post_salary_resources=_canonical_resources(post_salary, field="post_salary_resources"),
        next_day_salary_quote=next_day_salary,
        operating_buffer=_canonical_resources(operating_buffer, field="operating_buffer"),
        protected_resources=_canonical_resources(
            protected_resources,
            field="protected_resources",
        ),
        spendable_resources=_canonical_resources(
            spendable_resources,
            field="spendable_resources",
        ),
        production_rates=production_rates,
        silver_forecast_24h=silver_forecast_24h,
        silver_forecast_72h=silver_forecast_72h,
        grain_forecast_24h=grain_forecast_24h,
        grain_forecast_72h=grain_forecast_72h,
        recurring_silver_outflow_daily=recurring_silver_outflow_daily,
        salary_enabled=salary_enabled,
    )
    return snapshot, settlement


__all__ = [
    "ResourcePlanningError",
    "ResourcePlanningSnapshot",
    "build_resource_planning_snapshot",
    "salary_runway_commitment",
]
