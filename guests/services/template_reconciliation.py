"""Reconcile existing guests whose template rarity/archetype/base profile changed."""

from __future__ import annotations

import random
from collections.abc import Iterable
from typing import Any

from django.db import transaction

from core.config import GUEST
from gameplay.models import BotProfile, ItemTemplate, Message
from gameplay.models.items import message_has_protected_attachments
from gameplay.services.inventory.guest_reset_helpers import roll_guest_template_attributes
from gameplay.services.utils.cache import CacheKeys
from gameplay.services.utils.messages import _safe_cache_delete_many
from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX
from guests.growth_engine import (
    GrowthAllocation,
    _resolve_growth_range,
    allocate_growth_for_profile,
    extract_current_guest_growth,
    resolve_guest_growth_stats,
    resolve_non_growth_attribute_and_luck_bonuses,
)
from guests.guest_rules import compute_guest_level_hp_bonus
from guests.models import Guest
from guests.services.custom_guest import CUSTOM_GUEST_TEMPLATE_KEYS
from guests.utils.recruitment_variance import MAX_GROWABLE_ATTRIBUTE, MAX_RATIO, MIN_RATIO

_INITIAL_FIELDS = (
    ("initial_force", "base_attack"),
    ("initial_intellect", "base_intellect"),
    ("initial_defense", "base_defense"),
    ("initial_agility", "base_agility"),
)

# 各稀有度模板允许的最高初始四维总点（取同稀有度文/武模板总点较大值），用于区分「降档」与「同稀有度文武切换」。
_MAX_BASE_SUM_BY_RARITY = {
    "black": 81,
    "gray": 97,
    "green": 117,
    "red": 131,
    "blue": 147,
    "purple": 169,
    "orange": 183,
}

DOWNGRADE_COMPENSATION_ITEM_KEY = "super_guest_summon_card"
DOWNGRADE_COMPENSATION_TITLE = "门客稀有度调整补偿"
DOWNGRADE_COMPENSATION_BODY = (
    "由于部分门客稀有度档位及初始/成长属性进行了统一校准，现为您发放「超级门客召唤卡 ×1」作为补偿，"
    "请查收附件并前往仓库使用，感谢您的理解与支持。"
)

_RECONCILED_UPDATE_FIELDS = [
    "initial_force",
    "initial_intellect",
    "initial_defense",
    "initial_agility",
    "force",
    "intellect",
    "defense_stat",
    "agility",
    "luck",
    "current_hp",
]


def is_special_guest_template_key(template_key: str) -> bool:
    """自建门客与升阶丸子转换模板保留其专属初始基值，不纳入常规模板重匹配。"""
    key = str(template_key or "")
    return key in CUSTOM_GUEST_TEMPLATE_KEYS or key.startswith(RARITY_CONVERSION_TEMPLATE_KEY_PREFIX)


def _initial_sum(guest: Any) -> int:
    return (
        int(guest.initial_force)
        + int(guest.initial_intellect)
        + int(guest.initial_defense)
        + int(guest.initial_agility)
    )


def _template_base_sum(template: Any) -> int:
    return (
        int(template.base_attack)
        + int(template.base_intellect)
        + int(template.base_defense)
        + int(template.base_agility)
    )


def is_guest_rarity_downgraded(guest: Any, template: Any) -> bool:
    """判断门客初始四维总点是否高于当前模板稀有度档位上限（即发生过稀有度降档）。"""
    if is_special_guest_template_key(getattr(template, "key", "")):
        return False
    ceiling = _MAX_BASE_SUM_BY_RARITY.get(str(getattr(template, "rarity", "") or ""))
    if ceiling is None:
        return False
    return _initial_sum(guest) > ceiling


def _initial_stats_match_template(guest: Any, template: Any) -> bool:
    if _initial_sum(guest) != _template_base_sum(template):
        return False

    for guest_field, template_field in _INITIAL_FIELDS:
        base_val = int(getattr(template, template_field))
        low = max(1, int(base_val * MIN_RATIO))
        high = min(MAX_GROWABLE_ATTRIBUTE, int(base_val * MAX_RATIO))
        actual = int(getattr(guest, guest_field))
        if low <= high and not (low <= actual <= high):
            return False
    return True


def is_guest_mismatched_with_template(
    guest: Any,
    template: Any,
    *,
    non_growth_bonuses: GrowthAllocation | None = None,
) -> bool:
    """判断已有门客的初始四维或升级成长是否与当前模板失配。"""
    if is_special_guest_template_key(getattr(template, "key", "")):
        return False
    if not _initial_stats_match_template(guest, template):
        return True

    levels = max(0, int(guest.level or 1) - 1)
    if levels <= 0:
        return False

    min_growth, max_growth = _resolve_growth_range(str(template.rarity), template.growth_range)
    current_growth = extract_current_guest_growth(guest, non_growth_bonuses=non_growth_bonuses)
    growth_total = sum(current_growth.values())
    return not (levels * min_growth <= growth_total <= levels * max_growth)


