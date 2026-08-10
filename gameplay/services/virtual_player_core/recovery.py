"""Durable failure classification and per-entity recovery for Bot maintenance."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.db.models import CharField, Exists, OuterRef, Q
from django.db.models.functions import Cast
from django.utils import timezone

from gameplay.models import BotMaintenanceRecovery


class RecoveryFailureClass(StrEnum):
    PAUSED = "paused"
    BUSY = "busy"
    NO_ACTION = "no_action"
    COMMIT_UNCERTAIN = "commit_uncertain"
    PROGRAMMER_ERROR = "programmer_error"
    INFRASTRUCTURE = "infrastructure"


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    retry_base_seconds: int = 60
    retry_max_seconds: int = 3_600
    quarantine_after_failures: int = 3
    maximum_recovery_age: timedelta = timedelta(days=2)
    circuit_failure_threshold: int = 3
    circuit_window: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        for field_name in (
            "retry_base_seconds",
            "retry_max_seconds",
            "quarantine_after_failures",
            "circuit_failure_threshold",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("retry_base_seconds must not exceed retry_max_seconds")
        if self.maximum_recovery_age <= timedelta(0):
            raise ValueError("maximum_recovery_age must be positive")
        if self.circuit_window <= timedelta(0):
            raise ValueError("circuit_window must be positive")


def classify_failure(
    error: BaseException | None = None,
    *,
    outcome: str | None = None,
    commit_uncertain: bool = False,
) -> RecoveryFailureClass:
    """Translate execution errors into the small durable recovery vocabulary."""

    if commit_uncertain:
        return RecoveryFailureClass.COMMIT_UNCERTAIN
    normalized_outcome = str(outcome or "").strip().lower()
    if normalized_outcome in {"paused", "pause"}:
        return RecoveryFailureClass.PAUSED
    if normalized_outcome == "busy":
        return RecoveryFailureClass.BUSY
    if normalized_outcome == "no_action":
        return RecoveryFailureClass.NO_ACTION
    if isinstance(error, (OperationalError, DatabaseError)):
        return RecoveryFailureClass.INFRASTRUCTURE
    if isinstance(error, IntegrityError):
        return RecoveryFailureClass.INFRASTRUCTURE
    if error is None:
        return RecoveryFailureClass.PROGRAMMER_ERROR
    return RecoveryFailureClass.PROGRAMMER_ERROR


def digest_failure(
    failure_class: RecoveryFailureClass | str,
    *,
    error: BaseException | None = None,
    detail: str = "",
) -> str:
    normalized_class = str(failure_class)
    error_name = "" if error is None else type(error).__name__
    # The message is deliberately truncated and hashed.  Recovery rows are
    # operational state, not an exception dump or a place for user data.
    message = detail or ("" if error is None else str(error))
    payload = f"{normalized_class}|{error_name}|{message[:512]}".encode("utf-8", "replace")
    return hashlib.sha256(payload).hexdigest()


def _retry_at(now: datetime, streak: int, policy: RecoveryPolicy) -> datetime:
    delay = min(
        policy.retry_max_seconds,
        policy.retry_base_seconds * (2 ** max(0, min(streak - 1, 10))),
    )
    return now + timedelta(seconds=delay)


_CIRCUIT_SCOPE_BY_PATH = {
    "profile": BotMaintenanceRecovery.Scope.PROFILE,
    "population": BotMaintenanceRecovery.Scope.POPULATION_CELL,
    "population_cell": BotMaintenanceRecovery.Scope.POPULATION_CELL,
    "arena_member": BotMaintenanceRecovery.Scope.ARENA_MEMBER,
    "arena_demand": BotMaintenanceRecovery.Scope.ARENA_DEMAND,
}


def _circuit_scope_for_path(path: str, *, fallback_scope: str) -> str:
    normalized_path = str(path).strip()
    configured_scope = _CIRCUIT_SCOPE_BY_PATH.get(normalized_path)
    return str(configured_scope.value if configured_scope is not None else fallback_scope)


def _record_path_circuit_locked(
    *,
    path: str,
    scope: str,
    failure_digest: str,
    now: datetime,
    policy: RecoveryPolicy,
    circuit_scope: str | None = None,
) -> None:
    """Open one path-specific circuit after the same poison spreads."""

    window_start = now - policy.circuit_window
    affected = list(
        BotMaintenanceRecovery.objects.select_for_update()
        .filter(
            scope=scope,
            failure_digest=failure_digest,
            last_failed_at__gte=window_start,
        )
        .exclude(entity_key__startswith="circuit:")
        .exclude(status=BotMaintenanceRecovery.Status.REQUEUED)
        .values("entity_key", "first_failed_at", "last_failed_at")
    )
    if len(affected) < policy.circuit_failure_threshold:
        return
    normalized_path = str(path).strip()
    if not normalized_path:
        return
    normalized_circuit_scope = _circuit_scope_for_path(
        normalized_path,
        fallback_scope=(scope if circuit_scope is None else circuit_scope),
    )
    first_failed_at = min(row["first_failed_at"] for row in affected)
    circuit_key = f"circuit:{normalized_path}"
    circuit_payload = {
        "circuit_path": normalized_path,
        "source_scope": scope,
        "circuit_scope": normalized_circuit_scope,
        "failure_digest": failure_digest,
        "affected_entity_count": len(affected),
        "window_seconds": int(policy.circuit_window.total_seconds()),
        "quarantine_reason": "repeated_failure_digest",
    }
    circuit, created = BotMaintenanceRecovery.objects.select_for_update().get_or_create(
        scope=normalized_circuit_scope,
        entity_key=circuit_key,
        defaults={
            "status": BotMaintenanceRecovery.Status.QUARANTINED,
            "failure_code": "circuit_breaker",
            "failure_digest": failure_digest,
            "failure_streak": len(affected),
            "first_failed_at": first_failed_at,
            "last_failed_at": now,
            "next_retry_at": None,
            "quarantined_at": now,
            "last_operation_id": "",
            "payload": circuit_payload,
        },
    )
    if created:
        return
    circuit.status = BotMaintenanceRecovery.Status.QUARANTINED
    circuit.failure_code = "circuit_breaker"
    circuit.failure_digest = failure_digest
    circuit.failure_streak = len(affected)
    circuit.first_failed_at = min(circuit.first_failed_at, first_failed_at)
    circuit.last_failed_at = now
    circuit.next_retry_at = None
    circuit.quarantined_at = now
    circuit.payload = circuit_payload
    circuit.save(
        update_fields=[
            "status",
            "failure_code",
            "failure_digest",
            "failure_streak",
            "first_failed_at",
            "last_failed_at",
            "next_retry_at",
            "quarantined_at",
            "payload",
            "updated_at",
        ]
    )


def record_recovery_failure(
    *,
    scope: str,
    entity_key: str,
    failure_code: RecoveryFailureClass | str,
    now: datetime | None = None,
    error: BaseException | None = None,
    operation_id: str = "",
    payload: dict[str, Any] | None = None,
    policy: RecoveryPolicy = RecoveryPolicy(),
    circuit_path: str | None = None,
) -> BotMaintenanceRecovery:
    """Persist one isolated failure and return the locked row's new state."""

    current_time = now or timezone.now()
    if timezone.is_naive(current_time):
        raise ValueError("recovery timestamp must be timezone-aware")
    normalized_scope = str(scope).strip()
    normalized_entity = str(entity_key).strip()
    normalized_code = str(failure_code).strip()
    if not normalized_scope or not normalized_entity or not normalized_code:
        raise ValueError("recovery scope, entity_key and failure_code are required")
    failure_digest = digest_failure(normalized_code, error=error)
    with transaction.atomic():
        row = (
            BotMaintenanceRecovery.objects.select_for_update()
            .filter(scope=normalized_scope, entity_key=normalized_entity)
            .first()
        )
        same_failure = row is not None and row.failure_code == normalized_code and row.failure_digest == failure_digest
        if same_failure:
            assert row is not None
            streak = int(row.failure_streak) + 1
            first_failed_at = row.first_failed_at
        else:
            streak = 1
            first_failed_at = current_time
        age_exceeded = same_failure and current_time - first_failed_at >= policy.maximum_recovery_age
        quarantined = streak >= policy.quarantine_after_failures or age_exceeded
        updates = {
            "failure_code": normalized_code,
            "failure_digest": failure_digest,
            "failure_streak": streak,
            "first_failed_at": first_failed_at,
            "last_failed_at": current_time,
            "next_retry_at": None if quarantined else _retry_at(current_time, streak, policy),
            "quarantined_at": current_time if quarantined else None,
            "last_operation_id": str(operation_id or ""),
            "payload": dict(payload or {}),
            "status": (
                BotMaintenanceRecovery.Status.QUARANTINED if quarantined else BotMaintenanceRecovery.Status.RETRY
            ),
        }
        if age_exceeded:
            updates["payload"] = {
                **dict(payload or {}),
                "quarantine_reason": "maximum_recovery_age_exceeded",
            }
        if row is None:
            row = BotMaintenanceRecovery.objects.create(
                scope=normalized_scope,
                entity_key=normalized_entity,
                **updates,
            )
        else:
            for field_name, value in updates.items():
                setattr(row, field_name, value)
            row.save(update_fields=[*updates, "updated_at"])
        if normalized_code == RecoveryFailureClass.PROGRAMMER_ERROR.value:
            _record_path_circuit_locked(
                path=(normalized_scope if circuit_path is None else str(circuit_path)),
                scope=normalized_scope,
                failure_digest=failure_digest,
                now=current_time,
                policy=policy,
                circuit_scope=_circuit_scope_for_path(
                    normalized_scope if circuit_path is None else str(circuit_path),
                    fallback_scope=normalized_scope,
                ),
            )
        return row


