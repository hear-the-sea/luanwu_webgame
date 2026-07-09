"""
监牢与结义林服务

功能：
- 踢馆俘虏列表查询
- 招募俘虏（消耗金条，等级/装备重置）
- 结义关系管理（结义门客不可被俘获）
"""

from __future__ import annotations

import random
from typing import List

from django.db import transaction

from core.config import GUEST
from core.exceptions import (
    GuestCapacityFullError,
    GuestNotIdleError,
    ItemInsufficientError,
    ItemNotFoundError,
    JailError,
    OathBondAlreadyExistsError,
    OathCapacityFullError,
    OathGuestNotFoundError,
    PrisonerAlreadyHandledError,
    PrisonerNotFoundError,
    PrisonerUnavailableError,
)
from guests.models import Guest, GuestStatus, GuestTemplate
from guests.services.recruitment_guests import grant_template_skills
from guests.utils.recruitment_variance import apply_recruitment_variance
from trade.services.auction.gold_bars import consume_available_gold_bars_locked

from ..constants import PVPConstants
from ..models import JailPrisoner, Manor, OathBond
from .inventory.core import get_item_quantity

GOLD_BAR_ITEM_KEY = "gold_bar"

PRISONER_RECRUIT_DUPLICATE_TEMPLATE_GROUPS = {
    "hist_sljnbc_0589": ("hist_sljnbc_0589", "hist_sljnbc_0589_blue", "hist_sljnbc_0589_purple"),
    "hist_sljnbc_0589_blue": ("hist_sljnbc_0589", "hist_sljnbc_0589_blue", "hist_sljnbc_0589_purple"),
    "hist_sljnbc_0589_purple": ("hist_sljnbc_0589", "hist_sljnbc_0589_blue", "hist_sljnbc_0589_purple"),
    "hist_sljnbc_0590": ("hist_sljnbc_0590", "hist_sljnbc_0590_blue", "hist_sljnbc_0590_purple"),
    "hist_sljnbc_0590_blue": ("hist_sljnbc_0590", "hist_sljnbc_0590_blue", "hist_sljnbc_0590_purple"),
    "hist_sljnbc_0590_purple": ("hist_sljnbc_0590", "hist_sljnbc_0590_blue", "hist_sljnbc_0590_purple"),
    "orig_ma_wencai": ("orig_ma_wencai",),
    "orig_liang_shanbo": ("orig_liang_shanbo",),
    "orig_zhu_yingtai": ("orig_zhu_yingtai",),
}


PRISONER_RECRUIT_REPEATABLE_TEMPLATE_GROUPS: dict[str, tuple[str, ...]] = {
    "pubayi_green": ("pubayi_green", "pubayi_blue"),
    "pubayi_blue": ("pubayi_green", "pubayi_blue"),
    "orig_edward_blue": ("orig_edward_blue", "orig_edward_purple"),
    "orig_edward_purple": ("orig_edward_blue", "orig_edward_purple"),
}


def _get_prisoner_recruit_duplicate_keys(template_key: str) -> tuple[str, ...]:
    normalized_key = str(template_key or "").strip()
    if not normalized_key:
        return ()
    return PRISONER_RECRUIT_DUPLICATE_TEMPLATE_GROUPS.get(normalized_key, (normalized_key,))


def _get_prisoner_recruit_repeatable_keys(template_key: str) -> tuple[str, ...]:
    return PRISONER_RECRUIT_REPEATABLE_TEMPLATE_GROUPS.get(str(template_key or "").strip(), ())


def list_held_prisoners(manor: Manor) -> List[JailPrisoner]:
    return list(
        JailPrisoner.objects.filter(captor=manor, status=JailPrisoner.Status.HELD)
        .select_related("guest_template", "original_manor")
        .order_by("-captured_at")
    )


def list_oath_bonds(manor: Manor) -> List[OathBond]:
    return list(OathBond.objects.filter(manor=manor).select_related("guest", "guest__template").order_by("-created_at"))


@transaction.atomic
def add_oath_bond(manor: Manor, guest_id: int) -> OathBond:
    # Lock manor to serialize oath bond additions and prevent capacity bypass
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)

    guest = (
        Guest.objects.select_for_update()
        .select_related("template")
        .filter(pk=guest_id, manor_id=getattr(locked_manor, "pk", None))
        .first()
    )
    if not guest:
        raise OathGuestNotFoundError()
    if guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)

    # 容量校验：使用锁定后的对象读取容量
    capacity = int(getattr(locked_manor, "oath_capacity", 0) or 0)
    current = OathBond.objects.filter(manor=manor).count()
    if current >= capacity:
        raise OathCapacityFullError()

    bond, created = OathBond.objects.get_or_create(manor=manor, guest=guest)
    if not created:
        raise OathBondAlreadyExistsError()
    return bond


@transaction.atomic
def remove_oath_bond(manor: Manor, guest_id: int) -> int:
    guest = Guest.objects.select_for_update().filter(pk=guest_id, manor_id=getattr(manor, "pk", None)).first()
    if guest and guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)
    deleted, _ = OathBond.objects.filter(manor=manor, guest_id=guest_id).delete()
    return int(deleted)


