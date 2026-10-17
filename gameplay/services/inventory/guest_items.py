"""
Guest-target item usage helpers (rebirth / growth reroll / allocation reset / disguise).
"""

from __future__ import annotations

from typing import Any, Dict

from django.db import transaction

from core.exceptions import GuestItemConfigurationError, GuestNotRequirementError
from gameplay.models import InventoryItem, Manor
from guests.avatar import resolve_guest_disguise_avatar_asset
from guests.constants import (
    GUEST_DISGUISE_AVATAR_ACTION,
    GUEST_DISGUISE_AVATAR_PREFIXES,
    SUPER_GUEST_DISGUISE_ACTION,
    SUPER_GUEST_DISGUISE_AGILITY_BONUS,
    SUPER_GUEST_DISGUISE_AVATAR_KEY,
    SUPER_GUEST_DISGUISE_FORCE_BONUS,
    SUPER_GUEST_DISGUISE_HP_BONUS,
)
from guests.growth_engine import (
    XISUIDAN_MAX_REROLL_ATTEMPTS,
    build_allocation_reset_message,
    build_growth_reroll_message,
    reroll_guest_growth,
    reset_guest_allocation,
)
from guests.models import GuestStatus
from guests.services.world_unique import ensure_guest_not_world_unique

from .core import add_item_to_inventory_locked, consume_inventory_item_locked, inventory_use_source_is_bound
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
    SOUL_FUSION_ALLOWED_GUEST_STATUSES,
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


def _resolve_disguise_bonus(payload: object, key: str, default: int) -> int:
    if not isinstance(payload, dict):
        raise GuestItemConfigurationError("超级门客易容卡配置错误")
    raw_value = payload.get(key, default)
    if isinstance(raw_value, bool):
        raise GuestItemConfigurationError("超级门客易容卡属性配置错误")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise GuestItemConfigurationError("超级门客易容卡属性配置错误") from exc
    if value <= 0:
        raise GuestItemConfigurationError("超级门客易容卡属性配置错误")
    return value


def _resolve_super_guest_disguise_config(payload: object) -> tuple[str, int, int, int]:
    if not isinstance(payload, dict) or payload.get("action") != SUPER_GUEST_DISGUISE_ACTION:
        raise GuestItemConfigurationError("超级门客易容卡配置错误")

    avatar_key = payload.get("avatar_override", payload.get("avatar_override_key", SUPER_GUEST_DISGUISE_AVATAR_KEY))
    if avatar_key != SUPER_GUEST_DISGUISE_AVATAR_KEY:
        raise GuestItemConfigurationError("超级门客易容卡头像配置错误")

    return (
        SUPER_GUEST_DISGUISE_AVATAR_KEY,
        _resolve_disguise_bonus(payload, "hp", SUPER_GUEST_DISGUISE_HP_BONUS),
        _resolve_disguise_bonus(payload, "force", SUPER_GUEST_DISGUISE_FORCE_BONUS),
        _resolve_disguise_bonus(payload, "agility", SUPER_GUEST_DISGUISE_AGILITY_BONUS),
    )