def clear_recovery_failure(
    *,
    scope: str,
    entity_key: str,
    now: datetime | None = None,
) -> bool:
    """Clear a failure streak after a committed healthy result."""

    current_time = now or timezone.now()
    updated = BotMaintenanceRecovery.objects.filter(
        scope=str(scope),
        entity_key=str(entity_key),
    ).update(
        status=BotMaintenanceRecovery.Status.REQUEUED,
        failure_streak=0,
        next_retry_at=None,
        quarantined_at=None,
        requeued_at=current_time,
        last_success_at=current_time,
        updated_at=current_time,
    )
    return bool(updated)


def requeue_recovery(
    *,
    scope: str,
    entity_key: str,
    now: datetime | None = None,
    reason: str = "manual_requeue",
) -> BotMaintenanceRecovery:
    """Formal repair entry point; callers do not mutate recovery rows directly."""

    current_time = now or timezone.now()
    with transaction.atomic():
        row = BotMaintenanceRecovery.objects.select_for_update().get(
            scope=str(scope),
            entity_key=str(entity_key),
        )
        row.status = BotMaintenanceRecovery.Status.REQUEUED
        row.next_retry_at = current_time
        row.requeued_at = current_time
        row.quarantined_at = None
        row.failure_streak = 0
        row.payload = {**(row.payload or {}), "requeue_reason": str(reason)}
        row.save(
            update_fields=[
                "status",
                "next_retry_at",
                "requeued_at",
                "quarantined_at",
                "failure_streak",
                "payload",
                "updated_at",
            ]
        )
        return row


