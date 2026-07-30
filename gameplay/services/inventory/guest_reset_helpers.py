from __future__ import annotations

import logging
from dataclasses import dataclass
from random import Random

from django.db.models import F

from core.exceptions import (
    GameError,
    GuestItemConfigurationError,
    GuestItemOwnershipError,
    GuestNotIdleError,
    GuestOwnershipError,
    InsufficientStockError,
    ItemNotFoundError,
)
from gameplay.models import InventoryItem, ItemTemplate, Manor
from guests.models import Guest, GuestStatus, GuestTemplate

logger = logging.getLogger(__name__)

GUEST_RESET_UPDATE_FIELDS = [
    "level",
    "experience",
    "force",
    "intellect",
    "defense_stat",
    "agility",
    "luck",
    "attribute_points",
    "attack_bonus",
    "defense_bonus",
    "hp_bonus",
    "troop_capacity_bonus",
    "gear_set_bonus",
    "training_target_level",
    "training_complete_at",
    "status",
    "initial_force",
    "initial_intellect",
    "initial_defense",
    "initial_agility",
    "xisuidan_used",
    "allocated_force",
    "allocated_intellect",
    "allocated_defense",
    "allocated_agility",
]


@dataclass(frozen=True)
class GuestResetPreparation:
    guest_name: str
    old_level: int
    old_rarity_display: str
    unequipped_count: int
    skills_cleared: int


def validate_guest_item_use(
    manor: Manor,
    item: InventoryItem,
    guest_id: int,
    action: str,
) -> tuple[InventoryItem, Guest]:
    """Validate guest-target item usage prerequisites and lock related rows."""
    if not item.pk:
        raise ItemNotFoundError()

    locked_item = (
        InventoryItem.objects.select_for_update()
        .select_related("template", "manor")
        .filter(pk=item.pk, manor=manor)
        .first()
    )
    if not locked_item:
        raise GuestItemOwnershipError()

    payload = locked_item.template.effect_payload or {}
    if payload.get("action") != action:
        raise GuestItemConfigurationError("物品类型错误")

    if locked_item.quantity <= 0:
        raise InsufficientStockError(locked_item.template.name, 1, locked_item.quantity)

    guest = Guest.objects.select_for_update().select_related("template").filter(id=guest_id, manor=manor).first()
    if not guest:
        raise GuestOwnershipError(message="门客不存在或不属于您的庄园")
    if guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest, message=f"{guest.display_name} 当前非空闲状态，无法执行该操作")

    return locked_item, guest


def detach_guest_gears_for_reset(guest: Guest, *, action_label: str) -> int:
    """Best-effort detach all equipped gears for guest reset-like flows."""
    from guests.services.equipment import unequip_guest_item

    gear_items = list(guest.gear_items.select_related("template"))
    unequipped_count = 0

    for gear in gear_items:
        try:
            unequip_guest_item(gear, guest)
            unequipped_count += 1
            continue
        except GameError as exc:
            logger.warning(
                "门客%s时常规卸装失败，改为强制卸下: guest_id=%s, gear_id=%s, error=%s",
                action_label,
                guest.pk,
                gear.pk,
                exc,
            )
        updated = guest.gear_items.filter(pk=gear.pk, guest_id=guest.pk).update(guest=None)
        if updated:
            restore_gear_to_warehouse(guest.manor, gear.template.key)
            unequipped_count += 1
        else:
            logger.warning(
                "门客%s时强制卸装未命中: guest_id=%s, gear_id=%s",
                action_label,
                guest.pk,
                gear.pk,
            )

    return unequipped_count


def restore_gear_to_warehouse(manor: Manor, gear_template_key: str) -> None:
    item_template = ItemTemplate.objects.filter(key=gear_template_key).first()
    if not item_template:
        logger.warning("强制卸装后未找到回仓模板: manor_id=%s, gear_key=%s", manor.pk, gear_template_key)
        return

    restored = InventoryItem.objects.filter(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).update(quantity=F("quantity") + 1)
    if restored == 0:
        InventoryItem.objects.create(
            manor=manor,
            template=item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity=1,
        )


