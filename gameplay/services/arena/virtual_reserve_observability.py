from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction

from gameplay.models import ArenaVirtualDemand, ArenaVirtualReserveMember
from gameplay.services.virtual_player_core.config import load_virtual_player_v2_config
from gameplay.services.virtual_player_core.safety_metrics import log_safety_metric_failure, record_arena_shortage

logger = logging.getLogger("gameplay.services.arena.virtual_reserve_demand")


def emit_arena_shortage_after_commit(
    demand: ArenaVirtualDemand,
    *,
    population_prestige: int,
    observed_at: datetime,
) -> None:
    if demand.tournament_id is not None:
        tournament = demand.tournament
        if tournament is None:
            raise ValueError("tournament demand is missing its tournament")
        mode = "tournament"
        event_id = int(demand.tournament_id)
        capacity = int(tournament.player_limit)
    else:
        coop_event = demand.coop_event
        if coop_event is None:
            raise ValueError("coop demand is missing its event")
        mode = "coop"
        event_id = int(demand.coop_event_id or 0)
        capacity = int(coop_event.player_limit)
    operation_id = f"{mode}-{event_id}-v{int(demand.version)}-" f"{observed_at.strftime('%Y%m%dT%H%M%S%fZ')}"
    missing_count = int(demand.missing_entry_count)

    def _emit() -> None:
        try:
            config = load_virtual_player_v2_config()
            if config is None:
                raise ValueError("bot_development_v2 is not configured")
            prestige_band = config.band_for_prestige(int(population_prestige)).name
            record_arena_shortage(
                operation_id=operation_id,
                mode=mode,
                prestige_band=prestige_band,
                missing_count=missing_count,
                capacity=capacity,
                occurred_at=observed_at,
            )
        except Exception as exc:
            log_safety_metric_failure(
                operation="arena_shortage",
                exc=exc,
            )

    transaction.on_commit(_emit)


def log_demand_event(
    event_name: str,
    demand: ArenaVirtualDemand,
    *,
    message: str,
    level: int = logging.INFO,
    failure_reason: str | None = None,
    **details,
) -> None:
    if demand.tournament_id is not None:
        mode = "tournament"
        event_id = int(demand.tournament_id)
    else:
        mode = "coop"
        event_id = int(demand.coop_event_id or 0)
    ready_count = demand.reserve_members.filter(
        state=ArenaVirtualReserveMember.State.READY,
    ).count()
    training_count = demand.reserve_members.filter(
        state=ArenaVirtualReserveMember.State.TRAINING,
    ).count()
    extra = {
        "event": event_name,
        "mode": mode,
        "event_id": event_id,
        "demand_id": int(demand.id),
        "demand_version": int(demand.version),
        "missing_entry_count": int(demand.missing_entry_count),
        "reserve_target_count": int(demand.reserve_target_count),
        "ready_count": int(ready_count),
        "training_count": int(training_count),
        "failure_reason": str(demand.last_failure_reason if failure_reason is None else failure_reason),
    }
    extra.update(details)
    logger.log(level, message, extra=extra)


__all__ = ["emit_arena_shortage_after_commit", "log_demand_event"]
