from __future__ import annotations

import logging
from typing import Any

from django.http import HttpRequest

from gameplay.models import MissionTemplate, ResourceType
from gameplay.services.inventory.core import get_item_quantity
from gameplay.services.manor.core import project_manor_activity_for_read
from gameplay.services.missions_impl.attempts import bulk_get_mission_extra_attempts, bulk_mission_attempts_today
from gameplay.services.recruitment.recruitment import get_player_troops
from gameplay.utils.template_loader import get_item_templates_by_keys, get_troop_templates_by_keys
from gameplay.views.read_helpers import get_prepared_manor_for_read
from guests.models import GuestStatus, GuestTemplate, SkillBook

from . import mission_helpers

logger = logging.getLogger(__name__)


def build_troop_config() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    return mission_helpers.build_troop_config()


def build_task_board_context(request: HttpRequest) -> dict[str, Any]:
    manor = get_prepared_manor_for_read(
        request,
        project_fn=project_manor_activity_for_read,
        logger=logger,
        source="task_board_view",
    )
    missions = list(MissionTemplate.objects.all().order_by("id"))
    missions_by_key = {mission.key: mission for mission in missions}
    attempts = bulk_mission_attempts_today(manor, missions)
    extra_attempts = bulk_get_mission_extra_attempts(manor, missions)
    enemy_keys, troop_keys, drop_keys = mission_helpers.collect_mission_asset_keys(missions)
    guest_templates = {
        tpl.key: tpl for tpl in GuestTemplate.objects.filter(key__in=enemy_keys).only("key", "name", "avatar", "rarity")
    }
    guest_labels = {key: tpl.name for key, tpl in guest_templates.items()}

    troop_templates_objs = get_troop_templates_by_keys(troop_keys)
    item_templates = get_item_templates_by_keys(drop_keys)
    loot_labels = {key: tpl.name for key, tpl in item_templates.items()}
    loot_rarities = {key: (tpl.rarity or "default") for key, tpl in item_templates.items()}
    book_labels = {book.key: book.name for book in SkillBook.objects.filter(key__in=drop_keys)}
    loot_labels.update(book_labels)

    mission_data = mission_helpers.build_mission_data(missions, attempts, extra_attempts)
    selected_key = request.GET.get("mission")
    selected_mission, selected_attempts, selected_daily_limit, selected_remaining = (
        mission_helpers.build_selection_summary(selected_key, missions_by_key, attempts, extra_attempts)
    )
    active_tab = selected_mission.difficulty if selected_mission else MissionTemplate.Difficulty.JUNIOR
    available_guests = (
        manor.guests.filter(status=GuestStatus.IDLE)
        .select_related("template")
        .only(
            "id",
            "level",
            "current_hp",
            "status",
            "custom_name",
            "hp_bonus",
            "defense_stat",
            "template__id",
            "template__key",
            "template__name",
            "template__avatar",
            "template__rarity",
        )
    )

    mission_card_count = get_item_quantity(manor, mission_helpers.MISSION_CARD_KEY)
    troop_templates, config_items = build_troop_config()

    context: dict[str, Any] = {
        "manor": manor,
        "missions": missions,
        "attempts_today": attempts,
        "mission_data": mission_data,
        "selected_mission": selected_mission,
        "selected_attempts": selected_attempts,
        "selected_remaining": selected_remaining,
        "selected_daily_limit": selected_daily_limit,
        "active_tab": active_tab,
        "mission_card_count": mission_card_count,
        "selected_drop_items": [],
        "selected_probability_drop_items": [],
        "selected_entry_cost_items": [],
        "can_afford_entry_cost": True,
        "available_guests": available_guests,
        "troop_config": config_items,
        "player_troops": get_player_troops(manor),
        "guest_labels": guest_labels,
        "guest_templates": guest_templates,
        "troop_templates_objs": troop_templates_objs,
        "troop_labels": {key: data.get("label", key) for key, data in troop_templates.items()},
        "max_squad": getattr(manor, "max_squad_size", 5),
    }

    drop_labels = dict(ResourceType.choices)
    drop_labels.update(loot_labels)
    context["resource_labels"] = drop_labels

    if selected_mission:
        guaranteed_drops, probability_drops = mission_helpers.build_drop_lists(
            selected_mission,
            drop_labels,
            item_templates,
            book_labels,
            loot_rarities,
        )
        context["selected_drop_items"] = guaranteed_drops
        context["selected_probability_drop_items"] = probability_drops
        entry_cost_items, can_afford_entry_cost = mission_helpers.build_entry_cost_list(
            selected_mission,
            item_templates,
            manor,
            get_item_quantity_func=get_item_quantity,
        )
        context["selected_entry_cost_items"] = entry_cost_items
        context["can_afford_entry_cost"] = can_afford_entry_cost

    return context
