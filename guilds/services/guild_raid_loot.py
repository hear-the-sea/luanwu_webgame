from __future__ import annotations

from typing import Any, Sequence

from django.db.models import F

from core.utils import safe_non_negative_int, safe_positive_int
from gameplay.constants import PVPConstants
from gameplay.models import ItemTemplate
from gameplay.services.pvp_runtime.loot import (
    WeightedLootCandidate,
    calculate_item_loot_capacity,
    calculate_item_loot_draw_count,
    draw_weighted_item_loot,
    normalize_positive_int_mapping,
)

from ..models import Guild, GuildWarehouse
from . import guild_troops
from .guild_raid_rules import get_guild_raid_rules
from .warehouse import add_item_to_warehouse


def grant_guild_raid_battle_rewards(*, guild: Guild, rewards: dict[str, int]) -> dict[str, int]:
    normalized_rewards = {
        str(item_key).strip(): max(0, int(quantity or 0))
        for item_key, quantity in (rewards or {}).items()
        if str(item_key).strip() and int(quantity or 0) > 0
    }
    for item_key, quantity in normalized_rewards.items():
        add_item_to_warehouse(guild, item_key, quantity, 0, count_as_production=False)
    return normalized_rewards


def _calculate_guild_item_loot_capacity(
    *,
    guests: Sequence[Any],
    troop_loadout: dict[str, int],
    battle_report: Any,
) -> int:
    normalized_loadout = normalize_positive_int_mapping(troop_loadout)
    surviving_troops = guild_troops.calculate_surviving_guild_troops(normalized_loadout, battle_report)
    return calculate_item_loot_capacity(
        max_capacity=PVPConstants.LOOT_ITEM_CAPACITY_MAX,
        guest_count=len(guests),
        troop_loadout=normalized_loadout,
        surviving_troop_count=sum(surviving_troops.values()),
        full_guest_count=PVPConstants.LOOT_FULL_GUEST_COUNT,
        full_troop_count=PVPConstants.LOOT_FULL_TROOP_COUNT,
        min_cap_ratio=PVPConstants.LOOT_MIN_CAP_RATIO,
        survival_base_ratio=PVPConstants.LOOT_SURVIVAL_BASE_RATIO,
        survival_scaling_ratio=PVPConstants.LOOT_SURVIVAL_SCALING_RATIO,
    )


def _build_weighted_loot_candidates(rows: list[GuildWarehouse]) -> list[WeightedLootCandidate]:
    item_keys = [str(row.item_key) for row in rows]
    storage_spaces = {
        str(item_key): safe_positive_int(storage_space, 1)
        for item_key, storage_space in ItemTemplate.objects.filter(key__in=item_keys).values_list(
            "key", "storage_space"
        )
    }
    return [
        {
            "item_key": str(row.item_key),
            "remaining_quantity": safe_positive_int(row.quantity, 0),
            "storage_space": storage_spaces.get(str(row.item_key), 1),
        }
        for row in rows
        if safe_positive_int(row.quantity, 0) > 0
    ]


def reserve_guild_raid_loot(
    *,
    attacker_guild: Guild,
    defender_guild: Guild,
    guests: Sequence[Any],
    troop_loadout: dict[str, int],
    battle_report: Any,
) -> tuple[int, dict[str, int], dict[str, int]]:
    from .guild_raid_support import lock_guild_pair

    rules = get_guild_raid_rules()
    _attacker_locked, defender_locked = lock_guild_pair(
        attacker_guild_id=attacker_guild.pk,
        defender_guild_id=defender_guild.pk,
    )

    silver_floor = int(rules["silver_floor"] or 0)
    silver_percent = int(rules["silver_loot_percent"] or 0)
    silver_available = max(0, int(defender_locked.silver or 0) - silver_floor)
    loot_silver = silver_available * silver_percent // 100
    if loot_silver > 0:
        Guild.objects.filter(pk=defender_locked.pk, silver__gte=loot_silver).update(silver=F("silver") - loot_silver)

    whitelist = list(rules["warehouse_loot_whitelist"] or [])
    warehouse_percent = int(rules["warehouse_loot_percent"] or 0)
    loot_items: dict[str, int] = {}
    loot_item_contribution_costs: dict[str, int] = {}
    if warehouse_percent <= 0 or not whitelist:
        return loot_silver, loot_items, loot_item_contribution_costs

    locked_rows = list(
        GuildWarehouse.objects.select_for_update()
        .filter(guild=defender_locked, item_key__in=whitelist, quantity__gt=0)
        .order_by("item_key", "id")
    )
    total_quantity = sum(int(row.quantity or 0) for row in locked_rows)
    draw_count = calculate_item_loot_draw_count(total_quantity, warehouse_percent / 100)
    capacity = _calculate_guild_item_loot_capacity(
        guests=guests,
        troop_loadout=troop_loadout,
        battle_report=battle_report,
    )
    loot_items = draw_weighted_item_loot(
        _build_weighted_loot_candidates(locked_rows),
        draw_count=draw_count,
        capacity=capacity,
    )

    for row in locked_rows:
        loot_quantity = int(loot_items.get(row.item_key, 0) or 0)
        if loot_quantity <= 0:
            continue
        updated = GuildWarehouse.objects.filter(pk=row.pk, quantity__gte=loot_quantity).update(
            quantity=F("quantity") - loot_quantity,
        )
        if updated != 1:
            continue
        loot_item_contribution_costs[row.item_key] = max(0, int(row.contribution_cost or 0))

    GuildWarehouse.objects.filter(guild=defender_locked, quantity=0).delete()
    return loot_silver, loot_items, loot_item_contribution_costs


def grant_reserved_guild_raid_loot(
    *,
    attacker_guild: Guild,
    loot_silver: int,
    loot_items: dict[str, int],
    loot_item_contribution_costs: dict[str, int],
) -> tuple[int, dict[str, int]]:
    normalized_silver = safe_positive_int(loot_silver, 0)
    normalized_items = normalize_positive_int_mapping(loot_items)
    normalized_costs = {
        str(item_key).strip(): safe_non_negative_int(contribution_cost)
        for item_key, contribution_cost in (loot_item_contribution_costs or {}).items()
        if str(item_key).strip()
    }
    existing_costs = {
        str(item_key): max(0, int(contribution_cost or 0))
        for item_key, contribution_cost in GuildWarehouse.objects.filter(
            guild=attacker_guild,
            item_key__in=normalized_items,
        ).values_list("item_key", "contribution_cost")
    }

    if normalized_silver > 0:
        Guild.objects.filter(pk=attacker_guild.pk).update(silver=F("silver") + normalized_silver)

    for item_key, quantity in normalized_items.items():
        contribution_cost = existing_costs.get(item_key, normalized_costs.get(item_key, 0))
        add_item_to_warehouse(
            attacker_guild,
            item_key,
            quantity,
            contribution_cost,
            count_as_production=False,
        )
    return normalized_silver, normalized_items
