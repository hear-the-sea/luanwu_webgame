"""
Item usage logic (warehouse-usable items + guest-target items).

This module depends on the core inventory operations in `core.py`.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List

from django.db import transaction

from common.utils.random_utils import weighted_random_choice
from core.exceptions import (
    GuestAlreadyOwnedError,
    GuestCapacityFullError,
    ItemNotConfiguredError,
    ItemNotFoundError,
    ItemNotUsableError,
    ItemResourceOverflowConfirmationRequired,
)
from gameplay.models import InventoryItem, ItemTemplate, Manor, ResourceEvent, ResourceType
from gameplay.services.resources import grant_resources, grant_resources_locked, preview_resource_grant

from .core import add_item_to_inventory, consume_inventory_item_for_manor_locked, consume_inventory_item_locked
from .guest_items import (  # noqa: F401
    use_guest_rarity_upgrade_item,
    use_guest_rebirth_card,
    use_soul_container,
    use_xidianka,
    use_xisuidan,
)
from .random_source import inventory_random

logger = logging.getLogger(__name__)

ItemEffectHandler = Callable[[InventoryItem], dict[str, Any]]

RESOURCE_LABELS = dict(ResourceType.choices)

# 不在仓库使用的物品提示信息
NON_WAREHOUSE_MESSAGES: dict[str, str] = {
    ItemTemplate.EffectType.SKILL_BOOK: "技能书请在门客详情页为指定门客使用",
    ItemTemplate.EffectType.EXPERIENCE_ITEM: "经验道具请在门客详情页为指定门客使用",
    ItemTemplate.EffectType.MEDICINE: "药品道具请在门客详情页为指定门客使用",
}


def _require_effect_payload_dict(item: InventoryItem) -> dict[str, Any]:
    payload = item.template.effect_payload
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ItemNotConfiguredError("effect_payload 配置异常")
    return payload


def _normalize_non_empty_string(raw: Any, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    return raw.strip()


def _normalize_positive_config_int(raw: Any, *, field_name: str) -> int:
    if isinstance(raw, bool):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ItemNotConfiguredError(f"{field_name} 配置异常") from exc
    if value <= 0:
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    return value


def _normalize_non_empty_string_list(raw: Any, *, field_name: str) -> list[str]:
    if not isinstance(raw, list):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    normalized: list[str] = []
    for entry in raw:
        normalized.append(_normalize_non_empty_string(entry, field_name=field_name))
    return normalized


def _normalize_weighted_item_choices(raw: Any, *, field_name: str) -> list[dict[str, int | str]]:
    if not isinstance(raw, list) or not raw:
        raise ItemNotConfiguredError(f"{field_name} 配置异常")

    normalized: list[dict[str, int | str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ItemNotConfiguredError(f"{field_name} 配置异常")
        normalized.append(
            {
                "item_key": _normalize_non_empty_string(entry.get("item_key"), field_name=field_name),
                "weight": _normalize_positive_config_int(entry.get("weight"), field_name=field_name),
            }
        )
    return normalized


def _normalize_non_negative_config_int(raw: Any, *, field_name: str) -> int:
    if isinstance(raw, bool):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ItemNotConfiguredError(f"{field_name} 配置异常") from exc
    if value < 0:
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    return value


def _normalize_optional_tool_action(payload: dict[str, Any]) -> str | None:
    action = payload.get("action")
    if action is None:
        return None
    return _normalize_non_empty_string(action, field_name="action")


def _normalize_resource_reward_mapping(raw: Any, *, field_name: str) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    normalized: dict[str, int] = {}
    for resource_key, raw_amount in raw.items():
        normalized_key = _normalize_non_empty_string(resource_key, field_name=field_name)
        normalized[normalized_key] = _normalize_non_negative_config_int(raw_amount, field_name=field_name)
    return normalized


def _normalize_item_reward_entries(raw: Any, *, field_name: str) -> list[dict[str, int | str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")

    normalized: list[dict[str, int | str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ItemNotConfiguredError(f"{field_name} 配置异常")
        item_key = _normalize_non_empty_string(entry.get("item_key"), field_name=field_name)
        min_quantity = _normalize_non_negative_config_int(entry.get("min_quantity", 1), field_name=field_name)
        max_quantity = _normalize_non_negative_config_int(
            entry.get("max_quantity", min_quantity), field_name=field_name
        )
        if max_quantity < min_quantity:
            raise ItemNotConfiguredError(f"{field_name} 配置异常")
        normalized.append(
            {
                "item_key": item_key,
                "min_quantity": min_quantity,
                "max_quantity": max_quantity,
            }
        )
    return normalized


def _normalize_granted_resource_mapping(raw: Any, *, contract_name: str) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid {contract_name}: {raw!r}")
    normalized: dict[str, int] = {}
    for resource_key, raw_amount in raw.items():
        if not isinstance(resource_key, str) or not resource_key.strip():
            raise AssertionError(f"invalid {contract_name} resource key: {resource_key!r}")
        if raw_amount is None or isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
            raise AssertionError(f"invalid {contract_name} amount: {raw_amount!r}")
        if raw_amount < 0:
            raise AssertionError(f"invalid {contract_name} amount: {raw_amount!r}")
        normalized[resource_key.strip()] = raw_amount
    return normalized


def _collect_weighted_template_choices(choices: list) -> tuple[list[str], list[int]]:
    template_keys: List[str] = []
    weights: List[int] = []
    for entry in choices:
        if not isinstance(entry, dict):
            # Payload is developer-controlled; misconfig should not silently change behavior.
            raise ItemNotConfiguredError("门客召唤卡 choices 配置异常")
        template_key = _normalize_non_empty_string(entry.get("template_key"), field_name="choices")
        weight = _normalize_positive_config_int(entry.get("weight"), field_name="choices")
        template_keys.append(template_key)
        weights.append(weight)
    return template_keys, weights


def _weighted_choose_template_key(template_keys: List[str], weights: List[int]) -> str:
    total_weight = sum(weights)
    roll = inventory_random.random() * total_weight
    chosen_key = template_keys[-1]
    cumulative = 0
    for template_key, weight in zip(template_keys, weights):
        cumulative += weight
        if roll < cumulative:
            chosen_key = template_key
            break
    return chosen_key


def _ensure_guest_capacity(manor: Manor) -> None:
    if manor.guests.count() >= manor.guest_capacity:
        raise GuestCapacityFullError()


def _consume_required_items_locked(manor: Manor, payload: dict[str, Any]) -> None:
    required_items = payload.get("required_items")
    if required_items is None:
        return
    if not isinstance(required_items, dict):
        # Don't allow misconfigured costs to degrade to "free use".
        raise ItemNotConfiguredError("required_items 配置异常")

    for item_key, raw_amount in required_items.items():
        normalized_key = _normalize_non_empty_string(item_key, field_name="required_items")
        amount = _normalize_positive_config_int(raw_amount, field_name="required_items")
        consume_inventory_item_for_manor_locked(manor, normalized_key, amount)


def _grant_item_resources(
    manor: Manor,
    payload: dict[str, int],
    note: str,
) -> tuple[dict[str, int], dict[str, int]]:
    if transaction.get_connection().in_atomic_block:
        credited_raw, overflow_raw = grant_resources_locked(
            manor,
            payload,
            note,
            ResourceEvent.Reason.ITEM_USE,
            sync_production=False,
        )
        credited = _normalize_granted_resource_mapping(credited_raw, contract_name="inventory resource grant result")
        overflow = _normalize_granted_resource_mapping(overflow_raw, contract_name="inventory resource overflow result")
        return credited, overflow
    credited_raw = grant_resources(
        manor,
        payload,
        note,
        ResourceEvent.Reason.ITEM_USE,
        sync_production=False,
    )
    credited = _normalize_granted_resource_mapping(credited_raw, contract_name="inventory resource grant result")
    overflow = {
        resource: amount - credited.get(resource, 0)
        for resource, amount in payload.items()
        if amount > credited.get(resource, 0)
    }
    return credited, overflow


def _format_resource_parts(resources: dict[str, int]) -> list[str]:
    return [f"{RESOURCE_LABELS.get(key, '未知资源')}+{value}" for key, value in resources.items()]


def _format_resource_grant_message(credited: dict[str, int], overflow: dict[str, int]) -> str:
    credited_text = "、".join(_format_resource_parts(credited)) if credited else "无"
    message = f"实际获得：{credited_text}"
    if overflow:
        message += f"；因容量上限未获得：{'、'.join(_format_resource_parts(overflow))}"
    return message


def _build_resource_overflow_confirmation_message(
    *,
    item_name: str,
    requested: dict[str, int],
    credited: dict[str, int],
    overflow: dict[str, int],
) -> str:
    requested_text = "、".join(_format_resource_parts(requested))
    credited_text = "、".join(_format_resource_parts(credited)) if credited else "无"
    overflow_text = "、".join(_format_resource_parts(overflow))
    return (
        f"「{item_name}」包含：{requested_text}。"
        f"当前可实际获得：{credited_text}；因容量上限将无法获得：{overflow_text}。"
        "道具仍会消耗1个，是否继续使用？"
    )


def _build_resource_overflow_confirmation_snapshot(
    item: InventoryItem,
    requested: dict[str, int],
    credited: dict[str, int],
    overflow: dict[str, int],
) -> dict[str, object]:
    return {
        "item_id": item.pk,
        "manor_id": item.manor_id,
        "item_quantity": item.quantity,
        "requested_resources": requested,
        "credited_resources": credited,
        "overflow_resources": overflow,
    }


def _normalize_probability(value: Any, *, field_name: str) -> float:
    """Normalize probability config to [0, 1]. Supports 0.1 or 10 (percent)."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")

    try:
        prob = float(value)
    except (TypeError, ValueError):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    if not math.isfinite(prob):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")

    if prob < 0:
        raise ItemNotConfiguredError(f"{field_name} 配置异常")
    if prob > 1:
        if prob <= 100:
            prob = prob / 100.0
        else:
            raise ItemNotConfiguredError(f"{field_name} 配置异常")
    return prob


