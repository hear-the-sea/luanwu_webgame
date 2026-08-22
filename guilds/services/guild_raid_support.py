from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from django.db.models import F
from django.utils import timezone

from core.exceptions import GuildValidationError

from .. import constants as guild_constants
from ..models import Guild, GuildBattleLineupEntry, GuildRaidRun


def next_due_at(run: GuildRaidRun):
    if run.status == GuildRaidRun.Status.MARCHING:
        return run.battle_at
    if run.status in (GuildRaidRun.Status.RETURNING, GuildRaidRun.Status.RETREATED):
        return run.return_at
    return None


def dispatch_countdown_for_run(run: GuildRaidRun) -> int:
    due_at = next_due_at(run)
    if due_at is None:
        raise RuntimeError("guild raid run missing due time")
    remaining_seconds = math.ceil((due_at - timezone.now()).total_seconds())
    return max(0, remaining_seconds)


def load_defender_guests(defender_guild: Guild) -> list[Any]:
    guests: list[Any] = []
    lineup_rows = (
        GuildBattleLineupEntry.objects.filter(
            guild=defender_guild,
            pool_entry__owner_member__is_active=True,
            pool_entry__owner_member__guild_id=defender_guild.id,
            pool_entry__source_guest__manor__user_id=F("pool_entry__owner_member__user_id"),
        )
        .select_related("pool_entry__source_guest__template", "pool_entry__source_guest__manor")
        .order_by("slot_index", "id")
    )
    for row in lineup_rows:
        guest = row.pool_entry.source_guest
        if guest is not None:
            guests.append(guest)
    return guests


def lock_guild_pair(
    *,
    attacker_guild_id: int,
    defender_guild_id: int,
    guild_model=Guild,
    require_active: bool = False,
) -> tuple[Guild, Guild]:
    ordered_guild_ids = sorted({int(attacker_guild_id), int(defender_guild_id)})
    filter_kwargs: dict[str, object] = {"pk__in": ordered_guild_ids}
    if require_active:
        filter_kwargs["is_active"] = True
    locked_guilds = list(guild_model.objects.select_for_update().filter(**filter_kwargs).order_by("pk"))
    locked_guilds_by_pk = {guild.pk: guild for guild in locked_guilds}
    attacker_locked = locked_guilds_by_pk.get(attacker_guild_id)
    defender_locked = locked_guilds_by_pk.get(defender_guild_id)
    if attacker_locked is None or defender_locked is None:
        raise GuildValidationError("帮会不存在或已停用" if require_active else "帮会不存在")
    return attacker_locked, defender_locked


def apply_guild_defeat_protection(defender_guild: Guild, *, now=None) -> None:
    resolved_now = now or timezone.now()
    duration_seconds = int(guild_constants.GUILD_PVP_DEFEAT_PROTECTION_SECONDS or 0)
    if duration_seconds <= 0:
        return

    new_until = resolved_now + timedelta(seconds=duration_seconds)
    current_until = defender_guild.defeat_protection_until
    if current_until and current_until > new_until:
        new_until = current_until
    defender_guild.defeat_protection_until = new_until
    defender_guild.save(update_fields=["defeat_protection_until"])
