from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from battle.models import TroopTemplate

from ..models import Guild, GuildMissionRun
from . import guild_troops

logger = logging.getLogger(__name__)


@transaction.atomic
def fail_guild_mission_and_release_resources(
    run_id: int,
    *,
    failure_reason: str,
    now: datetime | None = None,
    failure_detail: str = "",
) -> bool:
    """终止异常帮会任务，并将明确可识别的预留护院返还一次。"""

    guild_id = GuildMissionRun.objects.filter(pk=run_id).values_list("guild_id", flat=True).first()
    if guild_id is None:
        return False

    locked_guild = Guild.objects.select_for_update().filter(pk=guild_id).first()
    if locked_guild is None:
        return False

    locked_run = GuildMissionRun.objects.select_for_update().filter(pk=run_id).first()
    if locked_run is None or locked_run.status != GuildMissionRun.Status.ACTIVE:
        return False

    normalized_loadout = guild_troops.normalize_guild_troop_loadout(locked_run.troop_loadout)
    known_keys = set(TroopTemplate.objects.filter(key__in=normalized_loadout.keys()).values_list("key", flat=True))
    returned_troops = {key: value for key, value in normalized_loadout.items() if key in known_keys}
    ignored_troop_keys = sorted(key for key in normalized_loadout if key not in known_keys)
    if returned_troops:
        guild_troops.add_guild_troops(guild=locked_guild, loadout=returned_troops)

    locked_run.status = GuildMissionRun.Status.FAILED
    locked_run.completed_at = now or timezone.now()
    locked_run.save(update_fields=["status", "completed_at"])
    logger.error(
        "guild_mission_failed_and_resources_released: run_id=%s reason=%s returned_troops=%s "
        "ignored_keys=%s detail=%s",
        locked_run.id,
        failure_reason,
        returned_troops,
        ignored_troop_keys,
        str(failure_detail or "")[:500],
        extra={
            "component": "guild_mission_failed_and_resources_released",
            "event": "guild_mission_settlement_failed",
            "run_id": locked_run.id,
            "failure_reason": failure_reason,
            "returned_troops": returned_troops,
            "ignored_troop_keys": ignored_troop_keys,
        },
    )
    return True
