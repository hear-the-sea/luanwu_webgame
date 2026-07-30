from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from gameplay.models import ArenaCoopEvent, ArenaTournament, ArenaVirtualDemand

from .virtual_reserve_demand import (
    DemandReconcileTransition,
    merge_arena_population_activation,
    reconcile_coop_demand_state_locked,
    reconcile_tournament_demand_state_locked,
)
from .virtual_reserve_observability import emit_arena_shortage_after_commit, log_demand_event
from .virtual_reserve_pool import reevaluate_existing_members, release_virtual_reserve_members_for_demand


def _apply_reconcile_transition(
    transition: DemandReconcileTransition,
    *,
    now,
) -> ArenaVirtualDemand | None:
    if transition.closed_demand is not None:
        release_virtual_reserve_members_for_demand(transition.closed_demand)
        return None

    demand = transition.active_demand
    if demand is None:
        return None
    if transition.reevaluate_members:
        reevaluate_existing_members(demand, now=now)
    merge_arena_population_activation(transition, now=now)
    log_demand_event(
        "arena_virtual_demand_reconciled",
        demand,
        message="arena virtual demand reconciled",
        demand_created=transition.demand_created,
    )
    if transition.population_prestige is not None:
        emit_arena_shortage_after_commit(
            demand,
            population_prestige=transition.population_prestige,
            observed_at=now,
        )
    return demand


def reconcile_tournament_demand_locked(
    tournament: ArenaTournament,
    *,
    now=None,
) -> ArenaVirtualDemand | None:
    current_time = now or timezone.now()
    transition = reconcile_tournament_demand_state_locked(tournament, now=current_time)
    return _apply_reconcile_transition(transition, now=current_time)


def reconcile_coop_demand_locked(
    event: ArenaCoopEvent,
    *,
    now=None,
) -> ArenaVirtualDemand | None:
    current_time = now or timezone.now()
    transition = reconcile_coop_demand_state_locked(event, now=current_time)
    return _apply_reconcile_transition(transition, now=current_time)


@transaction.atomic
def reconcile_tournament_demand(tournament_id: int, *, now=None) -> ArenaVirtualDemand | None:
    tournament = ArenaTournament.objects.select_for_update().filter(pk=tournament_id).first()
    if tournament is None:
        return None
    return reconcile_tournament_demand_locked(tournament, now=now or timezone.now())


@transaction.atomic
def reconcile_coop_demand(event_id: int, *, now=None) -> ArenaVirtualDemand | None:
    event = ArenaCoopEvent.objects.select_for_update().filter(pk=event_id).first()
    if event is None:
        return None
    return reconcile_coop_demand_locked(event, now=now or timezone.now())


__all__ = [
    "reconcile_coop_demand",
    "reconcile_coop_demand_locked",
    "reconcile_tournament_demand",
    "reconcile_tournament_demand_locked",
]
