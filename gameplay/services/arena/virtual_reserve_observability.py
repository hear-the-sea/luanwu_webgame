from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from celery import current_app
from django.db import transaction
from django.db.models import Count

from common.utils.celery import safe_apply_async
from gameplay.models import ArenaCoopEntry, ArenaEntry, ArenaVirtualDemand, ArenaVirtualReserveMember
from gameplay.services.virtual_player_core.config import load_virtual_player_v2_config
from gameplay.services.virtual_player_core.safety_metrics import (
    ARENA_SHORTAGE_METRIC,
    log_safety_metric_failure,
    record_arena_shortage,
    record_safety_metric_failure,
)
from gameplay.services.virtual_player_core.safety_provider import SafetyProviderError

logger = logging.getLogger("gameplay.services.arena.virtual_reserve_demand")
ARENA_SHORTAGE_METRIC_RETRY_MAX_ATTEMPTS = 3


def is_retryable_arena_shortage_metric_error(exc: Exception) -> bool:
    """Retry infrastructure failures, but stop on provider-validated terminal errors."""

    return not isinstance(exc, SafetyProviderError)


@dataclass(frozen=True, slots=True)
class _ArenaShortageObservationContext:
    """Immutable Arena state captured before an on_commit metric callback."""

    real_entry_count: int
    virtual_entry_count: int
    reserve_ready_count: int
    reserve_training_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "real_entry_count",
            "virtual_entry_count",
            "reserve_ready_count",
            "reserve_training_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ArenaShortageObservationSnapshot:
    """Canonical shortage dimensions frozen before the first provider write."""

    prestige_band: str
    real_entry_count: int
    virtual_entry_count: int
    reserve_ready_count: int
    reserve_training_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.prestige_band, str) or not self.prestige_band.strip():
            raise ValueError("prestige_band must be a non-empty string")
        context = _ArenaShortageObservationContext(
            real_entry_count=self.real_entry_count,
            virtual_entry_count=self.virtual_entry_count,
            reserve_ready_count=self.reserve_ready_count,
            reserve_training_count=self.reserve_training_count,
        )
        object.__setattr__(self, "prestige_band", self.prestige_band.strip())
        object.__setattr__(self, "real_entry_count", context.real_entry_count)
        object.__setattr__(self, "virtual_entry_count", context.virtual_entry_count)
        object.__setattr__(self, "reserve_ready_count", context.reserve_ready_count)
        object.__setattr__(self, "reserve_training_count", context.reserve_training_count)


def _capture_arena_shortage_observation_context(
    *,
    demand_id: int,
    mode: str,
    event_id: int,
) -> _ArenaShortageObservationContext:
    if mode == "tournament":
        real_entry_count = ArenaEntry.objects.filter(
            tournament_id=event_id,
            status=ArenaEntry.Status.REGISTERED,
            source=ArenaEntry.Source.PLAYER,
        ).count()
        virtual_entry_count = ArenaEntry.objects.filter(
            tournament_id=event_id,
            status=ArenaEntry.Status.REGISTERED,
            source=ArenaEntry.Source.VIRTUAL,
        ).count()
    elif mode == "coop":
        real_entry_count = ArenaCoopEntry.objects.filter(
            event_id=event_id,
            status=ArenaCoopEntry.Status.REGISTERED,
            source=ArenaCoopEntry.Source.PLAYER,
        ).count()
        virtual_entry_count = ArenaCoopEntry.objects.filter(
            event_id=event_id,
            status=ArenaCoopEntry.Status.REGISTERED,
            source=ArenaCoopEntry.Source.VIRTUAL,
        ).count()
    else:
        raise ValueError("arena shortage mode must be tournament or coop")
    reserve_ready_count = ArenaVirtualReserveMember.objects.filter(
        demand_id=demand_id,
        state=ArenaVirtualReserveMember.State.READY,
    ).count()
    reserve_training_count = ArenaVirtualReserveMember.objects.filter(
        demand_id=demand_id,
        state=ArenaVirtualReserveMember.State.TRAINING,
    ).count()
    return _ArenaShortageObservationContext(
        real_entry_count=int(real_entry_count),
        virtual_entry_count=int(virtual_entry_count),
        reserve_ready_count=int(reserve_ready_count),
        reserve_training_count=int(reserve_training_count),
    )


