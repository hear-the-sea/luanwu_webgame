"""专属门客的实例成长、吞噬及升阶。所有写入先锁庄园。"""

from __future__ import annotations

import random
import unicodedata
from typing import Any, Protocol

from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError, GuestCapacityFullError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.inventory.core import (
    add_item_to_inventory_locked,
    consume_inventory_item_locked,
    validate_inventory_item_group,
)
from guests.avatar import resolve_guest_disguise_avatar_asset
from guests.models import CustomGuestProgress, Guest, GuestStatus, GuestTemplate, default_custom_guest_weights

CUSTOM_GUEST_TEMPLATE_KEYS = frozenset(f"custom_guest_{rarity}" for rarity in ("green", "blue", "purple", "orange"))
CUSTOM_GUEST_CARD_KEY = "custom_guest_card"
WEIGHT_CAP = 25000
WEIGHT_GAINS = {"blue": 15, "purple": 50, "orange": 100}
DAILY_FEED_LIMIT = 100


class RandomSource(Protocol):
    def random(self) -> float: ...


GROWTH_RANGES = {"green": (3, 7), "blue": (5, 9), "purple": (6, 11), "orange": (6, 14)}


def is_custom_guest(guest: object) -> bool:
    return getattr(getattr(guest, "template", None), "key", "") in CUSTOM_GUEST_TEMPLATE_KEYS


def resolve_custom_growth_range(guest: Guest) -> tuple[int, int]:
    low, high = GROWTH_RANGES[guest.rarity]
    return low, high + (guest.custom_progress.enhancement_level if guest.rarity == "orange" else 0)


def resolve_custom_growth_weights(guest: Guest) -> dict[str, int]:
    return dict(guest.custom_progress.weights)


def get_custom_guest_initial_attributes(template: GuestTemplate) -> dict[str, int]:
    """专属门客固定使用模板基础值，不应用招募浮动。"""
    return {
        "force": template.base_attack,
        "intellect": template.base_intellect,
        "defense": template.base_defense,
        "agility": template.base_agility,
        "luck": template.base_luck,
    }


def get_custom_guest_context(guest: Guest) -> dict[str, Any]:
    progress = guest.custom_progress
    stage = None
    stages = {
        "green": ("blue", 5000, 10, 50),
        "blue": ("purple", 10000, 30, 30),
        "purple": ("orange", 20000, 50, 10),
    }
    if guest.rarity in stages:
        target, threshold, cost, initial = stages[guest.rarity]
        stage = {
            "target": target,
            "required_weight": threshold,
            "cost": cost,
            "label": "升阶",
            "chance": min(100, initial + progress.failed_attempts * 10),
        }
    elif progress.enhancement_level < 2:
        stage = {
            "target": "orange",
            "required_weight": 0,
            "cost": 50,
            "label": "强化",
            "chance": min(100, 10 + progress.failed_attempts * 10),
        }
    low, high = resolve_custom_growth_range(guest)
    feeding_used = progress.feeding_count if progress.feeding_date == timezone.localdate() else 0
    return {
        "weights": dict(progress.weights),
        "total_weight": sum(progress.weights.values()),
        "weight_cap": WEIGHT_CAP,
        "growth_min": low,
        "growth_max": high,
        "enhancement_level": progress.enhancement_level,
        "failed_attempts": progress.failed_attempts,
        "next_stage": stage,
        "feeding_used": feeding_used,
        "feeding_limit": DAILY_FEED_LIMIT,
        "feeding_remaining": max(0, DAILY_FEED_LIMIT - feeding_used),
    }


def _appearance(name: object, avatar_key: object) -> tuple[str, str]:
    if not isinstance(name, str):
        raise GameError("请输入门客姓名")
    name = name.strip()
    if not 2 <= len(name) <= 12 or any(unicodedata.category(char).startswith("C") for char in name):
        raise GameError("姓名须为2～12个字符，不能包含控制字符")
    avatar = resolve_guest_disguise_avatar_asset(avatar_key)
    if not avatar:
        raise GameError("请选择有效的门客头像")
    return name, avatar


