from __future__ import annotations

from typing import Any

from django.db import connection
from django.utils import timezone

from core.exceptions import ItemNotConfiguredError, ItemNotUsableError
from gameplay.models import InventoryItem
from guests.models import Guest, GuestStatus
from guests.services.health import heal_guest


def heal_manor_guests_locked(item: InventoryItem, heal_amount: Any) -> dict[str, Any]:
    """在仓库使用事务已锁庄园和库存后，为全体门客各治疗一次。

    库存统一由调用方扣减；忙碌门客只更新持久血量，不改变状态或战斗快照。
    """
    if isinstance(heal_amount, bool) or not isinstance(heal_amount, int) or heal_amount <= 0:
        raise ItemNotConfiguredError("hp 配置异常")
    lock_targets = ("self",) if connection.features.has_select_for_update_of else ()
    guests = list(
        Guest.objects.select_for_update(of=lock_targets)
        .select_related("template")
        .filter(manor_id=item.manor_id)
        .order_by("id")
    )
    now = timezone.now()
    healed_count = healed_hp = injury_cured_count = 0
    for guest in guests:
        if guest.current_hp >= guest.max_hp:
            continue
        if guest.status in {GuestStatus.IDLE, GuestStatus.INJURED}:
            result = heal_guest(guest, heal_amount, now=now)
            healed_hp += result["healed"]
            injury_cured_count += int(result["injury_cured"])
        else:
            previous_hp = guest.current_hp
            guest.current_hp = min(guest.max_hp, previous_hp + heal_amount)
            guest.last_hp_recovery_at = now
            guest.save(update_fields=["current_hp", "last_hp_recovery_at"])
            healed_hp += guest.current_hp - previous_hp
        healed_count += 1
    if not healed_count:
        raise ItemNotUsableError(item.template.name, message="没有需要恢复生命值的门客")
    return {
        "healed_count": healed_count,
        "healed_hp": healed_hp,
        "injury_cured_count": injury_cured_count,
        "_message": f"已为 {healed_count} 位门客各恢复至多 {heal_amount} 点生命值，共恢复 {healed_hp} 点",
    }
