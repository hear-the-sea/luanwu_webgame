from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from core.config import GUEST
from core.exceptions import GuestAllocationResetError
from guests.constants import SUPER_GUEST_DISGUISE_AGILITY_BONUS, SUPER_GUEST_DISGUISE_FORCE_BONUS
from guests.services.equipment_payloads import normalize_extra_stats
from guests.utils.equipment_utils import compute_set_bonus

from .growth_rules import (
    CIVIL_ATTRIBUTE_WEIGHTS,
    MILITARY_ATTRIBUTE_WEIGHTS,
    RARITY_ATTRIBUTE_GROWTH_RANGE,
    RARITY_SKILL_POINT_GAINS,
)
from .models import GuestArchetype

if TYPE_CHECKING:
    from .models import Guest

ATTRIBUTE_LABELS = {
    "force": "武力",
    "intellect": "智力",
    "defense": "防御",
    "agility": "敏捷",
}
GROWTH_KEYS = ("force", "intellect", "defense", "agility")
XISUIDAN_MAX_REROLL_ATTEMPTS = 32

GrowthAllocation = dict[str, int]
GrowthAllocator = Callable[["Guest", int, random.Random | None], GrowthAllocation]


@dataclass(frozen=True)
class GrowthRerollResult:
    old_total: int
    new_total: int
    growth_diff: int
    changes: GrowthAllocation


@dataclass(frozen=True)
class AllocationResetResult:
    total_returned: int
    details: GrowthAllocation


def _resolve_growth_range(rarity: str, growth_range: list | None) -> tuple[int, int]:
    if growth_range and len(growth_range) == 2:
        return int(growth_range[0]), int(growth_range[1])
    default_min, default_max = RARITY_ATTRIBUTE_GROWTH_RANGE.get(rarity, (1, 3))
    return int(default_min), int(default_max)


def _resolve_weights(archetype: str, attribute_weights: dict | None) -> dict[str, int]:
    if attribute_weights:
        weights = {
            "force": int(attribute_weights.get("force", 0) or 0),
            "intellect": int(attribute_weights.get("intellect", 0) or 0),
            "defense": int(attribute_weights.get("defense", 0) or 0),
            "agility": int(attribute_weights.get("agility", 0) or 0),
        }
        if sum(weights.values()) > 0:
            return weights
    if archetype == GuestArchetype.MILITARY:
        return MILITARY_ATTRIBUTE_WEIGHTS
    return CIVIL_ATTRIBUTE_WEIGHTS


def _build_weighted_choices(weights: dict[str, int]) -> list[str]:
    choices: list[str] = []
    for attr, weight in weights.items():
        if weight > 0:
            choices.extend([attr] * int(weight))
    return choices


def allocate_level_up_attributes(
    guest: "Guest",
    levels: int = 1,
    rng: random.Random | None = None,
) -> GrowthAllocation:
    if rng is None:
        rng = random.Random()

    template = guest.template
    from guests.services.custom_guest import is_custom_guest, resolve_custom_growth_range, resolve_custom_growth_weights

    min_growth, max_growth = (
        resolve_custom_growth_range(guest)
        if is_custom_guest(guest)
        else _resolve_growth_range(guest.rarity, template.growth_range)
    )
    weights = (
        resolve_custom_growth_weights(guest)
        if is_custom_guest(guest)
        else _resolve_weights(guest.archetype, template.attribute_weights)
    )
    choices = _build_weighted_choices(weights)

    allocation: GrowthAllocation = {key: 0 for key in GROWTH_KEYS}
    for _ in range(levels):
        points_this_level = rng.randint(min_growth, max_growth)
        for _ in range(points_this_level):
            allocation[rng.choice(choices)] += 1
    return allocation


def normalize_growth_allocation(allocation: object) -> GrowthAllocation:
    if not isinstance(allocation, dict):
        return {key: 0 for key in GROWTH_KEYS}
    return {key: int(allocation.get(key, 0) or 0) for key in GROWTH_KEYS}


def apply_attribute_growth(guest: "Guest", allocation: GrowthAllocation) -> None:
    guest.force += allocation.get("force", 0)
    guest.intellect += allocation.get("intellect", 0)
    guest.defense_stat += allocation.get("defense", 0)
    guest.agility += allocation.get("agility", 0)


def get_expected_growth(
    rarity: str,
    archetype: str,
    levels: int = 1,
    growth_range: list | None = None,
    attribute_weights: dict | None = None,
) -> dict[str, float]:
    min_growth, max_growth = _resolve_growth_range(rarity, growth_range)
    total_points = ((min_growth + max_growth) / 2) * levels
    weights = _resolve_weights(archetype, attribute_weights)
    total_weight = sum(weights.values())
    return {attr: (total_points * weight) / total_weight for attr, weight in weights.items()}