def _normalize_random_item_groups(raw: Any, *, field_name: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ItemNotConfiguredError(f"{field_name} 配置异常")

    normalized: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict) or "chance" not in entry:
            raise ItemNotConfiguredError(f"{field_name} 配置异常")
        try:
            chance = _normalize_probability(entry.get("chance"), field_name=field_name)
            min_quantity = _normalize_non_negative_config_int(
                entry.get("min_quantity", 1),
                field_name=field_name,
            )
            max_quantity = _normalize_non_negative_config_int(
                entry.get("max_quantity", min_quantity),
                field_name=field_name,
            )
            choices = _normalize_weighted_item_choices(entry.get("choices"), field_name=field_name)
        except ItemNotConfiguredError as exc:
            raise ItemNotConfiguredError(f"{field_name} 配置异常") from exc
        if max_quantity < min_quantity:
            raise ItemNotConfiguredError(f"{field_name} 配置异常")
        normalized.append(
            {
                "chance": chance,
                "min_quantity": min_quantity,
                "max_quantity": max_quantity,
                "choices": choices,
            }
        )
    return normalized


def _apply_resource_pack(item: InventoryItem) -> Dict[str, Any]:
    """使用资源包，发放资源奖励。"""
    normalized_payload = _normalize_resource_reward_mapping(
        _require_effect_payload_dict(item),
        field_name="effect_payload",
    )
    if not normalized_payload:
        raise ItemNotConfiguredError()
    granted_resources, overflow_resources = _grant_item_resources(
        item.manor,
        normalized_payload,
        item.template.name,
    )
    return {
        **granted_resources,
        "credited_resources": granted_resources,
        "overflow_resources": overflow_resources,
        "_message": _format_resource_grant_message(granted_resources, overflow_resources),
    }


