from __future__ import annotations

from typing import Any

from core.utils.time_scale import get_time_scale
from guests.models import GuestStatus

from ..services.raid import (
    can_attack_target,
    get_active_raids,
    get_active_scouts,
    get_incoming_raids,
    get_manor_public_info,
    get_protection_status,
    get_scout_count,
)
from ..services.recruitment.queries import get_player_troops


def _resolve_attack_fields(target_info: dict[str, Any], manor, target_manor) -> tuple[bool, str]:
    can_attack_value = target_info.get("can_attack")
    attack_reason_value = target_info.get("attack_reason")
    if isinstance(can_attack_value, bool) and isinstance(attack_reason_value, str):
        return can_attack_value, attack_reason_value
    return can_attack_target(manor, target_manor)


def get_map_context(manor, selected_region: str, search_query: str) -> dict:
    return {
        "manor": manor,
        "selected_region": selected_region,
        "search_query": search_query,
        "protection_status": get_protection_status(manor),
        "active_raids": get_active_raids(manor),
        "active_scouts": get_active_scouts(manor),
        "incoming_raids": get_incoming_raids(manor),
        "scout_count": get_scout_count(manor),
        "player_troops": get_player_troops(manor),
    }


def get_raid_config_context(manor, target_manor) -> dict:
    target_info = get_manor_public_info(target_manor, viewer=manor)
    can_attack, attack_reason = _resolve_attack_fields(target_info, manor, target_manor)
    available_guests = list(
        manor.guests.filter(status=GuestStatus.IDLE).select_related("template").order_by("-level", "template__name")
    )
    return {
        "manor": manor,
        "target_manor": target_manor,
        "target_info": target_info,
        "can_attack": can_attack,
        "attack_reason": attack_reason,
        "available_guests": available_guests,
        "player_troops": get_player_troops(manor),
        "scout_count": get_scout_count(manor),
        "max_squad_size": manor.max_squad_size,
        "pvp_time_scale_multiplier": get_time_scale().multiplier,
    }
