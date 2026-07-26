from __future__ import annotations

import random
from typing import Any, TypedDict

from core.utils import safe_positive_int


class WeightedLootCandidate(TypedDict):
    item_key: str
    remaining_quantity: int
    storage_space: int


class _NormalizedWeightedLootCandidate(TypedDict):
    item_key: str
    remaining_quantity: int
    capacity_cost: int


# PvP 运力以千分之一点为最小单位，粮食每份占 1 个内部单位。
_ITEM_LOOT_CAPACITY_SCALE = 1000
_GRAIN_ITEM_KEY = "grain"
_GRAIN_CAPACITY_COST = 1
_DOMINANT_BATCH_MIN_EXPECTED_RUN = 64


def _calculate_item_capacity_cost(item_key: str, storage_space: Any) -> int:
    if item_key == _GRAIN_ITEM_KEY:
        return _GRAIN_CAPACITY_COST
    return safe_positive_int(storage_space, 1) * _ITEM_LOOT_CAPACITY_SCALE


def _select_weighted_candidate(
    candidates: list[_NormalizedWeightedLootCandidate],
    total_weight: int,
) -> _NormalizedWeightedLootCandidate | None:
    selected_position = random.randrange(total_weight)
    for candidate in candidates:
        if selected_position < candidate["remaining_quantity"]:
            return candidate
        selected_position -= candidate["remaining_quantity"]
    return None


def _sample_dominant_run_length(dominant_quantity: int, other_quantity: int) -> int:
    """抽取下一件其他物品前连续命中主导物品的数量。"""
    if other_quantity == 1:
        return random.randrange(dominant_quantity + 1)

    # 该等待次数服从 beta-binomial(D, 1, O)，等价于随机排列中的首张其他物品位置。
    dominant_probability = random.betavariate(1.0, float(other_quantity))
    return random.binomialvariate(dominant_quantity, dominant_probability)


def normalize_positive_int_mapping(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, int] = {}
    for key, value in raw.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        try:
            normalized_value = int(value)
        except (TypeError, ValueError):
            continue
        if normalized_value > 0:
            normalized[normalized_key] = normalized_value
    return normalized


def calculate_item_loot_capacity(
    *,
    max_capacity: int,
    guest_count: int,
    troop_loadout: Any,
    surviving_troop_count: int | None,
    full_guest_count: int,
    full_troop_count: int,
    min_cap_ratio: float,
    survival_base_ratio: float,
    survival_scaling_ratio: float,
) -> int:
    resolved_max_capacity = safe_positive_int(max_capacity, 0)
    resolved_full_guest_count = safe_positive_int(full_guest_count, 0)
    resolved_full_troop_count = safe_positive_int(full_troop_count, 0)
    if resolved_max_capacity <= 0:
        return 0
    if resolved_full_guest_count <= 0 or resolved_full_troop_count <= 0:
        return resolved_max_capacity

    normalized_loadout = normalize_positive_int_mapping(troop_loadout)
    deployed_troops = sum(normalized_loadout.values())
    resolved_min_cap_ratio = min(1.0, max(0.0, float(min_cap_ratio)))
    if max(0, int(guest_count or 0)) <= 0 or deployed_troops <= 0:
        return int(resolved_max_capacity * resolved_min_cap_ratio)

    guest_factor = min(max(0, int(guest_count)) / resolved_full_guest_count, 1.0)
    troop_factor = min(deployed_troops / resolved_full_troop_count, 1.0)
    departure_ratio = max(resolved_min_cap_ratio, guest_factor * troop_factor)

    resolved_surviving_troops = deployed_troops
    if surviving_troop_count is not None:
        resolved_surviving_troops = min(deployed_troops, max(0, int(surviving_troop_count or 0)))
    survival_ratio = resolved_surviving_troops / deployed_troops
    survival_multiplier = (
        max(0.0, float(survival_base_ratio)) + max(0.0, float(survival_scaling_ratio)) * survival_ratio
    )
    return min(resolved_max_capacity, int(resolved_max_capacity * departure_ratio * survival_multiplier))


def calculate_item_loot_draw_count(total_quantity: int, max_ratio: float) -> int:
    resolved_total_quantity = safe_positive_int(total_quantity, 0)
    resolved_max_ratio = min(1.0, max(0.0, float(max_ratio)))
    return int(resolved_total_quantity * resolved_max_ratio)