def _resolve_non_growth_attribute_bonuses(guest: "Guest") -> GrowthAllocation:
    """Return direct attribute bonuses that must not be treated as growth.

    Equipment and the super disguise card write directly to the same fields as
    level growth.  Growth calculations therefore have to remove and restore
    those values explicitly; otherwise a wash can either report a false
    over-cap total or erase the bonuses while rewriting the base stats.
    """
    bonuses: GrowthAllocation = {key: 0 for key in GROWTH_KEYS}
    gear_items = list(guest.gear_items.select_related("template"))
    for gear in gear_items:
        extra_stats = normalize_extra_stats(gear.template.extra_stats)
        for key in GROWTH_KEYS:
            bonuses[key] += int(extra_stats.get(key, 0) or 0)

    # Set bonuses for force/intellect/defense/agility are written directly to
    # guest attributes and must not be counted as level growth.
    set_bonus = compute_set_bonus(gear_items)
    for key in GROWTH_KEYS:
        bonuses[key] += int(set_bonus.get(key, 0) or 0)

    if guest.super_disguise_applied:
        bonuses["force"] += SUPER_GUEST_DISGUISE_FORCE_BONUS
        bonuses["agility"] += SUPER_GUEST_DISGUISE_AGILITY_BONUS
    return bonuses


def extract_current_guest_growth(
    guest: "Guest", *, non_growth_bonuses: GrowthAllocation | None = None
) -> GrowthAllocation:
    if non_growth_bonuses is None:
        non_growth_bonuses = _resolve_non_growth_attribute_bonuses(guest)
    return {
        "force": max(0, guest.force - guest.initial_force - guest.allocated_force - non_growth_bonuses["force"]),
        "intellect": max(
            0,
            guest.intellect - guest.initial_intellect - guest.allocated_intellect - non_growth_bonuses["intellect"],
        ),
        "defense": max(
            0,
            guest.defense_stat - guest.initial_defense - guest.allocated_defense - non_growth_bonuses["defense"],
        ),
        "agility": max(
            0, guest.agility - guest.initial_agility - guest.allocated_agility - non_growth_bonuses["agility"]
        ),
    }


def resolve_guest_growth_stats(
    guest: "Guest",
    growth_allocation: GrowthAllocation,
    *,
    non_growth_bonuses: GrowthAllocation | None = None,
) -> GrowthAllocation:
    if non_growth_bonuses is None:
        non_growth_bonuses = _resolve_non_growth_attribute_bonuses(guest)
    return {
        "force": guest.initial_force + guest.allocated_force + growth_allocation["force"] + non_growth_bonuses["force"],
        "intellect": guest.initial_intellect
        + guest.allocated_intellect
        + growth_allocation["intellect"]
        + non_growth_bonuses["intellect"],
        "defense": guest.initial_defense
        + guest.allocated_defense
        + growth_allocation["defense"]
        + non_growth_bonuses["defense"],
        "agility": guest.initial_agility
        + guest.allocated_agility
        + growth_allocation["agility"]
        + non_growth_bonuses["agility"],
    }


def _allocate_growth_at_total(
    guest: "Guest",
    total_points: int,
    rng: random.Random,
) -> GrowthAllocation:
    """Distribute an exact total using the guest's own attribute weights."""
    allocation: GrowthAllocation = {key: 0 for key in GROWTH_KEYS}
    if total_points <= 0:
        return allocation

    from guests.services.custom_guest import is_custom_guest, resolve_custom_growth_weights

    weights = (
        resolve_custom_growth_weights(guest)
        if is_custom_guest(guest)
        else _resolve_weights(guest.archetype, guest.template.attribute_weights)
    )
    choices = _build_weighted_choices(weights)
    for _ in range(total_points):
        allocation[rng.choice(choices)] += 1
    return allocation


def apply_training_completion(
    guest: "Guest",
    *,
    levels_gained: int,
    allocate_level_up_attributes_func: GrowthAllocator = allocate_level_up_attributes,
) -> None:
    if levels_gained <= 0:
        return

    allocation = normalize_growth_allocation(allocate_level_up_attributes_func(guest, levels_gained, None))
    apply_attribute_growth(guest, allocation)

    target_level = min(guest.level + levels_gained, int(GUEST.MAX_LEVEL))
    per_level_points = RARITY_SKILL_POINT_GAINS.get(guest.rarity, 1)
    guest.level = target_level
    guest.attribute_points += per_level_points * levels_gained
    guest.experience = 0
    guest.current_hp = guest.max_hp


