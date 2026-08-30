from __future__ import annotations

import random
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from core.exceptions import ForgeOperationError

if TYPE_CHECKING:
    from ...models import InventoryItem


def build_decomposable_equipment_option(
    item: InventoryItem,
    *,
    rarity_labels: dict[str, str],
    category_labels: dict[str, str],
    infer_equipment_category: Callable[[str, str | None], str | None],
    to_decompose_category: Callable[[str | None], str | None],
    category_filter: str | None = None,
) -> dict[str, Any] | None:
    item_category = infer_equipment_category(item.template.key, item.template.effect_type)
    decompose_category = to_decompose_category(item_category)
    if category_filter and decompose_category != category_filter:
        return None

    rarity = item.template.rarity
    return {
        "key": item.template.key,
        "name": item.template.name,
        "effect_summary": str(getattr(item.template, "equipment_effect_summary", "") or ""),
        "rarity": rarity,
        "rarity_label": rarity_labels.get(rarity, rarity),
        "quantity": item.quantity,
        "effect_type": item.template.effect_type,
        "category": decompose_category,
        "category_name": category_labels.get(decompose_category, decompose_category) if decompose_category else "",
    }


def roll_decompose_rewards(
    rarity: str,
    quantity: int,
    config: dict[str, Any],
    *,
    randint_func: Callable[[int, int], int] | None = None,
    random_func: Callable[[], float] | None = None,
) -> dict[str, int]:
    supported_rarities = set(config["supported_rarities"])
    if rarity not in supported_rarities:
        raise ForgeOperationError("仅绿色及以上装备可分解")

    base_materials_map: dict[str, dict[str, list[int]]] = config["base_materials"]
    chance_rewards_map: dict[str, dict[str, float]] = config["chance_rewards"]
    base_materials = base_materials_map.get(rarity)
    chance_rewards = chance_rewards_map.get(rarity)
    if not base_materials or chance_rewards is None:
        raise ForgeOperationError("分解配置缺失，请联系管理员", rarity=rarity)

    roll_int = randint_func or random.randint
    roll_float = random_func or random.random
    rewards: dict[str, int] = {}

    for _ in range(quantity):
        for mat_key, amount_range in base_materials.items():
            min_amount, max_amount = amount_range
            amount = roll_int(min_amount, max_amount)
            if amount > 0:
                rewards[mat_key] = rewards.get(mat_key, 0) + amount

        for reward_key, probability in chance_rewards.items():
            if roll_float() < probability:
                rewards[reward_key] = rewards.get(reward_key, 0) + 1

    return rewards