def _lock_guest(manor: Manor, guest_id: int) -> tuple[Manor, Guest, CustomGuestProgress]:
    manor = Manor.objects.select_for_update().get(pk=manor.pk)
    guest = Guest.objects.select_for_update().select_related("template").filter(manor=manor, pk=guest_id).first()
    if guest is None or not is_custom_guest(guest):
        raise GameError("专属门客不存在")
    if guest.status != GuestStatus.IDLE:
        raise GameError("门客空闲时才能进行此操作")
    progress = CustomGuestProgress.objects.select_for_update().get(manor=manor, guest=guest)
    guest._state.fields_cache["custom_progress"] = progress
    return manor, guest, progress


def _inventory(manor: Manor, *, inventory_id: int | None = None, key: str | None = None) -> InventoryItem:
    query = (
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(manor=manor, storage_location=InventoryItem.StorageLocation.WAREHOUSE)
    )
    item = (
        query.filter(pk=inventory_id).first() if inventory_id is not None else query.filter(template__key=key).first()
    )
    if item is None or (key and item.template.key != key):
        raise GameError("仓库中没有所需道具")
    return item


@transaction.atomic
def ensure_custom_guest_card(manor: Manor) -> bool:
    if CustomGuestProgress.objects.filter(manor=manor, card_issued=True).exists():
        return False
    manor = Manor.objects.select_for_update().get(pk=manor.pk)
    template = ItemTemplate.objects.filter(key=CUSTOM_GUEST_CARD_KEY).first()
    if template is None:
        return False
    progress, _ = CustomGuestProgress.objects.select_for_update().get_or_create(manor=manor)
    if progress.card_issued:
        return False
    add_item_to_inventory_locked(manor, template.key, 1, template=template, is_bound=True)
    progress.card_issued = True
    progress.save(update_fields=["card_issued"])
    return True


@transaction.atomic
def create_custom_guest(manor: Manor, inventory_id: int, name: str, avatar_key: str) -> dict[str, Any]:
    name, avatar = _appearance(name, avatar_key)
    manor = Manor.objects.select_for_update().get(pk=manor.pk)
    progress, _ = CustomGuestProgress.objects.select_for_update().get_or_create(manor=manor)
    if progress.guest_id or manor.guests.filter(template__key__in=CUSTOM_GUEST_TEMPLATE_KEYS).exists():
        raise GameError("庄园已有专属门客")
    if manor.active_guest_count >= manor.guest_capacity:
        raise GuestCapacityFullError()
    item = _inventory(manor, inventory_id=inventory_id, key=CUSTOM_GUEST_CARD_KEY)
    validate_inventory_item_group(item, 1, None)
    template = GuestTemplate.objects.get(key="custom_guest_green")
    attributes = get_custom_guest_initial_attributes(template)
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        avatar_asset_key=avatar,
        force=attributes["force"],
        intellect=attributes["intellect"],
        defense_stat=attributes["defense"],
        agility=attributes["agility"],
        luck=attributes["luck"],
        initial_force=attributes["force"],
        initial_intellect=attributes["intellect"],
        initial_defense=attributes["defense"],
        initial_agility=attributes["agility"],
    )
    guest.restore_full_hp()
    progress.guest = guest
    progress.card_issued = True
    progress.save(update_fields=["guest", "card_issued"])
    consume_inventory_item_locked(item, 1)
    return {"guest_id": guest.pk, "message": f"{name}已加入庄园"}


def custom_equipment_weight_gain(template: ItemTemplate) -> tuple[str, int]:
    payload = template.effect_payload
    if (
        template.rarity not in WEIGHT_GAINS
        or template.effect_type
        not in {"equip_weapon", "equip_helmet", "equip_armor", "equip_shoes", "equip_mount", "equip_ornament"}
        or not isinstance(payload, dict)
    ):
        raise GameError("只能吞噬蓝色及以上的带前缀装备")
    stat = payload.get("equipment_affix")
    if stat not in default_custom_guest_weights() or str(payload.get("equipment_base_key", template.key)).startswith(
        "soulorn_"
    ):
        raise GameError("此装备不能用于吞噬")
    return stat, WEIGHT_GAINS[template.rarity]