def reroll_guest_growth(
    guest: "Guest",
    *,
    rng: random.Random,
    allocate_level_up_attributes_func: GrowthAllocator,
    max_attempts: int = XISUIDAN_MAX_REROLL_ATTEMPTS,
) -> GrowthRerollResult:
    non_growth_bonuses = _resolve_non_growth_attribute_bonuses(guest)
    current_growth = extract_current_guest_growth(guest, non_growth_bonuses=non_growth_bonuses)
    current_total = sum(current_growth.values())
    levels = max(0, guest.level - 1)
    from guests.services.custom_guest import is_custom_guest, resolve_custom_growth_range

    _minimum_growth, maximum_growth = (
        resolve_custom_growth_range(guest)
        if is_custom_guest(guest)
        else _resolve_growth_range(guest.rarity, guest.template.growth_range)
    )
    maximum_total = levels * maximum_growth

    if current_total > maximum_total:
        # Historical guests may have been trained under an older, broader
        # range.  Grandfather their actual total rather than lowering stats;
        # the current range still governs all future normal rolls.
        new_growth = _allocate_growth_at_total(guest, current_total, rng)
        new_total = current_total
    else:
        best_growth = normalize_growth_allocation(allocate_level_up_attributes_func(guest, levels, rng))
        best_total = sum(best_growth.values())

        attempts_remaining = max(0, max_attempts - 1)
        while best_total < current_total and attempts_remaining > 0:
            candidate_growth = normalize_growth_allocation(allocate_level_up_attributes_func(guest, levels, rng))
            candidate_total = sum(candidate_growth.values())
            if candidate_total > best_total:
                best_growth = candidate_growth
                best_total = candidate_total
            attempts_remaining -= 1

        if best_total < current_total:
            new_total = current_total
            new_growth = current_growth
            for _ in range(max(1, max_attempts)):
                candidate_growth = _allocate_growth_at_total(guest, current_total, rng)
                new_growth = candidate_growth
                if current_total == 0 or candidate_growth != current_growth:
                    break
        else:
            new_growth = best_growth
            new_total = best_total

    new_stats = resolve_guest_growth_stats(guest, new_growth, non_growth_bonuses=non_growth_bonuses)
    changes = {
        "force": new_stats["force"] - guest.force,
        "intellect": new_stats["intellect"] - guest.intellect,
        "defense": new_stats["defense"] - guest.defense_stat,
        "agility": new_stats["agility"] - guest.agility,
    }

    guest.force = new_stats["force"]
    guest.intellect = new_stats["intellect"]
    guest.defense_stat = new_stats["defense"]
    guest.agility = new_stats["agility"]
    guest.xisuidan_used += 1
    guest.save(update_fields=["force", "intellect", "defense_stat", "agility", "xisuidan_used"])

    return GrowthRerollResult(
        old_total=current_total,
        new_total=new_total,
        growth_diff=new_total - current_total,
        changes=changes,
    )


def build_growth_reroll_message(guest_name: str, result: GrowthRerollResult) -> str:
    change_parts = []
    for attr, diff in result.changes.items():
        if diff != 0:
            sign = "+" if diff > 0 else ""
            change_parts.append(f"{ATTRIBUTE_LABELS[attr]}{sign}{diff}")

    if result.growth_diff > 0:
        message = f"门客 {guest_name} 洗髓成功！成长点数+{result.growth_diff}（{result.old_total}→{result.new_total}）"
    else:
        message = f"门客 {guest_name} 洗髓完成，成长点数未变（{result.old_total}点），属性重新分配"

    if change_parts:
        message += f"，属性变化：{', '.join(change_parts)}"
    return message


def reset_guest_allocation(guest: "Guest") -> AllocationResetResult:
    allocation_details = {
        "force": guest.allocated_force,
        "intellect": guest.allocated_intellect,
        "defense": guest.allocated_defense,
        "agility": guest.allocated_agility,
    }
    total_allocated = sum(allocation_details.values())
    if total_allocated == 0:
        raise GuestAllocationResetError(guest)

    guest.force -= guest.allocated_force
    guest.intellect -= guest.allocated_intellect
    guest.defense_stat -= guest.allocated_defense
    guest.agility -= guest.allocated_agility
    guest.attribute_points += total_allocated
    guest.allocated_force = 0
    guest.allocated_intellect = 0
    guest.allocated_defense = 0
    guest.allocated_agility = 0
    guest.save(
        update_fields=[
            "force",
            "intellect",
            "defense_stat",
            "agility",
            "attribute_points",
            "allocated_force",
            "allocated_intellect",
            "allocated_defense",
            "allocated_agility",
        ]
    )

    return AllocationResetResult(total_returned=total_allocated, details=allocation_details)


def build_allocation_reset_message(guest_name: str, result: AllocationResetResult) -> str:
    detail_parts = [f"{ATTRIBUTE_LABELS[key]}-{value}" for key, value in result.details.items() if value > 0]
    return f"门客 {guest_name} 洗点成功！返还 {result.total_returned} 属性点（{', '.join(detail_parts)}）"
