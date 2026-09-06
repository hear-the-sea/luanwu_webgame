from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone

from core.config import GUEST
from core.exceptions import GuildMembershipError, GuildPermissionError, GuildValidationError
from guests.guest_combat_stats import resolve_guest_combat_stats
from guests.models import GearItem, GearSlot, Guest, GuestSkill
from guests.services.equipment_inventory import attach_troop_device_bonus_summaries
from guests.services.equipment_stats import slot_capacity

from .. import constants as guild_constants
from ..models import Guild, GuildBattleLineupEntry, GuildHeroPoolEntry, GuildMember
from .technology import get_guild_dispatch_capacity, get_guild_lineup_capacity


@dataclass(frozen=True)
class HeroPoolSubmitResult:
    entry: GuildHeroPoolEntry
    replaced: bool
    lineup_removed_count: int


@dataclass(frozen=True)
class HeroPoolRemoveResult:
    slot_index: int
    lineup_removed_count: int


@dataclass(frozen=True)
class LineupAddResult:
    lineup_entry: GuildBattleLineupEntry


@dataclass(frozen=True)
class GuildLineupLockResult:
    guest_ids: list[int]
    locked_at: Any
    removed_invalid_count: int


def _normalize_slot_index(raw_slot_index: Any) -> int:
    try:
        slot_index = int(raw_slot_index)
    except (TypeError, ValueError):
        raise GuildValidationError("槽位参数错误")
    if slot_index < 1 or slot_index > guild_constants.GUILD_HERO_POOL_SLOT_LIMIT:
        raise GuildValidationError(f"槽位必须在 1~{guild_constants.GUILD_HERO_POOL_SLOT_LIMIT} 之间")
    return slot_index


def _normalize_guest_id(raw_guest_id: Any) -> int:
    try:
        guest_id = int(raw_guest_id)
    except (TypeError, ValueError):
        raise GuildValidationError("门客参数错误")
    if guest_id <= 0:
        raise GuildValidationError("门客参数错误")
    return guest_id


def _lock_guild_and_active_member(
    *,
    guild_id: int,
    member_id: int | None = None,
    user: Any | None = None,
    error_msg: str = "您不在帮会中",
) -> tuple[Guild, GuildMember]:
    locked_guild = Guild.objects.select_for_update().get(pk=guild_id)
    member_qs = (
        GuildMember.objects.select_for_update()
        .select_related("guild", "user")
        .filter(
            guild=locked_guild,
            is_active=True,
        )
    )
    if member_id is not None:
        member_qs = member_qs.filter(pk=member_id)
    if user is not None:
        member_qs = member_qs.filter(user=user)
    locked_member = member_qs.first()
    if not locked_member:
        raise GuildMembershipError(error_msg)
    return locked_guild, locked_member


def _replace_cooldown_until(entry: GuildHeroPoolEntry) -> datetime:
    return entry.last_submitted_at + timedelta(seconds=guild_constants.GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS)


def _is_entry_invalid(entry: GuildHeroPoolEntry, *, guild_id: int | None = None) -> bool:
    if not entry.source_guest_id:
        return True
    if not entry.owner_member_id:
        return True
    if guild_id is not None and entry.guild_id != guild_id:
        return True
    if not entry.owner_member.is_active:
        return True
    if entry.owner_member.guild_id != entry.guild_id:
        return True
    source_guest = entry.source_guest
    if source_guest is None:
        return True
    if source_guest.manor.user_id != entry.owner_member.user_id:
        return True
    return False


