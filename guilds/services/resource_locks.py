"""帮会资源倍率变化统一先锁庄园，再锁帮会与成员。"""

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction

from core.exceptions import GuildMembershipError
from gameplay.models import Manor
from gameplay.services.resources import settle_resource_production_locked
from guilds.models import Guild, GuildMember


@dataclass(frozen=True)
class GuildResourceLockScope:
    member_user_ids: frozenset[int]
    manors: tuple[Manor, ...]


def lock_guild_resource_manors(guild_id: int) -> GuildResourceLockScope:
    """在帮会锁之前按主键锁定成员庄园；成员集合稍后在帮会锁内复核。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("guild resource locks require an atomic block")
    user_ids = frozenset(
        GuildMember.objects.filter(guild_id=guild_id, is_active=True).values_list("user_id", flat=True)
    )
    manors = tuple(Manor.objects.select_for_update().filter(user_id__in=user_ids).order_by("pk"))
    return GuildResourceLockScope(user_ids, manors)


def settle_guild_resource_manors(guild: Guild, scope: GuildResourceLockScope, *, now: datetime) -> list[int]:
    """只结算预先锁定的庄园；并发新成员出现时回滚，禁止倒序补锁。"""
    user_ids = list(
        GuildMember.objects.select_for_update()
        .filter(guild_id=guild.pk, is_active=True)
        .order_by("pk")
        .values_list("user_id", flat=True)
    )
    if not set(user_ids).issubset(scope.member_user_ids):
        raise GuildMembershipError("帮会成员已变化，请刷新后重试")
    active_user_ids = set(user_ids)
    for manor in scope.manors:
        if manor.user_id in active_user_ids:
            settle_resource_production_locked(manor, now=now, note="帮会资源产出结算")
    return user_ids
