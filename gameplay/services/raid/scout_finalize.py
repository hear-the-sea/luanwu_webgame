from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from ...constants import PVPConstants
from ...models import ScoutCooldown, ScoutRecord
from ..scout_intel_rules import SCOUT_INTEL_SCHEMA_VERSION, normalize_scout_level


def finalize_scout_command(
    record: Any,
    *,
    now: datetime | None = None,
    scout_record_model: Any = ScoutRecord,
    scout_cooldown_model: Any = ScoutCooldown,
    random_fn: Callable[[], float],
    gather_intel_fn: Callable[..., Any],
    schedule_followup_fn: Callable[..., None],
    schedule_return_completion_fn: Callable[[Any, int], None],
) -> None:
    current_time = now or timezone.now()

    with transaction.atomic():
        locked_record = (
            scout_record_model.objects.select_for_update()
            .select_related("attacker", "defender")
            .filter(pk=record.pk)
            .first()
        )
        if not locked_record or locked_record.status != scout_record_model.Status.SCOUTING:
            return

        followup_action: str | None = None
        is_success = random_fn() < locked_record.success_rate
        locked_record.is_success = is_success

        if is_success:
            dispatch_intel = locked_record.intel_data if isinstance(locked_record.intel_data, dict) else {}
            if dispatch_intel.get("intel_schema_version") == SCOUT_INTEL_SCHEMA_VERSION:
                locked_record.intel_data = gather_intel_fn(
                    locked_record.defender,
                    scout_level=normalize_scout_level(dispatch_intel.get("scout_level")),
                    now=current_time,
                )
            else:
                # 历史在途侦察没有出发等级，继续按原有基础情报结算。
                locked_record.intel_data = gather_intel_fn(locked_record.defender)
        else:
            followup_action = "detected_message"

        locked_record.status = scout_record_model.Status.RETURNING
        locked_record.return_at = current_time + timedelta(seconds=locked_record.travel_time)
        locked_record.save(update_fields=["status", "is_success", "intel_data", "return_at"])

        cooldown_until = current_time + timedelta(minutes=PVPConstants.SCOUT_COOLDOWN_MINUTES)
        scout_cooldown_model.objects.update_or_create(
            attacker=locked_record.attacker,
            defender=locked_record.defender,
            defaults={"cooldown_until": cooldown_until},
        )

        if followup_action is not None:
            schedule_followup_fn(
                followup_action,
                locked_record,
                record_id=locked_record.id,
                attacker_id=locked_record.attacker_id,
                defender_id=locked_record.defender_id,
            )

        schedule_return_completion_fn(locked_record, locked_record.travel_time)