@transaction.atomic
def submit_hero_pool_entry(
    member: GuildMember, *, guest_id: int, slot_index: int, now: datetime | None = None
) -> HeroPoolSubmitResult:
    current_time = now or timezone.now()
    normalized_slot = _normalize_slot_index(slot_index)
    normalized_guest_id = _normalize_guest_id(guest_id)

    guild, locked_member = _lock_guild_and_active_member(guild_id=member.guild_id, member_id=member.pk)

    locked_guest = (
        Guest.objects.select_for_update()
        .select_related("template")
        .filter(id=normalized_guest_id, manor=locked_member.user.manor)
        .first()
    )
    if not locked_guest:
        raise GuildMembershipError("该门客不存在或不属于您")

    existing_entry = (
        GuildHeroPoolEntry.objects.select_for_update()
        .select_related("owner_member", "source_guest")
        .filter(guild=guild, owner_member=locked_member, slot_index=normalized_slot)
        .first()
    )
    if existing_entry and _is_entry_invalid(existing_entry, guild_id=guild.id):
        existing_entry.delete()
        existing_entry = None

    duplicate_entry = (
        GuildHeroPoolEntry.objects.select_for_update()
        .select_related("owner_member", "source_guest")
        .filter(guild=guild, owner_member=locked_member, source_guest_id=locked_guest.id)
        .exclude(slot_index=normalized_slot)
        .first()
    )
    if duplicate_entry and _is_entry_invalid(duplicate_entry, guild_id=guild.id):
        duplicate_entry.delete()
        duplicate_entry = None
    if duplicate_entry:
        raise GuildValidationError("该门客已在另一槽位中")

    if existing_entry and existing_entry.source_guest_id == locked_guest.id:
        raise GuildValidationError("该槽位已是该门客")

    if existing_entry:
        cooldown_until = _replace_cooldown_until(existing_entry)
        if cooldown_until > current_time:
            remaining_seconds = int((cooldown_until - current_time).total_seconds())
            remaining_minutes = max(1, (remaining_seconds + 59) // 60)
            raise GuildValidationError(f"该槽位替换冷却中，请 {remaining_minutes} 分钟后再试")

    lineup_removed_count = 0
    if existing_entry:
        lineup_removed_count = GuildBattleLineupEntry.objects.filter(guild=guild, pool_entry=existing_entry).count()
        if lineup_removed_count:
            GuildBattleLineupEntry.objects.filter(guild=guild, pool_entry=existing_entry).delete()

        existing_entry.source_guest = locked_guest
        existing_entry.last_submitted_at = current_time
        existing_entry.save(update_fields=["source_guest", "last_submitted_at", "updated_at"])
        entry = existing_entry
        replaced = True
    else:
        entry = GuildHeroPoolEntry.objects.create(
            guild=guild,
            owner_member=locked_member,
            source_guest=locked_guest,
            slot_index=normalized_slot,
            last_submitted_at=current_time,
        )
        replaced = False

    return HeroPoolSubmitResult(entry=entry, replaced=replaced, lineup_removed_count=lineup_removed_count)


@transaction.atomic
def remove_hero_pool_entry(member: GuildMember, *, slot_index: int) -> HeroPoolRemoveResult:
    normalized_slot = _normalize_slot_index(slot_index)
    current_time = timezone.now()
    guild, locked_member = _lock_guild_and_active_member(guild_id=member.guild_id, member_id=member.pk)

    target_entry = (
        GuildHeroPoolEntry.objects.select_for_update()
        .filter(guild=guild, owner_member_id=locked_member.id, slot_index=normalized_slot)
        .first()
    )
    if not target_entry:
        raise GuildValidationError("该槽位当前没有门客")
    cooldown_until = _replace_cooldown_until(target_entry)
    if cooldown_until > current_time:
        remaining_seconds = int((cooldown_until - current_time).total_seconds())
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        raise GuildValidationError(f"该槽位替换冷却中，请 {remaining_minutes} 分钟后再试")

    lineup_removed_count = target_entry.lineup_entries.count()
    target_entry.delete()
    return HeroPoolRemoveResult(slot_index=normalized_slot, lineup_removed_count=lineup_removed_count)


def _next_lineup_slot(existing_slots: set[int], *, lineup_limit: int) -> int | None:
    for slot in range(1, lineup_limit + 1):
        if slot not in existing_slots:
            return slot
    return None


def _assert_entry_valid_or_cleanup(*, entry: GuildHeroPoolEntry, guild_id: int) -> None:
    if _is_entry_invalid(entry, guild_id=guild_id):
        entry.delete()
        raise GuildValidationError("该门客池条目已失效")


@transaction.atomic
def add_lineup_entry(
    *, guild: Guild, operator: Any, pool_entry_id: int, now: datetime | None = None
) -> LineupAddResult:
    del now
    locked_guild, operator_member = _lock_guild_and_active_member(
        guild_id=guild.pk,
        user=operator,
        error_msg="您不在该帮会中",
    )
    if not operator_member.can_manage:
        raise GuildPermissionError("只有管理员/帮主可以设置出战名单")

    pool_entry = (
        GuildHeroPoolEntry.objects.select_for_update()
        .select_related("owner_member", "source_guest")
        .filter(pk=pool_entry_id, guild=locked_guild)
        .first()
    )
    if not pool_entry:
        raise GuildValidationError("该门客池条目不存在")
    _assert_entry_valid_or_cleanup(entry=pool_entry, guild_id=locked_guild.id)

    cleanup_invalid_hero_pool_entries_for_guild(guild_id=locked_guild.id, limit=200)
    lineup_rows = list(
        GuildBattleLineupEntry.objects.select_for_update().filter(guild=locked_guild).order_by("slot_index")
    )
    lineup_limit = get_guild_lineup_capacity(locked_guild)
    if any(row.pool_entry_id == pool_entry.id for row in lineup_rows):
        raise GuildValidationError("该门客已在出战名单中")
    if len(lineup_rows) >= lineup_limit:
        raise GuildValidationError(f"出战名单已满（最多 {lineup_limit} 名）")

    slot_index = _next_lineup_slot({row.slot_index for row in lineup_rows}, lineup_limit=lineup_limit)
    if slot_index is None:
        raise GuildValidationError("未找到可用出战槽位")

    lineup_entry = GuildBattleLineupEntry.objects.create(
        guild=locked_guild,
        pool_entry=pool_entry,
        slot_index=slot_index,
        selected_by=operator,
    )
    return LineupAddResult(lineup_entry=lineup_entry)


@transaction.atomic
def remove_lineup_entry(*, guild: Guild, operator: Any, lineup_entry_id: int) -> None:
    locked_guild, operator_member = _lock_guild_and_active_member(
        guild_id=guild.pk,
        user=operator,
        error_msg="您不在该帮会中",
    )
    if not operator_member.can_manage:
        raise GuildPermissionError("只有管理员/帮主可以调整出战名单")

    lineup_entry = (
        GuildBattleLineupEntry.objects.select_for_update()
        .filter(
            pk=lineup_entry_id,
            guild=locked_guild,
        )
        .first()
    )
    if not lineup_entry:
        raise GuildValidationError("出战名单记录不存在")
    lineup_entry.delete()


@transaction.atomic
def invalidate_member_hero_pool(member: GuildMember) -> int:
    if not member:
        return 0
    deleted_count, _ = GuildHeroPoolEntry.objects.filter(
        guild_id=member.guild_id,
        owner_member_id=member.id,
    ).delete()
    return int(deleted_count)


def _invalid_hero_pool_q() -> Q:
    return (
        Q(source_guest__isnull=True)
        | Q(owner_member__is_active=False)
        | ~Q(owner_member__guild_id=F("guild_id"))
        | ~Q(source_guest__manor__user_id=F("owner_member__user_id"))
    )


def cleanup_invalid_hero_pool_entries(*, limit: int = 500) -> int:
    batch_size = max(1, int(limit))
    invalid_ids = list(
        GuildHeroPoolEntry.objects.filter(_invalid_hero_pool_q())
        .order_by("id")
        .values_list("id", flat=True)[:batch_size]
    )
    if not invalid_ids:
        return 0
    GuildHeroPoolEntry.objects.filter(id__in=invalid_ids).delete()
    return len(invalid_ids)


def cleanup_invalid_hero_pool_entries_for_guild(*, guild_id: int, limit: int = 100) -> int:
    batch_size = max(1, int(limit))
    invalid_ids = list(
        GuildHeroPoolEntry.objects.filter(guild_id=guild_id)
        .filter(_invalid_hero_pool_q())
        .order_by("id")
        .values_list("id", flat=True)[:batch_size]
    )
    if not invalid_ids:
        return 0
    GuildHeroPoolEntry.objects.filter(id__in=invalid_ids).delete()
    return len(invalid_ids)


@transaction.atomic
def lock_guild_lineup_for_dispatch(guild: Guild, *, now: datetime | None = None) -> GuildLineupLockResult:
    """
    出征时锁定出征阵容（锁定的是门客列表，不是属性快照）。

    说明：
    - 返回按出战位排序后的 guest_id 列表；
    - 结算阶段应使用该列表，不再重新读取帮会实时名单，避免中途换将影响本次战斗。
    """
    locked_at = now or timezone.now()
    locked_guild = Guild.objects.select_for_update().get(pk=guild.pk)
    lineup_entries = list(
        GuildBattleLineupEntry.objects.select_for_update()
        .select_related("pool_entry__owner_member", "pool_entry__source_guest")
        .filter(guild=locked_guild)
        .order_by("slot_index", "id")
    )

    guest_ids: list[int] = []
    invalid_entry_ids: list[int] = []
    for lineup in lineup_entries:
        pool_entry = lineup.pool_entry
        if _is_entry_invalid(pool_entry, guild_id=locked_guild.id):
            invalid_entry_ids.append(lineup.id)
            continue
        source_guest_id = pool_entry.source_guest_id
        if source_guest_id is None:
            invalid_entry_ids.append(lineup.id)
            continue
        guest_ids.append(source_guest_id)

    if invalid_entry_ids:
        GuildBattleLineupEntry.objects.filter(id__in=invalid_entry_ids).delete()

    dispatch_capacity = get_guild_dispatch_capacity(locked_guild)
    return GuildLineupLockResult(
        guest_ids=guest_ids[:dispatch_capacity],
        locked_at=locked_at,
        removed_invalid_count=len(invalid_entry_ids),
    )


def _seconds_until(target_time: datetime, *, now: datetime | None = None) -> int:
    current_time = now or timezone.now()
    return max(0, int((target_time - current_time).total_seconds()))


def _build_pool_row_payload(
    *,
    entry: GuildHeroPoolEntry,
    member_id: int,
    lineup_pool_entry_ids: set[int],
) -> dict[str, Any]:
    source_guest = entry.source_guest
    if source_guest is None:
        raise AssertionError("invalid hero pool row missing source_guest")

    if entry.id in lineup_pool_entry_ids:
        status_key = "lineup"
        status_label = "已上阵"
    elif entry.owner_member_id == member_id:
        status_key = "mine"
        status_label = "我的槽位"
    else:
        status_key = "joinable"
        status_label = "可加入"

    return {
        "entry": entry,
        "guest_name": source_guest.display_name,
        "guest_level": source_guest.level,
        "guest_rarity": source_guest.rarity,
        "owner_name": entry.owner_member.user.manor.display_name,
        "avatar_url": source_guest.template.avatar.url if source_guest.template.avatar else None,
        "status_key": status_key,
        "status_label": status_label,
        "is_owner": entry.owner_member_id == member_id,
        "in_lineup": entry.id in lineup_pool_entry_ids,
        "search_text": f"{source_guest.display_name} {entry.owner_member.user.manor.display_name}".lower(),
    }


def _build_filter_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "joinable": sum(1 for row in rows if row["status_key"] == "joinable"),
        "lineup": sum(1 for row in rows if row["status_key"] == "lineup"),
        "mine": sum(1 for row in rows if row["status_key"] == "mine"),
        "all": len(rows),
    }