def _resolve_arena_shortage_observation_context(
    *,
    demand_id: int,
    mode: str,
    event_id: int,
    real_entry_count: int | None,
    virtual_entry_count: int | None,
    reserve_ready_count: int | None,
    reserve_training_count: int | None,
) -> _ArenaShortageObservationContext:
    context = _explicit_arena_shortage_observation_context(
        real_entry_count=real_entry_count,
        virtual_entry_count=virtual_entry_count,
        reserve_ready_count=reserve_ready_count,
        reserve_training_count=reserve_training_count,
    )
    if context is not None:
        return context
    return _capture_arena_shortage_observation_context(
        demand_id=demand_id,
        mode=mode,
        event_id=event_id,
    )


def _explicit_arena_shortage_observation_context(
    *,
    real_entry_count: int | None,
    virtual_entry_count: int | None,
    reserve_ready_count: int | None,
    reserve_training_count: int | None,
) -> _ArenaShortageObservationContext | None:
    """Normalize a complete serialized context without performing I/O."""
    values = (
        real_entry_count,
        virtual_entry_count,
        reserve_ready_count,
        reserve_training_count,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError("Arena shortage observation context must be complete")
    assert real_entry_count is not None
    assert virtual_entry_count is not None
    assert reserve_ready_count is not None
    assert reserve_training_count is not None
    return _ArenaShortageObservationContext(
        real_entry_count=real_entry_count,
        virtual_entry_count=virtual_entry_count,
        reserve_ready_count=reserve_ready_count,
        reserve_training_count=reserve_training_count,
    )


def prepare_arena_shortage_observation_snapshot(
    *,
    demand_id: int,
    mode: str,
    event_id: int,
    population_prestige: int,
    prestige_band: str | None = None,
    real_entry_count: int | None = None,
    virtual_entry_count: int | None = None,
    reserve_ready_count: int | None = None,
    reserve_training_count: int | None = None,
) -> ArenaShortageObservationSnapshot:
    """Resolve every mutable dimension before a retryable provider write."""

    context = _resolve_arena_shortage_observation_context(
        demand_id=demand_id,
        mode=mode,
        event_id=event_id,
        real_entry_count=real_entry_count,
        virtual_entry_count=virtual_entry_count,
        reserve_ready_count=reserve_ready_count,
        reserve_training_count=reserve_training_count,
    )
    if prestige_band is None:
        config = load_virtual_player_v2_config()
        if config is None:
            raise ValueError("bot_development_v2 is not configured")
        normalized_prestige_band = config.band_for_prestige(int(population_prestige)).name
    elif not isinstance(prestige_band, str) or not prestige_band.strip():
        raise ValueError("prestige_band must be a non-empty string when provided")
    else:
        normalized_prestige_band = prestige_band.strip()
    return ArenaShortageObservationSnapshot(
        prestige_band=normalized_prestige_band,
        real_entry_count=context.real_entry_count,
        virtual_entry_count=context.virtual_entry_count,
        reserve_ready_count=context.reserve_ready_count,
        reserve_training_count=context.reserve_training_count,
    )


def record_arena_shortage_observation(
    *,
    mode: str,
    capacity: int,
    missing_count: int,
    operation_id: str,
    observed_at: datetime,
    snapshot: ArenaShortageObservationSnapshot,
) -> None:
    if not isinstance(snapshot, ArenaShortageObservationSnapshot):
        raise ValueError("snapshot must be an ArenaShortageObservationSnapshot")
    record_arena_shortage(
        operation_id=operation_id,
        mode=mode,
        prestige_band=snapshot.prestige_band,
        missing_count=missing_count,
        capacity=capacity,
        real_entry_count=snapshot.real_entry_count,
        virtual_entry_count=snapshot.virtual_entry_count,
        reserve_ready_count=snapshot.reserve_ready_count,
        reserve_training_count=snapshot.reserve_training_count,
        occurred_at=observed_at,
    )


def queue_arena_shortage_metric_retry(
    *,
    demand_id: int,
    mode: str,
    event_id: int,
    capacity: int,
    missing_count: int,
    population_prestige: int,
    operation_id: str,
    observed_at: datetime,
    retry_attempt: int,
    real_entry_count: int | None = None,
    virtual_entry_count: int | None = None,
    reserve_ready_count: int | None = None,
    reserve_training_count: int | None = None,
    prestige_band: str | None = None,
) -> bool:
    context = _explicit_arena_shortage_observation_context(
        real_entry_count=real_entry_count,
        virtual_entry_count=virtual_entry_count,
        reserve_ready_count=reserve_ready_count,
        reserve_training_count=reserve_training_count,
    )
    # An empty context is only a deferred capture request. The task freezes a
    # complete snapshot before its first provider write and carries it onward.
    context_values = (
        (None, None, None, None)
        if context is None
        else (
            context.real_entry_count,
            context.virtual_entry_count,
            context.reserve_ready_count,
            context.reserve_training_count,
        )
    )
    task = current_app.signature("gameplay.retry_arena_shortage_metric")
    return safe_apply_async(
        task,
        args=[
            int(demand_id),
            mode,
            int(event_id),
            int(capacity),
            int(missing_count),
            int(population_prestige),
            operation_id,
            observed_at.isoformat(),
            int(retry_attempt),
            *context_values,
            prestige_band,
        ],
        countdown=min(900, 60 * (2 ** max(0, int(retry_attempt)))),
        logger=logger,
        log_message="arena shortage metric retry dispatch failed",
        log_extra={
            "event": "arena_shortage_metric_retry_dispatch_failed",
            "demand_id": int(demand_id),
            "operation_id": operation_id,
            "retry_attempt": int(retry_attempt),
        },
    )


def record_arena_shortage_metric_failure(
    *,
    operation_id: str,
    observed_at: datetime,
    exc: Exception,
):
    """Persist a hard failure marker for an arena metric write."""

    return record_safety_metric_failure(
        operation=operation_id,
        source_metric=ARENA_SHORTAGE_METRIC,
        exc=exc,
        occurred_at=observed_at,
    )


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
    observation_snapshot: ArenaShortageObservationSnapshot | None = None
    try:
        observation_snapshot = prepare_arena_shortage_observation_snapshot(
            demand_id=int(demand.id),
            mode=mode,
            event_id=event_id,
            population_prestige=int(population_prestige),
        )
    except Exception:
        logger.exception(
            "arena shortage observation snapshot capture failed; callback will retry capture",
            extra={
                "event": "arena_shortage_observation_context_capture_failed",
                "demand_id": int(demand.id),
                "operation_id": operation_id,
            },
        )

    def _emit() -> None:
        snapshot = observation_snapshot
        try:
            if snapshot is None:
                snapshot = prepare_arena_shortage_observation_snapshot(
                    demand_id=int(demand.id),
                    mode=mode,
                    event_id=event_id,
                    population_prestige=int(population_prestige),
                )
            record_arena_shortage_observation(
                mode=mode,
                capacity=capacity,
                missing_count=missing_count,
                operation_id=operation_id,
                observed_at=observed_at,
                snapshot=snapshot,
            )
        except Exception as exc:
            try:
                record_arena_shortage_metric_failure(
                    operation_id=operation_id,
                    observed_at=observed_at,
                    exc=exc,
                )
            except Exception:
                logger.exception(
                    "arena shortage metric failure marker could not be persisted",
                    extra={
                        "event": "arena_shortage_metric_failure_marker_error",
                        "demand_id": int(demand.id),
                        "operation_id": operation_id,
                    },
                )
            retry_scheduled = False
            if ARENA_SHORTAGE_METRIC_RETRY_MAX_ATTEMPTS > 0 and is_retryable_arena_shortage_metric_error(exc):
                try:
                    retry_scheduled = queue_arena_shortage_metric_retry(
                        demand_id=int(demand.id),
                        mode=mode,
                        event_id=event_id,
                        capacity=capacity,
                        missing_count=missing_count,
                        population_prestige=int(population_prestige),
                        operation_id=operation_id,
                        observed_at=observed_at,
                        retry_attempt=1,
                        real_entry_count=(snapshot.real_entry_count if snapshot else None),
                        virtual_entry_count=(snapshot.virtual_entry_count if snapshot else None),
                        reserve_ready_count=(snapshot.reserve_ready_count if snapshot else None),
                        reserve_training_count=(snapshot.reserve_training_count if snapshot else None),
                        prestige_band=(snapshot.prestige_band if snapshot else None),
                    )
                except Exception:
                    logger.exception(
                        "arena shortage metric retry could not be scheduled",
                        extra={
                            "event": "arena_shortage_metric_retry_schedule_error",
                            "demand_id": int(demand.id),
                            "operation_id": operation_id,
                        },
                    )
            log_safety_metric_failure(
                operation="arena_shortage",
                exc=exc,
            )
            logger.warning(
                "arena shortage metric write deferred for retry: demand_id=%s operation_id=%s retry_scheduled=%s",
                demand.id,
                operation_id,
                retry_scheduled,
                extra={
                    "event": "arena_shortage_metric_write_deferred",
                    "demand_id": int(demand.id),
                    "operation_id": operation_id,
                    "retry_scheduled": bool(retry_scheduled),
                },
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
    member_counts = dict(
        demand.reserve_members.values("state").annotate(count=Count("id")).values_list("state", "count")
    )
    ready_count = int(member_counts.get(ArenaVirtualReserveMember.State.READY, 0))
    training_count = int(member_counts.get(ArenaVirtualReserveMember.State.TRAINING, 0))
    exhausted_count = int(member_counts.get(ArenaVirtualReserveMember.State.EXHAUSTED, 0))
    roster_target_distribution = {
        str(target): int(count)
        for target, count in (
            demand.reserve_members.values("roster_target_count")
            .annotate(count=Count("id"))
            .values_list("roster_target_count", "count")
        )
        if target is not None
    }
    extra = {
        "event": event_name,
        "mode": mode,
        "event_id": event_id,
        "demand_id": int(demand.id),
        "demand_version": int(demand.version),
        "demand_status": str(demand.status),
        "target_guest_count": int(demand.target_guest_count),
        "target_team_power": int(demand.target_team_power),
        "missing_entry_count": int(demand.missing_entry_count),
        "reserve_target_count": int(demand.reserve_target_count),
        "warm_target_count": int(demand.warm_target_count),
        "max_reserve_target_count": int(demand.max_reserve_target_count),
        "admission_attempt_high_water": int(demand.admission_attempt_high_water),
        "admission_paused_at": demand.admission_paused_at.isoformat() if demand.admission_paused_at else None,
        "admission_pause_reason": str(demand.admission_pause_reason),
        "consecutive_failure_count": int(demand.consecutive_failure_count),
        "last_progress_at": demand.last_progress_at.isoformat() if demand.last_progress_at else None,
        "last_input_change_at": demand.last_input_change_at.isoformat() if demand.last_input_change_at else None,
        "ready_count": int(ready_count),
        "training_count": int(training_count),
        "exhausted_count": int(exhausted_count),
        "roster_target_distribution": roster_target_distribution,
        "failure_reason": str(demand.last_failure_reason if failure_reason is None else failure_reason),
    }
    extra.update(details)
    logger.log(level, message, extra=extra)


__all__ = [
    "ARENA_SHORTAGE_METRIC_RETRY_MAX_ATTEMPTS",
    "ArenaShortageObservationSnapshot",
    "emit_arena_shortage_after_commit",
    "is_retryable_arena_shortage_metric_error",
    "log_demand_event",
    "prepare_arena_shortage_observation_snapshot",
    "queue_arena_shortage_metric_retry",
    "record_arena_shortage_metric_failure",
    "record_arena_shortage_observation",
]
