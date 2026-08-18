"""Durable reconcile for domain work completed by a virtual-player Manor."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from celery import current_app
from django.db import transaction
from django.utils import timezone

from common.utils.celery import safe_apply_async_with_dedup
from gameplay.models import (
    BotMaintenanceCompletionEvent,
    BotMaintenanceCycle,
    BotProfile,
    Building,
    Manor,
    PlayerTechnology,
)
from guests.models import Guest, GuestRecruitment
from guests.services.salary import bulk_check_salary_paid, quote_all_salaries

from .archetype_pacing import pacing_from_cycle_payload
from .config import load_virtual_player_config
from .maintenance_cycle import CANDIDATE_POOL_COOLDOWN_PAYLOAD_KEY, wake_candidate_pool_cooldowns
from .maintenance_resources import VIRTUAL_PLAYER_SALARY_SCALE, salary_runway_commitment
from .profile_store import set_next_growth_at
from .reference_snapshots import load_manor_strength_summary

logger = logging.getLogger(__name__)

COMPLETION_RECONCILE_TASK_NAME = "gameplay.reconcile_virtual_player_maintenance_completion"
COMPLETION_RECONCILE_DEDUP_SECONDS = 6 * 60 * 60
COMPLETION_RECONCILE_BATCH_SIZE = 200

_DOMAIN_TO_SOURCE: dict[str, str] = {
    BotMaintenanceCompletionEvent.DomainKind.BUILDING_UPGRADE: "building.upgrade_complete_at",
    BotMaintenanceCompletionEvent.DomainKind.TECHNOLOGY_UPGRADE: "technology.upgrade_complete_at",
    BotMaintenanceCompletionEvent.DomainKind.GUEST_TRAINING: "guest.training_complete_at",
    BotMaintenanceCompletionEvent.DomainKind.GUEST_RECRUITMENT: "guest_recruitment.complete_at",
}


class MaintenanceCompletionError(ValueError):
    """Raised when a completion event cannot satisfy its durable contract."""


def _normalize_positive_id(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MaintenanceCompletionError(f"{field} must be a positive integer")
    return value


def _normalize_completion_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        raise MaintenanceCompletionError("origin_completed_at must be timezone-aware")
    return value


def _datetime_payload(value: datetime | None) -> str | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        raise MaintenanceCompletionError("reconcile timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _event_identity(
    *,
    profile_id: int,
    domain_event_kind: str,
    domain_object_id: int,
    origin_completed_at: datetime,
) -> str:
    timestamp = origin_completed_at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return f"vp-maint-completion:{profile_id}:{domain_event_kind}:{domain_object_id}:{timestamp}"


def _snapshot_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {str(key): cast(Any, value) for key, value in row.items()}


def _queue_reconcile_task(event_id: int) -> bool:
    task = current_app.signature(COMPLETION_RECONCILE_TASK_NAME)
    return safe_apply_async_with_dedup(
        task,
        dedup_key=f"vp-maint-completion:{int(event_id)}",
        dedup_timeout=COMPLETION_RECONCILE_DEDUP_SECONDS,
        args=[int(event_id)],
        logger=logger,
        log_message="virtual-player maintenance completion dispatch failed; periodic scan will recover it",
        log_extra={
            "event": "virtual_player_maintenance_completion_dispatch_deferred",
            "completion_event_id": int(event_id),
        },
    )


def record_virtual_player_maintenance_completion(
    *,
    manor_id: int | None,
    domain_event_kind: str,
    domain_object_id: int,
    origin_completed_at: datetime | None,
    available_at: datetime | None = None,
) -> int | None:
    """Record one completion for an eligible V2 profile and enqueue reconcile.

    The helper is intentionally a no-op for real-player Manors.  Completion
    workers and fallback scans can therefore share it without creating a
    second domain event stream for ordinary users.
    """

    if manor_id is None:
        return None
    normalized_manor_id = _normalize_positive_id(manor_id, field="manor_id")
    normalized_object_id = _normalize_positive_id(domain_object_id, field="domain_object_id")
    normalized_kind = str(domain_event_kind).strip()
    if normalized_kind not in BotMaintenanceCompletionEvent.DomainKind.values:
        raise MaintenanceCompletionError("unsupported maintenance completion domain")
    normalized_time = _normalize_completion_time(origin_completed_at)
    if normalized_time is None:
        return None
    normalized_available_at = _normalize_completion_time(available_at) or timezone.now()

    profile_id = (
        BotProfile.objects.filter(
            manor_id=normalized_manor_id,
            engine_version=2,
            policy_version=2,
        )
        .values_list("id", flat=True)
        .first()
    )
    if profile_id is None:
        return None
    event_id = _event_identity(
        profile_id=int(profile_id),
        domain_event_kind=normalized_kind,
        domain_object_id=normalized_object_id,
        origin_completed_at=normalized_time,
    )
    event, _created = BotMaintenanceCompletionEvent.objects.get_or_create(
        domain_event_id=event_id,
        defaults={
            "profile_id": int(profile_id),
            "domain_event_kind": normalized_kind,
            "domain_object_id": normalized_object_id,
            "origin_completed_at": normalized_time,
            "available_at": normalized_available_at,
        },
    )
    if event.status == BotMaintenanceCompletionEvent.Status.PENDING:
        event_id_for_dispatch = int(event.id)
        transaction.on_commit(lambda: _queue_reconcile_task(event_id_for_dispatch), robust=True)
    return int(event.id)


def _serialize_strength(summary: Any) -> dict[str, Any]:
    return {
        "composite": int(summary.composite),
        "components": {str(key): int(value) for key, value in summary.components.items()},
    }


def _queue_times(
    *,
    manor_id: int,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "building": [
            {
                "id": int(row["id"]),
                "completion_at": _datetime_payload(row["upgrade_complete_at"]),
            }
            for row in Building.objects.filter(manor_id=manor_id, is_upgrading=True)
            .order_by("upgrade_complete_at", "id")
            .values("id", "upgrade_complete_at")
        ],
        "technology": [
            {
                "id": int(row["id"]),
                "completion_at": _datetime_payload(row["upgrade_complete_at"]),
            }
            for row in PlayerTechnology.objects.filter(manor_id=manor_id, is_upgrading=True)
            .order_by("upgrade_complete_at", "id")
            .values("id", "upgrade_complete_at")
        ],
        "guest_training": [
            {
                "id": int(row["id"]),
                "completion_at": _datetime_payload(row["training_complete_at"]),
            }
            for row in Guest.objects.filter(manor_id=manor_id, training_complete_at__isnull=False)
            .order_by("training_complete_at", "id")
            .values("id", "training_complete_at")
        ],
        "guest_recruitment": [
            {
                "id": int(row["id"]),
                "completion_at": _datetime_payload(row["complete_at"]),
            }
            for row in GuestRecruitment.objects.filter(
                manor_id=manor_id,
                status=GuestRecruitment.Status.PENDING,
            )
            .order_by("complete_at", "id")
            .values("id", "complete_at")
        ],
    }


def _domain_object_snapshot(
    *,
    domain_event_kind: str,
    domain_object_id: int,
    manor_id: int,
) -> dict[str, Any] | None:
    if domain_event_kind == BotMaintenanceCompletionEvent.DomainKind.BUILDING_UPGRADE:
        raw_row = (
            Building.objects.filter(pk=domain_object_id, manor_id=manor_id)
            .values("id", "level", "is_upgrading", "upgrade_complete_at")
            .first()
        )
        snapshot = _snapshot_row(raw_row)
        if snapshot is not None:
            snapshot["id"] = int(snapshot["id"])
            snapshot["level"] = int(snapshot["level"])
            snapshot["is_upgrading"] = bool(snapshot["is_upgrading"])
            snapshot["upgrade_complete_at"] = _datetime_payload(snapshot["upgrade_complete_at"])
        return snapshot
    if domain_event_kind == BotMaintenanceCompletionEvent.DomainKind.TECHNOLOGY_UPGRADE:
        raw_technology_row = (
            PlayerTechnology.objects.filter(pk=domain_object_id, manor_id=manor_id)
            .values("id", "tech_key", "level", "is_upgrading", "upgrade_complete_at")
            .first()
        )
        snapshot = _snapshot_row(raw_technology_row)
        if snapshot is not None:
            snapshot["id"] = int(snapshot["id"])
            snapshot["level"] = int(snapshot["level"])
            snapshot["is_upgrading"] = bool(snapshot["is_upgrading"])
            snapshot["upgrade_complete_at"] = _datetime_payload(snapshot["upgrade_complete_at"])
        return snapshot
    if domain_event_kind == BotMaintenanceCompletionEvent.DomainKind.GUEST_TRAINING:
        raw_guest_training_row = (
            Guest.objects.filter(pk=domain_object_id, manor_id=manor_id)
            .values(
                "id",
                "level",
                "force",
                "intellect",
                "defense_stat",
                "agility",
                "hp_bonus",
                "current_hp",
                "training_complete_at",
            )
            .first()
        )
        snapshot = _snapshot_row(raw_guest_training_row)
        if snapshot is not None:
            for field in (
                "id",
                "level",
                "force",
                "intellect",
                "defense_stat",
                "agility",
                "hp_bonus",
                "current_hp",
            ):
                snapshot[field] = int(snapshot[field] or 0)
            snapshot["training_complete_at"] = _datetime_payload(snapshot["training_complete_at"])
        return snapshot
    raw_recruitment_row = (
        GuestRecruitment.objects.filter(pk=domain_object_id, manor_id=manor_id)
        .values("id", "status", "draw_count", "complete_at", "finished_at", "result_count")
        .first()
    )
    snapshot = _snapshot_row(raw_recruitment_row)
    if snapshot is not None:
        snapshot["id"] = int(snapshot["id"])
        snapshot["draw_count"] = int(snapshot["draw_count"])
        snapshot["result_count"] = int(snapshot["result_count"])
        snapshot["complete_at"] = _datetime_payload(snapshot["complete_at"])
        snapshot["finished_at"] = _datetime_payload(snapshot["finished_at"])
    return snapshot


def _salary_snapshot(
    manor: Manor,
    guests: tuple[Guest, ...],
    *,
    now: datetime,
    salary_enabled: bool,
) -> dict[str, Any]:
    today: date = timezone.localdate(now)
    guest_ids = [int(guest.id) for guest in guests]
    tomorrow_date = today + timedelta(days=1)
    if not salary_enabled:
        return {
            "enabled": False,
            "current": {
                "for_date": today.isoformat(),
                "guest_ids": guest_ids,
                "unpaid_guest_ids": [],
                "total_amount": 0,
                "payable_from_current_silver": True,
            },
            "next_day": {
                "for_date": tomorrow_date.isoformat(),
                "guest_ids": guest_ids,
                "unpaid_guest_ids": [],
                "total_amount": 0,
            },
            "protected_silver": 0,
        }
    paid_today = bulk_check_salary_paid([int(guest.id) for guest in guests], today)
    current = quote_all_salaries(
        manor,
        for_date=today,
        guests=guests,
        paid_guest_ids=paid_today,
        salary_scale=VIRTUAL_PLAYER_SALARY_SCALE,
    )
    next_day = quote_all_salaries(
        manor,
        for_date=tomorrow_date,
        guests=guests,
        paid_guest_ids=set(),
        salary_scale=VIRTUAL_PLAYER_SALARY_SCALE,
    )
    return {
        "enabled": True,
        "current": {
            "for_date": current.for_date.isoformat(),
            "guest_ids": list(current.guest_ids),
            "unpaid_guest_ids": list(current.unpaid_guest_ids),
            "total_amount": int(current.total_amount),
            "payable_from_current_silver": int(manor.silver or 0) >= int(current.total_amount),
        },
        "next_day": {
            "for_date": next_day.for_date.isoformat(),
            "guest_ids": list(next_day.guest_ids),
            "unpaid_guest_ids": list(next_day.unpaid_guest_ids),
            "total_amount": int(next_day.total_amount),
        },
        "protected_silver": int(salary_runway_commitment(int(next_day.total_amount))),
    }


def _effective_state_snapshot(
    *,
    event: BotMaintenanceCompletionEvent,
    profile: BotProfile,
    manor: Manor,
    now: datetime,
) -> dict[str, Any]:
    guests = tuple(Guest.objects.filter(manor_id=manor.id).select_related("template").order_by("id"))
    strength = load_manor_strength_summary(manor_id=int(manor.id), guests=guests)
    return {
        "recorded_at": _datetime_payload(now),
        "profile": {
            "id": int(profile.id),
            "maintenance_sequence": int(profile.maintenance_sequence),
            "next_growth_at": _datetime_payload(profile.next_growth_at),
            "current_prestige_band": str(profile.current_prestige_band),
        },
        "domain": {
            "kind": str(event.domain_event_kind),
            "object_id": int(event.domain_object_id),
            "origin_completed_at": _datetime_payload(event.origin_completed_at),
            "source": _DOMAIN_TO_SOURCE[str(event.domain_event_kind)],
            "object": _domain_object_snapshot(
                domain_event_kind=str(event.domain_event_kind),
                domain_object_id=int(event.domain_object_id),
                manor_id=int(manor.id),
            ),
        },
        "strength": _serialize_strength(strength),
        "resources": {
            "silver": int(manor.silver or 0),
            "grain": int(manor.grain or 0),
            "silver_capacity": int(manor.silver_capacity or 0),
            "grain_capacity": int(manor.grain_capacity or 0),
            "resource_updated_at": _datetime_payload(manor.resource_updated_at),
        },
        "salary": _salary_snapshot(
            manor,
            guests,
            now=now,
            salary_enabled=int(profile.policy_version) != 2,
        ),
        "queues": _queue_times(manor_id=int(manor.id)),
    }


def _matching_pending_action(
    payload: dict[str, Any],
    *,
    event: BotMaintenanceCompletionEvent,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    pending = [dict(item) for item in (payload.get("pending_domain_actions") or []) if isinstance(item, dict)]
    matching: dict[str, Any] | None = None
    for item in pending:
        if (
            str(item.get("domain_event_kind")) == str(event.domain_event_kind)
            and int(item.get("domain_object_id") or 0) == int(event.domain_object_id)
            and str(item.get("expected_completion_at") or "") == str(_datetime_payload(event.origin_completed_at) or "")
        ):
            matching = item
            item["reconciled_at"] = _datetime_payload(timezone.now())
            break
    return pending[-16:], matching


def _find_cycle_for_completion(
    *,
    profile_id: int,
    event: BotMaintenanceCompletionEvent,
) -> tuple[BotMaintenanceCycle, list[dict[str, Any]], dict[str, Any] | None] | None:
    """Find the cycle that owns a domain completion before using a safe fallback.

    A profile may already have opened its next cycle while the previous cycle's
    last domain action is still running.  The pending-action payload is the
    durable ownership key in that case; looking only at the latest cycle can
    otherwise attach an old completion to a newer cycle merely because both
    actions have the same completion source.
    """

    cycles = list(
        BotMaintenanceCycle.objects.select_for_update()
        .select_related("profile")
        .filter(profile_id=int(profile_id), trigger=BotMaintenanceCycle.Trigger.SCHEDULED)
        .order_by("-cycle_ordinal", "-id")
    )
    if not cycles:
        return None

    completion_source = _DOMAIN_TO_SOURCE[str(event.domain_event_kind)]
    latest_fallback: tuple[BotMaintenanceCycle, list[dict[str, Any]], None] | None = None
    for index, cycle in enumerate(cycles):
        payload = dict(cycle.payload or {})
        pending_actions, matching_action = _matching_pending_action(payload, event=event)
        if matching_action is not None:
            return cycle, pending_actions, matching_action

        # Older cycles without the pending-action payload can still be
        # reconciled, but only for the latest cycle and only while its current
        # state proves that a domain action was submitted after the cycle
        # began.  This prevents a late completion from being attributed to a
        # newer cycle when the source string happens to be identical.
        if (
            index == 0
            and cycle.current_action_state == BotMaintenanceCycle.ActionState.SUBMITTED
            and str(cycle.last_action_completion_source) == completion_source
            and event.origin_completed_at >= cycle.started_at
        ):
            latest_fallback = (cycle, pending_actions, None)

    return latest_fallback


def _is_independent_virtual_recruitment_event(*, event: BotMaintenanceCompletionEvent, manor_id: int) -> bool:
    """Keep the ordinary cycle isolated from the independent virtual queue."""

    return bool(
        event.domain_event_kind == BotMaintenanceCompletionEvent.DomainKind.GUEST_RECRUITMENT
        and GuestRecruitment.objects.filter(
            pk=int(event.domain_object_id),
            manor_id=int(manor_id),
            source=GuestRecruitment.Source.VIRTUAL,
        ).exists()
    )


def _reconcile_cycle_schedule(
    cycle: BotMaintenanceCycle,
    *,
    pending_action: dict[str, Any] | None,
    completed_at: datetime,
    now: datetime,
) -> None:
    if pending_action is None:
        return
    if int(pending_action.get("action_ordinal") or 0) != int(cycle.action_ordinal):
        return
    if cycle.status != BotMaintenanceCycle.Status.OPEN:
        if cycle.current_action_state == BotMaintenanceCycle.ActionState.SUBMITTED:
            cycle.current_action_state = BotMaintenanceCycle.ActionState.COMPLETED
        return
    if cycle.next_slot_due_at is None:
        return
    from .maintenance_cycle import next_ordinary_slot_due_at

    completion_due = next_ordinary_slot_due_at(
        cycle.interval_seed or cycle.cycle_id,
        completed_at=completed_at,
        next_slot_ordinal=int(cycle.action_ordinal) + 1,
        interval_minutes=pacing_from_cycle_payload(
            cycle.payload,
            fallback_archetype=str(cycle.profile.archetype),
            config=load_virtual_player_config(),
        ).slot_interval_minutes,
    )
    next_due = max(cycle.next_slot_due_at, completion_due)
    cycle.next_slot_due_at = next_due
    cycle.next_decision_at = now if next_due <= now else next_due
    cycle.current_action_state = (
        BotMaintenanceCycle.ActionState.PLANNING if next_due <= now else BotMaintenanceCycle.ActionState.READY
    )


def _wake_candidate_pool_cooldowns_for_event(
    profile: BotProfile,
    *,
    event: BotMaintenanceCompletionEvent,
    now: datetime,
) -> dict[str, Any] | None:
    """Wake the latest cycle when a completion changes an empty pool's state."""

    cycle = (
        BotMaintenanceCycle.objects.select_for_update()
        .filter(
            profile_id=int(profile.id),
            trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
        )
        .order_by("-cycle_ordinal", "-id")
        .first()
    )
    if cycle is None:
        return None
    payload = dict(cycle.payload or {})
    remaining, woken = wake_candidate_pool_cooldowns(
        payload,
        domain_event_kind=str(event.domain_event_kind),
        now=now,
    )
    if not woken:
        return None
    if remaining:
        payload[CANDIDATE_POOL_COOLDOWN_PAYLOAD_KEY] = remaining
    else:
        payload.pop(CANDIDATE_POOL_COOLDOWN_PAYLOAD_KEY, None)

    update_fields = ["payload"]
    wake_deferred = False
    if cycle.status == BotMaintenanceCycle.Status.OPEN:
        if cycle.current_action_state in {
            BotMaintenanceCycle.ActionState.READY,
            BotMaintenanceCycle.ActionState.NO_ACTION,
            BotMaintenanceCycle.ActionState.COMPLETED,
        }:
            cycle.next_slot_due_at = now
            cycle.next_decision_at = now
            cycle.current_action_state = BotMaintenanceCycle.ActionState.PLANNING
            update_fields.extend(["next_slot_due_at", "next_decision_at", "current_action_state"])
        else:
            wake_deferred = True
    else:
        if profile.next_growth_at is None or profile.next_growth_at > now:
            set_next_growth_at(profile, next_growth_at=now)

    cycle.last_reason = "candidate_pool_state_changed"
    update_fields.append("last_reason")
    cycle.payload = payload
    cycle.save(update_fields=[*dict.fromkeys([*update_fields, "updated_at"])])
    return {
        "cycle_id": str(cycle.cycle_id),
        "woken_entries": woken,
        "deferred": wake_deferred,
    }