def _apply_peace_shield(item: InventoryItem) -> Dict[str, Any]:
    """使用免战牌，激活保护状态。"""
    from gameplay.services.raid import activate_peace_shield

    payload = _require_effect_payload_dict(item)
    duration = _normalize_positive_config_int(payload.get("duration"), field_name="duration")

    manor = item.manor
    activate_peace_shield(manor, duration)
    hours = duration // 3600
    return {
        "duration_seconds": duration,
        "duration_hours": hours,
        "_message": f"获得 {hours}小时 免战保护",
    }


def _apply_guest_summon(item: InventoryItem) -> Dict[str, Any]:
    """
    使用门客召唤卡：按权重随机获得一个门客模板并直接加入聚贤庄。
    """
    payload = _require_effect_payload_dict(item)
    choices = payload.get("choices")
    if not isinstance(choices, list):
        raise ItemNotConfiguredError("choices 配置异常")

    template_keys, weights = _collect_weighted_template_choices(choices)
    if not template_keys:
        raise ItemNotConfiguredError("choices 配置异常")

    chosen_key = _weighted_choose_template_key(template_keys, weights)

    manor = item.manor
    _ensure_guest_capacity(manor)

    from guests.models import GuestTemplate
    from guests.services.recruitment_guests import build_recruitment_custom_name, create_guest_from_template
    from guests.services.world_unique import (
        WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY,
        claim_world_unique_guest_from_scroll,
        is_world_unique_template,
    )

    template = GuestTemplate.objects.filter(key=chosen_key).first()
    if not template:
        raise ItemNotConfiguredError(f"门客模板不存在: {chosen_key}")

    if is_world_unique_template(template) and item.template.key != WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY:
        raise ItemNotUsableError(
            template.name,
            message="全服唯一门客只能通过专属召唤卷轴获得",
        )

    exclusive_template_keys_raw = payload.get("exclusive_template_keys")
    normalized_exclusive_keys: list[str] = []
    if exclusive_template_keys_raw is not None:
        normalized_exclusive_keys = _normalize_non_empty_string_list(
            exclusive_template_keys_raw,
            field_name="exclusive_template_keys",
        )
        if normalized_exclusive_keys and manor.guests.filter(template__key__in=normalized_exclusive_keys).exists():
            raise GuestAlreadyOwnedError(template)

    _consume_required_items_locked(manor, payload)

    summon_rng = inventory_random.Random()
    custom_name = build_recruitment_custom_name(template, summon_rng)
    if is_world_unique_template(template):
        guest = claim_world_unique_guest_from_scroll(
            manor,
            item.template.key,
            template,
            custom_name=custom_name,
            rng=summon_rng,
        )
    else:
        guest = create_guest_from_template(
            manor=manor,
            template=template,
            custom_name=custom_name,
            rng=summon_rng,
        )

    rarity_display = template.get_rarity_display()
    return {
        "获得门客": guest.display_name,
        "稀有度": rarity_display,
        "全服唯一": is_world_unique_template(template),
        "_message": f"获得门客 {guest.display_name}（{rarity_display}）",
    }


