from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.exceptions import ItemNotUsableError
from gameplay.models import (
    InventoryItem,
    Manor,
    ResourceEvent,
    TargetItemEffectCooldown,
    TemporaryGuestOccupation,
    WorkAssignment,
)
from gameplay.services.inventory.core import add_items_to_inventory_locked, consume_inventory_item_locked
from guests.models import Guest, GuestStatus
from guests.services.loyalty import start_injury_loyalty_decay
from guests.services.status import GUEST_STATUS_UPDATE_FIELDS, prepare_guest_status_transition

TARGET_ITEM_ACTIONS = {
    "occupy_target_guest": ("slave", "奴隶卡"),
    "steal_target_item": ("theft", "抢夺卡"),
    "injure_target_guest": ("injury", "重伤卡"),
    "transfer_target_grain": ("grain_transfer", "均粮卡"),
    "transfer_target_silver": ("silver_transfer", "均贫卡"),
    "sheep_wrath_target": ("sheep_wrath", "羊的愤怒"),
}
TARGET_ITEM_COOLDOWN = timedelta(hours=1)
TEMPORARY_GUEST_OCCUPATION = timedelta(hours=1)
STEALABLE_ITEM_RARITIES = ("blue", "purple", "orange")


def is_target_item_action(action: Any) -> bool:
    return isinstance(action, str) and action in TARGET_ITEM_ACTIONS


def validate_target_manor(attacker: Manor, target: Manor, *, now: datetime | None = None) -> None:
    """Apply the same attacker, newbie, peace-shield and prestige protections as raids."""
    from gameplay.services.raid.utils import can_attack_target

    can_attack, reason = can_attack_target(
        attacker,
        target,
        now=now,
        use_cached_recent_attacks=False,
    )
    if not can_attack:
        raise ItemNotUsableError(message=reason or "当前无法对该庄园使用目标卡")


def _get_locked_target(item: InventoryItem, *, action: str) -> Manor:
    target = getattr(item, "_use_target_manor", None)
    if not isinstance(target, Manor):
        raise ItemNotUsableError(item.template.name, message="请选择目标庄园")
    validate_target_manor(item.manor, target)
    effect_key, _label = TARGET_ITEM_ACTIONS[action]
    now = timezone.now()
    cooldown = (
        TargetItemEffectCooldown.objects.select_for_update()
        .filter(
            manor=target,
            effect_key=effect_key,
        )
        .first()
    )
    if cooldown and cooldown.expires_at > now:
        remaining_seconds = max(1, int((cooldown.expires_at - now).total_seconds()))
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        raise ItemNotUsableError(
            item.template.name,
            message=f"该庄园近1小时内已受到{_label}影响，请{remaining_minutes}分钟后再试",
        )
    return target


def _record_target_cooldown(target: Manor, *, action: str, now: datetime) -> None:
    effect_key, _label = TARGET_ITEM_ACTIONS[action]
    cooldown, _created = TargetItemEffectCooldown.objects.select_for_update().get_or_create(
        manor=target,
        effect_key=effect_key,
        defaults={"expires_at": now + TARGET_ITEM_COOLDOWN},
    )
    if cooldown.expires_at <= now:
        cooldown.expires_at = now + TARGET_ITEM_COOLDOWN
        cooldown.save(update_fields=["expires_at", "updated_at"])


def release_expired_guest_occupations(*, now: datetime | None = None, limit: int = 500) -> int:
    """Restore expired slave-card guests while preserving other status changes."""
    current_time = now or timezone.now()
    with transaction.atomic():
        expired_manor_ids = list(
            TemporaryGuestOccupation.objects.filter(expires_at__lte=current_time)
            .order_by("manor_id")
            .values_list("manor_id", flat=True)
            .distinct()[: max(1, int(limit))]
        )
        if not expired_manor_ids:
            return 0
        list(Manor.objects.select_for_update().filter(pk__in=expired_manor_ids).order_by("pk"))
        occupations = list(
            TemporaryGuestOccupation.objects.select_for_update()
            .filter(expires_at__lte=current_time, manor_id__in=expired_manor_ids)
            .order_by("expires_at", "pk")[: max(1, int(limit))]
        )
        if not occupations:
            return 0

        guest_ids = [occupation.guest_id for occupation in occupations]
        guests = {
            guest.pk: guest for guest in Guest.objects.select_for_update().filter(pk__in=guest_ids).order_by("pk")
        }
        active_work_guest_ids = set(
            WorkAssignment.objects.filter(
                status=WorkAssignment.Status.WORKING,
                guest_id__in=guest_ids,
            ).values_list("guest_id", flat=True)
        )
        for occupation in occupations:
            guest = guests.get(occupation.guest_id)
            if (
                guest is not None
                and guest.manor_id == occupation.manor_id
                and guest.status == GuestStatus.WORKING
                and guest.pk not in active_work_guest_ids
            ):
                prepare_guest_status_transition(guest, GuestStatus.IDLE, now=current_time)
                guest.save(update_fields=GUEST_STATUS_UPDATE_FIELDS)
            occupation.delete()
        return len(occupations)