@transaction.atomic
def use_super_guest_disguise_card(
    manor: Manor, item: InventoryItem, guest_id: int, *, binding_mode: str | None = None
) -> Dict[str, Any]:
    """给指定门客应用一次性超级易容效果。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    manor = Manor.objects.select_for_update().get(pk=manor.pk)
    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        SUPER_GUEST_DISGUISE_ACTION,
        allowed_statuses=GuestStatus.values,
        binding_mode=binding_mode,
    )

    avatar_key, hp_bonus, force_bonus, agility_bonus = _resolve_super_guest_disguise_config(
        locked_item.template.effect_payload
    )
    if guest.super_disguise_applied or guest.avatar_override_key:
        raise GuestItemConfigurationError("该门客已经使用过超级门客易容卡")

    current_hp = max(0, int(guest.current_hp or 0))
    was_at_full_hp = current_hp >= guest.max_hp
    if guest.original_avatar_asset_key is None:
        guest.original_avatar_asset_key = guest.avatar_asset_key
    guest.use_original_avatar = False
    guest.avatar_override_key = avatar_key
    guest.super_disguise_applied = True
    guest.hp_bonus += hp_bonus
    guest.force += force_bonus
    guest.agility += agility_bonus
    guest.current_hp = guest.max_hp if was_at_full_hp else min(guest.max_hp, current_hp)
    guest.save(
        update_fields=[
            "avatar_override_key",
            "original_avatar_asset_key",
            "use_original_avatar",
            "super_disguise_applied",
            "hp_bonus",
            "force",
            "agility",
            "current_hp",
        ]
    )
    consume_inventory_item_locked(locked_item, 1, binding_mode=binding_mode)

    return {
        "guest_name": guest.display_name,
        "avatar_override_key": avatar_key,
        "hp_bonus": hp_bonus,
        "force_bonus": force_bonus,
        "agility_bonus": agility_bonus,
        "_message": (
            f"门客 {guest.display_name} 已完成超级易容：形象替换为吕布猴，"
            f"生命+{hp_bonus}、武力+{force_bonus}、敏捷+{agility_bonus}"
        ),
    }


@transaction.atomic
def use_guest_disguise_card(
    manor: Manor,
    item: InventoryItem,
    guest_id: int,
    avatar_key: str,
    *,
    binding_mode: str | None = None,
) -> Dict[str, Any]:
    """为所属门客应用玩家选择的猴、猪或鸭头像。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    manor = Manor.objects.select_for_update().get(pk=manor.pk)
    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        GUEST_DISGUISE_AVATAR_ACTION,
        allowed_statuses=GuestStatus.values,
        binding_mode=binding_mode,
    )

    payload = locked_item.template.effect_payload
    if not isinstance(payload, dict) or payload.get("action") != GUEST_DISGUISE_AVATAR_ACTION:
        raise GuestItemConfigurationError("门客易容卡配置错误")
    configured_prefixes = payload.get("avatar_prefixes", list(GUEST_DISGUISE_AVATAR_PREFIXES))
    if (
        not isinstance(configured_prefixes, list)
        or len(configured_prefixes) != len(GUEST_DISGUISE_AVATAR_PREFIXES)
        or any(not isinstance(prefix, str) or not prefix.strip() for prefix in configured_prefixes)
        or {prefix.strip() for prefix in configured_prefixes} != set(GUEST_DISGUISE_AVATAR_PREFIXES)
    ):
        raise GuestItemConfigurationError("门客易容卡头像范围配置错误")

    if not isinstance(avatar_key, str) or not avatar_key.strip():
        raise GuestItemConfigurationError("请选择有效的门客形象")
    normalized_avatar_key = avatar_key.strip()
    avatar_asset_key = resolve_guest_disguise_avatar_asset(normalized_avatar_key)
    if not avatar_asset_key:
        raise GuestItemConfigurationError("门客形象不存在或不可用")
    if guest.super_disguise_applied or guest.avatar_override_key:
        raise GuestItemConfigurationError("该门客已经使用过特殊易容效果")

    if guest.original_avatar_asset_key is None:
        guest.original_avatar_asset_key = guest.avatar_asset_key
    guest.use_original_avatar = False
    guest.avatar_asset_key = avatar_asset_key
    guest.save(update_fields=["avatar_asset_key", "original_avatar_asset_key", "use_original_avatar"])
    consume_inventory_item_locked(locked_item, 1, binding_mode=binding_mode)

    return {
        "guest_name": guest.display_name,
        "avatar_key": normalized_avatar_key,
        "avatar_asset_key": avatar_asset_key,
        "_message": f"门客 {guest.display_name} 已更换为所选形象",
    }


@transaction.atomic
def use_guest_rebirth_card(
    manor: Manor, item: InventoryItem, guest_id: int, *, binding_mode: str | None = None
) -> Dict[str, Any]:
    """使用门客重生卡，将指定门客重置为1级。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    manor = Manor.objects.select_for_update().get(pk=manor.pk)

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "rebirth_guest",
        binding_mode=binding_mode,
    )

    reset_prep = prepare_guest_for_reset(guest, action_label="重生", manor=manor)
    from guests.services.custom_guest import get_custom_guest_initial_attributes, is_custom_guest

    if is_custom_guest(guest):
        appearance = {field: getattr(guest, field) for field in ("avatar_asset_key",)}
        varied_attrs = get_custom_guest_initial_attributes(guest.template)
        apply_guest_template_reset(guest, target_template=guest.template, varied_attrs=varied_attrs)
        for field, value in appearance.items():
            setattr(guest, field, value)
        guest.save(update_fields=list(appearance))
    else:
        varied_attrs = roll_guest_template_attributes(guest.template, rng=inventory_random.Random())
        apply_guest_template_reset(guest, target_template=guest.template, varied_attrs=varied_attrs)

    from guests.services.training import ensure_auto_training

    ensure_auto_training(guest)

    consume_inventory_item_locked(locked_item, 1, binding_mode=binding_mode)

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
def use_xisuidan(
    manor: Manor, item: InventoryItem, guest_id: int, *, binding_mode: str | None = None
) -> Dict[str, Any]:
    """使用洗髓丹，重新随机门客的升级成长点数。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    manor = Manor.objects.select_for_update().get(pk=manor.pk)

    from guests.utils.attribute_growth import allocate_level_up_attributes

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "reroll_growth",
        binding_mode=binding_mode,
    )

    from guests.services.custom_guest import is_custom_guest

    if (is_custom_guest(guest) and guest.level != 100) or (not is_custom_guest(guest) and guest.xisuidan_used >= 10):
        raise GuestItemConfigurationError("洗髓需要100级，普通门客最多使用10次")

    result = reroll_guest_growth(
        guest,
        rng=inventory_random.Random(),
        allocate_level_up_attributes_func=allocate_level_up_attributes,
        max_attempts=XISUIDAN_MAX_REROLL_ATTEMPTS,
    )

    consume_inventory_item_locked(locked_item, 1, binding_mode=binding_mode)

    return {
        "guest_name": guest.display_name,
        "old_total": result.old_total,
        "new_total": result.new_total,
        "growth_diff": result.growth_diff,
        "changes": result.changes,
        "_message": build_growth_reroll_message(guest.display_name, result),
    }


