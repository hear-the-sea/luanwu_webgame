from __future__ import annotations

from typing import Any

from common.constants.resources import ResourceType
from gameplay.constants import BUILDING_MAX_LEVELS
from gameplay.models import BuildingCategory
from gameplay.services.city_defense import is_city_defense_key, prepare_city_defense_display
from gameplay.services.manor.core import get_rename_card_count
from gameplay.services.manor.prestige import get_prestige_progress
from gameplay.services.ranking import get_ranking_with_player_context


def _normalize_dashboard_category(raw_category: str | None) -> str:
    category = (raw_category or "resource").strip()
    if category not in [choice[0] for choice in BuildingCategory.choices]:
        return "resource"
    return category


def _prepare_building_display(buildings: Any) -> list[Any]:
    prepared: list[Any] = []
    for building in buildings:
        max_level = BUILDING_MAX_LEVELS.get(building.building_type.key)
        is_max_level = max_level is not None and building.level >= max_level
        building.max_level = max_level
        building.is_max_level = is_max_level
        building.can_upgrade = not building.is_upgrading and not is_max_level
        building.next_level_cost_display = None if is_max_level else building.next_level_cost()
        building.is_city_defense = is_city_defense_key(building.building_type.key)
        if building.is_city_defense:
            prepare_city_defense_display(building)
        prepared.append(building)
    return prepared


def get_dashboard_context(manor: Any, *, category: str) -> dict[str, Any]:
    normalized_category = _normalize_dashboard_category(category)
    buildings = (
        manor.buildings.select_related("building_type")
        .filter(building_type__category=normalized_category)
        .order_by("building_type__name")
    )
    return {
        "current_category": normalized_category,
        "category_label": dict(BuildingCategory.choices).get(normalized_category, "资源生产"),
        "categories": BuildingCategory.choices,
        "buildings": _prepare_building_display(buildings),
        "resource_labels": dict(ResourceType.choices),
    }


def get_settings_page_context(manor: Any) -> dict[str, Any]:
    return {
        "rename_card_count": get_rename_card_count(manor),
    }


def get_ranking_page_context(manor: Any) -> dict[str, Any]:
    ranking_data = get_ranking_with_player_context(manor)
    prestige_info = get_prestige_progress(manor)
    return {
        "ranking": ranking_data["ranking"],
        "player_rank": ranking_data["player_rank"],
        "player_in_ranking": ranking_data["player_in_ranking"],
        "total_players": ranking_data["total_players"],
        "prestige_info": prestige_info,
    }