def _apply_tool(item: InventoryItem) -> Dict[str, Any]:
    """
    使用道具类物品（统一 effect_type=tool）。
    """
    payload = _require_effect_payload_dict(item)
    normalized_action = _normalize_optional_tool_action(payload)
    if normalized_action == "summon_guest":
        return _apply_guest_summon(item)
    if normalized_action == "rebirth_guest":
        # 门客重生卡需要选择目标门客，抛出提示让前端引导选择
        raise ItemNotUsableError(item.template.name, message="请选择要重生的门客")
    if normalized_action == "upgrade_guest_rarity":
        raise ItemNotUsableError(item.template.name, message="请选择要升阶的门客")
    if normalized_action == "soul_fusion":
        raise ItemNotUsableError(item.template.name, message="请选择要融合的门客")
    key = item.template.key or ""
    if key.startswith("peace_shield_"):
        return _apply_peace_shield(item)
    raise ItemNotUsableError(item.template.name, message="未知的道具效果")


def _apply_loot_box(item: InventoryItem) -> Dict[str, Any]:
    """使用宝箱类物品，按配置发放多种奖励。"""
    payload = _require_effect_payload_dict(item)
    if not payload:
        raise ItemNotConfiguredError()

    manor = item.manor
    rewards: List[str] = []
    skipped_bonus_items: List[str] = []

    # 1. 固定资源掉落（可选）
    resources = _normalize_resource_reward_mapping(payload.get("resources"), field_name="resources")
    if resources:
        result, _overflow = _grant_item_resources(manor, resources, item.template.name)
        parts = _format_resource_parts(result)
        rewards.append("资源：" + "、".join(parts))

    # 2. 普通物品掉落（随机数量，可选）
    item_rewards = _normalize_item_reward_entries(payload.get("item_rewards"), field_name="item_rewards")
    for reward in item_rewards:
        item_key = str(reward["item_key"])
        quantity = inventory_random.randint(int(reward["min_quantity"]), int(reward["max_quantity"]))
        if quantity <= 0:
            continue
        try:
            add_item_to_inventory(manor, item_key, quantity)
            reward_template = ItemTemplate.objects.filter(key=item_key).first()
            reward_name = reward_template.name if reward_template else "未知物品"
            rewards.append(f"物品【{reward_name}】×{quantity}")
        except ItemNotFoundError as exc:
            logger.warning(
                "loot box item reward grant skipped: manor_id=%s loot_box_item_id=%s reward_item_key=%s error=%s",
                manor.id,
                item.id,
                item_key,
                exc,
            )
            skipped_bonus_items.append(item_key)

    # 3. 独立概率物品组（每组命中后按权重选择一种物品）
    random_item_groups = _normalize_random_item_groups(
        payload.get("random_item_groups"),
        field_name="random_item_groups",
    )
    for group in random_item_groups:
        if inventory_random.random() >= float(group["chance"]):
            continue
        choices = group["choices"]
        item_key = weighted_random_choice(
            [str(choice["item_key"]) for choice in choices],
            [float(choice["weight"]) for choice in choices],
            inventory_random,
        )
        quantity = inventory_random.randint(
            int(group["min_quantity"]),
            int(group["max_quantity"]),
        )
        if quantity <= 0:
            continue
        try:
            add_item_to_inventory(manor, item_key, quantity)
            reward_template = ItemTemplate.objects.filter(key=item_key).first()
            reward_name = reward_template.name if reward_template else "未知物品"
            rewards.append(f"物品【{reward_name}】×{quantity}")
        except ItemNotFoundError as exc:
            logger.warning(
                "loot box random item group grant skipped: manor_id=%s loot_box_item_id=%s reward_item_key=%s error=%s",
                manor.id,
                item.id,
                item_key,
                exc,
            )
            skipped_bonus_items.append(item_key)

    # 4. 随机银两（可选）
    silver_min_raw = payload.get("silver_min")
    silver_max_raw = payload.get("silver_max")
    if silver_min_raw is not None or silver_max_raw is not None:
        if isinstance(silver_min_raw, bool) or isinstance(silver_max_raw, bool):
            raise ItemNotConfiguredError("silver_min/silver_max 配置异常")
        try:
            silver_min = int(silver_min_raw if silver_min_raw is not None else 0)
            silver_max = int(silver_max_raw if silver_max_raw is not None else silver_min)
        except (TypeError, ValueError):
            raise ItemNotConfiguredError("silver_min/silver_max 配置异常")

        if silver_min < 0 or silver_max < 0:
            raise ItemNotConfiguredError("silver_min/silver_max 配置异常")
        if silver_max < silver_min:
            raise ItemNotConfiguredError("silver_min/silver_max 配置异常")

        rolled_silver = inventory_random.randint(silver_min, silver_max)
        if rolled_silver > 0:
            silver_result, _overflow = _grant_item_resources(
                manor,
                {"silver": rolled_silver},
                item.template.name,
            )
            granted_silver = silver_result.get("silver", 0)
            if granted_silver > 0:
                rewards.append(f"银两+{granted_silver}")

    # 5. 装备掉落（概率，随机一件）
    gear_keys_raw = payload.get("gear_keys")
    gear_keys: list[str] = []
    if gear_keys_raw is not None:
        gear_keys = _normalize_non_empty_string_list(gear_keys_raw, field_name="gear_keys")
    gear_choices_raw = payload.get("gear_choices")
    gear_choices: list[dict[str, int | str]] = []
    if gear_choices_raw is not None:
        gear_choices = _normalize_weighted_item_choices(gear_choices_raw, field_name="gear_choices")
    gear_chance = _normalize_probability(payload.get("gear_chance"), field_name="gear_chance")
    if gear_chance > 0 and (gear_choices or gear_keys) and inventory_random.random() < gear_chance:
        from guests.models import GearTemplate
        from guests.services.equipment import give_gear

        if gear_choices:
            choice_keys = [str(entry["item_key"]) for entry in gear_choices]
            choice_weights = [float(entry["weight"]) for entry in gear_choices]
            gear_key = weighted_random_choice(choice_keys, choice_weights, inventory_random)
        else:
            gear_key = inventory_random.choice(gear_keys)
        gear_template = GearTemplate.objects.filter(key=gear_key).first()
        if gear_template:
            give_gear(manor, gear_template)
            rewards.append(f"装备【{gear_template.name}】")
        else:
            from guests.utils.equipment_utils import EQUIP_SLOT_MAP

            item_template = ItemTemplate.objects.filter(key=gear_key).first()
            if item_template and item_template.effect_type in EQUIP_SLOT_MAP:
                add_item_to_inventory(manor, gear_key, 1)
                rewards.append(f"装备【{item_template.name}】")
            else:
                skipped_bonus_items.append(gear_key)

    # 6. 技能书掉落（概率，随机一本）
    skill_book_chance = _normalize_probability(payload.get("skill_book_chance"), field_name="skill_book_chance")
    skill_book_keys_raw = payload.get("skill_book_keys")
    skill_book_keys: list[str] = []
    if skill_book_keys_raw is not None:
        skill_book_keys = _normalize_non_empty_string_list(skill_book_keys_raw, field_name="skill_book_keys")
    skill_book_choices_raw = payload.get("skill_book_choices")
    skill_book_choices: list[dict[str, int | str]] = []
    if skill_book_choices_raw is not None:
        skill_book_choices = _normalize_weighted_item_choices(skill_book_choices_raw, field_name="skill_book_choices")
    if (
        skill_book_chance > 0
        and (skill_book_choices or skill_book_keys)
        and inventory_random.random() < skill_book_chance
    ):
        if skill_book_choices:
            choice_keys = [str(entry["item_key"]) for entry in skill_book_choices]
            choice_weights = [float(entry["weight"]) for entry in skill_book_choices]
            book_key = weighted_random_choice(choice_keys, choice_weights, inventory_random)
        else:
            book_key = inventory_random.choice(skill_book_keys)
        try:
            add_item_to_inventory(manor, book_key, 1)
            book_template = ItemTemplate.objects.filter(key=book_key).first()
            book_name = book_template.name if book_template else "未知技能书"
            rewards.append(f"技能书【{book_name}】")
        except ItemNotFoundError as exc:
            logger.warning(
                "loot box bonus item grant skipped: manor_id=%s loot_box_item_id=%s bonus_item_key=%s error=%s",
                manor.id,
                item.id,
                book_key,
                exc,
            )
            skipped_bonus_items.append(book_key)

    reward_text = "、".join(rewards) if rewards else "空"
    return {
        "rewards": rewards,
        "skipped_bonus_items": skipped_bonus_items,
        "_message": f"打开宝箱获得：{reward_text}",
    }


