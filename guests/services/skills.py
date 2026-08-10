from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.db import transaction

from core.config import GUEST
from core.exceptions import (
    GuestItemConfigurationError,
    GuestItemOwnershipError,
    GuestNotFoundError,
    GuestNotIdleError,
    GuestNotRequirementError,
    GuestSkillAlreadyLearnedError,
    GuestSkillNotFoundError,
    InsufficientStockError,
    SkillSlotFullError,
)

from ..models import Guest, GuestSkill, GuestStatus, Skill

if TYPE_CHECKING:
    from gameplay.models import InventoryItem, Manor

MAX_GUEST_SKILL_SLOTS = int(GUEST.MAX_SKILL_SLOTS)
SKILL_REQUIREMENT_FIELDS = (
    ("level", "required_level", "level", "等级"),
    ("force", "required_force", "force", "武力"),
    ("intellect", "required_intellect", "intellect", "智力"),
    ("defense", "required_defense", "defense_stat", "防御"),
    ("agility", "required_agility", "agility", "敏捷"),
)


def _iter_skill_requirements(guest: Guest | None, skill: Skill) -> Iterator[tuple[str, str, int, int | None]]:
    for requirement_type, skill_field, guest_field, label in SKILL_REQUIREMENT_FIELDS:
        required = int(getattr(skill, skill_field, 0) or 0)
        if required <= 0:
            continue
        actual = None if guest is None else int(getattr(guest, guest_field, 0) or 0)
        yield requirement_type, label, required, actual


def collect_skill_requirements(skill: Skill | None) -> list[str]:
    if skill is None:
        return []
    return [f"{label}需 ≥ {required}" for _kind, label, required, _actual in _iter_skill_requirements(None, skill)]


def collect_unmet_skill_requirements(guest: Guest, skill: Skill | None) -> list[str]:
    if skill is None:
        return []
    unmet: list[str] = []
    for _kind, label, required, actual in _iter_skill_requirements(guest, skill):
        if actual is not None and actual < required:
            unmet.append(f"{label}需 ≥ {required}")
    return unmet


def assert_guest_meets_skill_requirements(guest: Guest, skill: Skill) -> None:
    for requirement_type, _label, required, actual in _iter_skill_requirements(guest, skill):
        if actual is not None and actual < required:
            raise GuestNotRequirementError(guest, requirement_type, required, actual)


