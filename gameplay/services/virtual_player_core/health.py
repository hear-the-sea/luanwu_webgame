from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from django.conf import settings
from django.db import transaction

from gameplay.models import BotVirtualPlayerHealth


@dataclass(frozen=True, slots=True)
class VirtualPlayerHealthSnapshot:
    status: str
    retryable_failure_streak: int
    clean_success_streak: int
    next_probe_at: datetime | None
    revision: int


def _health_row() -> BotVirtualPlayerHealth | None:
    """Read the singleton health row without taking a database lock."""
    return BotVirtualPlayerHealth.objects.filter(
        key=BotVirtualPlayerHealth.GLOBAL_KEY,
    ).first()


def _health_row_locked(*, create: bool) -> BotVirtualPlayerHealth | None:
    row = (
        BotVirtualPlayerHealth.objects.select_for_update()
        .filter(
            key=BotVirtualPlayerHealth.GLOBAL_KEY,
        )
        .first()
    )
    if row is None and create:
        BotVirtualPlayerHealth.objects.get_or_create(key=BotVirtualPlayerHealth.GLOBAL_KEY)
        row = BotVirtualPlayerHealth.objects.select_for_update().get(
            key=BotVirtualPlayerHealth.GLOBAL_KEY,
        )
    return row


def _snapshot(row: BotVirtualPlayerHealth) -> VirtualPlayerHealthSnapshot:
    return VirtualPlayerHealthSnapshot(
        status=str(row.status),
        retryable_failure_streak=int(row.retryable_failure_streak),
        clean_success_streak=int(row.clean_success_streak),
        next_probe_at=row.next_probe_at,
        revision=int(row.revision),
    )


def _error_digest(error: object) -> str:
    return sha256(str(error).encode("utf-8", errors="replace")).hexdigest()


def retryable_failure(
    *,
    failure_code: str,
    error: object,
    now: datetime,
) -> VirtualPlayerHealthSnapshot:
    """Open a bounded circuit for systemic transient failures."""
    if not transaction.get_connection().in_atomic_block:
        with transaction.atomic():
            return retryable_failure(failure_code=failure_code, error=error, now=now)
    row = _health_row_locked(create=True)
    assert row is not None
    row.retryable_failure_streak = min(255, int(row.retryable_failure_streak) + 1)
    row.clean_success_streak = 0
    row.last_failure_code = str(failure_code)[:64]
    row.last_error_digest = _error_digest(error)
    row.last_failure_at = now
    if row.retryable_failure_streak >= int(settings.VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD):
        row.status = BotVirtualPlayerHealth.Status.DEGRADED
        row.next_probe_at = now + timedelta(seconds=int(settings.VIRTUAL_PLAYER_HEALTH_COOLDOWN_SECONDS))
    else:
        row.status = BotVirtualPlayerHealth.Status.HEALTHY
        row.next_probe_at = None
    row.revision += 1
    row.save(
        update_fields=[
            "status",
            "retryable_failure_streak",
            "clean_success_streak",
            "next_probe_at",
            "last_failure_code",
            "last_error_digest",
            "last_failure_at",
            "revision",
            "updated_at",
        ]
    )
    return _snapshot(row)


def reconciliation_deferred_until(*, now: datetime) -> datetime | None:
    """Return the next health probe deadline without consuming an attempt."""
    row = _health_row()
    if row is None or row.next_probe_at is None:
        return None
    if row.status not in {
        BotVirtualPlayerHealth.Status.DEGRADED,
        BotVirtualPlayerHealth.Status.RECOVERING,
    }:
        return None
    return row.next_probe_at if row.next_probe_at > now else None


def reconciliation_success(*, now: datetime) -> VirtualPlayerHealthSnapshot | None:
    """Move the circuit through recovery and close it after clean probes."""
    if not transaction.get_connection().in_atomic_block:
        with transaction.atomic():
            return reconciliation_success(now=now)
    row = _health_row_locked(create=False)
    if row is None:
        return None
    if row.status == BotVirtualPlayerHealth.Status.HEALTHY:
        if row.retryable_failure_streak == 0 and row.clean_success_streak == 0:
            return _snapshot(row)
        row.retryable_failure_streak = 0
        row.clean_success_streak = 0
        row.next_probe_at = None
        row.revision += 1
        row.save(
            update_fields=[
                "retryable_failure_streak",
                "clean_success_streak",
                "next_probe_at",
                "revision",
                "updated_at",
            ]
        )
        return _snapshot(row)
    row.clean_success_streak = min(255, int(row.clean_success_streak) + 1)
    if row.clean_success_streak >= int(settings.VIRTUAL_PLAYER_HEALTH_RECOVERY_SUCCESS_THRESHOLD):
        row.status = BotVirtualPlayerHealth.Status.HEALTHY
        row.retryable_failure_streak = 0
        row.clean_success_streak = 0
        row.next_probe_at = None
        row.last_recovered_at = now
    else:
        row.status = BotVirtualPlayerHealth.Status.RECOVERING
        row.next_probe_at = now + timedelta(seconds=int(settings.VIRTUAL_PLAYER_HEALTH_RECOVERY_PROBE_SECONDS))
    row.revision += 1
    row.save(
        update_fields=[
            "status",
            "retryable_failure_streak",
            "clean_success_streak",
            "next_probe_at",
            "last_recovered_at",
            "revision",
            "updated_at",
        ]
    )
    return _snapshot(row)


__all__ = [
    "VirtualPlayerHealthSnapshot",
    "reconciliation_deferred_until",
    "reconciliation_success",
    "retryable_failure",
]
