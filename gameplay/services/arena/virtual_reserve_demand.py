from __future__ import annotations

import logging
from dataclasses import dataclass

from celery import current_app
from django.db import transaction
from django.utils import timezone

from common.utils.celery import safe_apply_async
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaTournament, ArenaVirtualDemand
from gameplay.services.runtime_configs import read_virtual_player_routing
from gameplay.services.virtual_player_core.config import BootstrapMode
from gameplay.services.virtual_player_core.population_runtime import merge_population_recompute_demand_for_prestige

from .virtual_lineups import lineup_power
from .virtual_reserve_references import median_entry, reference_snapshots

logger = logging.getLogger(__name__)

RESERVE_MULTIPLIER = 3
RESERVE_MINIMUM = 6


@dataclass(frozen=True)
class DemandReconcileTransition:
    active_demand: ArenaVirtualDemand | None = None
    closed_demand: ArenaVirtualDemand | None = None
    reevaluate_members: bool = False
    demand_created: bool = False
    population_region: str | None = None
    population_prestige: int | None = None


def queue_virtual_reserve_reconcile(mode: str, event_id: int) -> bool:
    task = current_app.signature("gameplay.reconcile_arena_virtual_reserve")
    return safe_apply_async(
        task,
        args=[str(mode), int(event_id)],
        logger=logger,
        log_message="arena virtual reserve reconcile dispatch failed; relying on periodic scan",
        log_extra={
            "event": "arena_virtual_reconcile_dispatch_deferred",
            "mode": str(mode),
            "event_id": int(event_id),
        },
    )


def _queue_virtual_player_population_reconcile(
    *,
    region: str,
    prestige_band: str,
) -> bool:
    task = current_app.signature("gameplay.reconcile_virtual_player_population_cell")
    return safe_apply_async(
        task,
        args=[region, prestige_band],
        logger=logger,
        log_message=("arena population reconcile dispatch failed; relying on periodic scan"),
        log_extra={
            "event": "arena_population_reconcile_dispatch_deferred",
            "region": region,
            "prestige_band": prestige_band,
        },
    )


def merge_arena_population_activation(
    transition: DemandReconcileTransition,
    *,
    now,
) -> None:
    if not (transition.demand_created or transition.reevaluate_members):
        return
    if (
        transition.population_region is None
        or transition.population_prestige is None
        or read_virtual_player_routing().bootstrap_mode is not BootstrapMode.V2_ACTIVE
    ):
        return
    demand = merge_population_recompute_demand_for_prestige(
        region=transition.population_region,
        prestige=transition.population_prestige,
        now=now,
    )
    if demand is None:
        return
    region = str(demand.region)
    prestige_band = str(demand.prestige_band)
    transaction.on_commit(
        lambda: _queue_virtual_player_population_reconcile(
            region=region,
            prestige_band=prestige_band,
        )
    )


def _reserve_target(missing: int) -> int:
    normalized = max(0, int(missing))
    return 0 if normalized == 0 else max(normalized * RESERVE_MULTIPLIER, RESERVE_MINIMUM)


def close_virtual_demand_state_locked(
    demand: ArenaVirtualDemand,
    *,
    status: str,
) -> DemandReconcileTransition:
    demand.status = status
    demand.missing_entry_count = 0
    demand.reserve_target_count = 0
    demand.next_retry_at = None
    demand.consecutive_failure_count = 0
    demand.last_failure_reason = ""
    demand.save(
        update_fields=[
            "status",
            "missing_entry_count",
            "reserve_target_count",
            "next_retry_at",
            "consecutive_failure_count",
            "last_failure_reason",
            "updated_at",
        ]
    )
    return DemandReconcileTransition(closed_demand=demand)


def delete_virtual_demands_for_tournaments(tournament_ids: list[int]) -> int:
    normalized_ids = tuple(dict.fromkeys(int(value) for value in tournament_ids))
    if not normalized_ids:
        return 0
    deleted, _details = ArenaVirtualDemand.objects.filter(tournament_id__in=normalized_ids).delete()
    return int(deleted)


def delete_virtual_demands_for_coop_events(coop_event_ids: list[int]) -> int:
    normalized_ids = tuple(dict.fromkeys(int(value) for value in coop_event_ids))
    if not normalized_ids:
        return 0
    deleted, _details = ArenaVirtualDemand.objects.filter(coop_event_id__in=normalized_ids).delete()
    return int(deleted)


