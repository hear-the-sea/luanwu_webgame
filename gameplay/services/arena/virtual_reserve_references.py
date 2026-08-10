from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
)
from gameplay.services.arena.snapshots import build_entry_guest_snapshot

from .virtual_lineups import lineup_power
from .virtual_reserve_growth_budget import (
    ArenaGrowthAttemptOutcome,
    InvalidArenaGrowthBudgetError,
    parse_arena_growth_budget_entries,
    prune_arena_growth_budget_entries,
)
from .virtual_reserve_policy import assess_reserve_admission
from .virtual_reserve_training_policy import (
    demand_supply_prestige,
    demand_supply_prestige_band,
    demand_supply_prestige_band_priority,
    demand_uses_arena_training_policy,
)


@dataclass(frozen=True, slots=True)
class ArenaPopulationActivation:
    region: str
    prestige: int
    needed: int


@dataclass(frozen=True, slots=True)
class ArenaPopulationFunnelSnapshot:
    region: str
    prestige: int
    demand_count: int
    materialization_need: int
    raw_materialization_need: int
    suppressed_materialization_need: int
    warm_target_count: int
    replacement_target_count: int
    admission_attempt_high_water: int
    admission_high_water_lag_count: int
    ready_count: int
    training_count: int
    exhausted_count: int
    growth_attempt_count: int
    growth_applied_count: int
    effective_progress_count: int
    selected_growth_bps_total: int
    selected_growth_bps_max: int
    invalid_growth_budget_count: int
    oldest_ready_member_age_seconds: int
    oldest_training_member_age_seconds: int
    oldest_exhausted_member_age_seconds: int
    guard_reason_counts: tuple[tuple[str, int], ...]
    retry_reason_counts: tuple[tuple[str, int], ...]


@dataclass(slots=True)
class _ArenaPopulationFunnelAccumulator:
    demand_count: int = 0
    materialization_need: int = 0
    raw_materialization_need: int = 0
    suppressed_materialization_need: int = 0
    warm_target_count: int = 0
    replacement_target_count: int = 0
    admission_attempt_high_water: int = 0
    admission_high_water_lag_count: int = 0
    ready_count: int = 0
    training_count: int = 0
    exhausted_count: int = 0
    growth_attempt_count: int = 0
    growth_applied_count: int = 0
    effective_progress_count: int = 0
    selected_growth_bps_total: int = 0
    selected_growth_bps_max: int = 0
    invalid_growth_budget_count: int = 0
    oldest_ready_member_age_seconds: int = 0
    oldest_training_member_age_seconds: int = 0
    oldest_exhausted_member_age_seconds: int = 0
    guard_reason_counts: Counter[str] = field(default_factory=Counter)
    retry_reason_counts: Counter[str] = field(default_factory=Counter)


def reference_snapshots(entry: ArenaEntry | ArenaCoopEntry) -> list[dict]:
    snapshots: list[dict] = []
    for link in entry.entry_guests.all():
        snapshot = link.snapshot or (build_entry_guest_snapshot(link.guest) if link.guest else None)
        if snapshot:
            copied = deepcopy(snapshot)
            # Historical entry snapshots may predate the agility component.
            # Keep their frozen combat fields untouched, but make the
            # missing-input semantics explicit before they reach lineup_power
            # and demand comparison.
            if "agility" not in copied:
                copied["arena_power_snapshot_semantics"] = "legacy_missing_agility"
            snapshots.append(copied)
    return snapshots


