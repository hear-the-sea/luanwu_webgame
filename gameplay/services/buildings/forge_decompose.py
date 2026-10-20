from __future__ import annotations

from typing import Any

from django.db import transaction

from core.exceptions import ForgeOperationError
from gameplay.models import InventoryItem
from gameplay.models import Manor as ManorModel

from ..inventory.core import add_item_to_inventory_locked, consume_inventory_item_locked
from .forge_decompose_helpers import build_decomposable_equipment_option

NON_DECOMPOSABLE_EQUIPMENT_EFFECT_TYPES = frozenset({"equip_mount"})


def _normalize_decompose_reward_mapping(raw_value: object, *, contract_name: str) -> dict[str, int]:
    if not isinstance(raw_value, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    normalized: dict[str, int] = {}
    for reward_key, reward_amount in raw_value.items():
        if not isinstance(reward_key, str) or not reward_key.strip():
            raise AssertionError(f"invalid {contract_name} key: {reward_key!r}")
        if reward_amount is None or isinstance(reward_amount, bool):
            raise AssertionError(f"invalid {contract_name} amount: {reward_amount!r}")
        reward_amount_for_int: Any = reward_amount
        try:
            parsed_amount = int(reward_amount_for_int)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"invalid {contract_name} amount: {reward_amount!r}") from exc
        if parsed_amount <= 0:
            raise AssertionError(f"invalid {contract_name} amount: {reward_amount!r}")
        normalized[reward_key.strip()] = parsed_amount
    if not normalized:
        raise AssertionError(f"invalid {contract_name}: empty rewards")
    return normalized


def collect_recruitment_equipment_keys(*, load_troop_templates: Any) -> set[str]:
    data = load_troop_templates()
    if not isinstance(data, dict):
        return set()

    equipment_keys: set[str] = set()
    for troop in data.get("troops", []):
        if not isinstance(troop, dict):
            continue
        recruit = troop.get("recruit") or {}
        if not isinstance(recruit, dict):
            continue
        for item_key in recruit.get("equipment", []) or []:
            if isinstance(item_key, str) and item_key:
                equipment_keys.add(item_key)
    return equipment_keys


def get_decomposable_equipment_options(
    manor: Any,
    category: str | None = None,
    *,
    config: dict[str, Any],
    recruit_equipment_keys: set[str],
    infer_equipment_category: Any,
    to_decompose_category: Any,
    category_labels: dict[str, str],
) -> list[dict[str, Any]]:
    supported_rarities = set(config["supported_rarities"])
    rarity_labels: dict[str, str] = config["rarity_labels"]
    rarity_order: dict[str, int] = config["rarity_order"]

    query = (
        InventoryItem.objects.filter(
            manor=manor,
            quantity__gt=0,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            template__effect_type__startswith="equip_",
            template__rarity__in=supported_rarities,
        )
        .exclude(template__effect_type__in=NON_DECOMPOSABLE_EQUIPMENT_EFFECT_TYPES)
        .select_related("template")
        .order_by("template__name")
    )
    if recruit_equipment_keys:
        query = query.exclude(template__key__in=recruit_equipment_keys)

    options: list[dict[str, Any]] = []
    for item in query:
        option = build_decomposable_equipment_option(
            item,
            rarity_labels=rarity_labels,
            category_labels=category_labels,
            infer_equipment_category=infer_equipment_category,
            to_decompose_category=to_decompose_category,
            category_filter=category,
        )
        if option is not None:
            option["image_url"] = item.template.image.url if item.template.image else ""
            options.append(option)

    options.sort(key=lambda row: (-rarity_order.get(row["rarity"], 0), row["name"]))
    return options


def roll_decompose_rewards(
    rarity: str,
    quantity: int,
    config: dict[str, Any],
    *,
    reward_roller: Any,
    randint_func: Any,
    random_func: Any,
) -> dict[str, int]:
    return reward_roller(
        rarity,
        quantity,
        config,
        randint_func=randint_func,
        random_func=random_func,
    )


def decompose_equipment(
    manor: Any,
    equipment_key: str,
    quantity: int = 1,
    *,
    recruit_equipment_keys: set[str],
    config: dict[str, Any],
    roll_decompose_rewards: Any,
) -> dict[str, Any]:
    if quantity < 1:
        raise ForgeOperationError("分解数量至少为1")

    if equipment_key in recruit_equipment_keys:
        raise ForgeOperationError("用于募兵（招募护院）的装备不可分解")

    supported_rarities = set(config["supported_rarities"])

    with transaction.atomic():
        locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)
        locked_item = (
            InventoryItem.objects.select_for_update()
            .select_related("template")
            .filter(
                manor=locked_manor,
                template__key=equipment_key,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .first()
        )

        if not locked_item:
            raise ForgeOperationError("仓库中没有该装备")
        if locked_item.quantity < quantity:
            raise ForgeOperationError("装备数量不足")

        template = locked_item.template
        effect_type = str(template.effect_type or "")
        if effect_type in NON_DECOMPOSABLE_EQUIPMENT_EFFECT_TYPES:
            raise ForgeOperationError("坐骑不可分解")
        if not effect_type.startswith("equip_"):
            raise ForgeOperationError("该物品不是可分解装备")
        if template.rarity not in supported_rarities:
            raise ForgeOperationError("仅绿色及以上装备可分解")

        rewards = _normalize_decompose_reward_mapping(
            roll_decompose_rewards(template.rarity, quantity, config),
            contract_name="forge decompose rewards",
        )
        bound_used = consume_inventory_item_locked(locked_item, quantity)
        for reward_key, reward_amount in rewards.items():
            add_item_to_inventory_locked(locked_manor, reward_key, reward_amount, is_bound=bool(bound_used))

    return {
        "equipment_key": equipment_key,
        "equipment_name": template.name,
        "quantity": quantity,
        "rewards": rewards,
    }
