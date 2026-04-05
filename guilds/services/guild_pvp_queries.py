from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.utils import timezone

from gameplay.constants import REGION_CHOICES

from .. import constants as guild_constants
from ..models import Guild, GuildBattleLineupEntry, GuildMember, GuildRaidRun, GuildTroopStorage
from .guild_pvp_display import (
    GuildPvpRunDisplay,
    GuildPvpTargetCardDisplay,
    project_active_guild_pvp_run,
    project_guild_pvp_target_card,
    project_incoming_guild_pvp_run,
)
from .guild_raid_rules import calculate_guild_raid_travel_time, can_attack_guild, get_effective_pvp_counter_state
from .technology import get_guild_dispatch_capacity

IN_FLIGHT_GUILD_RAID_STATUSES = (
    GuildRaidRun.Status.MARCHING,
    GuildRaidRun.Status.BATTLING,
    GuildRaidRun.Status.RETURNING,
    GuildRaidRun.Status.RETREATED,
)


def _project_target_card(
    *,
    guild: Guild,
    target: Guild,
    lineup_guests: list[Any],
    now,
) -> tuple[GuildPvpTargetCardDisplay, bool]:
    can_attack, blocked_reason = can_attack_guild(attacker_guild=guild, defender_guild=target, now=now)
    return (
        project_guild_pvp_target_card(
            target,
            can_attack=can_attack,
            blocked_reason=blocked_reason,
            travel_time_seconds=calculate_guild_raid_travel_time(lineup_guests, {}),
        ),
        can_attack,
    )


def get_guild_pvp_page_context(member: GuildMember, *, now=None) -> dict[str, Any]:
    resolved_now = now or timezone.now()
    today = timezone.localdate(resolved_now)
    guild = member.guild
    guild_counter_state = get_effective_pvp_counter_state(guild, today=today)

    lineup_entries = list(
        GuildBattleLineupEntry.objects.filter(guild=guild)
        .select_related("pool_entry__source_guest__template", "pool_entry__owner_member__user__manor")
        .order_by("slot_index", "id")
    )
    lineup_guests = [row.pool_entry.source_guest for row in lineup_entries if row.pool_entry.source_guest is not None]
    troop_storages = list(
        GuildTroopStorage.objects.filter(guild=guild, count__gt=0)
        .select_related("troop_template")
        .order_by("troop_template__priority", "troop_template__id")
    )
    target_guilds = list(
        Guild.objects.filter(is_active=True)
        .exclude(pk=guild.pk)
        .select_related("founder__manor")
        .order_by("level", "id")
    )
    projected_targets = [
        _project_target_card(guild=guild, target=target, lineup_guests=lineup_guests, now=resolved_now)
        for target in target_guilds
    ]
    targets = [projected_target for projected_target, _can_attack in projected_targets]
    default_target = next(
        (projected_target for projected_target, can_attack in projected_targets if can_attack),
        projected_targets[0][0] if projected_targets else None,
    )
    target_filter_counts = {
        "all": len(targets),
        "attackable": sum(1 for _projected_target, can_attack in projected_targets if can_attack),
        "blocked": sum(1 for _projected_target, can_attack in projected_targets if not can_attack),
    }
    active_run_row = (
        GuildRaidRun.objects.select_related("defender_guild", "started_by__user")
        .filter(attacker_guild=guild, status__in=IN_FLIGHT_GUILD_RAID_STATUSES)
        .filter(Q(return_at__isnull=True) | Q(return_at__gt=resolved_now))
        .order_by("-started_at", "-id")
        .first()
    )
    active_run: GuildPvpRunDisplay | None = None
    if active_run_row is not None:
        active_run = project_active_guild_pvp_run(active_run_row, now=resolved_now, can_manage=member.can_manage)
    incoming_run_rows = list(
        GuildRaidRun.objects.select_related("attacker_guild", "started_by__user")
        .filter(
            defender_guild=guild,
        )
        .filter(
            Q(status=GuildRaidRun.Status.MARCHING)
            | Q(status=GuildRaidRun.Status.BATTLING, return_at__isnull=True)
            | Q(status=GuildRaidRun.Status.BATTLING, return_at__gt=resolved_now)
        )
        .order_by("battle_at", "return_at", "id")
    )
    incoming_runs: list[GuildPvpRunDisplay] = [
        project_incoming_guild_pvp_run(run, now=resolved_now) for run in incoming_run_rows
    ]
    return {
        "guild": guild,
        "member": member,
        "targets": targets,
        "default_target_id": default_target.guild.id if default_target else None,
        "target_filter_counts": target_filter_counts,
        "region_filter_choices": [{"value": value, "label": label} for value, label in REGION_CHOICES],
        "active_run": active_run,
        "incoming_runs": incoming_runs,
        "lineup_entries": lineup_entries,
        "troop_storages": troop_storages,
        "dispatch_limit": get_guild_dispatch_capacity(guild),
        "attack_count": guild_counter_state["attack_count"],
        "attack_limit": guild_constants.GUILD_PVP_MAX_DAILY_ATTACK_COUNT,
        "defense_count": guild_counter_state["defense_count"],
        "defense_limit": guild_constants.GUILD_PVP_MAX_DAILY_DEFENSE_COUNT,
    }