def _require_atomic_block(name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{name} must be called inside transaction.atomic()")


def learn_guest_skill_locked(
    manor: "Manor",
    locked_guest: Guest,
    inventory_item_id: int,
    *,
    expected_skill_id: int | None = None,
) -> GuestSkill:
    """在已锁 Manor -> Guest 的事务内学习并消费一本仓库技能书。"""
    from gameplay.models import InventoryItem, ItemTemplate
    from gameplay.services.inventory.core import consume_inventory_item_locked

    _require_atomic_block("learn_guest_skill_locked")
    if not manor.pk or not locked_guest.pk or locked_guest.manor_id != manor.pk:
        raise GuestItemOwnershipError(message="门客或技能书不属于您的庄园")
    if locked_guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(
            locked_guest,
            message=f"{locked_guest.display_name} 当前非空闲状态，无法学习技能",
        )

    locked_item = (
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(
            pk=inventory_item_id,
            manor_id=manor.pk,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            template__effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        )
        .first()
    )
    if locked_item is None:
        raise GuestItemOwnershipError(message="技能书不存在或不属于您的庄园")
    if locked_item.quantity < 1:
        raise InsufficientStockError(
            locked_item.template.name,
            1,
            locked_item.quantity,
        )

    payload = locked_item.template.effect_payload
    if not isinstance(payload, dict):
        raise GuestItemConfigurationError("技能书配置有误")
    raw_skill_key = payload.get("skill_key")
    if not isinstance(raw_skill_key, str) or not raw_skill_key.strip():
        raise GuestItemConfigurationError("技能书配置有误")
    skill = Skill.objects.filter(key=raw_skill_key.strip()).first()
    if skill is None or (expected_skill_id is not None and skill.pk != expected_skill_id):
        raise GuestItemConfigurationError("技能书配置有误")

    if locked_guest.guest_skills.count() >= MAX_GUEST_SKILL_SLOTS:
        raise SkillSlotFullError("技能位已满")
    if locked_guest.guest_skills.filter(skill=skill).exists():
        raise GuestSkillAlreadyLearnedError(locked_guest, skill)
    assert_guest_meets_skill_requirements(locked_guest, skill)

    guest_skill = GuestSkill.objects.create(
        guest=locked_guest,
        skill=skill,
        source=GuestSkill.Source.BOOK,
    )
    consume_inventory_item_locked(locked_item)
    return guest_skill


def learn_guest_skill_from_virtual_book_locked(
    manor: "Manor",
    locked_guest: Guest,
    item_template_id: int,
    *,
    expected_skill_id: int | None = None,
    expected_item_key: str | None = None,
) -> GuestSkill:
    """Learn from a validated virtual skill-book definition without inventory."""

    from gameplay.models import ItemTemplate

    _require_atomic_block("learn_guest_skill_from_virtual_book_locked")
    if not manor.pk or not locked_guest.pk or locked_guest.manor_id != manor.pk:
        raise GuestItemOwnershipError(message="门客不属于该庄园")
    if locked_guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(
            locked_guest,
            message=f"{locked_guest.display_name} 当前非空闲状态，无法学习技能",
        )
    template = (
        ItemTemplate.objects.select_for_update()
        .filter(
            pk=int(item_template_id),
            effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        )
        .first()
    )
    if template is None or (expected_item_key is not None and str(template.key) != str(expected_item_key)):
        raise GuestItemConfigurationError("虚拟技能书定义已发生变化")
    payload = template.effect_payload
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("skill_key"), str)
        or not payload["skill_key"].strip()
    ):
        raise GuestItemConfigurationError("技能书配置有误")
    skill = Skill.objects.filter(key=payload["skill_key"].strip()).first()
    if skill is None or (expected_skill_id is not None and int(skill.pk) != int(expected_skill_id)):
        raise GuestItemConfigurationError("技能书配置有误")
    if locked_guest.guest_skills.count() >= MAX_GUEST_SKILL_SLOTS:
        raise SkillSlotFullError("技能位已满")
    if locked_guest.guest_skills.filter(skill=skill).exists():
        raise GuestSkillAlreadyLearnedError(locked_guest, skill)
    assert_guest_meets_skill_requirements(locked_guest, skill)
    return GuestSkill.objects.create(
        guest=locked_guest,
        skill=skill,
        source=GuestSkill.Source.VIRTUAL,
    )


def learn_guest_skill(
    guest: Guest,
    skill: Skill,
    inventory_item: "InventoryItem",
) -> GuestSkill:
    from gameplay.models import Manor

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().filter(pk=guest.manor_id).first()
        if locked_manor is None:
            raise GuestNotFoundError()
        locked_guest = Guest.objects.select_for_update().filter(pk=guest.pk, manor_id=locked_manor.pk).first()
        if locked_guest is None:
            raise GuestNotFoundError()
        return learn_guest_skill_locked(
            locked_manor,
            locked_guest,
            inventory_item.pk,
            expected_skill_id=skill.pk,
        )


def forget_guest_skill(guest: Guest, guest_skill_id: int) -> str:
    with transaction.atomic():
        locked_guest = Guest.objects.select_for_update().select_related("template").filter(pk=guest.pk).first()
        if locked_guest is None:
            raise GuestNotFoundError()
        if locked_guest.status != GuestStatus.IDLE:
            raise GuestNotIdleError(
                locked_guest,
                message=f"{locked_guest.display_name} 当前非空闲状态，无法遗忘技能",
            )

        locked_guest_skill = locked_guest.guest_skills.select_related("skill").filter(pk=guest_skill_id).first()
        if locked_guest_skill is None:
            raise GuestSkillNotFoundError("未找到要遗忘的技能")

        skill_name = locked_guest_skill.skill.name
        locked_guest_skill.delete()
        return skill_name
