from __future__ import annotations

from typing import Any

from django.http import JsonResponse

from core.utils import json_success


def resolve_attack_fields_from_info(
    info: dict[str, Any],
    viewer_manor: Any,
    target_manor: Any,
    *,
    fallback_fn,
) -> tuple[bool, str]:
    can_attack_value = info.get("can_attack")
    attack_reason_value = info.get("attack_reason")
    if isinstance(can_attack_value, bool) and isinstance(attack_reason_value, str):
        return can_attack_value, attack_reason_value
    return fallback_fn(viewer_manor, target_manor)


def build_raid_status_response_payload(
    *,
    active_raids: list[Any],
    active_scouts: list[Any],
    incoming_raids: list[Any],
    refreshed: int | None = None,
) -> JsonResponse:
    payload: dict[str, Any] = {
        "active_raids": [
            {
                "id": raid.id,
                "target_name": raid.defender.display_name,
                "status": raid.status,
                "status_display": raid.get_status_display(),
                "time_remaining": raid.time_remaining,
                "can_retreat": raid.can_retreat,
            }
            for raid in active_raids
        ],
        "active_scouts": [
            {
                "id": scout.id,
                "target_name": scout.defender.display_name,
                "time_remaining": scout.time_remaining,
                "success_rate": round(scout.success_rate * 100),
            }
            for scout in active_scouts
        ],
        "incoming_raids": [
            {
                "id": raid.id,
                "attacker_name": raid.attacker.display_name,
                "attacker_location": raid.attacker.location_display,
                "time_remaining": raid.time_remaining,
            }
            for raid in incoming_raids
        ],
    }
    if refreshed is not None:
        payload["refreshed"] = max(0, int(refreshed))
        payload["pending"] = any(
            row["time_remaining"] <= 0
            for group in (payload["active_raids"], payload["active_scouts"], payload["incoming_raids"])
            for row in group
        )
    return json_success(**payload)
