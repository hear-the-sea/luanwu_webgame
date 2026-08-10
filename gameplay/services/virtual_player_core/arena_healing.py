"""Independent, free arena roster-healing sweep.

Healing is a cycle preamble rather than a candidate.  Keeping it here makes
the no-resource guarantee explicit and gives the arena worker a durable
parent/child audit boundary that can be replayed without consuming an action
slot or a growth budget entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from gameplay.models import BotMaintenanceAttempt, BotProfile
from guests.models import Guest, GuestStatus

from .maintenance_cycle import CycleTrigger, record_durable_attempt
from .virtual_assets import free_arena_shadow_cost


@dataclass(frozen=True, slots=True)
class ArenaHealingSweepResult:
    operation_id: str
    outcome: str
    guest_ids: tuple[int, ...]
    healed_guest_ids: tuple[int, ...]
    reason: str = ""
    shadow_cost: dict[str, int] | None = None

    @property
    def applied(self) -> bool:
        return self.outcome == BotMaintenanceAttempt.Outcome.APPLIED


def _child_operation_id(parent_operation_id: str, guest_id: int) -> str:
    return f"{str(parent_operation_id)[:48]}:guest:{int(guest_id)}"


def _replayed_sweep(attempt: BotMaintenanceAttempt) -> ArenaHealingSweepResult:
    payload = attempt.shadow_cost or {}
    guest_ids = tuple(int(value) for value in payload.get("guest_ids", ()) if int(value) > 0)
    healed_guest_ids = tuple(int(value) for value in payload.get("healed_guest_ids", ()) if int(value) > 0)
    shadow_cost = {
        str(key): int(value)
        for key, value in payload.items()
        if key not in {"guest_ids", "healed_guest_ids"} and isinstance(value, int) and not isinstance(value, bool)
    }
    return ArenaHealingSweepResult(
        operation_id=str(attempt.operation_id),
        outcome=str(attempt.outcome),
        guest_ids=guest_ids,
        healed_guest_ids=healed_guest_ids,
        reason=str(attempt.reason or ""),
        shadow_cost=shadow_cost,
    )


@transaction.atomic
def run_arena_guest_healing_sweep(
    profile_id: int,
    *,
    operation_id: str,
    now: datetime | None = None,
) -> ArenaHealingSweepResult:
    """Heal every currently healable roster guest exactly once per operation."""

    normalized_operation_id = str(operation_id).strip()
    if not normalized_operation_id or len(normalized_operation_id) > 64:
        raise ValueError("arena healing operation_id must be non-empty and at most 64 characters")
    existing = BotMaintenanceAttempt.objects.filter(operation_id=normalized_operation_id).first()
    if existing is not None:
        if int(existing.profile_id) != int(profile_id):
            raise ValueError("arena healing operation_id belongs to a different profile")
        return _replayed_sweep(existing)

    profile = BotProfile.objects.select_for_update().select_related("manor").filter(pk=profile_id).first()
    if profile is None:
        raise ValueError("arena healing profile does not exist")
    # The fast-path lookup above is intentionally outside the profile lock,
    # but two workers can both miss it before one creates the parent attempt.
    # Re-read after locking the profile so the second worker replays the
    # committed sweep instead of racing the unique operation_id constraint.
    existing = BotMaintenanceAttempt.objects.filter(operation_id=normalized_operation_id).first()
    if existing is not None:
        if int(existing.profile_id) != int(profile_id):
            raise ValueError("arena healing operation_id belongs to a different profile")
        return _replayed_sweep(existing)
    current_time = now or timezone.now()
    guests = tuple(
        Guest.objects.select_for_update()
        .filter(
            manor_id=profile.manor_id,
            status__in=[GuestStatus.IDLE, GuestStatus.INJURED],
        )
        .select_related("template")
        .order_by("id")
    )
    guest_ids = tuple(int(guest.id) for guest in guests if int(guest.current_hp) < int(guest.max_hp))
    healed_guest_ids: list[int] = []
    for guest in guests:
        if int(guest.current_hp) >= int(guest.max_hp):
            continue
        guest.restore_full_hp()
        healed_guest_ids.append(int(guest.id))
        record_durable_attempt(
            profile,
            operation_id=_child_operation_id(normalized_operation_id, int(guest.id)),
            trigger=CycleTrigger.ARENA_ACCELERATION,
            action_kind="guest_healing",
            attempt_ordinal=1,
            outcome=BotMaintenanceAttempt.Outcome.APPLIED,
            reason="guest_healing_sweep",
            shadow_cost={"guest_id": int(guest.id), "silver": 1000, "real_silver": 0},
            started_at=current_time,
        )
    if not guest_ids:
        outcome = BotMaintenanceAttempt.Outcome.NO_ACTION
        reason = "no_guests_to_heal"
        shadow_cost = free_arena_shadow_cost()
    else:
        outcome = BotMaintenanceAttempt.Outcome.APPLIED
        reason = ""
        shadow_cost = free_arena_shadow_cost(
            silver=1000 * len(healed_guest_ids),
            medicine=len(healed_guest_ids),
        )
    payload: dict[str, Any] = {
        **shadow_cost,
        "guest_ids": list(guest_ids),
        "healed_guest_ids": healed_guest_ids,
        "real_silver": 0,
    }
    parent = record_durable_attempt(
        profile,
        operation_id=normalized_operation_id,
        trigger=CycleTrigger.ARENA_ACCELERATION,
        action_kind="guest_healing",
        attempt_ordinal=1,
        outcome=outcome,
        reason=reason,
        shadow_cost=payload,
        started_at=current_time,
    )
    return ArenaHealingSweepResult(
        operation_id=str(parent.operation_id),
        outcome=str(parent.outcome),
        guest_ids=guest_ids,
        healed_guest_ids=tuple(healed_guest_ids),
        reason=reason,
        shadow_cost=shadow_cost,
    )


__all__ = ["ArenaHealingSweepResult", "run_arena_guest_healing_sweep"]
