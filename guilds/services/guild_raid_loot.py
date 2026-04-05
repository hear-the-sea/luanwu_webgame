from __future__ import annotations

import random

from django.db.models import F

from ..models import Guild, GuildWarehouse
from .guild_raid_rules import get_guild_raid_rules
from .warehouse import add_item_to_warehouse


def grant_guild_raid_battle_rewards(*, guild: Guild, rewards: dict[str, int]) -> dict[str, int]:
    normalized_rewards = {
        str(item_key).strip(): max(0, int(quantity or 0))
        for item_key, quantity in (rewards or {}).items()
        if str(item_key).strip() and int(quantity or 0) > 0
    }
    for item_key, quantity in normalized_rewards.items():
        add_item_to_warehouse(guild, item_key, quantity, 0)
    return normalized_rewards


def _draw_weighted_item_loot(rows: list[GuildWarehouse], *, draw_count: int) -> dict[str, int]:
    total_quantity = sum(int(row.quantity or 0) for row in rows)
    resolved_draw_count = min(max(0, int(draw_count or 0)), total_quantity)
    if resolved_draw_count <= 0 or total_quantity <= 0:
        return {}

    selected_positions = sorted(random.sample(range(total_quantity), resolved_draw_count))
    loot_items: dict[str, int] = {}
    cursor = 0
    selection_index = 0

    for row in rows:
        row_quantity = int(row.quantity or 0)
        if row_quantity <= 0:
            continue

        next_cursor = cursor + row_quantity
        selected_count = 0
        while selection_index < len(selected_positions) and selected_positions[selection_index] < next_cursor:
            selected_count += 1
            selection_index += 1
        if selected_count > 0:
            loot_items[row.item_key] = selected_count
        cursor = next_cursor

    return loot_items


def transfer_guild_raid_loot(*, attacker_guild: Guild, defender_guild: Guild) -> tuple[int, dict[str, int]]:
    from .guild_raid_support import lock_guild_pair

    rules = get_guild_raid_rules()
    attacker_locked, defender_locked = lock_guild_pair(
        attacker_guild_id=attacker_guild.pk,
        defender_guild_id=defender_guild.pk,
    )

    silver_floor = int(rules["silver_floor"] or 0)
    silver_percent = int(rules["silver_loot_percent"] or 0)
    silver_available = max(0, int(defender_locked.silver or 0) - silver_floor)
    loot_silver = silver_available * silver_percent // 100
    if loot_silver > 0:
        Guild.objects.filter(pk=defender_locked.pk, silver__gte=loot_silver).update(silver=F("silver") - loot_silver)
        Guild.objects.filter(pk=attacker_locked.pk).update(silver=F("silver") + loot_silver)

    whitelist = list(rules["warehouse_loot_whitelist"] or [])
    warehouse_percent = int(rules["warehouse_loot_percent"] or 0)
    loot_items: dict[str, int] = {}
    if warehouse_percent <= 0 or not whitelist:
        return loot_silver, loot_items

    locked_rows = list(
        GuildWarehouse.objects.select_for_update()
        .filter(guild=defender_locked, item_key__in=whitelist, quantity__gt=0)
        .order_by("item_key", "id")
    )
    total_quantity = sum(int(row.quantity or 0) for row in locked_rows)
    draw_count = total_quantity * warehouse_percent // 100
    loot_items = _draw_weighted_item_loot(locked_rows, draw_count=draw_count)

    for row in locked_rows:
        loot_quantity = int(loot_items.get(row.item_key, 0) or 0)
        if loot_quantity <= 0:
            continue
        updated = GuildWarehouse.objects.filter(pk=row.pk, quantity__gte=loot_quantity).update(
            quantity=F("quantity") - loot_quantity,
            total_exchanged=F("total_exchanged") + loot_quantity,
        )
        if updated != 1:
            continue
        add_item_to_warehouse(attacker_locked, row.item_key, loot_quantity, row.contribution_cost)

    GuildWarehouse.objects.filter(guild=defender_locked, quantity=0).delete()
    return loot_silver, loot_items
