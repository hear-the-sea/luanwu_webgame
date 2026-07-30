from __future__ import annotations

import logging
from datetime import datetime

from celery import shared_task
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gameplay.services.jail import VIRTUAL_JAIL_CLEANUP_DEFAULT_BATCH_SIZE, cleanup_virtual_player_jail
from gameplay.services.virtual_player_core.external_reconciliation import (
    reconcile_external_reconciliation,
    scan_external_reconciliations,
)
from gameplay.services.virtual_player_core.maintenance import maintain_due_virtual_players
from gameplay.services.virtual_player_core.population_runtime import (
    plan_virtual_player_population,
    reconcile_virtual_player_population_cell,
    roll_virtual_player_population,
    scan_virtual_player_population_demands,
)
from gameplay.services.virtual_player_core.safety_metrics import record_safety_heartbeat
from gameplay.services.virtual_player_core.safety_monitor import finalize_due_safety_windows, run_safety_monitor
from gameplay.services.virtual_player_core.safety_provider import cleanup_safety_metric_retention

logger = logging.getLogger(__name__)


def _heartbeat_payload(stream: str) -> dict[str, object]:
    result = record_safety_heartbeat(stream)
    return {
        "stream": stream,
        "event_id": result.event_id,
        "created": result.created,
    }


def _virtual_jail_cleanup_cutoff(value: str | None) -> datetime:
    if value is None:
        return timezone.now()
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        raise ValueError("cutoff must be an ISO-8601 timezone-aware datetime")
    return parsed


@shared_task(name="gameplay.plan_virtual_players")
def plan_virtual_players_task() -> dict:
    """Record an instantaneous virtual-player population plan without creating players."""
    return plan_virtual_player_population()


@shared_task(name="gameplay.roll_virtual_players")
def roll_virtual_players_task(limit: int | None = None) -> int:
    """Apply a small rolling slice of virtual-player population changes."""
    maintained = maintain_due_virtual_players(limit=100)
    population_processed = roll_virtual_player_population(limit=limit)
    logger.info(
        "Completed virtual player roll: maintained=%d population_processed=%d",
        maintained,
        population_processed,
        extra={
            "event": "virtual_player_roll_completed",
            "maintained_count": maintained,
            "population_processed_count": population_processed,
        },
    )
    return population_processed


@shared_task(name="gameplay.reconcile_virtual_player_population_cell")
def reconcile_virtual_player_population_cell_task(
    region: str,
    prestige_band: str,
    limit: int = 8,
) -> dict:
    """Reconcile one durable population cell without running Maintenance."""
    return reconcile_virtual_player_population_cell(
        region=region,
        prestige_band=prestige_band,
        limit=limit,
    ).to_payload()


@shared_task(name="gameplay.scan_virtual_player_population_demands")
def scan_virtual_player_population_demands_task(
    limit: int = 100,
    cell_limit: int = 8,
) -> list[dict]:
    """Recover due or expired durable population claims."""
    return [
        result.to_payload()
        for result in scan_virtual_player_population_demands(
            limit=limit,
            cell_limit=cell_limit,
        )
    ]


@shared_task(name="gameplay.reconcile_external_strength_reconciliation")
def reconcile_external_strength_reconciliation_task(
    reconciliation_id: int,
) -> dict:
    """Process one durable external-strength reconciliation intent."""
    return reconcile_external_reconciliation(reconciliation_id).to_payload()


@shared_task(name="gameplay.scan_external_strength_reconciliations")
def scan_external_strength_reconciliations_task(limit: int = 100) -> list[dict]:
    """Recover due or expired external-strength reconciliation claims."""
    return [result.to_payload() for result in scan_external_reconciliations(limit=limit)]


@shared_task(name="gameplay.heartbeat_virtual_player_maintenance_attempt_emitter")
def heartbeat_virtual_player_maintenance_attempt_emitter_task() -> dict[str, object]:
    return _heartbeat_payload("maintenance_attempt_emitter")


@shared_task(name="gameplay.heartbeat_virtual_player_h01_callback_attempt_emitter")
def heartbeat_virtual_player_h01_callback_attempt_emitter_task() -> dict[str, object]:
    return _heartbeat_payload("h01_callback_attempt_emitter")


@shared_task(name="gameplay.heartbeat_virtual_player_arena_shortage_emitter")
def heartbeat_virtual_player_arena_shortage_emitter_task() -> dict[str, object]:
    return _heartbeat_payload("arena_shortage_emitter")


@shared_task(name="gameplay.aggregate_virtual_player_safety")
def aggregate_virtual_player_safety_task(limit: int = 100) -> dict[str, object]:
    windows = finalize_due_safety_windows(limit=limit)
    heartbeat = _heartbeat_payload("safety_aggregator")
    return {
        "heartbeat": heartbeat,
        "finalized_count": len(windows),
        "window_ids": [window.window_id for window in windows],
    }


@shared_task(name="gameplay.monitor_virtual_player_safety")
def monitor_virtual_player_safety_task(limit: int = 100) -> dict[str, object]:
    cycle = run_safety_monitor(limit=limit)
    result = cycle.monitor
    heartbeat = _heartbeat_payload("safety_monitor")
    return {
        "heartbeat": heartbeat,
        "finalized_count": len(cycle.finalized_windows),
        "finalized_window_ids": [window.window_id for window in cycle.finalized_windows],
        "decision_count": len(result.decisions),
        "consumed_count": result.consumed_count,
        "paused": result.paused,
        "cas_conflicts": result.cas_conflicts,
        "window_ids": [decision.window_id for decision in result.decisions],
    }


@shared_task(name="gameplay.cleanup_virtual_player_safety_metrics")
def cleanup_virtual_player_safety_metrics_task(
    batch_size: int = 1_000,
) -> dict[str, object]:
    result = cleanup_safety_metric_retention(batch_size=batch_size)
    return {
        "events_deleted": result.events_deleted,
        "windows_deleted": result.windows_deleted,
        "event_cutoff": result.event_cutoff.isoformat(),
        "window_cutoff": result.window_cutoff.isoformat(),
    }


@shared_task(name="gameplay.cleanup_virtual_player_jail")
def cleanup_virtual_player_jail_task(
    cutoff: str | None = None,
    batch_size: int = VIRTUAL_JAIL_CLEANUP_DEFAULT_BATCH_SIZE,
) -> dict[str, object]:
    frozen_cutoff = _virtual_jail_cleanup_cutoff(cutoff)
    result = cleanup_virtual_player_jail(
        cutoff=frozen_cutoff,
        batch_size=batch_size,
    )
    payload = result.to_payload()
    logger.info(
        "Cleaned virtual-player jail: cutoff=%s released=%d skipped=%d failed=%d oldest_remaining_age_seconds=%s",
        payload["cutoff"],
        payload["released"],
        payload["skipped"],
        payload["failed"],
        payload["oldest_remaining_age_seconds"],
    )
    return payload