def _build_guest_detail_equipment_panels(gear_items: list[GearItem]) -> list[dict[str, Any]]:
    equipment_by_slot: dict[str, list[GearItem]] = {slot.value: [] for slot in GearSlot}
    for gear_item in gear_items:
        equipment_by_slot.setdefault(gear_item.template.slot, []).append(gear_item)

    panels: list[dict[str, Any]] = []
    known_slots: set[str] = set()
    for slot in GearSlot:
        known_slots.add(slot.value)
        panels.append(
            {
                "value": slot.value,
                "label": slot.label,
                "items": equipment_by_slot.get(slot.value, []),
                "capacity": slot_capacity(slot.value),
            }
        )

    for slot_value in sorted(set(equipment_by_slot) - known_slots):
        panels.append(
            {
                "value": slot_value,
                "label": slot_value or "其他",
                "items": equipment_by_slot[slot_value],
                "capacity": len(equipment_by_slot[slot_value]),
            }
        )
    return panels


def _build_guest_detail_skill_slots(guest_skill_records: list[GuestSkill]) -> list[dict[str, Any]]:
    ordered_records = sorted(guest_skill_records, key=lambda record: (record.learned_at, record.id))
    return [
        {
            "index": index + 1,
            "record": ordered_records[index] if index < len(ordered_records) else None,
            "skill": ordered_records[index].skill if index < len(ordered_records) else None,
        }
        for index in range(int(GUEST.MAX_SKILL_SLOTS))
    ]


