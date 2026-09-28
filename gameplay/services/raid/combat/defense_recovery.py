"""踢馆连续受攻时的防守血量恢复。"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from gameplay.constants import PVPConstants
from gameplay.models import Building, Manor, RaidRun
from gameplay.services.city_defense_rules import CITY_DEFENSE_MAX_HP, city_defense_max_hp
from guests.models import Guest


def has_recent_defensive_battle(run: RaidRun, *, now: datetime) -> bool:
    """只统计已生成战报的实际战斗，当前这一场是窗口内的第二场时返回真。"""
    window_start = now - timedelta(seconds=PVPConstants.RAID_REPEAT_DEFENSE_WINDOW_SECONDS)
    return (
        RaidRun.objects.filter(
            defender_id=run.defender_id,
            battle_report__created_at__gte=window_start,
            battle_report__created_at__lte=now,
        )
        .exclude(pk=run.pk)
        .exists()
    )


def restore_defender_battle_hp(manor: Manor, defender_guests: list[Guest], *, now: datetime | None = None) -> None:
    """在战斗事务中回满参战门客和城防，供随后的战斗读取。"""
    now = now or timezone.now()

    for guest in defender_guests:
        guest.current_hp = guest.max_hp
        guest.last_hp_recovery_at = now
    if defender_guests:
        Guest.objects.bulk_update(defender_guests, ["current_hp", "last_hp_recovery_at"])

    buildings = list(
        Building.objects.select_for_update()
        .select_related("building_type")
        .filter(manor_id=manor.pk, building_type__key__in=CITY_DEFENSE_MAX_HP, level__gt=0)
        .order_by("pk")
    )
    for building in buildings:
        building.current_hp = city_defense_max_hp(building.building_type.key, building.level)
        building.hp_updated_at = now
    if buildings:
        Building.objects.bulk_update(buildings, ["current_hp", "hp_updated_at"])
