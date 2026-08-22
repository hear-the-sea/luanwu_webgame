from __future__ import annotations

from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from guests.models import GuestStatus
from guests.services.status import persist_guest_status_transitions


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
) -> bool:
    now = now or timezone.now()

    with transaction.atomic():
        locked_run = load_locked_raid_run(run.pk)
        if not locked_run or locked_run.status not in {
            locked_run.Status.RETURNING,
            locked_run.Status.RETREATED,
        }:
            return True

        guests = list(locked_run.guests.select_for_update())
        persist_guest_status_transitions(
            [guest for guest in guests if guest.status == GuestStatus.DEPLOYED],
            GuestStatus.IDLE,
            source="raid_finalize",
        )

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
                _, overflow = grant_resources_locked(
                    attacker_locked,
                    loot_resources,
                    note="踢馆掠夺",
                    reason=battle_reward_reason,
                    sync_production=False,
                )
                if overflow:
                    # 战斗时已按攻击方容量裁剪；返程期间若容量又被占满，保持
                    # RETURNING 并等待容量释放，不能把已从防守方扣除的资源静默丢掉。
                    transaction.set_rollback(True)
                    return False
            if loot_items:
                grant_loot_items(attacker_locked, loot_items)

        locked_run.status = locked_run.Status.COMPLETED
        locked_run.completed_at = now
        locked_run.save(update_fields=["status", "completed_at"])
        return True
