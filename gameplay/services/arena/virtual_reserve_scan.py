from __future__ import annotations

import logging

from django.db import DatabaseError
from django.db.models import F, Q
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaTournament,
    ArenaVirtualDemand,
    BotMaintenanceRecovery,
)
from gameplay.services.virtual_player_core.recovery import (
    classify_failure,
    clear_recovery_failure,
    record_recovery_failure,
    recovery_circuit_is_open,
    recovery_is_blocked,
)

from .virtual_fill_service import observe_fill_service
from .virtual_reserve_demand import (
    BLOCKED_DEMAND_RETRY_COOLDOWN,
    POLICY_BLOCKED_DEMAND_REASONS,
    RECOVERABLE_BLOCKED_DEMAND_REASONS,
)
from .virtual_reserve_fill import fill_due_coop_reserve, fill_due_tournament_reserve
from .virtual_reserve_pool import release_expired_exhausted_virtual_reserve_members, replenish_virtual_reserve
from .virtual_reserve_reconcile import reconcile_coop_demand, reconcile_tournament_demand

logger = logging.getLogger(__name__)


def scan_virtual_reserve_demands(*, now=None, limit: int = 20) -> dict[str, int]:
    current_time = now or timezone.now()
    normalized_limit = max(0, int(limit))
    result = {
        "scanned": 0,
        "reconciled": 0,
        "ready": 0,
        "training": 0,
        "filled_entries": 0,
    }
    if normalized_limit <= 0:
        return result
    if recovery_circuit_is_open(path="arena_demand", now=current_time):
        return result
    release_expired_exhausted_virtual_reserve_members(now=current_time, limit=normalized_limit)
    blocked_retry_due = Q(
        virtual_demand__last_failure_reason__in=RECOVERABLE_BLOCKED_DEMAND_REASONS | POLICY_BLOCKED_DEMAND_REASONS
    ) & (
        Q(virtual_demand__next_retry_at__lte=current_time)
        | Q(
            virtual_demand__next_retry_at__isnull=True,
            virtual_demand__last_checked_at__lte=current_time - BLOCKED_DEMAND_RETRY_COOLDOWN,
        )
    )

    candidates: dict[tuple[str, int], float] = {}

    def _add_candidate(mode: str, event_id: int, due_at, created_at) -> None:
        priority_at = due_at or created_at
        candidates[(mode, int(event_id))] = float("inf") if priority_at is None else float(priority_at.timestamp())

    active_demands = list(
        ArenaVirtualDemand.objects.filter(status=ArenaVirtualDemand.Status.ACTIVE)
        .select_related("tournament", "coop_event")
        .order_by("last_checked_at", "id")[:normalized_limit]
    )
    for demand in active_demands:
        if demand.tournament_id is not None and demand.tournament is not None:
            _add_candidate(
                "tournament",
                demand.tournament_id,
                demand.last_checked_at,
                demand.tournament.created_at,
            )
        elif demand.coop_event_id is not None and demand.coop_event is not None:
            _add_candidate(
                "coop",
                demand.coop_event_id,
                demand.last_checked_at,
                demand.coop_event.created_at,
            )

    tournament_rows = list(
        ArenaTournament.objects.filter(
            status=ArenaTournament.Status.RECRUITING,
            entries__status=ArenaEntry.Status.REGISTERED,
            entries__source=ArenaEntry.Source.PLAYER,
        )
        .filter(
            Q(virtual_demand__isnull=True)
            | (
                Q(virtual_demand__status=ArenaVirtualDemand.Status.BLOCKED)
                & (
                    Q(virtual_demand__updated_at__isnull=True)
                    | Q(entries__joined_at__gt=F("virtual_demand__updated_at"))
                    | blocked_retry_due
                )
            )
        )
        .distinct()
        .order_by(F("virtual_fill_at").asc(nulls_last=True), "created_at", "id")
        .values_list("id", "virtual_fill_at", "created_at")[:normalized_limit]
    )
    for event_id, due_at, created_at in tournament_rows:
        _add_candidate("tournament", event_id, due_at, created_at)

    coop_rows = list(
        ArenaCoopEvent.objects.filter(
            status=ArenaCoopEvent.Status.RECRUITING,
            entries__status=ArenaCoopEntry.Status.REGISTERED,
            entries__source=ArenaCoopEntry.Source.PLAYER,
        )
        .filter(
            Q(virtual_demand__isnull=True)
            | (
                Q(virtual_demand__status=ArenaVirtualDemand.Status.BLOCKED)
                & (
                    Q(virtual_demand__updated_at__isnull=True)
                    | Q(entries__joined_at__gt=F("virtual_demand__updated_at"))
                    | blocked_retry_due
                )
            )
        )
        .distinct()
        .order_by(F("virtual_fill_at").asc(nulls_last=True), "created_at", "id")
        .values_list("id", "virtual_fill_at", "created_at")[:normalized_limit]
    )
    for event_id, due_at, created_at in coop_rows:
        _add_candidate("coop", event_id, due_at, created_at)

    ordered_candidates = sorted(
        candidates,
        key=lambda item: (candidates[item], item[0], item[1]),
    )[:normalized_limit]
    result["scanned"] = len(ordered_candidates)
    for mode, event_id in ordered_candidates:
        entity_key = f"{mode}:{int(event_id)}"
        if recovery_is_blocked(
            scope=BotMaintenanceRecovery.Scope.ARENA_DEMAND,
            entity_key=entity_key,
            now=current_time,
        ):
            continue
        try:
            if mode == "tournament":
                reconciled_demand = reconcile_tournament_demand(event_id, now=current_time)
            elif mode == "coop":
                reconciled_demand = reconcile_coop_demand(event_id, now=current_time)
            else:
                continue
            if reconciled_demand is None:
                observe_fill_service(mode=mode, event_id=event_id, now=current_time, phase="scan")
                clear_recovery_failure(
                    scope=BotMaintenanceRecovery.Scope.ARENA_DEMAND,
                    entity_key=entity_key,
                    now=current_time,
                )
                continue

            replenished = replenish_virtual_reserve(reconciled_demand.id, now=current_time)
            filled_entries = (
                fill_due_tournament_reserve(
                    event_id,
                    now=current_time,
                    emit_shortage_observation=False,
                )
                if mode == "tournament"
                else fill_due_coop_reserve(
                    event_id,
                    now=current_time,
                    emit_shortage_observation=False,
                )
            )
            result["reconciled"] += 1
            result["ready"] += int(replenished.ready_count)
            result["training"] += int(replenished.training_count)
            result["filled_entries"] += int(filled_entries)
            clear_recovery_failure(
                scope=BotMaintenanceRecovery.Scope.ARENA_DEMAND,
                entity_key=entity_key,
                now=current_time,
            )
        except DatabaseError:
            logger.exception("Arena virtual reserve demand scan hit a database failure: %s", entity_key)
            raise
        except Exception as exc:
            record_recovery_failure(
                scope=BotMaintenanceRecovery.Scope.ARENA_DEMAND,
                entity_key=entity_key,
                failure_code=classify_failure(exc),
                error=exc,
                operation_id=f"arena-demand-scan-{mode}-{int(event_id)}",
                payload={"mode": mode, "event_id": int(event_id), "phase": "scan"},
            )
            logger.exception("Arena virtual reserve demand was isolated during scan: %s", entity_key)
    return result


__all__ = ["scan_virtual_reserve_demands"]
