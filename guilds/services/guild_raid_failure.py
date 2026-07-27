from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from battle.models import TroopTemplate

from ..models import GuildRaidRun
from . import guild_troops
from .guild_raid_support import lock_guild_pair

logger = logging.getLogger(__name__)


@transaction.atomic
def fail_guild_raid_and_release_resources(
    run_id: int,
    *,
    failure_reason: str,
    now: datetime | None = None,
    failure_detail: str = "",
    audit_event: str = "battle_snapshot_invalid",
) -> bool:
    """Move an unreported guild raid to FAILED and return reserved troops once."""

    guild_ids = (
        GuildRaidRun.objects.filter(pk=run_id)
        .values_list(
            "attacker_guild_id",
            "defender_guild_id",
        )
        .first()
    )
    if guild_ids is None:
        return False
    attacker_locked, defender_locked = lock_guild_pair(
        attacker_guild_id=guild_ids[0],
        defender_guild_id=guild_ids[1],
        require_active=False,
    )
    locked_run = (
        GuildRaidRun.objects.select_for_update()
        .select_related("attacker_guild", "defender_guild", "battle_report")
        .filter(pk=run_id)
        .first()
    )
    if locked_run is None:
        return False
    is_inactive_attacker_failure = failure_reason == GuildRaidRun.FailureReason.INACTIVE_ATTACKER_GUILD
    if locked_run.status in {
        GuildRaidRun.Status.COMPLETED,
        GuildRaidRun.Status.RETREATED,
    }:
        return False
    if locked_run.status == GuildRaidRun.Status.RETURNING and not is_inactive_attacker_failure:
        return False
    if locked_run.battle_report_id is not None and not is_inactive_attacker_failure:
        return False
    if locked_run.status == GuildRaidRun.Status.FAILED and locked_run.resources_released:
        return False

    locked_run.attacker_guild = attacker_locked
    locked_run.defender_guild = defender_locked
    returned_troops: dict[str, int] = {}
    ignored_troop_keys: list[str] = []
    if not locked_run.resources_released:
        normalized = guild_troops.normalize_guild_troop_loadout(locked_run.troop_loadout)
        if locked_run.battle_report_id is not None:
            normalized = guild_troops.calculate_surviving_guild_troops(
                normalized,
                locked_run.battle_report,
            )
        known_keys = set(TroopTemplate.objects.filter(key__in=normalized.keys()).values_list("key", flat=True))
        returned_troops = {key: value for key, value in normalized.items() if key in known_keys}
        ignored_troop_keys = sorted(key for key in normalized if key not in known_keys)
        if returned_troops:
            guild_troops.add_guild_troops(guild=attacker_locked, loadout=returned_troops)

    current_time = now or timezone.now()
    locked_run.status = GuildRaidRun.Status.FAILED
    locked_run.failure_reason = failure_reason
    locked_run.resources_released = True
    locked_run.loot_settled = True
    locked_run.completed_at = current_time
    locked_run.save(
        update_fields=[
            "status",
            "failure_reason",
            "resources_released",
            "loot_settled",
            "completed_at",
        ]
    )
    logger.error(
        "guild_raid_failed_and_resources_released: run_id=%s reason=%s returned_troops=%s ignored_keys=%s detail=%s",
        locked_run.id,
        failure_reason,
        returned_troops,
        ignored_troop_keys,
        str(failure_detail or "")[:500],
        extra={
            "component": "guild_raid_failed_and_resources_released",
            "event": audit_event,
            "run_id": locked_run.id,
            "failure_reason": failure_reason,
            "returned_troops": returned_troops,
            "ignored_troop_keys": ignored_troop_keys,
        },
    )
    return True