def _upsert_demand_state_locked(
    *,
    tournament: ArenaTournament | None = None,
    coop_event: ArenaCoopEvent | None = None,
    target_guest_count: int,
    target_team_power: int,
    missing_entry_count: int,
    population_region: str,
    population_prestige: int,
    now,
) -> DemandReconcileTransition:
    lookup: dict[str, ArenaTournament | ArenaCoopEvent | None] = (
        {"tournament": tournament} if tournament is not None else {"coop_event": coop_event}
    )
    demand = ArenaVirtualDemand.objects.select_for_update().filter(**lookup).first()
    reserve_target_count = _reserve_target(missing_entry_count)
    if demand is None:
        demand = ArenaVirtualDemand.objects.create(
            **lookup,
            status=ArenaVirtualDemand.Status.ACTIVE,
            target_guest_count=target_guest_count,
            target_team_power=target_team_power,
            missing_entry_count=missing_entry_count,
            reserve_target_count=reserve_target_count,
            max_reserve_target_count=reserve_target_count,
            next_retry_at=now,
            last_progress_at=now,
            last_checked_at=now,
        )
        return DemandReconcileTransition(
            active_demand=demand,
            demand_created=True,
            population_region=population_region,
            population_prestige=population_prestige,
        )

    changed = (
        demand.target_guest_count != target_guest_count
        or demand.target_team_power != target_team_power
        or demand.missing_entry_count != missing_entry_count
        or demand.reserve_target_count != reserve_target_count
    )
    if changed:
        demand.version += 1
    demand.status = ArenaVirtualDemand.Status.ACTIVE
    demand.target_guest_count = target_guest_count
    demand.target_team_power = target_team_power
    demand.missing_entry_count = missing_entry_count
    demand.reserve_target_count = reserve_target_count
    demand.max_reserve_target_count = max(demand.max_reserve_target_count, reserve_target_count)
    demand.last_checked_at = now
    update_fields = [
        "status",
        "version",
        "target_guest_count",
        "target_team_power",
        "missing_entry_count",
        "reserve_target_count",
        "max_reserve_target_count",
        "last_checked_at",
        "updated_at",
    ]
    if changed:
        demand.next_retry_at = now
        demand.consecutive_failure_count = 0
        demand.last_failure_reason = ""
        demand.last_progress_at = now
        update_fields.extend(
            [
                "next_retry_at",
                "consecutive_failure_count",
                "last_failure_reason",
                "last_progress_at",
            ]
        )
    demand.save(update_fields=update_fields)
    return DemandReconcileTransition(
        active_demand=demand,
        reevaluate_members=changed,
        population_region=population_region,
        population_prestige=population_prestige,
    )


def _close_existing_tournament_demand_state(
    tournament: ArenaTournament,
) -> DemandReconcileTransition:
    demand = ArenaVirtualDemand.objects.select_for_update().filter(tournament=tournament).first()
    if demand is None:
        return DemandReconcileTransition()
    return close_virtual_demand_state_locked(demand, status=ArenaVirtualDemand.Status.CLOSED)


def _close_existing_coop_demand_state(
    event: ArenaCoopEvent,
) -> DemandReconcileTransition:
    demand = ArenaVirtualDemand.objects.select_for_update().filter(coop_event=event).first()
    if demand is None:
        return DemandReconcileTransition()
    return close_virtual_demand_state_locked(demand, status=ArenaVirtualDemand.Status.CLOSED)


def reconcile_tournament_demand_state_locked(
    tournament: ArenaTournament,
    *,
    now=None,
) -> DemandReconcileTransition:
    current_time = now or timezone.now()
    if tournament.status != ArenaTournament.Status.RECRUITING:
        return _close_existing_tournament_demand_state(tournament)

    registered_entries = tournament.entries.filter(status=ArenaEntry.Status.REGISTERED)
    real_entries = list(
        registered_entries.filter(source=ArenaEntry.Source.PLAYER)
        .select_related("manor")
        .prefetch_related("entry_guests")
    )
    missing = max(0, int(tournament.player_limit) - registered_entries.count())
    if not real_entries or missing <= 0:
        return _close_existing_tournament_demand_state(tournament)

    reference_entry = median_entry(real_entries)
    snapshots = reference_snapshots(reference_entry)
    return _upsert_demand_state_locked(
        tournament=tournament,
        target_guest_count=len(snapshots),
        target_team_power=lineup_power(snapshots),
        missing_entry_count=missing,
        population_region=str(reference_entry.manor.region),
        population_prestige=int(reference_entry.manor.prestige or 0),
        now=current_time,
    )


def reconcile_coop_demand_state_locked(
    event: ArenaCoopEvent,
    *,
    now=None,
) -> DemandReconcileTransition:
    current_time = now or timezone.now()
    if event.status != ArenaCoopEvent.Status.RECRUITING:
        return _close_existing_coop_demand_state(event)

    registered_entries = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED)
    real_entries = list(
        registered_entries.filter(source=ArenaCoopEntry.Source.PLAYER)
        .select_related("manor")
        .prefetch_related("entry_guests")
    )
    missing = max(0, int(event.player_limit) - registered_entries.count())
    if not real_entries or missing <= 0:
        return _close_existing_coop_demand_state(event)

    reference_entry = median_entry(real_entries)
    snapshots = reference_snapshots(reference_entry)[: int(event.guest_limit_per_entry)]
    return _upsert_demand_state_locked(
        coop_event=event,
        target_guest_count=len(snapshots),
        target_team_power=lineup_power(snapshots),
        missing_entry_count=missing,
        population_region=str(reference_entry.manor.region),
        population_prestige=int(reference_entry.manor.prestige or 0),
        now=current_time,
    )


__all__ = [
    "DemandReconcileTransition",
    "delete_virtual_demands_for_coop_events",
    "delete_virtual_demands_for_tournaments",
    "merge_arena_population_activation",
    "queue_virtual_reserve_reconcile",
    "reconcile_coop_demand_state_locked",
    "reconcile_tournament_demand_state_locked",
]