def get_hero_pool_guest_detail_context(member: GuildMember, *, pool_entry_id: int) -> dict[str, Any] | None:
    """Return read-only details for a valid guest currently visible in this guild's pool."""

    try:
        normalized_entry_id = int(pool_entry_id)
    except (TypeError, ValueError):
        return None
    if normalized_entry_id <= 0:
        return None

    active_member_exists = GuildMember.objects.filter(
        pk=member.pk,
        guild_id=member.guild_id,
        user_id=member.user_id,
        is_active=True,
    ).exists()
    if not active_member_exists:
        return None

    entry = (
        GuildHeroPoolEntry.objects.filter(
            pk=normalized_entry_id,
            guild_id=member.guild_id,
            owner_member__is_active=True,
            owner_member__guild_id=F("guild_id"),
            source_guest__isnull=False,
            source_guest__manor__user_id=F("owner_member__user_id"),
        )
        .select_related("owner_member__user__manor", "source_guest__template")
        .prefetch_related(
            Prefetch(
                "source_guest__gear_items",
                queryset=GearItem.objects.select_related("template").only(
                    "id",
                    "guest_id",
                    "template_id",
                    "template__key",
                    "template__name",
                    "template__slot",
                    "template__rarity",
                    "template__set_key",
                    "template__set_description",
                    "template__set_bonus",
                    "template__attack_bonus",
                    "template__defense_bonus",
                    "template__extra_stats",
                ),
            ),
            Prefetch(
                "source_guest__guest_skills",
                queryset=GuestSkill.objects.select_related("skill").only(
                    "id",
                    "guest_id",
                    "skill_id",
                    "source",
                    "learned_at",
                    "skill__key",
                    "skill__name",
                    "skill__rarity",
                    "skill__description",
                    "skill__kind",
                ),
            ),
        )
        .first()
    )
    if entry is None or entry.source_guest is None:
        return None

    guest = entry.source_guest
    gear_items = list(guest.gear_items.all())
    attach_troop_device_bonus_summaries(item.template for item in gear_items)
    guest_skill_records = list(guest.guest_skills.all())
    combat_stats = resolve_guest_combat_stats(guest)
    current_hp = max(0, min(combat_stats.max_hp, int(guest.current_hp or 0)))

    return {
        "pool_entry": entry,
        "guest": guest,
        "owner_name": entry.owner_member.user.manor.display_name,
        "health_stat": {
            "current": current_hp,
            "max": combat_stats.max_hp,
        },
        "core_stat_rows": [
            {"key": "force", "label": "武力", "value": guest.force},
            {"key": "intellect", "label": "智力", "value": guest.intellect},
            {"key": "defense", "label": "防御", "value": guest.defense_stat},
            {"key": "agility", "label": "敏捷", "value": guest.agility},
            {"key": "luck", "label": "运势", "value": guest.luck},
        ],
        "equipment_panels": _build_guest_detail_equipment_panels(gear_items),
        "skill_slots": _build_guest_detail_skill_slots(guest_skill_records),
        "skill_count": len(guest_skill_records),
        "skill_capacity": int(GUEST.MAX_SKILL_SLOTS),
    }