def prepare_guest_for_reset(guest: Guest, *, action_label: str) -> GuestResetPreparation:
    unequipped_count = detach_guest_gears_for_reset(guest, action_label=action_label)
    skills_count = guest.guest_skills.count()
    guest.guest_skills.all().delete()
    return GuestResetPreparation(
        guest_name=guest.display_name,
        old_level=guest.level,
        old_rarity_display=guest.template.get_rarity_display(),
        unequipped_count=unequipped_count,
        skills_cleared=skills_count,
    )


def resolve_rarity_upgrade_target(guest: Guest, *, payload: object) -> GuestTemplate:
    if not isinstance(payload, dict):
        raise GuestItemConfigurationError("升阶道具配置错误")

    target_template_map = payload.get("target_template_map") or {}
    if not isinstance(target_template_map, dict):
        raise GuestItemConfigurationError("升阶道具配置错误")

    source_template_key = str(getattr(getattr(guest, "template", None), "key", "") or "").strip()
    if not source_template_key:
        raise GuestItemConfigurationError("门客模板异常")

    target_template_key = str(target_template_map.get(source_template_key) or "").strip()
    if not target_template_key:
        raise GuestItemConfigurationError("该门客无法使用此升阶道具")

    target_template = GuestTemplate.objects.select_for_update().filter(key=target_template_key).first()
    if not target_template:
        raise GuestItemConfigurationError("目标稀有度模板不存在")

    return target_template


def roll_guest_template_attributes(template: GuestTemplate, *, rng: Random) -> dict[str, int]:
    from guests.utils.recruitment_variance import apply_recruitment_variance

    template_attrs = {
        "force": template.base_attack,
        "intellect": template.base_intellect,
        "defense": template.base_defense,
        "agility": template.base_agility,
        "luck": template.base_luck,
    }
    varied_attrs = apply_recruitment_variance(
        template_attrs,
        rarity=template.rarity,
        archetype=template.archetype,
        rng=rng,
    )
    return {
        "force": int(varied_attrs["force"]),
        "intellect": int(varied_attrs["intellect"]),
        "defense": int(varied_attrs["defense"]),
        "agility": int(varied_attrs["agility"]),
        "luck": int(varied_attrs["luck"]),
    }


def apply_guest_template_reset(
    guest: Guest,
    *,
    target_template: GuestTemplate,
    varied_attrs: dict[str, int],
    include_template: bool = False,
) -> None:
    if include_template:
        guest.template = target_template

    guest.level = 1
    guest.experience = 0
    guest.force = varied_attrs["force"]
    guest.intellect = varied_attrs["intellect"]
    guest.defense_stat = varied_attrs["defense"]
    guest.agility = varied_attrs["agility"]
    guest.luck = varied_attrs["luck"]
    guest.attribute_points = 0
    guest.attack_bonus = 0
    guest.defense_bonus = 0
    guest.hp_bonus = 0
    guest.troop_capacity_bonus = 0
    guest.gear_set_bonus = {}
    guest.training_target_level = 0
    guest.training_complete_at = None
    guest.status = GuestStatus.IDLE
    guest.initial_force = varied_attrs["force"]
    guest.initial_intellect = varied_attrs["intellect"]
    guest.initial_defense = varied_attrs["defense"]
    guest.initial_agility = varied_attrs["agility"]
    guest.allocated_force = 0
    guest.allocated_intellect = 0
    guest.allocated_defense = 0
    guest.allocated_agility = 0
    guest.xisuidan_used = 0

    update_fields = list(GUEST_RESET_UPDATE_FIELDS)
    if include_template:
        update_fields.insert(0, "template")
    guest.save(update_fields=update_fields)
    guest.restore_full_hp()


def build_reset_extra_parts(
    *,
    unequipped_count: int,
    skills_cleared: int,
    base_parts: list[str] | None = None,
) -> list[str]:
    extras = list(base_parts or [])
    if unequipped_count > 0:
        extras.append(f"装备已卸下（{unequipped_count}件）")
    if skills_cleared > 0:
        extras.append(f"技能已清空（{skills_cleared}个）")
    return extras
