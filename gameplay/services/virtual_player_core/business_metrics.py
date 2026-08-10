"""Queryable business dimensions for virtual-player maintenance audit rows."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Avg, Count, Max, Min, QuerySet, Sum

from gameplay.models import BotMaintenanceAttempt


@dataclass(frozen=True, slots=True)
class MaintenanceBusinessMetric:
    """One SQL-grouped maintenance KPI row."""

    archetype: str
    trigger: str
    action_kind: str
    outcome: str
    reason_category: str
    attempt_count: int
    silver_cost: int
    grain_cost: int
    salary_runway_days_min: int
    salary_runway_days_avg: float
    salary_runway_days_max: int
    salary_runway_silver_min: int
    salary_runway_silver_avg: float
    salary_runway_silver_max: int


def maintenance_business_metrics_queryset(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    archetypes: Iterable[str] = (),
    triggers: Iterable[str] = (),
) -> QuerySet:
    """Return a read-only SQL aggregation over immutable maintenance attempts."""

    queryset = BotMaintenanceAttempt.objects.all()
    if since is not None:
        queryset = queryset.filter(completed_at__gte=since)
    if until is not None:
        queryset = queryset.filter(completed_at__lt=until)
    normalized_archetypes = tuple(sorted({str(value).strip() for value in archetypes if str(value).strip()}))
    if normalized_archetypes:
        queryset = queryset.filter(archetype__in=normalized_archetypes)
    normalized_triggers = tuple(sorted({str(value).strip() for value in triggers if str(value).strip()}))
    if normalized_triggers:
        queryset = queryset.filter(trigger__in=normalized_triggers)
    return (
        queryset.values("archetype", "trigger", "action_kind", "outcome", "reason_category")
        .annotate(
            attempt_count=Count("id"),
            silver_cost=Sum("silver_cost"),
            grain_cost=Sum("grain_cost"),
            salary_runway_days_min=Min("salary_runway_days"),
            salary_runway_days_avg=Avg("salary_runway_days"),
            salary_runway_days_max=Max("salary_runway_days"),
            salary_runway_silver_min=Min("salary_runway_silver"),
            salary_runway_silver_avg=Avg("salary_runway_silver"),
            salary_runway_silver_max=Max("salary_runway_silver"),
        )
        .order_by("archetype", "trigger", "action_kind", "outcome", "reason_category")
    )


def query_maintenance_business_metrics(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    archetypes: Iterable[str] = (),
    triggers: Iterable[str] = (),
) -> tuple[MaintenanceBusinessMetric, ...]:
    """Materialize grouped maintenance KPIs without changing the write path."""

    return tuple(
        MaintenanceBusinessMetric(
            archetype=str(row["archetype"] or ""),
            trigger=str(row["trigger"] or ""),
            action_kind=str(row["action_kind"] or ""),
            outcome=str(row["outcome"] or ""),
            reason_category=str(row["reason_category"] or ""),
            attempt_count=int(row["attempt_count"] or 0),
            silver_cost=int(row["silver_cost"] or 0),
            grain_cost=int(row["grain_cost"] or 0),
            salary_runway_days_min=int(row["salary_runway_days_min"] or 0),
            salary_runway_days_avg=float(row["salary_runway_days_avg"] or 0),
            salary_runway_days_max=int(row["salary_runway_days_max"] or 0),
            salary_runway_silver_min=int(row["salary_runway_silver_min"] or 0),
            salary_runway_silver_avg=float(row["salary_runway_silver_avg"] or 0),
            salary_runway_silver_max=int(row["salary_runway_silver_max"] or 0),
        )
        for row in maintenance_business_metrics_queryset(
            since=since,
            until=until,
            archetypes=archetypes,
            triggers=triggers,
        )
    )


__all__ = [
    "MaintenanceBusinessMetric",
    "maintenance_business_metrics_queryset",
    "query_maintenance_business_metrics",
]