def get_hero_pool_page_context(member: GuildMember) -> dict[str, Any]:
    now = timezone.now()
    guild_id = member.guild_id

    active_entries = list(
        GuildHeroPoolEntry.objects.filter(guild_id=guild_id)
        .filter(
            owner_member__is_active=True,
            owner_member__guild_id=guild_id,
            source_guest__manor__user_id=F("owner_member__user_id"),
        )
        .select_related("owner_member__user__manor", "source_guest__template")
        .order_by("owner_member_id", "slot_index")
    )
    lineup_entries = list(
        GuildBattleLineupEntry.objects.filter(guild_id=guild_id)
        .filter(
            pool_entry__owner_member__is_active=True,
            pool_entry__owner_member__guild_id=guild_id,
            pool_entry__source_guest__manor__user_id=F("pool_entry__owner_member__user_id"),
        )
        .select_related("pool_entry__owner_member__user__manor", "pool_entry__source_guest__template")
        .order_by("slot_index")
    )

    my_entries_by_slot = {entry.slot_index: entry for entry in active_entries if entry.owner_member_id == member.id}
    my_guest_ids = {entry.source_guest_id for entry in my_entries_by_slot.values() if entry.source_guest_id}
    lineup_pool_entry_ids = {entry.pool_entry_id for entry in lineup_entries}

    available_guests = list(member.user.manor.guests.select_related("template").order_by("-level", "id"))

    slot_rows: list[dict[str, Any]] = []
    for slot_index in range(1, guild_constants.GUILD_HERO_POOL_SLOT_LIMIT + 1):
        entry = my_entries_by_slot.get(slot_index)
        cooldown_until = None
        cooldown_seconds = 0
        if entry:
            cooldown_until = _replace_cooldown_until(entry)
            cooldown_seconds = _seconds_until(cooldown_until, now=now)

        slot_rows.append(
            {
                "slot_index": slot_index,
                "entry": entry,
                "cooldown_until": cooldown_until,
                "cooldown_seconds": cooldown_seconds,
            }
        )

    pool_rows = [
        _build_pool_row_payload(entry=entry, member_id=member.id, lineup_pool_entry_ids=lineup_pool_entry_ids)
        for entry in active_entries
    ]
    lineup_summary_rows = [
        {
            "lineup_entry_id": lineup.id,
            "pool_entry_id": lineup.pool_entry_id,
            "slot_index": lineup.slot_index,
            "guest_name": lineup.pool_entry.source_guest.display_name,
            "guest_rarity": lineup.pool_entry.source_guest.rarity,
            "owner_name": lineup.pool_entry.owner_member.user.manor.display_name,
            "avatar_url": (
                lineup.pool_entry.source_guest.template.avatar.url
                if lineup.pool_entry.source_guest.template.avatar
                else None
            ),
        }
        for lineup in lineup_entries
        if lineup.pool_entry and lineup.pool_entry.source_guest
    ]

    return {
        "guild": member.guild,
        "member": member,
        "slot_rows": slot_rows,
        "pool_rows": pool_rows,
        "lineup_summary_rows": lineup_summary_rows,
        "lineup_entries": lineup_entries,
        "available_guests": available_guests,
        "my_guest_ids": my_guest_ids,
        "hero_pool_filter_counts": _build_filter_counts(pool_rows),
        "lineup_count": len(lineup_entries),
        "lineup_limit": get_guild_lineup_capacity(member.guild),
        "hero_pool_slot_limit": guild_constants.GUILD_HERO_POOL_SLOT_LIMIT,
        "replace_cooldown_minutes": guild_constants.GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS // 60,
    }
