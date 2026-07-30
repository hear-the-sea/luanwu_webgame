from __future__ import annotations

from django.db.models import F
from django.utils import timezone

from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaTournament, ArenaVirtualDemand

from .virtual_reserve_fill import fill_due_coop_reserve, fill_due_tournament_reserve
from .virtual_reserve_pool import replenish_virtual_reserve
from .virtual_reserve_reconcile import reconcile_coop_demand, reconcile_tournament_demand


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

    candidates: dict[tuple[str, int], float] = {}

    def _add_candidate(mode: str, event_id: int, due_at, created_at) -> None:
        priority_at = due_at or created_at
        candidates[(mode, int(event_id))] = float("inf") if priority_at is None else float(priority_at.timestamp())

    active_demands = list(
        ArenaVirtualDemand.objects.filter(status=ArenaVirtualDemand.Status.ACTIVE)
        .select_related("tournament", "coop_event")
        .order_by("next_retry_at", "id")[:normalized_limit]
    )
    for demand in active_demands:
        if demand.tournament_id is not None and demand.tournament is not None:
            _add_candidate(
                "tournament",
                demand.tournament_id,
                demand.tournament.virtual_fill_at,
                demand.tournament.created_at,
            )
        elif demand.coop_event_id is not None and demand.coop_event is not None:
            _add_candidate(
                "coop",
                demand.coop_event_id,
                demand.coop_event.virtual_fill_at,
                demand.coop_event.created_at,
            )

    tournament_rows = list(
        ArenaTournament.objects.filter(
            status=ArenaTournament.Status.RECRUITING,
            entries__status=ArenaEntry.Status.REGISTERED,
            entries__source=ArenaEntry.Source.PLAYER,
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
        if mode == "tournament":
            reconciled_demand = reconcile_tournament_demand(event_id, now=current_time)
        elif mode == "coop":
            reconciled_demand = reconcile_coop_demand(event_id, now=current_time)
        else:
            continue
        if reconciled_demand is None:
            continue

        result["reconciled"] += 1
        replenished = replenish_virtual_reserve(reconciled_demand.id, now=current_time)
        result["ready"] += int(replenished.ready_count)
        result["training"] += int(replenished.training_count)
        if mode == "tournament":
            result["filled_entries"] += fill_due_tournament_reserve(
                event_id,
                now=current_time,
            )
        else:
            result["filled_entries"] += fill_due_coop_reserve(
                event_id,
                now=current_time,
            )
    return result


__all__ = ["scan_virtual_reserve_demands"]