def _release_expired_occupations_for_manor(target: Manor, *, now: datetime) -> None:
    occupations = list(
        TemporaryGuestOccupation.objects.select_for_update()
        .filter(manor=target, expires_at__lte=now)
        .order_by("expires_at", "pk")
    )
    if not occupations:
        return
    guests = {
        guest.pk: guest
        for guest in Guest.objects.select_for_update()
        .filter(pk__in=[row.guest_id for row in occupations])
        .order_by("pk")
    }
    active_work_guest_ids = set(
        WorkAssignment.objects.filter(
            manor=target,
            status=WorkAssignment.Status.WORKING,
            guest_id__in=[row.guest_id for row in occupations],
        ).values_list("guest_id", flat=True)
    )
    for occupation in occupations:
        guest = guests.get(occupation.guest_id)
        if (
            guest is not None
            and guest.manor_id == target.pk
            and guest.status == GuestStatus.WORKING
            and guest.pk not in active_work_guest_ids
        ):
            prepare_guest_status_transition(guest, GuestStatus.IDLE, now=now)
            guest.save(update_fields=GUEST_STATUS_UPDATE_FIELDS)
        occupation.delete()


def use_slave_card(item: InventoryItem) -> dict[str, Any]:
    action = "occupy_target_guest"
    target = _get_locked_target(item, action=action)
    now = timezone.now()
    _release_expired_occupations_for_manor(target, now=now)

    active_work_guest_ids = WorkAssignment.objects.filter(
        manor=target,
        status=WorkAssignment.Status.WORKING,
    ).values_list("guest_id", flat=True)
    idle_guests = list(
        Guest.objects.select_for_update()
        .filter(
            manor=target,
            status=GuestStatus.IDLE,
            training_complete_at__isnull=True,
            training_remaining_seconds__isnull=True,
        )
        .exclude(pk__in=active_work_guest_ids)
        .order_by("pk")
    )
    if not idle_guests:
        raise ItemNotUsableError(item.template.name, message="目标庄园没有可占用的空闲门客")

    from .random_source import inventory_random

    guest = inventory_random.choice(idle_guests)
    prepare_guest_status_transition(guest, GuestStatus.WORKING, now=now)
    guest.save(update_fields=GUEST_STATUS_UPDATE_FIELDS)
    TemporaryGuestOccupation.objects.create(
        manor=target,
        guest=guest,
        expires_at=now + TEMPORARY_GUEST_OCCUPATION,
    )
    _record_target_cooldown(target, action=action, now=now)
    return {
        "目标庄园": target.display_name,
        "受影响门客": guest.display_name,
        "占用时长": "1小时",
        "_message": f"{target.display_name}的门客{guest.display_name}已被占用1小时",
    }


def use_steal_card(item: InventoryItem) -> dict[str, Any]:
    action = "steal_target_item"
    target = _get_locked_target(item, action=action)
    from .random_source import inventory_random

    candidates = list(
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(
            manor=target,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            template__tradeable=True,
            template__rarity__in=STEALABLE_ITEM_RARITIES,
            quantity__gt=F("bound_quantity"),
        )
        .order_by("pk")
    )
    if not candidates:
        raise ItemNotUsableError(item.template.name, message="目标仓库没有可抢夺的蓝色及以上未绑定物品")

    stolen = inventory_random.choice(candidates)
    consume_inventory_item_locked(stolen, 1, allow_bound=False)
    add_items_to_inventory_locked(
        item.manor,
        {stolen.template.key: 1},
        templates={stolen.template.key: stolen.template},
        is_bound=False,
    )
    now = timezone.now()
    _record_target_cooldown(target, action=action, now=now)
    return {
        "目标庄园": target.display_name,
        "获得物品": stolen.template.name,
        "数量": 1,
        "_message": f"从{target.display_name}夺得【{stolen.template.name}】×1",
    }


def use_injury_card(item: InventoryItem) -> dict[str, Any]:
    action = "injure_target_guest"
    target = _get_locked_target(item, action=action)
    from .random_source import inventory_random

    candidates = list(
        Guest.objects.select_for_update().filter(manor=target).exclude(status=GuestStatus.INJURED).order_by("pk")
    )
    if not candidates:
        raise ItemNotUsableError(item.template.name, message="目标庄园没有可使其重伤的门客")

    guest = inventory_random.choice(candidates)
    now = timezone.now()
    guest.current_hp = 1
    guest.last_hp_recovery_at = now
    prepare_guest_status_transition(guest, GuestStatus.INJURED, now=now)
    start_injury_loyalty_decay(guest, now=now)
    guest.save(
        update_fields=[
            *GUEST_STATUS_UPDATE_FIELDS,
            "current_hp",
            "last_hp_recovery_at",
            "injury_loyalty_processed_at",
        ]
    )
    _record_target_cooldown(target, action=action, now=now)
    return {
        "目标庄园": target.display_name,
        "受伤门客": guest.display_name,
        "生命": 1,
        "_message": f"{target.display_name}的门客{guest.display_name}已重伤，生命降至1",
    }