ITEM_EFFECT_HANDLERS: dict[str, ItemEffectHandler] = {
    ItemTemplate.EffectType.RESOURCE_PACK: _apply_resource_pack,
    ItemTemplate.EffectType.TOOL: _apply_tool,
    ItemTemplate.EffectType.LOOT_BOX: _apply_loot_box,
}


@transaction.atomic
def use_inventory_item(
    item: InventoryItem,
    manor: Manor | None = None,
    *,
    resource_overflow_confirmation: dict[str, object] | None = None,
) -> Dict[str, Any]:
    """
    使用背包物品（仓库可用）。

    Args:
        item: 要使用的物品实例
        manor: 庄园实例（可选，用于安全校验）
        resource_overflow_confirmation: 已确认的资源发放快照

    Returns:
        使用效果摘要字典

    Raises:
        ItemNotFoundError: 物品不存在或不属于指定庄园
        InsufficientStockError: 物品数量不足
        ItemNotUsableError: 物品不可用
    """
    from core.exceptions import InsufficientStockError

    if not item.pk:
        raise ItemNotFoundError()

    # 死锁预防：统一锁顺序 Manor -> InventoryItem
    # 商店服务是先锁 Manor 后锁 Item，此处必须保持一致
    target_manor_id = manor.pk if manor else item.manor_id
    if target_manor_id:
        Manor.objects.select_for_update().get(pk=target_manor_id)

    # 构建查询条件
    query_filter: dict[str, object] = {"pk": item.pk}
    if manor is not None:
        # 如果提供了manor，校验物品归属
        query_filter["manor"] = manor

    locked_item = (
        InventoryItem.objects.select_for_update().select_related("template", "manor").filter(**query_filter).first()
    )
    if not locked_item:
        if manor is not None:
            raise ItemNotFoundError("物品不存在或不属于您的庄园")
        raise InsufficientStockError(item.template.name, 1, 0)
    if locked_item.quantity <= 0:
        raise InsufficientStockError(locked_item.template.name, 1, locked_item.quantity)

    template = locked_item.template
    if not template.is_usable:
        raise ItemNotUsableError(template.name, "not_warehouse_usable")

    handler = ITEM_EFFECT_HANDLERS.get(template.effect_type)
    if handler:
        if template.effect_type == ItemTemplate.EffectType.RESOURCE_PACK:
            requested_resources = _normalize_resource_reward_mapping(
                _require_effect_payload_dict(locked_item),
                field_name="effect_payload",
            )
            if not requested_resources:
                raise ItemNotConfiguredError()
            credited_resources, overflow_resources = preview_resource_grant(
                locked_item.manor,
                requested_resources,
            )
            if overflow_resources:
                confirmation_snapshot = _build_resource_overflow_confirmation_snapshot(
                    locked_item,
                    requested_resources,
                    credited_resources,
                    overflow_resources,
                )
                if resource_overflow_confirmation != confirmation_snapshot:
                    raise ItemResourceOverflowConfirmationRequired(
                        template.name,
                        requested_resources,
                        credited_resources,
                        overflow_resources,
                        confirmation_snapshot,
                        message=_build_resource_overflow_confirmation_message(
                            item_name=template.name,
                            requested=requested_resources,
                            credited=credited_resources,
                            overflow=overflow_resources,
                        ),
                    )
        effect_summary = handler(locked_item)
    else:
        effect_type = template.effect_type or ""
        if effect_type.startswith("equip_"):
            raise ItemNotUsableError(template.name, "equip_in_guest_detail")
        message = NON_WAREHOUSE_MESSAGES.get(effect_type)
        if message:
            raise ItemNotUsableError(template.name, effect_type)
        raise ItemNotUsableError(template.name, "unknown_effect")

    locked_item.refresh_from_db(fields=["quantity"])
    consume_inventory_item_locked(locked_item, 1)
    return effect_summary
