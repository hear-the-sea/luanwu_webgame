from __future__ import annotations

from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from guests.models import Guest, GuestStatus


def finalize_raid(
    run: Any,
    *,
    now: Any = None,
    load_locked_raid_run: Callable[[Any], Any],
    normalize_positive_int_mapping: Callable[[Any], dict[str, int]],
    return_surviving_troops: Callable[[Any], None],
    load_locked_attacker: Callable[[int], Any],
    grant_resources_locked: Callable[..., Any],
    grant_loot_items: Callable[[Any, dict[str, int]], None],
    battle_reward_reason: Any,
) -> None:
    now = now or timezone.now()

    with transaction.atomic():
        locked_run = load_locked_raid_run(run.pk)
        if not locked_run or locked_run.status == locked_run.Status.COMPLETED:
            return

        guests = list(locked_run.guests.select_for_update())
        guests_to_update = []
        for guest in guests:
            if guest.status == GuestStatus.DEPLOYED:
                guest.status = GuestStatus.IDLE
                guests_to_update.append(guest)

        if guests_to_update:
            Guest.objects.bulk_update(guests_to_update, ["status"])

        return_surviving_troops(locked_run)

        if locked_run.is_attacker_victory:
            attacker_locked = load_locked_attacker(locked_run.attacker_id)
            loot_resources = normalize_positive_int_mapping(locked_run.loot_resources)
            loot_items = normalize_positive_int_mapping(locked_run.loot_items)

            # 新记录的粮食来自物品池；旧记录可能在两个字段中都有粮食，统一合并后入资源账本。
            grain_loot = loot_resources.pop("grain", 0) + loot_items.pop("grain", 0)
            if grain_loot > 0:
                loot_resources["grain"] = grain_loot
            if loot_resources:
                grant_resources_locked(
                    attacker_locked,
                    loot_resources,
                    note="踢馆掠夺",
                    reason=battle_reward_reason,
                    sync_production=False,
                )
            if loot_items:
                grant_loot_items(attacker_locked, loot_items)

        locked_run.status = locked_run.Status.COMPLETED
        locked_run.completed_at = now
        locked_run.save(update_fields=["status", "completed_at"])
