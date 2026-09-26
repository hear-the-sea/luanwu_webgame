from __future__ import annotations

from typing import Any

from django.utils import timezone

from common.constants.resources import ResourceType
from gameplay.constants import BUILDING_MAX_LEVELS
from gameplay.models import BuildingCategory, PlayerTechnology
from gameplay.services.action_points import ACTION_POINT_BUILDING_UPGRADE_COST
from gameplay.services.city_defense import is_city_defense_key, prepare_city_defense_display
from gameplay.services.manor.core import (
    calculate_building_upgrade_duration,
    calculate_building_upgrade_resource_cost,
    get_rename_card_count,
)
from gameplay.services.manor.prestige import get_prestige_progress
from gameplay.services.ranking import get_ranking_with_player_context
from gameplay.services.technology import get_tech_bonus_from_levels
from guests.models import GuestStatus


def _normalize_dashboard_category(raw_category: str | None) -> str:
    category = (raw_category or "resource").strip()
    if category not in [choice[0] for choice in BuildingCategory.choices]:
        return "resource"
    return category


def _prepare_building_display(
    buildings: Any,
    *,
    time_reduction: float,
    building_cost_reduction: float,
) -> list[Any]:
    prepared: list[Any] = []
    now = timezone.now()
    for building in buildings:
        max_level = BUILDING_MAX_LEVELS.get(building.building_type.key)
        is_max_level = max_level is not None and building.level >= max_level
        building.max_level = max_level
        building.is_max_level = is_max_level
        building.can_upgrade = not building.is_upgrading and not is_max_level
        if is_max_level:
            building.next_level_base_cost_display = None
            building.next_level_cost_display = None
            building.next_level_cost_reduction = 0.0
            building.next_level_cost_reduction_percent = 0.0
        else:
            base_cost = building.next_level_cost()
            building.next_level_base_cost_display = {
                str(resource): int(amount) for resource, amount in base_cost.items()
            }
            building.next_level_cost_display = calculate_building_upgrade_resource_cost(
                base_cost,
                building_cost_reduction,
            )
            building.next_level_cost_reduction = building_cost_reduction
            building.next_level_cost_reduction_percent = building_cost_reduction * 100
        if building.can_upgrade:
            building.upgrade_duration = calculate_building_upgrade_duration(
                building.next_level_duration(), time_reduction
            )
        if building.is_upgrading and building.upgrade_complete_at:
            building.upgrade_time_remaining = max(0, (building.upgrade_complete_at - now).total_seconds())
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
    technology_levels = {
        str(tech_key): int(level)
        for tech_key, level in PlayerTechnology.objects.filter(manor_id=manor.pk).values_list("tech_key", "level")
    }
    building_cost_reduction = get_tech_bonus_from_levels(technology_levels, "building_cost_reduction")
    context = {
        "current_category": normalized_category,
        "category_label": dict(BuildingCategory.choices).get(normalized_category, "资源生产"),
        "categories": BuildingCategory.choices,
        "building_upgrade_action_point_cost": ACTION_POINT_BUILDING_UPGRADE_COST,
        "buildings": _prepare_building_display(
            buildings,
            time_reduction=float(manor.citang_building_time_reduction),
            building_cost_reduction=building_cost_reduction,
        ),
        "resource_labels": dict(ResourceType.choices),
    }
    if normalized_category == BuildingCategory.SPECIAL:
        context["xiaoyao_occupied"] = manor.guests.filter(status=GuestStatus.VACATION).count()
    return context


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
