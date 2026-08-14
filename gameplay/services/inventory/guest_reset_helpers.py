from __future__ import annotations

import logging
from dataclasses import dataclass
from random import Random

from django.db import IntegrityError, transaction

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
from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX
from guests.models import Guest, GuestRarity, GuestStatus, GuestTemplate
from guests.rarity import GUEST_RARITY_ORDER

from .core import add_item_to_inventory_locked

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
    "training_remaining_seconds",
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


def detach_guest_gears_for_reset(guest: Guest, *, action_label: str, manor: Manor | None = None) -> int:
    """Best-effort detach all equipped gears for guest reset-like flows."""
    from guests.services.equipment import unequip_guest_item

    restore_manor = manor or guest.manor
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
            restore_gear_to_warehouse(restore_manor, gear.template.key)
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

    add_item_to_inventory_locked(
        manor,
        item_template.key,
        1,
        template=item_template,
    )


def prepare_guest_for_reset(
    guest: Guest,
    *,
    action_label: str,
    manor: Manor | None = None,
) -> GuestResetPreparation:
    unequipped_count = detach_guest_gears_for_reset(guest, action_label=action_label, manor=manor)
    skills_count = guest.guest_skills.count()
    guest.guest_skills.all().delete()
    return GuestResetPreparation(
        guest_name=guest.display_name,
        old_level=guest.level,
        old_rarity_display=guest.template.get_rarity_display(),
        unequipped_count=unequipped_count,
        skills_cleared=skills_count,
    )


def _normalize_guest_rarity(raw_value: object, *, field_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise GuestItemConfigurationError(f"{field_name}配置错误")

    rarity = raw_value.strip()
    valid_rarities = {value for value, _label in GuestRarity.choices}
    if rarity not in valid_rarities:
        raise GuestItemConfigurationError(f"{field_name}配置错误")
    return rarity


def _require_higher_rarity(source_rarity: str, target_rarity: str) -> None:
    try:
        source_rank = GUEST_RARITY_ORDER.index(source_rarity)
        target_rank = GUEST_RARITY_ORDER.index(target_rarity)
    except ValueError as exc:
        raise GuestItemConfigurationError("稀有度配置错误") from exc
    if source_rank >= target_rank:
        raise GuestItemConfigurationError("目标稀有度必须高于来源稀有度")


def _get_or_create_rarity_conversion_template(guest: Guest, *, target_rarity: str) -> GuestTemplate:
    """Create one non-recruitable target template per source template and target rarity.

    调用方必须已在事务内持有 Manor 行锁；同 source template 的并发创建会由
    source template 行锁串行化，这里再补一层唯一约束冲突重试作为防御。
    """
    source_template = GuestTemplate.objects.select_for_update().get(pk=guest.template_id)
    source_rarity = _normalize_guest_rarity(source_template.rarity, field_name="来源稀有度")
    normalized_target_rarity = _normalize_guest_rarity(target_rarity, field_name="目标稀有度")
    _require_higher_rarity(source_rarity, normalized_target_rarity)
    conversion_key = f"{RARITY_CONVERSION_TEMPLATE_KEY_PREFIX}{normalized_target_rarity}_{source_template.pk}"
    target_template = GuestTemplate.objects.select_for_update().filter(key=conversion_key).first()
    if target_template:
        if target_template.rarity != normalized_target_rarity:
            raise GuestItemConfigurationError("目标稀有度模板配置错误")
        return target_template

    source_avatar = source_template.avatar.name if source_template.avatar else ""
    source_attribute_weights = source_template.attribute_weights
    if not isinstance(source_attribute_weights, dict):
        source_attribute_weights = {}

    try:
        # 嵌套 atomic 提供 savepoint：唯一约束冲突回滚到 savepoint 后，
        # 外层事务仍可继续查询，不会因 PG 事务中止而失败。
        with transaction.atomic():
            return GuestTemplate.objects.create(
                key=conversion_key,
                name=source_template.name,
                archetype=source_template.archetype,
                rarity=normalized_target_rarity,
                base_attack=source_template.base_attack,
                base_intellect=source_template.base_intellect,
                base_defense=source_template.base_defense,
                base_agility=source_template.base_agility,
                base_luck=source_template.base_luck,
                base_hp=source_template.base_hp,
                avatar=source_avatar,
                flavor=source_template.flavor,
                default_gender=source_template.default_gender,
                default_morality=source_template.default_morality,
                recruitable=False,
                is_hermit=source_template.is_hermit,
                # Empty means the blue rarity default range is used by the growth engine.
                growth_range=[],
                attribute_weights=dict(source_attribute_weights),
            )
    except IntegrityError:
        # 另一个事务先创建了同 key 模板；重新读取并返回，保持幂等。
        existing = GuestTemplate.objects.select_for_update().filter(key=conversion_key).first()
        if existing is None:
            raise
        if existing.rarity != normalized_target_rarity:
            raise GuestItemConfigurationError("目标稀有度模板配置错误")
        return existing


def resolve_rarity_upgrade_target(guest: Guest, *, payload: object) -> GuestTemplate:
    if not isinstance(payload, dict):
        raise GuestItemConfigurationError("升阶道具配置错误")

    source_template_key = str(getattr(getattr(guest, "template", None), "key", "") or "").strip()
    if not source_template_key:
        raise GuestItemConfigurationError("门客模板异常")

    target_template_map = payload.get("target_template_map")
    if target_template_map is not None:
        if not isinstance(target_template_map, dict):
            raise GuestItemConfigurationError("升阶道具配置错误")

        target_template_key = str(target_template_map.get(source_template_key) or "").strip()
        if not target_template_key:
            raise GuestItemConfigurationError("该门客无法使用此升阶道具")

        target_template = GuestTemplate.objects.select_for_update().filter(key=target_template_key).first()
        if not target_template:
            raise GuestItemConfigurationError("目标稀有度模板不存在")
        source_rarity = _normalize_guest_rarity(guest.template.rarity, field_name="来源稀有度")
        target_rarity = _normalize_guest_rarity(target_template.rarity, field_name="目标稀有度")
        _require_higher_rarity(source_rarity, target_rarity)
        return target_template

    source_rarity = _normalize_guest_rarity(payload.get("source_rarity"), field_name="来源稀有度")
    target_rarity = _normalize_guest_rarity(payload.get("target_rarity"), field_name="目标稀有度")
    if guest.template.rarity != source_rarity:
        raise GuestItemConfigurationError("该门客无法使用此升阶道具")
    _require_higher_rarity(source_rarity, target_rarity)

    return _get_or_create_rarity_conversion_template(guest, target_rarity=target_rarity)


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
    guest.training_remaining_seconds = None
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
