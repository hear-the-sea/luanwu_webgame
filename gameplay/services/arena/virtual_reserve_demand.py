from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from celery import current_app
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from common.utils.celery import safe_apply_async
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaTournament, ArenaVirtualDemand
from gameplay.services.virtual_player_core.population_runtime import merge_population_recompute_demand_for_prestige
from gameplay.services.virtual_player_core.runtime_assessment import assess_virtual_player_runtime

from .virtual_lineups import lineup_power
from .virtual_reserve_policy import RESERVE_MINIMUM as POLICY_RESERVE_MINIMUM
from .virtual_reserve_policy import RESERVE_MULTIPLIER as POLICY_RESERVE_MULTIPLIER
from .virtual_reserve_policy import reserve_target_for_missing, reserve_target_plan
from .virtual_reserve_references import median_entry, reference_snapshots
from .virtual_reserve_training_policy import ArenaTrainingPolicyDecision, resolve_configured_arena_training_policy

logger = logging.getLogger(__name__)

RESERVE_MULTIPLIER = POLICY_RESERVE_MULTIPLIER
RESERVE_MINIMUM = POLICY_RESERVE_MINIMUM
MAX_DEMAND_NO_PROGRESS_AGE = timedelta(hours=12)
_POPULATION_SUPPLY_RETRY_REASONS = frozenset(
    {
        "insufficient_ready_members",
        "dynamic_population_cap_reached",
        "population_region_unavailable",
    }
)


def _queue_reconcile_callback(*, mode: str, event_id: int) -> Callable[[], None]:
    def callback() -> None:
        queue_virtual_reserve_reconcile(mode, event_id)

    return callback


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


@transaction.atomic
def wake_arena_demands_after_routing_resume(*, now=None) -> int:
    """Re-arm demand retries after maintenance routing becomes writable again.

    Routing availability is an external demand input. Advancing the input
    clock prevents a long safety pause from being charged as Arena
    no-progress, while only the timeout terminal state is reopened. Admission
    high-water and replacement-budget accounting remain monotonic.
    """
    current_time = now or timezone.now()
    demands = list(
        ArenaVirtualDemand.objects.select_for_update()
        .filter(
            Q(
                status=ArenaVirtualDemand.Status.ACTIVE,
                missing_entry_count__gt=0,
            )
            | Q(
                status=ArenaVirtualDemand.Status.BLOCKED,
                last_failure_reason="no_progress_timeout",
                missing_entry_count__gt=0,
            )
        )
        .select_related("tournament", "coop_event")
        .order_by("id")
    )
    for demand in demands:
        was_blocked = demand.status == ArenaVirtualDemand.Status.BLOCKED
        demand.last_input_change_at = current_time
        demand.last_checked_at = current_time
        demand.next_retry_at = current_time
        update_fields = ["last_input_change_at", "last_checked_at", "next_retry_at", "updated_at"]
        if was_blocked:
            target_plan = reserve_target_plan(int(demand.missing_entry_count))
            demand.status = ArenaVirtualDemand.Status.ACTIVE
            demand.version = int(demand.version) + 1
            demand.reserve_target_count = target_plan.replacement_target_count
            demand.warm_target_count = target_plan.warm_target_count
            demand.max_reserve_target_count = max(
                int(demand.max_reserve_target_count),
                target_plan.replacement_target_count,
            )
            demand.consecutive_failure_count = 0
            demand.last_failure_reason = ""
            demand.admission_paused_at = None
            demand.admission_pause_reason = ""
            demand.admission_probe_target_ordinal = None
            update_fields.extend(
                [
                    "status",
                    "version",
                    "reserve_target_count",
                    "warm_target_count",
                    "max_reserve_target_count",
                    "consecutive_failure_count",
                    "last_failure_reason",
                    "admission_paused_at",
                    "admission_pause_reason",
                    "admission_probe_target_ordinal",
                ]
            )
        demand.save(update_fields=update_fields)
        mode = "tournament" if demand.tournament_id is not None else "coop"
        event_id = demand.tournament_id or demand.coop_event_id
        if event_id is not None:
            transaction.on_commit(
                _queue_reconcile_callback(mode=mode, event_id=int(event_id)),
                robust=True,
            )
    return len(demands)


