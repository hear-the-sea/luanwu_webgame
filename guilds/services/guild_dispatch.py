from __future__ import annotations

from typing import Any

from django.db.models import F

from core.exceptions import GuildMembershipError, GuildPermissionError, GuildValidationError

from ..models import Guild, GuildBattleLineupEntry, GuildMember


def normalize_positive_ids(raw_values: list[Any]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in raw_values or []:
        if raw is None or isinstance(raw, bool):
            raise GuildValidationError("门客参数错误")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise GuildValidationError("门客参数错误") from exc
        if value <= 0:
            raise GuildValidationError("门客参数错误")
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def lock_manage_member(*, guild: Guild, operator, permission_label: str) -> GuildMember:
    membership = (
        GuildMember.objects.select_for_update()
        .select_related("guild", "user")
        .filter(guild=guild, user=operator, is_active=True)
        .first()
    )
    if not membership:
        raise GuildMembershipError("您不在帮会中")
    if not membership.can_manage:
        raise GuildPermissionError(f"只有管理员/帮主可以{permission_label}")
    return membership


def load_dispatch_lineup_rows(*, guild: Guild, pool_entry_ids: list[int]) -> list[GuildBattleLineupEntry]:
    row_map = {
        row.pool_entry_id: row
        for row in GuildBattleLineupEntry.objects.select_for_update()
        .select_related("pool_entry__source_guest__template")
        .filter(
            guild=guild,
            pool_entry_id__in=pool_entry_ids,
            pool_entry__owner_member__is_active=True,
            pool_entry__owner_member__guild_id=guild.id,
            pool_entry__source_guest__manor__user_id=F("pool_entry__owner_member__user_id"),
        )
        .order_by("slot_index", "id")
    }
    if len(row_map) != len(pool_entry_ids):
        raise GuildValidationError("每次出征只能从上阵门客中选取")

    ordered_rows: list[GuildBattleLineupEntry] = []
    for pool_entry_id in pool_entry_ids:
        row = row_map.get(pool_entry_id)
        if row is None or row.pool_entry.source_guest is None:
            raise GuildValidationError("每次出征只能从上阵门客中选取")
        ordered_rows.append(row)
    return ordered_rows


def parse_troop_loadout_from_post(post_data) -> dict[str, int]:
    loadout: dict[str, int] = {}
    for key, raw_value in getattr(post_data, "items", lambda: [])():
        if not isinstance(key, str) or not key.startswith("troop_"):
            continue
        troop_key = key.removeprefix("troop_").strip()
        if not troop_key:
            continue
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            quantity = int(raw_value)
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            loadout[troop_key] = quantity
    return loadout