def reconcile_guest_to_template(
    guest: Any,
    template: Any,
    *,
    rng: random.Random | None = None,
) -> bool:
    """按当前模板基值、权重与成长区间重匹配单名门客（保留手动加点与装备/易容加成）。"""
    if is_special_guest_template_key(getattr(template, "key", "")):
        return False

    non_growth_bonuses, gear_luck = resolve_non_growth_attribute_and_luck_bonuses(guest)
    if not is_guest_mismatched_with_template(guest, template, non_growth_bonuses=non_growth_bonuses):
        return False

    if rng is None:
        rng = random.Random(f"guest-reconcile:{guest.pk}:{template.key}:{template.rarity}:{template.archetype}")

    varied_attrs = roll_guest_template_attributes(template, rng=rng)
    guest.initial_force = varied_attrs["force"]
    guest.initial_intellect = varied_attrs["intellect"]
    guest.initial_defense = varied_attrs["defense"]
    guest.initial_agility = varied_attrs["agility"]
    guest.luck = varied_attrs["luck"] + gear_luck

    levels = max(0, int(guest.level or 1) - 1)
    new_growth = allocate_growth_for_profile(
        rarity=str(template.rarity),
        archetype=str(template.archetype),
        growth_range=template.growth_range,
        attribute_weights=template.attribute_weights,
        levels=levels,
        rng=rng,
    )
    old_defense = int(guest.defense_stat)
    new_stats = resolve_guest_growth_stats(guest, new_growth, non_growth_bonuses=non_growth_bonuses)
    guest.force = new_stats["force"]
    guest.intellect = new_stats["intellect"]
    guest.defense_stat = new_stats["defense"]
    guest.agility = new_stats["agility"]

    hp_floor = int(GUEST.MIN_HP_FLOOR)
    defense_to_hp = int(GUEST.DEFENSE_TO_HP_MULTIPLIER)
    level_hp = compute_guest_level_hp_bonus(int(guest.level or 1), archetype=str(template.archetype or ""))
    old_max_hp = max(hp_floor, int(template.base_hp) + int(guest.hp_bonus) + old_defense * defense_to_hp) + level_hp
    new_max_hp = (
        max(hp_floor, int(template.base_hp) + int(guest.hp_bonus) + int(guest.defense_stat) * defense_to_hp) + level_hp
    )
    current_hp = int(guest.current_hp)
    if current_hp <= 0:
        guest.current_hp = 0
    elif current_hp >= old_max_hp:
        guest.current_hp = new_max_hp
    else:
        guest.current_hp = max(1, min(new_max_hp, current_hp * new_max_hp // old_max_hp))
    return True


def reconcile_mismatched_guests(
    *,
    guest_model: Any = None,
    database: str | None = None,
    batch_size: int = 500,
    downgraded_manor_ids: set[int] | None = None,
) -> int:
    """批量重匹配所有与当前模板失配的已有门客，返回实际更新数量。"""
    model = guest_model or Guest
    manager = model.objects.using(database) if database else model.objects
    updated_count = 0
    last_pk = 0
    while batch := list(
        manager.select_related("template")
        .prefetch_related("gear_items__template")
        .filter(pk__gt=last_pk)
        .order_by("pk")[:batch_size]
    ):
        last_pk = batch[-1].pk
        dirty: list[Any] = []
        for guest in batch:
            was_downgraded = is_guest_rarity_downgraded(guest, guest.template)
            if reconcile_guest_to_template(guest, guest.template):
                if was_downgraded and downgraded_manor_ids is not None and guest.manor_id is not None:
                    downgraded_manor_ids.add(int(guest.manor_id))
                dirty.append(guest)
        if dirty:
            manager.bulk_update(dirty, _RECONCILED_UPDATE_FIELDS, batch_size=batch_size)
            updated_count += len(dirty)
    return updated_count


def send_downgraded_guest_compensation_messages(
    *,
    manor_ids: Iterable[int],
    bot_profile_model: Any = None,
    item_template_model: Any = None,
    message_model: Any = None,
    database: str | None = None,
) -> int:
    """为持有稀有度降档门客的真人庄园每人补偿 1 张超级门客召唤卡（幂等，跳过虚拟玩家）。"""
    candidate_ids = sorted({int(mid) for mid in manor_ids if mid is not None})
    if not candidate_ids:
        return 0

    bot_cls = bot_profile_model or BotProfile
    item_cls = item_template_model or ItemTemplate
    msg_cls = message_model or Message

    bot_qs = bot_cls.objects.using(database) if database else bot_cls.objects
    item_qs = item_cls.objects.using(database) if database else item_cls.objects
    msg_mgr = msg_cls.objects.using(database) if database else msg_cls.objects

    if not item_qs.filter(key=DOWNGRADE_COMPENSATION_ITEM_KEY).exists():
        return 0

    bot_manor_ids = set(bot_qs.values_list("manor_id", flat=True))
    human_ids = [mid for mid in candidate_ids if mid not in bot_manor_ids]
    if not human_ids:
        return 0

    already_sent = set(
        msg_mgr.filter(manor_id__in=human_ids, title=DOWNGRADE_COMPENSATION_TITLE).values_list("manor_id", flat=True)
    )
    target_ids = [mid for mid in human_ids if mid not in already_sent]
    if not target_ids:
        return 0

    attachments = {"bound_items": {DOWNGRADE_COMPENSATION_ITEM_KEY: 1}}
    protected = message_has_protected_attachments(attachments, is_claimed=False)
    messages_to_create = [
        msg_cls(
            manor_id=manor_id,
            kind="reward",
            title=DOWNGRADE_COMPENSATION_TITLE,
            body=DOWNGRADE_COMPENSATION_BODY,
            attachments=attachments,
            is_claimed=False,
            is_deletion_protected=protected,
            is_read=False,
        )
        for manor_id in target_ids
    ]
    msg_mgr.bulk_create(messages_to_create)

    cache_keys = [CacheKeys.unread_count(manor_id) for manor_id in target_ids]
    transaction.on_commit(lambda: _safe_cache_delete_many(cache_keys), using=database, robust=True)
    return len(target_ids)
