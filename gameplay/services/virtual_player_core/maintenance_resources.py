from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from gameplay.models import Manor, ResourceType
from gameplay.services.resources import ResourceProductionBasis, preview_resource_production
from guests.models import Guest
from guests.services.salary import SalaryBatchQuote, quote_all_salaries

from .economy import ForcedSettlementBudget, ForcedSettlementDecision, plan_forced_settlement


class ResourcePlanningError(ValueError):
    pass


_SUPPORTED_RESOURCES = frozenset({ResourceType.SILVER, ResourceType.GRAIN})
_OPERATING_BUFFER_SALARY_NUMERATOR = 1
_OPERATING_BUFFER_SALARY_DENOMINATOR = 10


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
        if not isinstance(self.current_salary_quote, SalaryBatchQuote):
            raise ResourcePlanningError("current_salary_quote must be a SalaryBatchQuote")
        if not isinstance(self.next_day_salary_quote, SalaryBatchQuote):
            raise ResourcePlanningError("next_day_salary_quote must be a SalaryBatchQuote")
        if not isinstance(self.current_salary_payable, bool):
            raise ResourcePlanningError("current_salary_payable must be a boolean")
        if self.next_day_salary_quote.for_date <= self.current_salary_quote.for_date:
            raise ResourcePlanningError("next-day salary quote must follow the current salary date")

    @property
    def salary_shortfall(self) -> bool:
        return bool(self.current_salary_quote.unpaid_guest_ids and not self.current_salary_payable)

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
) -> tuple[ResourcePlanningSnapshot, ForcedSettlementDecision]:
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
    current_salary = quote_all_salaries(
        manor,
        for_date=salary_date,
        guests=guests,
        paid_guest_ids=(None if paid_guest_ids is None else set(paid_guest_ids)),
    )
    current_salary_payable = post_settlement[ResourceType.SILVER] >= current_salary.total_amount
    post_salary = dict(post_settlement)
    if current_salary_payable:
        post_salary[ResourceType.SILVER] -= current_salary.total_amount

    next_day_salary = quote_all_salaries(
        manor,
        for_date=salary_date + timedelta(days=1),
        guests=guests,
        paid_guest_ids=set(),
    )
    operating_silver = salary_runway_commitment(next_day_salary.total_amount) - next_day_salary.total_amount
    operating_buffer: dict[str, int] = {
        str(ResourceType.SILVER): operating_silver,
        str(ResourceType.GRAIN): 0,
    }
    protected_resources: dict[str, int] = {
        str(ResourceType.SILVER): next_day_salary.total_amount + operating_silver,
        str(ResourceType.GRAIN): 0,
    }
    spendable_resources: dict[str, int] = {
        str(ResourceType.SILVER): (
            max(0, post_salary[ResourceType.SILVER] - protected_resources[ResourceType.SILVER])
            if current_salary_payable
            else 0
        ),
        str(ResourceType.GRAIN): post_salary[ResourceType.GRAIN],
    }
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
    )
    return snapshot, settlement


__all__ = [
    "ResourcePlanningError",
    "ResourcePlanningSnapshot",
    "build_resource_planning_snapshot",
    "salary_runway_commitment",
]
