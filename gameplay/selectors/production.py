from __future__ import annotations

from typing import Any

from django.core.paginator import Paginator

from gameplay.constants import UIConstants
from gameplay.services.buildings import forge as forge_service
from gameplay.services.buildings.forge_helpers import DECOMPOSE_CATEGORIES, DECOMPOSE_WEAPON_CATEGORIES
from gameplay.services.buildings.ranch import (
    get_active_livestock_productions,
    get_livestock_options,
    get_max_livestock_quantity,
    get_ranch_speed_bonus,
    has_active_livestock_production,
)
from gameplay.services.buildings.smithy import (
    get_active_smelting_productions,
    get_max_smelting_quantity,
    get_metal_options,
    get_smithy_speed_bonus,
    has_active_smelting_production,
)
from gameplay.services.buildings.stable import (
    get_active_productions,
    get_horse_options,
    get_max_production_quantity,
    get_stable_speed_bonus,
    has_active_production,
)
from gameplay.services.technology import get_player_technology_level
from gameplay.views.production_helpers import (
    annotate_blueprint_synthesis_options,
    build_categories_with_all,
    get_filtered_equipment_options,
    normalize_forge_category,
    resolve_decompose_category,
)

SMITHY_CATEGORIES = (
    {"key": "metal", "label": "金属"},
    {"key": "medicine", "label": "药品"},
)


def get_stable_page_context(manor: Any) -> dict[str, Any]:
    from gameplay.services.inventory.core import get_warehouse_grain_quantity

    speed_bonus = get_stable_speed_bonus(manor)
    return {
        "horse_options": get_horse_options(manor),
        "active_productions": get_active_productions(manor),
        "speed_bonus": speed_bonus,
        "speed_bonus_percent": int(speed_bonus * 100),
        "horsemanship_level": get_player_technology_level(manor, "horsemanship"),
        "max_production_quantity": get_max_production_quantity(manor),
        "is_producing": has_active_production(manor),
        "warehouse_grain_quantity": get_warehouse_grain_quantity(manor),
    }


def get_ranch_page_context(manor: Any) -> dict[str, Any]:
    from gameplay.services.inventory.core import get_warehouse_grain_quantity

    speed_bonus = get_ranch_speed_bonus(manor)
    return {
        "livestock_options": get_livestock_options(manor),
        "active_productions": get_active_livestock_productions(manor),
        "speed_bonus": speed_bonus,
        "speed_bonus_percent": int(speed_bonus * 100),
        "animal_husbandry_level": get_player_technology_level(manor, "animal_husbandry"),
        "max_livestock_quantity": get_max_livestock_quantity(manor),
        "is_producing": has_active_livestock_production(manor),
        "warehouse_grain_quantity": get_warehouse_grain_quantity(manor),
    }


def _normalize_smithy_category(raw_category: str | None) -> str:
    category = (raw_category or "metal").strip()
    available = {item["key"] for item in SMITHY_CATEGORIES}
    return category if category in available else "metal"


def get_smithy_page_context(manor: Any, *, current_category: str | None = None) -> dict[str, Any]:
    speed_bonus = get_smithy_speed_bonus(manor)
    normalized_category = _normalize_smithy_category(current_category)
    metal_options = get_metal_options(manor)
    return {
        "current_smithy_category": normalized_category,
        "smithy_categories": SMITHY_CATEGORIES,
        "metal_options": metal_options,
        "selected_metal_options": [option for option in metal_options if option.get("category") == normalized_category],
        "active_productions": get_active_smelting_productions(manor),
        "speed_bonus": speed_bonus,
        "speed_bonus_percent": int(speed_bonus * 100),
        "smelting_level": get_player_technology_level(manor, "smelting"),
        "max_smelting_quantity": get_max_smelting_quantity(manor),
        "is_producing": has_active_smelting_production(manor),
    }


def _normalize_forge_mode(raw_mode: str | None, *, default: str = "synthesize") -> str:
    mode = (raw_mode or default).strip()
    if mode not in {"synthesize", "decompose"}:
        return default
    return mode


def get_forge_page_context(
    manor: Any,
    *,
    current_mode: str,
    current_category: str,
    page: str | int,
    items_per_page: int = UIConstants.FORGE_ITEMS_PER_PAGE,
    decompose_items_per_page: int = 9,
    blueprint_items_per_page: int = UIConstants.FORGE_BLUEPRINTS_PER_PAGE,
    blueprint_page: str | int = 1,
) -> dict[str, Any]:
    forging_level = get_player_technology_level(manor, "forging")
    max_quantity = forge_service.get_max_forging_quantity(manor)
    is_forging = forge_service.has_active_forging(manor)
    normalized_mode = _normalize_forge_mode(current_mode, default="synthesize")

    active_categories = DECOMPOSE_CATEGORIES
    normalized_category = normalize_forge_category(
        current_category or "all",
        active_categories=active_categories,
        weapon_categories=DECOMPOSE_WEAPON_CATEGORIES,
    )
    equipment_list = get_filtered_equipment_options(
        manor=manor,
        current_category=normalized_category,
        weapon_categories=DECOMPOSE_WEAPON_CATEGORIES,
        get_equipment_options=forge_service.get_equipment_options,
    )
    paginator = Paginator(equipment_list, items_per_page)
    page_obj = paginator.get_page(page)

    blueprint_synthesis_options = annotate_blueprint_synthesis_options(
        forge_service.get_blueprint_synthesis_options(manor),
        active_categories=active_categories,
        current_category=normalized_category,
        infer_equipment_category=forge_service.infer_equipment_category,
        to_decompose_category=forge_service.to_decompose_category,
    )
    blueprint_paginator = Paginator(blueprint_synthesis_options, blueprint_items_per_page)
    blueprint_page_obj = blueprint_paginator.get_page(blueprint_page)

    decompose_category = resolve_decompose_category(normalized_category)
    decomposable_equipment = forge_service.get_decomposable_equipment_options(manor, category=decompose_category)
    decompose_paginator = Paginator(decomposable_equipment, decompose_items_per_page)
    decompose_page_obj = decompose_paginator.get_page(page)
    speed_bonus = forge_service.get_forge_speed_bonus(manor)

    return {
        "current_mode": normalized_mode,
        "equipment_categories": build_categories_with_all(active_categories),
        "current_category": normalized_category,
        "equipment_list": page_obj,
        "page_obj": page_obj,
        "decompose_page_obj": decompose_page_obj,
        "active_forgings": forge_service.get_active_forgings(manor),
        "blueprint_synthesis_options": blueprint_synthesis_options,
        "blueprint_page_obj": blueprint_page_obj,
        "decomposable_equipment": decompose_page_obj,
        "speed_bonus": speed_bonus,
        "speed_bonus_percent": int(speed_bonus * 100),
        "forging_level": forging_level,
        "max_forging_quantity": max_quantity,
        "is_forging": is_forging,
    }