def use_silver_transfer_card(item: InventoryItem) -> dict[str, Any]:
    return _use_resource_equalization_card(item, resource="silver", label="银两", capacity_label="银库")


def use_grain_transfer_card(item: InventoryItem) -> dict[str, Any]:
    return _use_resource_equalization_card(item, resource="grain", label="粮食", capacity_label="粮仓")


def _use_resource_equalization_card(
    item: InventoryItem, *, resource: str, label: str, capacity_label: str
) -> dict[str, Any]:
    """Average settled balances, discarding the user's capacity overflow and fractional units."""
    action = f"transfer_target_{resource}"
    target = _get_locked_target(item, action=action)
    source = item.manor
    now = timezone.now()

    from gameplay.services.inventory.core import set_warehouse_grain_quantity_locked
    from gameplay.services.resources import log_resource_gain, settle_resource_production_locked

    settle_resource_production_locked(source, now=now, note=f"{item.template.name}使用前结算")
    settle_resource_production_locked(target, now=now, note=f"{item.template.name}受影响前结算")
    average = (int(getattr(source, resource)) + int(getattr(target, resource))) // 2
    source_balance = min(average, int(getattr(source, f"{resource}_capacity")))
    discarded = average - source_balance

    for manor, balance in ((source, source_balance), (target, average)):
        delta = balance - int(getattr(manor, resource))
        if delta == 0:
            continue
        setattr(manor, resource, balance)
        manor.save(update_fields=[resource])
        if resource == "grain":
            set_warehouse_grain_quantity_locked(manor, balance)
        log_resource_gain(
            manor,
            {resource: delta},
            ResourceEvent.Reason.ITEM_USE,
            note=f"{item.template.name}平分{label}",
        )
    _record_target_cooldown(target, action=action, now=now)

    return {
        "目标庄园": target.display_name,
        f"平均{label}": average,
        f"我方{label}": source_balance,
        f"对方{label}": average,
        "超限丢弃": discarded,
        "_message": (
            f"与{target.display_name}平分{label}，平均×{average}，我方现有×{source_balance}，对方现有×{average}"
            + (f"，超出我方{capacity_label}上限的×{discarded}已丢弃" if discarded else "")
        ),
    }


def use_sheep_wrath_card(item: InventoryItem) -> dict[str, Any]:
    """Consume gold bars, injure all target guests and clear target manor resources."""
    action = "sheep_wrath_target"
    target = _get_locked_target(item, action=action)
    source = item.manor
    now = timezone.now()

    from gameplay.services.resources import settle_resource_production_locked, spend_resources_locked
    from guests.models import Guest, GuestStatus
    from guests.services.loyalty import start_injury_loyalty_decay
    from guests.services.status import GUEST_STATUS_UPDATE_FIELDS, prepare_guest_status_transition
    from trade.services.auction.gold_bars import consume_available_gold_bars_locked

    consume_available_gold_bars_locked(source, 10)
    settle_resource_production_locked(target, now=now, note="遭受羊的愤怒前结算")

    guests = list(Guest.objects.select_for_update().filter(manor=target).order_by("pk"))
    for guest in guests:
        guest.current_hp = 1
        guest.last_hp_recovery_at = now
        if guest.status != GuestStatus.INJURED:
            prepare_guest_status_transition(guest, GuestStatus.INJURED, now=now)
            start_injury_loyalty_decay(guest, now=now)
        guest.save(
            update_fields=[
                *GUEST_STATUS_UPDATE_FIELDS,
                "current_hp",
                "last_hp_recovery_at",
                "injury_loyalty_processed_at",
            ]
        )

    cleared = {
        "silver": int(target.silver),
        "grain": int(target.grain),
    }
    resources_to_clear = {resource: amount for resource, amount in cleared.items() if amount > 0}
    if resources_to_clear:
        spend_resources_locked(
            target,
            resources_to_clear,
            note="羊的愤怒清空庄园资源",
            reason=ResourceEvent.Reason.ITEM_USE,
            sync_production=False,
        )

    _record_target_cooldown(target, action=action, now=now)
    return {
        "目标庄园": target.display_name,
        "受影响门客": len(guests),
        "清空银两": cleared["silver"],
        "清空粮食": cleared["grain"],
        "消耗金条": 10,
        "_message": (
            f"羊的愤怒已生效：{target.display_name}的{len(guests)}名门客生命降至1，"
            f"银两×{cleared['silver']}、粮食×{cleared['grain']}已清空；消耗金条×10"
        ),
    }
