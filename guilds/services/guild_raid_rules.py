from __future__ import annotations

from typing import TypedDict

from django.utils import timezone

from gameplay.services.missions_impl.loadout import travel_time_seconds

from .. import constants as guild_constants
from .warehouse_config import get_warehouse_production_item_keys

DEFAULT_GUILD_RAID_BASE_TRAVEL_TIME = 600


class GuildRaidRules(TypedDict):
    silver_floor: int
    silver_loot_percent: int
    warehouse_loot_percent: int
    warehouse_loot_whitelist: list[str]


def get_effective_pvp_counter_state(guild, *, today=None) -> dict[str, int]:
    today = today or timezone.localdate()
    attack_count = int(guild.pvp_attack_count_today or 0) if guild.pvp_attack_count_reset_at == today else 0
    defense_count = int(guild.pvp_defense_count_today or 0) if guild.pvp_defense_count_reset_at == today else 0
    return {
        "attack_count": attack_count,
        "defense_count": defense_count,
    }


def reset_guild_pvp_counters_if_needed(guild, *, today=None) -> None:
    today = today or timezone.localdate()
    update_fields: list[str] = []

    if guild.pvp_attack_count_reset_at != today:
        guild.pvp_attack_count_today = 0
        guild.pvp_attack_count_reset_at = today
        update_fields.extend(["pvp_attack_count_today", "pvp_attack_count_reset_at"])

    if guild.pvp_defense_count_reset_at != today:
        guild.pvp_defense_count_today = 0
        guild.pvp_defense_count_reset_at = today
        update_fields.extend(["pvp_defense_count_today", "pvp_defense_count_reset_at"])

    if update_fields:
        guild.save(update_fields=update_fields)


def get_guild_battle_block_reason(*, defender_guild, now=None) -> str:
    now = now or timezone.now()
    if defender_guild.newbie_protection_until and defender_guild.newbie_protection_until > now:
        return "对方处于新帮保护期"
    if defender_guild.defeat_protection_until and defender_guild.defeat_protection_until > now:
        return "对方处于战败保护期"
    return ""


def can_attack_guild(*, attacker_guild, defender_guild, now=None) -> tuple[bool, str]:
    now = now or timezone.now()
    today = timezone.localdate(now)
    if not getattr(attacker_guild, "is_active", False):
        return False, "当前帮会不可发起攻击"
    if not getattr(defender_guild, "is_active", False):
        return False, "目标帮会不可被攻击"
    if attacker_guild.pk == defender_guild.pk:
        return False, "不能攻击自己帮会"
    if abs(int(attacker_guild.level) - int(defender_guild.level)) > guild_constants.GUILD_PVP_MAX_TARGET_LEVEL_GAP:
        return False, "目标帮会等级差超过允许范围"
    if attacker_guild.newbie_protection_until and attacker_guild.newbie_protection_until > now:
        return False, "新帮保护期内无法发起攻击"

    defender_block_reason = get_guild_battle_block_reason(defender_guild=defender_guild, now=now)
    if defender_block_reason:
        return False, defender_block_reason

    attacker_counter_state = get_effective_pvp_counter_state(attacker_guild, today=today)
    defender_counter_state = get_effective_pvp_counter_state(defender_guild, today=today)
    if attacker_counter_state["attack_count"] >= guild_constants.GUILD_PVP_MAX_DAILY_ATTACK_COUNT:
        return False, "今日主动进攻次数已达上限"
    if defender_counter_state["defense_count"] >= guild_constants.GUILD_PVP_MAX_DAILY_DEFENSE_COUNT:
        return False, "对方今日被攻击次数已达上限"
    if int(attacker_guild.silver or 0) < guild_constants.GUILD_PVP_FIXED_ATTACK_COST_SILVER:
        return False, f"帮会银两不足，需要{guild_constants.GUILD_PVP_FIXED_ATTACK_COST_SILVER}"
    return True, ""


def get_guild_raid_rules() -> GuildRaidRules:
    configured_item_keys = list(
        dict.fromkeys(
            str(item_key).strip()
            for item_key in (guild_constants.GUILD_PVP_WAREHOUSE_LOOT_WHITELIST or [])
            if str(item_key).strip()
        )
    )
    production_item_keys = get_warehouse_production_item_keys()
    warehouse_loot_item_keys = [
        *configured_item_keys,
        *sorted(production_item_keys.difference(configured_item_keys)),
    ]
    return {
        "silver_floor": int(guild_constants.GUILD_PVP_SILVER_FLOOR or 0),
        "silver_loot_percent": int(guild_constants.GUILD_PVP_SILVER_LOOT_PERCENT or 0),
        "warehouse_loot_percent": int(guild_constants.GUILD_PVP_WAREHOUSE_LOOT_PERCENT or 0),
        "warehouse_loot_whitelist": warehouse_loot_item_keys,
    }


def calculate_guild_raid_travel_time(guests, troop_loadout: dict[str, int]) -> int:
    return travel_time_seconds(DEFAULT_GUILD_RAID_BASE_TRAVEL_TIME, guests, troop_loadout or {})