def recovery_is_due(
    row: BotMaintenanceRecovery,
    *,
    now: datetime | None = None,
) -> bool:
    current_time = now or timezone.now()
    if row.status == BotMaintenanceRecovery.Status.QUARANTINED:
        return False
    return row.next_retry_at is None or row.next_retry_at <= current_time


def recovery_is_blocked(
    *,
    scope: str,
    entity_key: str,
    now: datetime | None = None,
) -> bool:
    row = BotMaintenanceRecovery.objects.filter(
        scope=str(scope),
        entity_key=str(entity_key),
    ).first()
    if row is None:
        return False
    return not recovery_is_due(row, now=now)


def recovery_circuit_is_open(
    *,
    path: str,
    now: datetime | None = None,
    scope: str | None = None,
) -> bool:
    normalized_path = str(path).strip()
    circuit_scope = _circuit_scope_for_path(
        normalized_path,
        fallback_scope=(BotMaintenanceRecovery.Scope.PROFILE if scope is None else str(scope)),
    )
    row = BotMaintenanceRecovery.objects.filter(
        scope=circuit_scope,
        entity_key=f"circuit:{normalized_path}",
    ).first()
    return row is not None and not recovery_is_due(row, now=now)


def exclude_blocked_profile_recoveries(queryset, *, now: datetime | None = None):
    """Exclude only quarantined/not-yet-due profiles before applying a batch cap."""

    return exclude_blocked_entity_recoveries(
        queryset,
        scope=BotMaintenanceRecovery.Scope.PROFILE,
        now=now,
    )


def exclude_blocked_entity_recoveries(
    queryset,
    *,
    scope: RecoveryFailureClass | str,
    now: datetime | None = None,
    entity_field: str = "pk",
):
    """Exclude durable recovery rows before a caller applies its batch cap.

    The small ``hasattr`` fallback keeps lightweight selector fakes usable in
    unit tests; real Django querysets always take the SQL ``EXISTS`` path.
    """

    current_time = now or timezone.now()
    if not hasattr(queryset, "annotate"):
        return queryset
    blocked = BotMaintenanceRecovery.objects.filter(
        scope=str(scope),
        entity_key=Cast(OuterRef(entity_field), output_field=CharField()),
    ).filter(
        Q(status=BotMaintenanceRecovery.Status.QUARANTINED) | Q(next_retry_at__gt=current_time),
    )
    return queryset.annotate(_has_blocked_recovery=Exists(blocked)).filter(_has_blocked_recovery=False)


__all__ = [
    "RecoveryFailureClass",
    "RecoveryPolicy",
    "classify_failure",
    "clear_recovery_failure",
    "digest_failure",
    "exclude_blocked_entity_recoveries",
    "exclude_blocked_profile_recoveries",
    "record_recovery_failure",
    "recovery_circuit_is_open",
    "recovery_is_blocked",
    "recovery_is_due",
    "requeue_recovery",
]