@transaction.atomic
def wake_active_arena_demands_for_population_region(
    *,
    region: str,
    now=None,
) -> int:
    """Wake supply-blocked Arena demands after a population cell gains supply."""
    current_time = now or timezone.now()
    normalized_region = str(region)
    region_filter = Q(
        tournament__entries__status=ArenaEntry.Status.REGISTERED,
        tournament__entries__source=ArenaEntry.Source.PLAYER,
        tournament__entries__manor__region=normalized_region,
    ) | Q(
        coop_event__entries__status=ArenaCoopEntry.Status.REGISTERED,
        coop_event__entries__source=ArenaCoopEntry.Source.PLAYER,
        coop_event__entries__manor__region=normalized_region,
    )
    demands = list(
        ArenaVirtualDemand.objects.select_for_update()
        .filter(
            status=ArenaVirtualDemand.Status.ACTIVE,
            missing_entry_count__gt=0,
            last_failure_reason__in=_POPULATION_SUPPLY_RETRY_REASONS,
            next_retry_at__gt=current_time,
        )
        .filter(region_filter)
        .distinct()
    )
    for demand in demands:
        demand.next_retry_at = current_time
        demand.save(update_fields=["next_retry_at", "updated_at"])
        mode = "tournament" if demand.tournament_id is not None else "coop"
        event_id = demand.tournament_id or demand.coop_event_id
        if event_id is not None:
            transaction.on_commit(
                _queue_reconcile_callback(mode=mode, event_id=int(event_id)),
                robust=True,
            )
    return len(demands)


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
    if transition.population_region is None or transition.population_prestige is None:
        return
    if not assess_virtual_player_runtime().v2_population_activation_allowed:
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
        ),
        robust=True,
    )


def _reserve_target(missing: int) -> int:
    return reserve_target_for_missing(missing)


def _arena_training_snapshot_fields(
    decision: ArenaTrainingPolicyDecision,
) -> dict[str, int | str | list[str]]:
    return {
        "arena_training_policy_version": int(decision.policy_version),
        "arena_training_policy_checksum": str(decision.policy_checksum),
        "arena_strength_segment": decision.strength_segment,
        "arena_strength_envelope_digest": decision.envelope_digest,
        "arena_supply_prestige_band": decision.supply_prestige_band,
        "arena_supply_prestige_band_priority": list(decision.supply_prestige_band_priority),
        "arena_supply_prestige": int(decision.supply_prestige),
    }


def _arena_training_snapshot_changed(
    demand: ArenaVirtualDemand,
    *,
    snapshot_fields: dict[str, int | str | list[str]],
) -> bool:
    return any(getattr(demand, field_name) != value for field_name, value in snapshot_fields.items())