@transaction.atomic
def consume_custom_guest_equipment(
    manor: Manor, guest_id: int, inventory_id: int, quantity: int, binding_mode: str | None = None
) -> dict[str, Any]:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise GameError("吞噬数量须为正整数")
    manor, guest, progress = _lock_guest(manor, guest_id)
    today = timezone.localdate()
    used = progress.feeding_count if progress.feeding_date == today else 0
    if quantity > DAILY_FEED_LIMIT - used:
        raise GameError(f"今日还可吞噬{max(0, DAILY_FEED_LIMIT - used)}件装备")
    item = _inventory(manor, inventory_id=inventory_id)
    stat, gain = custom_equipment_weight_gain(item.template)
    remaining = WEIGHT_CAP - sum(progress.weights.values())
    if remaining <= 0:
        raise GameError("成长权重已达上限")
    # 只有最后一件可以溢出，避免整批吞掉没有收益的装备。
    if quantity > (remaining + gain - 1) // gain:
        raise GameError("吞噬数量超过剩余权重所需数量")
    validate_inventory_item_group(item, quantity, binding_mode)
    actual_gain = min(remaining, quantity * gain)
    progress.weights = {**progress.weights, stat: progress.weights[stat] + actual_gain}
    progress.feeding_date = today
    progress.feeding_count = used + quantity
    progress.save(update_fields=["weights", "feeding_date", "feeding_count"])
    consume_inventory_item_locked(item, quantity, binding_mode=binding_mode)
    return {"stat": stat, "gain": actual_gain, "quantity": quantity, "message": f"吞噬成功，成长权重增加{actual_gain}"}


def reset_custom_guest_level(guest: Guest, *, target_template: GuestTemplate | None = None) -> None:
    from gameplay.services.inventory.guest_reset_helpers import apply_guest_template_reset, prepare_guest_for_reset

    appearance = {field: getattr(guest, field) for field in ("avatar_asset_key",)}
    prepare_guest_for_reset(guest, action_label="升阶", manor=guest.manor)
    apply_guest_template_reset(
        guest,
        target_template=target_template or guest.template,
        varied_attrs=get_custom_guest_initial_attributes(target_template or guest.template),
        include_template=target_template is not None,
    )
    for field, value in appearance.items():
        setattr(guest, field, value)
    guest.save(update_fields=list(appearance))


@transaction.atomic
def advance_custom_guest(manor: Manor, guest_id: int, rng: RandomSource | None = None) -> dict[str, Any]:
    manor, guest, progress = _lock_guest(manor, guest_id)
    context = get_custom_guest_context(guest)
    stage = context["next_stage"]
    if stage is None:
        raise GameError("已完成全部强化")
    if context["total_weight"] < stage["required_weight"]:
        raise GameError("成长权重尚未达到升阶要求")
    item = _inventory(manor, key="yijinjing")
    validate_inventory_item_group(item, stage["cost"], None)
    success = (rng or random.SystemRandom()).random() < stage["chance"] / 100
    if success:
        if guest.rarity == "orange":
            progress.enhancement_level += 1
        else:
            target = GuestTemplate.objects.get(key=f"custom_guest_{stage['target']}")
            reset_custom_guest_level(guest, target_template=target)
        progress.failed_attempts = 0
    else:
        progress.failed_attempts += 1
    progress.save(update_fields=["enhancement_level", "failed_attempts"])
    consume_inventory_item_locked(item, stage["cost"])
    return {
        "success": success,
        "chance": stage["chance"],
        "message": f"{stage['label']}{'成功' if success else '失败，下次成功率提升'}",
    }


@transaction.atomic
def reset_custom_guest_weights(manor: Manor, guest_id: int, binding_mode: str | None = None) -> dict[str, Any]:
    manor, guest, progress = _lock_guest(manor, guest_id)
    if progress.weights == default_custom_guest_weights():
        raise GameError("成长权重已为初始状态")
    item = _inventory(manor, key="millennium_zhuguo")
    validate_inventory_item_group(item, 1, binding_mode)
    progress.weights = default_custom_guest_weights()
    progress.save(update_fields=["weights"])
    consume_inventory_item_locked(item, 1, binding_mode=binding_mode)
    return {"message": "四项成长权重已恢复为各500"}


@transaction.atomic
def update_custom_guest_appearance(manor: Manor, guest_id: int, name: str, avatar_key: str) -> dict[str, Any]:
    name, avatar = _appearance(name, avatar_key)
    _, guest, _ = _lock_guest(manor, guest_id)
    guest.custom_name = name
    guest.avatar_asset_key = avatar
    guest.avatar_override_key = ""
    guest.original_avatar_asset_key = None
    guest.use_original_avatar = False
    guest.save(
        update_fields=[
            "custom_name",
            "avatar_asset_key",
            "avatar_override_key",
            "original_avatar_asset_key",
            "use_original_avatar",
        ]
    )
    return {"message": "姓名与形象已更新"}
