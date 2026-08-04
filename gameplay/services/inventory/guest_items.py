"""
Guest-target item usage helpers (rebirth / growth reroll / allocation reset).
"""

from __future__ import annotations

from typing import Any, Dict

from django.db import transaction

from core.exceptions import GuestItemConfigurationError, GuestNotRequirementError
from gameplay.models import InventoryItem, Manor
from guests.growth_engine import (
    XISUIDAN_MAX_REROLL_ATTEMPTS,
    build_allocation_reset_message,
    build_growth_reroll_message,
    reroll_guest_growth,
    reset_guest_allocation,
)

from .core import consume_inventory_item_locked
from .guest_reset_helpers import (
    apply_guest_template_reset,
    build_reset_extra_parts,
    detach_guest_gears_for_reset,
    prepare_guest_for_reset,
    resolve_rarity_upgrade_target,
    roll_guest_template_attributes,
    validate_guest_item_use,
)
from .random_source import inventory_random
from .soul_fusion_helpers import (  # noqa: F401
    SOUL_FUSION_ARCHETYPE_BASE_WEIGHTS,
    SOUL_FUSION_DEFAULT_ALLOWED_RARITIES,
    SOUL_FUSION_DEFAULT_MIN_LEVEL,
    SOUL_FUSION_RESULT_CONFIG,
    SOUL_FUSION_STAT_KEYS,
    _allocate_soul_fusion_secondary_stats,
    _build_soul_fusion_weights,
    _create_soul_fusion_ornament_template,
    _extract_soul_fusion_source_stats,
    _format_soul_fusion_stat_summary,
    _guest_level_ratio,
    _normalize_soul_fusion_allowed_rarities,
    _normalize_soul_fusion_min_level,
    _roll_biased_value,
    _roll_soul_fusion_stats,
    get_soul_fusion_requirements,
    guest_is_eligible_for_soul_fusion,
)

_validate_guest_item_use = validate_guest_item_use
_detach_guest_gears_for_reset = detach_guest_gears_for_reset


@transaction.atomic
def use_guest_rebirth_card(manor: Manor, item: InventoryItem, guest_id: int) -> Dict[str, Any]:
    """使用门客重生卡，将指定门客重置为1级。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    Manor.objects.select_for_update().get(pk=manor.pk)

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "rebirth_guest",
    )

    reset_prep = prepare_guest_for_reset(guest, action_label="重生")
    varied_attrs = roll_guest_template_attributes(guest.template, rng=inventory_random.Random())
    apply_guest_template_reset(guest, target_template=guest.template, varied_attrs=varied_attrs)

    from guests.services.training import ensure_auto_training

    ensure_auto_training(guest)

    consume_inventory_item_locked(locked_item, 1)

    extras = build_reset_extra_parts(
        unequipped_count=reset_prep.unequipped_count,
        skills_cleared=reset_prep.skills_cleared,
    )
    extra_msg = "，" + "，".join(extras) if extras else ""

    return {
        "guest_name": reset_prep.guest_name,
        "old_level": reset_prep.old_level,
        "unequipped_count": reset_prep.unequipped_count,
        "skills_cleared": reset_prep.skills_cleared,
        "_message": f"门客 {reset_prep.guest_name} 已重生为1级（原{reset_prep.old_level}级）{extra_msg}",
    }


@transaction.atomic
def use_xisuidan(manor: Manor, item: InventoryItem, guest_id: int) -> Dict[str, Any]:
    """使用洗髓丹，重新随机门客的升级成长点数。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    Manor.objects.select_for_update().get(pk=manor.pk)

    from guests.utils.attribute_growth import allocate_level_up_attributes

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "reroll_growth",
    )

    result = reroll_guest_growth(
        guest,
        rng=inventory_random.Random(),
        allocate_level_up_attributes_func=allocate_level_up_attributes,
        max_attempts=XISUIDAN_MAX_REROLL_ATTEMPTS,
    )

    consume_inventory_item_locked(locked_item, 1)

    return {
        "guest_name": guest.display_name,
        "old_total": result.old_total,
        "new_total": result.new_total,
        "growth_diff": result.growth_diff,
        "changes": result.changes,
        "_message": build_growth_reroll_message(guest.display_name, result),
    }