@transaction.atomic
def reconcile_virtual_player_maintenance_completion(
    event_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one completion event exactly once and update its owning cycle.

    The lock order is deliberately ``BotProfile -> Manor -> CompletionEvent``.
    Guest replacement already holds the profile and manor locks before it
    removes a pending guest-training event.  Resolving the owner from an
    unlocked snapshot and re-reading the event after those locks are held
    keeps the two paths from waiting on the same rows in opposite orders.
    """

    normalized_id = _normalize_positive_id(event_id, field="event_id")
    current_time = now or timezone.now()
    if timezone.is_naive(current_time):
        raise MaintenanceCompletionError("now must be timezone-aware")
    event_snapshot = (
        BotMaintenanceCompletionEvent.objects.filter(pk=normalized_id)
        .values("id", "status", "profile_id", "result_summary")
        .first()
    )
    if event_snapshot is None:
        return {"event_id": normalized_id, "status": "not_found"}
    if event_snapshot["status"] == BotMaintenanceCompletionEvent.Status.APPLIED:
        return {
            "event_id": normalized_id,
            "status": event_snapshot["status"],
            "summary": dict(event_snapshot["result_summary"] or {}),
        }

    profile = BotProfile.objects.select_for_update().filter(pk=int(event_snapshot["profile_id"])).first()
    if profile is None:
        # The profile can disappear while a stale inbox row is being scanned;
        # take the event lock only after confirming there is no owner to lock.
        event = BotMaintenanceCompletionEvent.objects.select_for_update().filter(pk=normalized_id).first()
        if event is None:
            return {"event_id": normalized_id, "status": "not_found"}
        if event.status == BotMaintenanceCompletionEvent.Status.APPLIED:
            return {
                "event_id": normalized_id,
                "status": event.status,
                "summary": dict(event.result_summary or {}),
            }
        event.status = BotMaintenanceCompletionEvent.Status.APPLIED
        event.processed_at = current_time
        event.attempt_count = min(32, int(event.attempt_count) + 1)
        event.result_summary = {"status": "profile_missing"}
        event.save(update_fields=["status", "processed_at", "attempt_count", "result_summary", "updated_at"])
        return {"event_id": normalized_id, "status": "profile_missing"}

    manor = Manor.objects.select_for_update().get(pk=profile.manor_id)
    event = BotMaintenanceCompletionEvent.objects.select_for_update().filter(pk=normalized_id).first()
    if event is None:
        # Replacement won the profile/manor lock first and deleted this
        # pending training event.  The timer completion is now a harmless
        # stale delivery, not an error and not a reason to wake a cycle.
        return {"event_id": normalized_id, "status": "not_found"}
    if event.status == BotMaintenanceCompletionEvent.Status.APPLIED:
        return {
            "event_id": normalized_id,
            "status": event.status,
            "summary": dict(event.result_summary or {}),
        }
    if int(event.profile_id) != int(profile.id):
        raise MaintenanceCompletionError("completion event owner changed during reconciliation")

    independent_virtual_recruitment = _is_independent_virtual_recruitment_event(
        event=event,
        manor_id=int(manor.id),
    )
    effective_state = _effective_state_snapshot(
        event=event,
        profile=profile,
        manor=manor,
        now=current_time,
    )
    cycle_match = _find_cycle_for_completion(profile_id=int(profile.id), event=event)
    result_summary: dict[str, Any] = {
        "status": "reconciled",
        "profile_id": int(profile.id),
        "cycle_id": None,
        "independent_domain_queue": independent_virtual_recruitment,
        "effective_state": effective_state,
    }
    if cycle_match is not None and not independent_virtual_recruitment:
        cycle, pending_actions, matching_action = cycle_match
        payload = dict(cycle.payload or {})
        result_summary["cycle_id"] = str(cycle.cycle_id)
        payload["pending_domain_actions"] = pending_actions
        history = list(payload.get("completion_reconcile_history") or [])
        history.append(
            {
                "event_id": int(event.id),
                "domain_event_id": str(event.domain_event_id),
                "domain_event_kind": str(event.domain_event_kind),
                "domain_object_id": int(event.domain_object_id),
                "origin_completed_at": _datetime_payload(event.origin_completed_at),
                "matched_pending_action": matching_action is not None,
                "effective_state": effective_state,
            }
        )
        payload["completion_reconcile_history"] = history[-16:]
        payload["last_completion_reconcile"] = history[-1]
        _reconcile_cycle_schedule(
            cycle,
            pending_action=matching_action,
            completed_at=event.origin_completed_at,
            now=current_time,
        )
        cycle.payload = payload
        cycle.save(
            update_fields=[
                "payload",
                "current_action_state",
                "next_slot_due_at",
                "next_decision_at",
                "updated_at",
            ]
        )

    candidate_pool_wake = _wake_candidate_pool_cooldowns_for_event(
        profile,
        event=event,
        now=current_time,
    )
    if candidate_pool_wake is not None:
        result_summary["candidate_pool_wake"] = candidate_pool_wake

    event.status = BotMaintenanceCompletionEvent.Status.APPLIED
    event.processed_at = current_time
    event.attempt_count = min(32, int(event.attempt_count) + 1)
    event.result_summary = result_summary
    event.save(update_fields=["status", "processed_at", "attempt_count", "result_summary", "updated_at"])
    return {"event_id": normalized_id, "status": event.status, "summary": result_summary}


def scan_virtual_player_maintenance_completions(
    *,
    limit: int = COMPLETION_RECONCILE_BATCH_SIZE,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current_time = now or timezone.now()
    normalized_limit = max(0, min(int(limit), COMPLETION_RECONCILE_BATCH_SIZE))
    event_ids = tuple(
        BotMaintenanceCompletionEvent.objects.filter(
            status=BotMaintenanceCompletionEvent.Status.PENDING,
            available_at__lte=current_time,
        )
        .order_by("available_at", "id")
        .values_list("id", flat=True)[:normalized_limit]
    )
    return [reconcile_virtual_player_maintenance_completion(int(event_id), now=current_time) for event_id in event_ids]


__all__ = [
    "COMPLETION_RECONCILE_BATCH_SIZE",
    "COMPLETION_RECONCILE_TASK_NAME",
    "MaintenanceCompletionError",
    "record_virtual_player_maintenance_completion",
    "reconcile_virtual_player_maintenance_completion",
    "scan_virtual_player_maintenance_completions",
]