def draw_weighted_item_loot(
    candidates: list[WeightedLootCandidate],
    *,
    draw_count: int,
    capacity: int,
) -> dict[str, int]:
    pool: list[_NormalizedWeightedLootCandidate] = []
    for raw_candidate in candidates:
        item_key = str(raw_candidate.get("item_key") or "").strip()
        quantity = safe_positive_int(raw_candidate.get("remaining_quantity"), 0)
        if not item_key or quantity <= 0:
            continue
        pool.append(
            {
                "item_key": item_key,
                "remaining_quantity": quantity,
                "capacity_cost": _calculate_item_capacity_cost(item_key, raw_candidate.get("storage_space")),
            }
        )

    loot_items: dict[str, int] = {}
    remaining_draws = max(0, int(draw_count or 0))
    remaining_capacity = max(0, int(capacity or 0)) * _ITEM_LOOT_CAPACITY_SCALE

    # 每件库存对应一张抽取票；抽中后移除一张，实现按库存数量加权的无放回抽取。
    while remaining_draws > 0 and remaining_capacity > 0:
        eligible_candidates = [
            candidate
            for candidate in pool
            if candidate["remaining_quantity"] > 0 and candidate["capacity_cost"] <= remaining_capacity
        ]
        if not eligible_candidates:
            break

        # 只剩一种可装物品时结果已确定，批量结算可避免大量粮食逐件循环。
        if len(eligible_candidates) == 1:
            batch_candidate = eligible_candidates[0]
            selected_quantity = min(
                remaining_draws,
                batch_candidate["remaining_quantity"],
                remaining_capacity // batch_candidate["capacity_cost"],
            )
            if selected_quantity <= 0:
                break
            item_key = batch_candidate["item_key"]
            batch_candidate["remaining_quantity"] -= selected_quantity
            loot_items[item_key] = loot_items.get(item_key, 0) + selected_quantity
            remaining_draws -= selected_quantity
            remaining_capacity -= batch_candidate["capacity_cost"] * selected_quantity
            break

        total_weight = sum(candidate["remaining_quantity"] for candidate in eligible_candidates)
        if total_weight <= 0:
            break

        dominant_candidate = max(eligible_candidates, key=lambda candidate: candidate["remaining_quantity"])
        dominant_quantity = dominant_candidate["remaining_quantity"]
        other_quantity = total_weight - dominant_quantity
        if dominant_quantity >= _DOMINANT_BATCH_MIN_EXPECTED_RUN * (other_quantity + 1):
            dominant_cost = dominant_candidate["capacity_cost"]
            batch_limit = min(
                remaining_draws,
                dominant_quantity,
                remaining_capacity // dominant_cost,
            )
            for candidate in eligible_candidates:
                if candidate is dominant_candidate:
                    continue
                draws_until_ineligible = ((remaining_capacity - candidate["capacity_cost"]) // dominant_cost) + 1
                batch_limit = min(batch_limit, draws_until_ineligible)

            dominant_run_length = _sample_dominant_run_length(dominant_quantity, other_quantity)
            selected_quantity = min(dominant_run_length, batch_limit)
            if selected_quantity > 0:
                item_key = dominant_candidate["item_key"]
                dominant_candidate["remaining_quantity"] -= selected_quantity
                loot_items[item_key] = loot_items.get(item_key, 0) + selected_quantity
                remaining_draws -= selected_quantity
                remaining_capacity -= dominant_cost * selected_quantity

            if dominant_run_length >= batch_limit:
                continue

            other_candidates = [candidate for candidate in eligible_candidates if candidate is not dominant_candidate]
            selected_candidate = _select_weighted_candidate(other_candidates, other_quantity)
        else:
            selected_candidate = _select_weighted_candidate(eligible_candidates, total_weight)

        if selected_candidate is None:
            break

        item_key = selected_candidate["item_key"]
        selected_candidate["remaining_quantity"] -= 1
        loot_items[item_key] = loot_items.get(item_key, 0) + 1
        remaining_draws -= 1
        remaining_capacity -= selected_candidate["capacity_cost"]

    return loot_items