def close_virtual_demand_state_locked(
    demand: ArenaVirtualDemand,
    *,
    status: str,
    failure_reason: str = "",
    checked_at=None,
) -> DemandReconcileTransition:
    demand.status = status
    if status != ArenaVirtualDemand.Status.BLOCKED:
        demand.missing_entry_count = 0
    demand.reserve_target_count = 0
    demand.warm_target_count = 0
    demand.next_retry_at = None
    demand.consecutive_failure_count = 0
    demand.last_failure_reason = str(failure_reason)[:64]
    demand.last_checked_at = checked_at or timezone.now()
    demand.admission_paused_at = None
    demand.admission_pause_reason = ""
    demand.admission_probe_target_ordinal = None
    demand.save(
        update_fields=[
            "status",
            "missing_entry_count",
            "reserve_target_count",
            "warm_target_count",
            "next_retry_at",
            "consecutive_failure_count",
            "last_failure_reason",
            "last_checked_at",
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
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
    arena_training: ArenaTrainingPolicyDecision,
    now,
) -> DemandReconcileTransition:
    lookup: dict[str, ArenaTournament | ArenaCoopEvent | None] = (
        {"tournament": tournament} if tournament is not None else {"coop_event": coop_event}
    )
    demand = ArenaVirtualDemand.objects.select_for_update().filter(**lookup).first()
    target_plan = reserve_target_plan(missing_entry_count)
    reserve_target_count = target_plan.replacement_target_count
    warm_target_count = target_plan.warm_target_count
    training_snapshot = _arena_training_snapshot_fields(arena_training)
    if demand is None:
        if not arena_training.available:
            demand = ArenaVirtualDemand.objects.create(
                **lookup,
                status=ArenaVirtualDemand.Status.BLOCKED,
                target_guest_count=target_guest_count,
                target_team_power=target_team_power,
                missing_entry_count=missing_entry_count,
                last_failure_reason=arena_training.reason,
                last_progress_at=now,
                last_input_change_at=now,
                last_checked_at=now,
                **training_snapshot,
            )
            return DemandReconcileTransition(closed_demand=demand)
        demand = ArenaVirtualDemand.objects.create(
            **lookup,
            status=ArenaVirtualDemand.Status.ACTIVE,
            target_guest_count=target_guest_count,
            target_team_power=target_team_power,
            missing_entry_count=missing_entry_count,
            reserve_target_count=reserve_target_count,
            warm_target_count=warm_target_count,
            max_reserve_target_count=reserve_target_count,
            next_retry_at=now,
            last_progress_at=now,
            last_input_change_at=now,
            last_checked_at=now,
            **training_snapshot,
        )
        return DemandReconcileTransition(
            active_demand=demand,
            demand_created=True,
            population_region=population_region,
            population_prestige=int(arena_training.supply_prestige),
        )

    changed = (
        demand.target_guest_count != target_guest_count
        or demand.target_team_power != target_team_power
        or demand.missing_entry_count != missing_entry_count
        or demand.reserve_target_count != reserve_target_count
        or demand.warm_target_count != warm_target_count
        or _arena_training_snapshot_changed(demand, snapshot_fields=training_snapshot)
    )
    was_blocked = demand.status == ArenaVirtualDemand.Status.BLOCKED
    if demand.status == ArenaVirtualDemand.Status.BLOCKED and not changed:
        return DemandReconcileTransition()
    if not arena_training.available:
        if changed:
            demand.version += 1
        demand.status = ArenaVirtualDemand.Status.BLOCKED
        demand.target_guest_count = target_guest_count
        demand.target_team_power = target_team_power
        demand.missing_entry_count = missing_entry_count
        for field_name, value in training_snapshot.items():
            setattr(demand, field_name, value)
        if changed:
            demand.last_input_change_at = now
        demand.save(
            update_fields=[
                "status",
                "version",
                "target_guest_count",
                "target_team_power",
                "missing_entry_count",
                *training_snapshot.keys(),
                "last_input_change_at",
                "updated_at",
            ]
        )
        return close_virtual_demand_state_locked(
            demand,
            status=ArenaVirtualDemand.Status.BLOCKED,
            failure_reason=arena_training.reason,
            checked_at=now,
        )
    last_progress_at = demand.last_progress_at or demand.created_at
    last_input_change_at = demand.last_input_change_at or demand.created_at
    last_activity_at = max(last_progress_at, last_input_change_at)
    runtime_assessment = assess_virtual_player_runtime()
    if (
        demand.status == ArenaVirtualDemand.Status.ACTIVE
        and not changed
        and now - last_activity_at >= MAX_DEMAND_NO_PROGRESS_AGE
        and runtime_assessment.growth_allowed
    ):
        return close_virtual_demand_state_locked(
            demand,
            status=ArenaVirtualDemand.Status.BLOCKED,
            failure_reason="no_progress_timeout",
            checked_at=now,
        )
    if changed:
        demand.version += 1
    demand.status = ArenaVirtualDemand.Status.ACTIVE
    demand.target_guest_count = target_guest_count
    demand.target_team_power = target_team_power
    demand.missing_entry_count = missing_entry_count
    demand.reserve_target_count = reserve_target_count
    demand.warm_target_count = warm_target_count
    for field_name, value in training_snapshot.items():
        setattr(demand, field_name, value)
    if was_blocked:
        demand.max_reserve_target_count = reserve_target_count
        demand.admission_attempt_high_water = 0
    else:
        demand.max_reserve_target_count = max(demand.max_reserve_target_count, reserve_target_count)
    demand.last_checked_at = now
    update_fields = [
        "status",
        "version",
        "target_guest_count",
        "target_team_power",
        "missing_entry_count",
        "reserve_target_count",
        "warm_target_count",
        "max_reserve_target_count",
        *training_snapshot.keys(),
        "last_checked_at",
        "updated_at",
    ]
    if was_blocked:
        update_fields.append("admission_attempt_high_water")
    if changed:
        demand.next_retry_at = now
        demand.consecutive_failure_count = 0
        demand.last_failure_reason = ""
        demand.last_input_change_at = now
        demand.admission_paused_at = None
        demand.admission_pause_reason = ""
        demand.admission_probe_target_ordinal = None
        update_fields.extend(
            [
                "next_retry_at",
                "consecutive_failure_count",
                "last_failure_reason",
                "last_input_change_at",
                "admission_paused_at",
                "admission_pause_reason",
                "admission_probe_target_ordinal",
            ]
        )
    demand.save(update_fields=update_fields)
    return DemandReconcileTransition(
        active_demand=demand,
        reevaluate_members=changed,
        population_region=population_region,
        population_prestige=int(arena_training.supply_prestige),
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
    target_team_power = lineup_power(snapshots)
    return _upsert_demand_state_locked(
        tournament=tournament,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
        missing_entry_count=missing,
        population_region=str(reference_entry.manor.region),
        arena_training=resolve_configured_arena_training_policy(target_team_power=target_team_power),
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
    target_team_power = lineup_power(snapshots)
    return _upsert_demand_state_locked(
        coop_event=event,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
        missing_entry_count=missing,
        population_region=str(reference_entry.manor.region),
        arena_training=resolve_configured_arena_training_policy(target_team_power=target_team_power),
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
    "wake_active_arena_demands_for_population_region",
    "wake_arena_demands_after_routing_resume",
]