@transaction.atomic
def use_xidianka(
    manor: Manor, item: InventoryItem, guest_id: int, *, binding_mode: str | None = None
) -> Dict[str, Any]:
    """使用洗点卡，重置门客的属性点分配。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    manor = Manor.objects.select_for_update().get(pk=manor.pk)

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "reset_allocation",
        binding_mode=binding_mode,
    )

    result = reset_guest_allocation(guest)

    consume_inventory_item_locked(locked_item, 1, binding_mode=binding_mode)

    return {
        "guest_name": guest.display_name,
        "total_returned": result.total_returned,
        "details": result.details,
        "_message": build_allocation_reset_message(guest.display_name, result),
    }


@transaction.atomic
def use_guest_rarity_upgrade_item(
    manor: Manor, item: InventoryItem, guest_id: int, *, binding_mode: str | None = None
) -> Dict[str, Any]:
    """使用升阶道具，将指定门客升级到目标稀有度模板。"""
    # 死锁预防：统一锁顺序 Manor -> InventoryItem -> Guest
    manor = Manor.objects.select_for_update().get(pk=manor.pk)

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "upgrade_guest_rarity",
        binding_mode=binding_mode,
    )
    from guests.services.custom_guest import is_custom_guest

    if is_custom_guest(guest):
        raise GuestItemConfigurationError("专属门客请在详情页升阶")
    ensure_guest_not_world_unique(guest, action="升阶")

    target_template = resolve_rarity_upgrade_target(guest, payload=locked_item.template.effect_payload or {})
    reset_prep = prepare_guest_for_reset(guest, action_label="升阶", manor=manor)
    varied_attrs = roll_guest_template_attributes(target_template, rng=inventory_random.Random())
    apply_guest_template_reset(
        guest,
        target_template=target_template,
        varied_attrs=varied_attrs,
        include_template=True,
    )

    from guests.services.training import ensure_auto_training

    ensure_auto_training(guest)

    consume_inventory_item_locked(locked_item, 1, binding_mode=binding_mode)

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
def use_soul_container(
    manor: Manor, item: InventoryItem, guest_id: int, *, binding_mode: str | None = None
) -> Dict[str, Any]:
    """使用灵魂容器，融合门客并生成一件专属饰品。"""
    manor = Manor.objects.select_for_update().get(pk=manor.pk)

    locked_item, guest = _validate_guest_item_use(
        manor,
        item,
        guest_id,
        "soul_fusion",
        allowed_statuses=SOUL_FUSION_ALLOWED_GUEST_STATUSES,
        binding_mode=binding_mode,
    )
    from guests.services.custom_guest import is_custom_guest

    if is_custom_guest(guest):
        raise GuestItemConfigurationError("专属门客无法进行灵魂融合")
    ensure_guest_not_world_unique(guest, action="灵魂融合")

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
    generated_item = add_item_to_inventory_locked(
        manor,
        generated_template.key,
        1,
        template=generated_template,
        is_bound=inventory_use_source_is_bound(locked_item),
    )

    unequipped_count = _detach_guest_gears_for_reset(guest, action_label="灵魂融合", manor=manor)
    guest.delete()
    consume_inventory_item_locked(locked_item, 1, binding_mode=binding_mode)

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
