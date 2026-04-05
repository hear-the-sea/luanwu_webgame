from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.utils import timezone

from gameplay.constants import REGION_CHOICES, REGION_DICT

from .. import constants as guild_constants
from ..models import Guild, GuildBattleLineupEntry, GuildMember, GuildRaidRun, GuildTroopStorage
from .guild_raid_rules import calculate_guild_raid_travel_time, can_attack_guild, get_effective_pvp_counter_state
from .technology import get_guild_dispatch_capacity

IN_FLIGHT_GUILD_RAID_STATUSES = (
    GuildRaidRun.Status.MARCHING,
    GuildRaidRun.Status.BATTLING,
    GuildRaidRun.Status.RETURNING,
    GuildRaidRun.Status.RETREATED,
)


def _build_target_card(*, guild: Guild, target: Guild, lineup_guests: list[Any], now) -> dict[str, Any]:
    can_attack, blocked_reason = can_attack_guild(attacker_guild=guild, defender_guild=target, now=now)
    status_key = "attackable" if can_attack else "blocked"
    founder_manor = getattr(getattr(target, "founder", None), "manor", None)
    region_key = getattr(founder_manor, "region", "")
    region_display = REGION_DICT.get(region_key, region_key or "未知区域")
    return {
        "guild": target,
        "can_attack": can_attack,
        "blocked_reason": blocked_reason,
        "status_key": status_key,
        "region_key": region_key,
        "region_display": region_display,
        "status_label": "可进攻" if can_attack else "暂不可进攻",
        "detail_message": blocked_reason or "符合当前条件，可作为本次进攻目标。",
        "search_text": f"{target.name} {region_display} lv {target.level} {status_key}",
        "travel_time_seconds": calculate_guild_raid_travel_time(lineup_guests, {}),
    }


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
    targets = [
        _build_target_card(guild=guild, target=target, lineup_guests=lineup_guests, now=resolved_now)
        for target in target_guilds
    ]
    default_target = next((target for target in targets if target["can_attack"]), targets[0] if targets else None)
    target_filter_counts = {
        "all": len(targets),
        "attackable": sum(1 for target in targets if target["can_attack"]),
        "blocked": sum(1 for target in targets if not target["can_attack"]),
    }
    active_run = (
        GuildRaidRun.objects.select_related("defender_guild", "started_by__user")
        .filter(attacker_guild=guild, status__in=IN_FLIGHT_GUILD_RAID_STATUSES)
        .filter(Q(return_at__isnull=True) | Q(return_at__gt=resolved_now))
        .order_by("-started_at", "-id")
        .first()
    )
    incoming_runs = list(
        GuildRaidRun.objects.select_related("attacker_guild", "started_by__user")
        .filter(defender_guild=guild, status=GuildRaidRun.Status.MARCHING, battle_at__gt=resolved_now)
        .order_by("battle_at", "id")
    )
    return {
        "guild": guild,
        "member": member,
        "targets": targets,
        "default_target_id": default_target["guild"].id if default_target else None,
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