@transaction.atomic
def use_xidianka(manor: Manor, item: InventoryItem, guest_id: int) -> Dict[str, Any]:
    """使用洗点卡，重置门客的属性点分配。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    Manor.objects.select_for_update().get(pk=manor.pk)

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "reset_allocation",
    )

    result = reset_guest_allocation(guest)

    consume_inventory_item_locked(locked_item, 1)

    return {
        "guest_name": guest.display_name,
        "total_returned": result.total_returned,
        "details": result.details,
        "_message": build_allocation_reset_message(guest.display_name, result),
    }


@transaction.atomic
def use_guest_rarity_upgrade_item(manor: Manor, item: InventoryItem, guest_id: int) -> Dict[str, Any]:
    """使用升阶道具，将指定门客升级到目标稀有度模板。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    Manor.objects.select_for_update().get(pk=manor.pk)

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "upgrade_guest_rarity",
    )

    target_template = resolve_rarity_upgrade_target(guest, payload=locked_item.template.effect_payload or {})
    reset_prep = prepare_guest_for_reset(guest, action_label="升阶")
    varied_attrs = roll_guest_template_attributes(target_template, rng=inventory_random.Random())
    apply_guest_template_reset(
        guest,
        target_template=target_template,
        varied_attrs=varied_attrs,
        include_template=True,
    )

    from guests.services.training import ensure_auto_training

    ensure_auto_training(guest)

    consume_inventory_item_locked(locked_item, 1)

    extras = build_reset_extra_parts(
        unequipped_count=reset_prep.unequipped_count,
        skills_cleared=reset_prep.skills_cleared,
        base_parts=["等级重置为1级", "洗髓丹计数已重置"],
    )

    return {
        "guest_name": guest.display_name,
        "old_level": reset_prep.old_level,
        "new_level": guest.level,
        "unequipped_count": reset_prep.unequipped_count,
        "skills_cleared": reset_prep.skills_cleared,
        "old_rarity": reset_prep.old_rarity_display,
        "new_rarity": target_template.get_rarity_display(),
        "_message": (
            f"门客 {guest.display_name} 已从{reset_prep.old_rarity_display}升至{target_template.get_rarity_display()}，"
            f"{'，'.join(extras)}"
        ),
    }


@transaction.atomic
def use_soul_container(manor: Manor, item: InventoryItem, guest_id: int) -> Dict[str, Any]:
    """使用灵魂容器，融合门客并生成一件专属饰品。"""
    Manor.objects.select_for_update().get(pk=manor.pk)

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "soul_fusion",
    )

    payload = locked_item.template.effect_payload or {}
    min_level, allowed_rarities = get_soul_fusion_requirements(payload)

    if guest.level < min_level:
        raise GuestNotRequirementError(
            guest,
            "level",
            min_level,
            guest.level,
            message=f"仅可融合{min_level}级及以上门客",
        )

    guest_rarity = str(getattr(getattr(guest, "template", None), "rarity", "") or "").strip()
    if guest_rarity not in allowed_rarities:
        raise GuestItemConfigurationError("仅可融合绿色、蓝色或紫色门客")

    config = SOUL_FUSION_RESULT_CONFIG.get(guest_rarity)
    if not config:
        raise GuestItemConfigurationError("该门客的灵魂尚不足以稳定成器")

    guest_name = guest.display_name
    source_stats = _extract_soul_fusion_source_stats(guest)
    rng = inventory_random.Random()
    rolled_stats = _roll_soul_fusion_stats(guest, source_stats, config, rng)
    generated_template = _create_soul_fusion_ornament_template(guest, config, rolled_stats)
    generated_item = InventoryItem.objects.create(
        manor=manor,
        template=generated_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    unequipped_count = _detach_guest_gears_for_reset(guest, action_label="灵魂融合")
    guest.delete()
    consume_inventory_item_locked(locked_item, 1)

    extra_parts = [f"获得饰品【{generated_template.name}】", _format_soul_fusion_stat_summary(rolled_stats)]
    if unequipped_count > 0:
        extra_parts.append(f"原装备已归还仓库（{unequipped_count}件）")

    return {
        "guest_name": guest_name,
        "item_name": generated_template.name,
        "item_rarity": generated_template.rarity,
        "generated_item_id": generated_item.id,
        "stats": rolled_stats,
        "unequipped_count": unequipped_count,
        "_message": f"门客 {guest_name} 已完成灵魂融合，{'，'.join(extra_parts)}",
    }