@transaction.atomic
def release_prisoner(manor: Manor, prisoner_id: int) -> JailPrisoner:
    """
    释放囚徒：将囚徒状态设置为已释放
    """
    prisoner = (
        JailPrisoner.objects.select_for_update()
        .filter(pk=prisoner_id, captor=manor, status=JailPrisoner.Status.HELD)
        .first()
    )
    if not prisoner:
        raise PrisonerUnavailableError()

    prisoner.status = JailPrisoner.Status.RELEASED
    prisoner.save(update_fields=["status"])

    return prisoner


@transaction.atomic
def draw_pie(manor: Manor, prisoner_id: int) -> JailPrisoner:
    """
    画饼：消耗1金条，随机降低囚徒5-10点忠诚度
    """
    prisoner = (
        JailPrisoner.objects.select_for_update()
        .filter(pk=prisoner_id, captor=manor, status=JailPrisoner.Status.HELD)
        .first()
    )
    if not prisoner:
        raise PrisonerUnavailableError()

    cost = int(getattr(PVPConstants, "JAIL_PERSUADE_GOLD_BAR_COST", 1) or 1)
    try:
        consume_available_gold_bars_locked(manor, cost)
    except (ItemInsufficientError, ItemNotFoundError) as exc:
        have = exc.context.get("available", get_item_quantity(manor, GOLD_BAR_ITEM_KEY))
        raise JailError(f"金条不足，需要 {cost} 个（当前 {have} 个）")

    # 随机降低忠诚度
    loyalty_min = int(getattr(PVPConstants, "JAIL_PERSUADE_LOYALTY_MIN", 5) or 5)
    loyalty_max = int(getattr(PVPConstants, "JAIL_PERSUADE_LOYALTY_MAX", 10) or 10)
    loyalty_reduction = random.randint(loyalty_min, loyalty_max)
    prisoner.loyalty = max(0, prisoner.loyalty - loyalty_reduction)
    prisoner.save(update_fields=["loyalty"])
    # 存储减少值供视图使用
    setattr(prisoner, "_reduction", loyalty_reduction)

    return prisoner


@transaction.atomic
def recruit_prisoner(manor: Manor, prisoner_id: int) -> Guest:
    # 死锁/并发预防：先锁定 Manor，确保容量检查原子化
    # 必须使用锁定后的对象来检查容量，防止陈旧读
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)

    prisoner = (
        JailPrisoner.objects.select_for_update()
        .select_related("guest_template")
        .filter(pk=prisoner_id, captor=manor)
        .first()
    )
    if not prisoner:
        raise PrisonerNotFoundError()
    if prisoner.status != JailPrisoner.Status.HELD:
        raise PrisonerAlreadyHandledError()

    threshold = int(getattr(PVPConstants, "JAIL_RECRUIT_LOYALTY_THRESHOLD", 30) or 30)
    if int(prisoner.loyalty) > threshold:
        raise JailError("忠诚度过高，无法招募")

    # 使用锁定后的 manor 对象检查容量
    capacity = locked_manor.guest_capacity
    current = locked_manor.guests.count()
    if current >= capacity:
        raise GuestCapacityFullError()

    template: GuestTemplate = prisoner.guest_template
    repeatable_template_keys = _get_prisoner_recruit_repeatable_keys(template.key)
    duplicate_template_keys = _get_prisoner_recruit_duplicate_keys(template.key)
    if (
        duplicate_template_keys
        and not repeatable_template_keys
        and locked_manor.guests.filter(template__key__in=duplicate_template_keys).exists()
    ):
        raise JailError(f"庄园已拥有门客「{template.name}」，不可重复招募")

    cost = int(getattr(PVPConstants, "JAIL_RECRUIT_GOLD_BAR_COST", 1) or 1)
    if cost > 0:
        try:
            consume_available_gold_bars_locked(locked_manor, cost)
        except (ItemInsufficientError, ItemNotFoundError) as exc:
            have = exc.context.get("available", get_item_quantity(manor, GOLD_BAR_ITEM_KEY))
            raise JailError(f"金条不足，需要 {cost} 个（当前 {have} 个）")

    rng = random.Random()
    gender_choice = template.default_gender
    if not gender_choice or gender_choice == "unknown":
        gender_choice = rng.choice(["male", "female"])
    morality_value = template.default_morality or rng.randint(30, 100)

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

    initial_hp = max(
        int(GUEST.MIN_HP_FLOOR),
        template.base_hp + varied_attrs["defense"] * int(GUEST.DEFENSE_TO_HP_MULTIPLIER),
    )

    custom_name = ""
    prisoner_name = (prisoner.original_guest_name or "").strip()
    if prisoner_name and prisoner_name != template.name:
        custom_name = prisoner_name

    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=1,
        experience=0,
        custom_name=custom_name,
        force=varied_attrs["force"],
        intellect=varied_attrs["intellect"],
        defense_stat=varied_attrs["defense"],
        agility=varied_attrs["agility"],
        luck=varied_attrs["luck"],
        initial_force=varied_attrs["force"],
        initial_intellect=varied_attrs["intellect"],
        initial_defense=varied_attrs["defense"],
        initial_agility=varied_attrs["agility"],
        loyalty=60,
        gender=gender_choice,
        morality=morality_value,
        current_hp=initial_hp,
    )
    grant_template_skills(guest)

    prisoner.status = JailPrisoner.Status.RECRUITED
    prisoner.save(update_fields=["status"])
    return guest
