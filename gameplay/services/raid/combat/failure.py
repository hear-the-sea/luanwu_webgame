from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from guests.models import Guest, GuestStatus

from ....models import Manor, RaidRun
from .troop_ops import _add_troops_batch
from .troops import _normalize_positive_int_mapping

logger = logging.getLogger(__name__)


def _lock_raid_roots(run_id: int) -> tuple[Manor, Manor, RaidRun] | None:
    manor_ids = RaidRun.objects.filter(pk=run_id).values_list("attacker_id", "defender_id").first()
    if manor_ids is None:
        return None

    ordered_ids = sorted(set(manor_ids))
    locked_manors = {
        manor.pk: manor for manor in Manor.objects.select_for_update().filter(pk__in=ordered_ids).order_by("pk")
    }
    attacker = locked_manors.get(manor_ids[0])
    defender = locked_manors.get(manor_ids[1])
    if attacker is None or defender is None:
        return None

    locked_run = (
        RaidRun.objects.select_for_update()
        .select_related("attacker", "defender", "battle_report")
        .prefetch_related("guests")
        .filter(pk=run_id)
        .first()
    )
    if locked_run is None:
        return None
    locked_run.attacker = attacker
    locked_run.defender = defender
    return attacker, defender, locked_run


@transaction.atomic
def fail_raid_run_and_release_resources(
    run_id: int,
    *,
    failure_reason: str,
    now: datetime | None = None,
    failure_detail: str = "",
) -> bool:
    """Move or repair an unreported FAILED raid and release reserved resources once."""

    locked = _lock_raid_roots(run_id)
    if locked is None:
        return False
    attacker, _defender, locked_run = locked
    if locked_run.status in {
        RaidRun.Status.COMPLETED,
        RaidRun.Status.RETURNING,
        RaidRun.Status.RETREATED,
    }:
        return False
    if locked_run.battle_report_id is not None:
        return False
    if locked_run.status == RaidRun.Status.FAILED and locked_run.resources_released:
        return False

    current_time = now or timezone.now()
    repairing_failed_run = locked_run.status == RaidRun.Status.FAILED
    completed_at = locked_run.completed_at if repairing_failed_run and locked_run.completed_at else current_time
    returned_guests = 0
    returned_troops: dict[str, int] = {}
    if not locked_run.resources_released:
        guests = list(locked_run.guests.select_for_update())
        guests_to_update: list[Guest] = []
        for guest in guests:
            if guest.status != GuestStatus.DEPLOYED:
                continue
            guest.status = GuestStatus.IDLE
            guests_to_update.append(guest)
        if guests_to_update:
            Guest.objects.bulk_update(guests_to_update, ["status"])
            returned_guests = len(guests_to_update)

        returned_troops = _normalize_positive_int_mapping(locked_run.troop_loadout)
        if returned_troops:
            _add_troops_batch(attacker, returned_troops)

    locked_run.status = RaidRun.Status.FAILED
    locked_run.failure_reason = failure_reason
    locked_run.resources_released = True
    locked_run.completed_at = completed_at
    locked_run.save(
        update_fields=[
            "status",
            "failure_reason",
            "resources_released",
            "completed_at",
        ]
    )
    logger.error(
        "raid_failed_and_resources_released: run_id=%s reason=%s returned_guests=%s returned_troops=%s detail=%s",
        locked_run.id,
        failure_reason,
        returned_guests,
        returned_troops,
        str(failure_detail or "")[:500],
        extra={
            "component": "raid_failed_and_resources_released",
            "event": "battle_snapshot_invalid",
            "run_id": locked_run.id,
            "failure_reason": failure_reason,
            "returned_guest_count": returned_guests,
            "returned_troops": returned_troops,
        },
    )
    return True