def median_entry(entries: Sequence[ArenaEntry | ArenaCoopEntry]) -> ArenaEntry | ArenaCoopEntry:
    return sorted(entries, key=lambda entry: lineup_power(reference_snapshots(entry)))[len(entries) // 2]


def reference_snapshots_for_demand(demand: ArenaVirtualDemand) -> list[dict]:
    real_entries: Sequence[ArenaEntry | ArenaCoopEntry]
    if demand.tournament_id is not None:
        real_entries = list(
            ArenaEntry.objects.filter(
                tournament_id=demand.tournament_id,
                status=ArenaEntry.Status.REGISTERED,
                source=ArenaEntry.Source.PLAYER,
            ).prefetch_related("entry_guests")
        )
    else:
        real_entries = list(
            ArenaCoopEntry.objects.filter(
                event_id=demand.coop_event_id,
                status=ArenaCoopEntry.Status.REGISTERED,
                source=ArenaCoopEntry.Source.PLAYER,
            ).prefetch_related("entry_guests")
        )
    if not real_entries:
        return []
    snapshots = reference_snapshots(median_entry(real_entries))
    return snapshots[: max(0, int(demand.target_guest_count))]


def _member_age_seconds(*, created_at: datetime, now: datetime) -> int:
    return max(0, int((now - created_at).total_seconds()))


def active_arena_population_funnel_snapshots(
    *,
    now: datetime | None = None,
    include_growth_details: bool = True,
) -> tuple[ArenaPopulationFunnelSnapshot, ...]:
    """Build low-cardinality Arena admission snapshots without mutating demand state."""

    current_time = now or timezone.now()
    demands = list(
        ArenaVirtualDemand.objects.filter(
            status=ArenaVirtualDemand.Status.ACTIVE,
            missing_entry_count__gt=0,
        )
        .only(
            "id",
            "tournament_id",
            "coop_event_id",
            "warm_target_count",
            "max_reserve_target_count",
            "admission_attempt_high_water",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "arena_training_policy_version",
            "arena_supply_prestige_band",
            "arena_supply_prestige_band_priority",
            "arena_supply_prestige",
        )
        .order_by("id")
    )
    if not demands:
        return ()

    demand_ids = [demand.id for demand in demands]
    members_by_demand: dict[int, list[ArenaVirtualReserveMember]] = defaultdict(list)
    member_counts: dict[int, tuple[int, int, int, int]] = {}
    if include_growth_details:
        for member in (
            ArenaVirtualReserveMember.objects.filter(demand_id__in=demand_ids)
            .only(
                "demand_id",
                "state",
                "growth_retry_reason",
                "arena_growth_budget_entries",
                "created_at",
            )
            .order_by("demand_id", "id")
        ):
            members_by_demand[int(member.demand_id)].append(member)
    else:
        member_counts = {
            int(row["demand_id"]): (
                int(row["ready_count"] or 0),
                int(row["training_count"] or 0),
                int(row["exhausted_count"] or 0),
                int(row["attempt_count"] or 0),
            )
            for row in ArenaVirtualReserveMember.objects.filter(demand_id__in=demand_ids)
            .values("demand_id")
            .annotate(
                ready_count=Count(
                    "id",
                    filter=Q(state=ArenaVirtualReserveMember.State.READY),
                ),
                training_count=Count(
                    "id",
                    filter=Q(state=ArenaVirtualReserveMember.State.TRAINING),
                ),
                exhausted_count=Count(
                    "id",
                    filter=Q(state=ArenaVirtualReserveMember.State.EXHAUSTED),
                ),
                attempt_count=Count("id"),
            )
        }

    tournament_ids = {int(demand.tournament_id) for demand in demands if demand.tournament_id is not None}
    coop_event_ids = {int(demand.coop_event_id) for demand in demands if demand.coop_event_id is not None}
    tournament_entries: dict[int, list[ArenaEntry]] = defaultdict(list)
    if tournament_ids:
        tournament_entry_rows = (
            ArenaEntry.objects.filter(
                tournament_id__in=sorted(tournament_ids),
                status=ArenaEntry.Status.REGISTERED,
                source=ArenaEntry.Source.PLAYER,
            )
            .select_related("manor")
            .prefetch_related(
                Prefetch(
                    "entry_guests",
                    queryset=ArenaEntryGuest.objects.select_related("guest").order_by("id"),
                )
            )
            .order_by("tournament_id", "id")
        )
        for tournament_entry in tournament_entry_rows:
            tournament_entries[int(tournament_entry.tournament_id)].append(tournament_entry)

    coop_entries: dict[int, list[ArenaCoopEntry]] = defaultdict(list)
    if coop_event_ids:
        coop_entry_rows = (
            ArenaCoopEntry.objects.filter(
                event_id__in=sorted(coop_event_ids),
                status=ArenaCoopEntry.Status.REGISTERED,
                source=ArenaCoopEntry.Source.PLAYER,
            )
            .select_related("manor")
            .prefetch_related(
                Prefetch(
                    "entry_guests",
                    queryset=ArenaCoopEntryGuest.objects.select_related("guest").order_by("id"),
                )
            )
            .order_by("event_id", "id")
        )
        for coop_entry in coop_entry_rows:
            coop_entries[int(coop_entry.event_id)].append(coop_entry)

    totals: dict[tuple[str, int], _ArenaPopulationFunnelAccumulator] = defaultdict(_ArenaPopulationFunnelAccumulator)
    for demand in demands:
        real_entries: Sequence[ArenaEntry | ArenaCoopEntry]
        if demand.tournament_id is not None:
            real_entries = tournament_entries.get(int(demand.tournament_id), [])
        else:
            real_entries = coop_entries.get(int(demand.coop_event_id or 0), [])
        if not real_entries:
            continue
        reference_entry = median_entry(real_entries)
        supply_prestige = demand_supply_prestige(demand)
        supply_band = demand_supply_prestige_band(demand)
        supply_priority = demand_supply_prestige_band_priority(demand)
        if demand_uses_arena_training_policy(demand) and (
            supply_prestige is None or supply_band is None or supply_priority is None
        ):
            continue
        key = (
            str(reference_entry.manor.region),
            (max(0, int(reference_entry.manor.prestige or 0)) if supply_prestige is None else int(supply_prestige)),
        )
        members = members_by_demand.get(int(demand.id), [])
        if include_growth_details:
            ready_count = sum(1 for member in members if member.state == ArenaVirtualReserveMember.State.READY)
            training_count = sum(1 for member in members if member.state == ArenaVirtualReserveMember.State.TRAINING)
            exhausted_count = sum(1 for member in members if member.state == ArenaVirtualReserveMember.State.EXHAUSTED)
            leased_attempt_count = len(members)
        else:
            ready_count, training_count, exhausted_count, leased_attempt_count = member_counts.get(
                int(demand.id),
                (0, 0, 0, 0),
            )
        assessment = assess_reserve_admission(
            warm_target=int(demand.warm_target_count or 0),
            ready_count=ready_count,
            training_count=training_count,
            leased_attempts=leased_attempt_count,
            admission_attempt_high_water=int(demand.admission_attempt_high_water or 0),
            replacement_target=int(demand.max_reserve_target_count or 0),
            active_pause_reason=str(demand.admission_pause_reason or ""),
            admission_probe_target_ordinal=demand.admission_probe_target_ordinal,
        )
        total = totals[key]
        total.demand_count += 1
        total.materialization_need += assessment.admitted_materialization_needed
        total.raw_materialization_need += assessment.raw_materialization_needed
        total.suppressed_materialization_need += assessment.suppressed_materialization_needed
        total.warm_target_count += int(demand.warm_target_count or 0)
        total.replacement_target_count += int(demand.max_reserve_target_count or 0)
        total.admission_attempt_high_water += assessment.attempt_high_water
        total.admission_high_water_lag_count += int(
            int(demand.admission_attempt_high_water or 0)
            < max(
                0,
                leased_attempt_count,
                int(demand.admission_attempt_high_water or 0),
            )
        )
        total.ready_count += ready_count
        total.training_count += training_count
        total.exhausted_count += exhausted_count
        total.guard_reason_counts.update(assessment.guard_reasons)

        for member in members:
            age_seconds = _member_age_seconds(created_at=member.created_at, now=current_time)
            if member.state == ArenaVirtualReserveMember.State.READY:
                total.oldest_ready_member_age_seconds = max(
                    total.oldest_ready_member_age_seconds,
                    age_seconds,
                )
            elif member.state == ArenaVirtualReserveMember.State.TRAINING:
                total.oldest_training_member_age_seconds = max(
                    total.oldest_training_member_age_seconds,
                    age_seconds,
                )
            else:
                total.oldest_exhausted_member_age_seconds = max(
                    total.oldest_exhausted_member_age_seconds,
                    age_seconds,
                )
            if member.growth_retry_reason:
                total.retry_reason_counts[str(member.growth_retry_reason)] += 1
            try:
                budget_entries = prune_arena_growth_budget_entries(
                    parse_arena_growth_budget_entries(
                        member.arena_growth_budget_entries,
                        now=current_time,
                    ),
                    now=current_time,
                )
            except InvalidArenaGrowthBudgetError:
                total.invalid_growth_budget_count += 1
                continue
            total.growth_attempt_count += len(budget_entries)
            for entry in budget_entries:
                if entry.outcome is not ArenaGrowthAttemptOutcome.APPLIED:
                    continue
                total.growth_applied_count += 1
                total.effective_progress_count += int(entry.effective_progress)
                total.selected_growth_bps_total += int(entry.selected_growth_bps)
                total.selected_growth_bps_max = max(
                    total.selected_growth_bps_max,
                    int(entry.selected_growth_bps),
                )

    return tuple(
        ArenaPopulationFunnelSnapshot(
            region=region,
            prestige=prestige,
            demand_count=total.demand_count,
            materialization_need=total.materialization_need,
            raw_materialization_need=total.raw_materialization_need,
            suppressed_materialization_need=total.suppressed_materialization_need,
            warm_target_count=total.warm_target_count,
            replacement_target_count=total.replacement_target_count,
            admission_attempt_high_water=total.admission_attempt_high_water,
            admission_high_water_lag_count=total.admission_high_water_lag_count,
            ready_count=total.ready_count,
            training_count=total.training_count,
            exhausted_count=total.exhausted_count,
            growth_attempt_count=total.growth_attempt_count,
            growth_applied_count=total.growth_applied_count,
            effective_progress_count=total.effective_progress_count,
            selected_growth_bps_total=total.selected_growth_bps_total,
            selected_growth_bps_max=total.selected_growth_bps_max,
            invalid_growth_budget_count=total.invalid_growth_budget_count,
            oldest_ready_member_age_seconds=total.oldest_ready_member_age_seconds,
            oldest_training_member_age_seconds=total.oldest_training_member_age_seconds,
            oldest_exhausted_member_age_seconds=total.oldest_exhausted_member_age_seconds,
            guard_reason_counts=tuple(sorted(total.guard_reason_counts.items())),
            retry_reason_counts=tuple(sorted(total.retry_reason_counts.items())),
        )
        for (region, prestige), total in sorted(totals.items())
    )


def active_arena_population_activations() -> tuple[ArenaPopulationActivation, ...]:
    """Return guarded V2 population demand derived from active Arena shortages."""

    return tuple(
        ArenaPopulationActivation(
            region=snapshot.region,
            prestige=snapshot.prestige,
            needed=snapshot.materialization_need,
        )
        for snapshot in active_arena_population_funnel_snapshots(include_growth_details=False)
        if snapshot.materialization_need > 0
    )


__all__ = [
    "ArenaPopulationActivation",
    "ArenaPopulationFunnelSnapshot",
    "active_arena_population_activations",
    "active_arena_population_funnel_snapshots",
    "median_entry",
    "reference_snapshots",
    "reference_snapshots_for_demand",
]
