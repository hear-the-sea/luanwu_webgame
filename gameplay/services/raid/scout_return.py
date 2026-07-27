from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from core.exceptions import ScoutRetreatStateError

from ...models import ScoutRecord
from ..pvp_runtime.lifecycle import TravelTimeline


def finalize_scout_return_command(
    record: Any,
    *,
    now: datetime | None = None,
    scout_record_model: Any = ScoutRecord,
    restore_scout_troops_fn: Callable[..., None],
    schedule_followup_fn: Callable[..., None],
) -> None:
    current_time = now or timezone.now()

    with transaction.atomic():
        locked_record = (
            scout_record_model.objects.select_for_update()
            .select_related("attacker", "defender")
            .filter(pk=record.pk)
            .first()
        )

        if not locked_record or locked_record.status != scout_record_model.Status.RETURNING:
            return

        if locked_record.was_retreated:
            locked_record.status = scout_record_model.Status.FAILED
            followup_action = "retreat_result_message"
        elif locked_record.is_success:
            locked_record.status = scout_record_model.Status.SUCCESS
            restore_scout_troops_fn(locked_record.attacker, locked_record.scout_cost, now=current_time)
            followup_action = "success_result_message"
        else:
            locked_record.status = scout_record_model.Status.FAILED
            followup_action = "failure_result_message"

        locked_record.completed_at = current_time
        locked_record.save(update_fields=["status", "completed_at"])
        schedule_followup_fn(
            followup_action,
            locked_record,
            record_id=locked_record.id,
            attacker_id=locked_record.attacker_id,
            defender_id=locked_record.defender_id,
        )


def request_scout_retreat_command(
    record: Any,
    *,
    now_fn: Callable[[], datetime] = timezone.now,
    scout_record_model: Any = ScoutRecord,
    restore_scout_troops_fn: Callable[..., None],
    schedule_return_completion_fn: Callable[[Any, int], None] | None = None,
) -> tuple[Any, int]:
    if record.status != scout_record_model.Status.SCOUTING:
        raise ScoutRetreatStateError()

    current_time = now_fn()

    with transaction.atomic():
        locked_record = (
            scout_record_model.objects.select_for_update().select_related("attacker").filter(pk=record.pk).first()
        )

        if not locked_record or locked_record.status != scout_record_model.Status.SCOUTING:
            raise ScoutRetreatStateError()

        retreat_schedule = TravelTimeline.from_activity(locked_record).retreat_schedule(now=current_time)
        locked_record.status = scout_record_model.Status.RETURNING
        locked_record.is_success = None
        locked_record.was_retreated = True
        locked_record.return_at = retreat_schedule.return_at
        locked_record.save(update_fields=["status", "is_success", "was_retreated", "return_at"])

        restore_scout_troops_fn(locked_record.attacker, locked_record.scout_cost, now=current_time)
        if schedule_return_completion_fn is not None:
            schedule_return_completion_fn(locked_record, retreat_schedule.elapsed_seconds)

    return locked_record, retreat_schedule.elapsed_seconds
