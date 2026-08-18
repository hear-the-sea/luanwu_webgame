from __future__ import annotations

from typing import Any

from gameplay.constants import BuildingKeys
from gameplay.services.manor.troop_bank import (
    TROOP_BANK_CAPACITY,
    get_troop_bank_remaining_space,
    get_troop_bank_used_space,
)
from gameplay.services.manor.troop_capacity import (
    MANOR_TROOP_CAPACITY,
    get_manor_troop_remaining_space,
    get_manor_troop_used_space,
)
from gameplay.services.recruitment.recruitment import (
    get_active_recruitments,
    get_player_troops,
    get_recruitment_options,
    has_active_recruitment,
)
from gameplay.services.technology import get_troop_class_for_key

RECRUITMENT_CATEGORY_LABELS: dict[str, str] = {
    "dao": "刀系",
    "qiang": "枪系",
    "jian": "剑系",
    "quan": "拳系",
    "gong": "弓系",
    "scout": "探子",
    "other": "其他",
}


def _build_recruitment_categories(available_classes: set[str]) -> list[dict[str, str]]:
    categories: list[dict[str, str]] = [{"key": "all", "name": "全部"}]
    ordered = ["dao", "qiang", "jian", "quan", "gong", "scout", "other"]
    used = {"all"}

    for class_key in ordered:
        if class_key in available_classes:
            categories.append({"key": class_key, "name": RECRUITMENT_CATEGORY_LABELS.get(class_key, "其他")})
            used.add(class_key)

    for class_key in sorted(available_classes):
        if class_key not in used:
            categories.append({"key": class_key, "name": RECRUITMENT_CATEGORY_LABELS.get(class_key, "其他")})
    return categories


def get_troop_recruitment_context(manor: Any, *, selected_category: str) -> dict[str, Any]:
    training_level = manor.get_building_level(BuildingKeys.LIANGGONG_CHANG)
    citang_level = manor.get_building_level(BuildingKeys.CITANG)
    is_recruiting = has_active_recruitment(manor)

    training_multiplier = manor.guard_training_speed_multiplier
    citang_multiplier = manor.citang_recruitment_speed_multiplier
    total_multiplier = training_multiplier * citang_multiplier
    speed_bonus_percent = int((total_multiplier - 1) * 100)

    recruitment_options = get_recruitment_options(manor)
    available_classes: set[str] = set()
    for troop in recruitment_options:
        troop_class = get_troop_class_for_key(str(troop.get("key", ""))) or "other"
        troop["troop_class"] = troop_class
        available_classes.add(troop_class)

    recruitment_categories = _build_recruitment_categories(available_classes)
    valid_category_keys = {item["key"] for item in recruitment_categories}
    normalized_category = (selected_category or "all").strip() or "all"
    if normalized_category not in valid_category_keys:
        normalized_category = "all"
    if normalized_category != "all":
        recruitment_options = [
            troop for troop in recruitment_options if troop.get("troop_class") == normalized_category
        ]

    manor_troop_used = get_manor_troop_used_space(manor)
    manor_troop_remaining = get_manor_troop_remaining_space(manor)
    troop_bank_used = get_troop_bank_used_space(manor)
    troop_bank_remaining = get_troop_bank_remaining_space(manor)

    return {
        "training_level": training_level,
        "citang_level": citang_level,
        "can_recruit": training_level >= 1,
        "recruitment_options": recruitment_options,
        "recruitment_categories": recruitment_categories,
        "current_category": normalized_category,
        "active_recruitments": get_active_recruitments(manor),
        "player_troops": get_player_troops(manor),
        "speed_bonus_percent": speed_bonus_percent,
        "training_multiplier": training_multiplier,
        "citang_multiplier": citang_multiplier,
        "is_recruiting": is_recruiting,
        "manor_troop_used": manor_troop_used,
        "manor_troop_capacity": MANOR_TROOP_CAPACITY,
        "manor_troop_remaining": manor_troop_remaining,
        "troop_bank_used": troop_bank_used,
        "troop_bank_capacity": TROOP_BANK_CAPACITY,
        "troop_bank_remaining": troop_bank_remaining,
    }
