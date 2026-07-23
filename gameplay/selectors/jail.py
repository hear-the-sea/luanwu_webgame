from __future__ import annotations

from typing import Any

from django.utils import timezone

from gameplay.constants import PVPConstants, get_raid_capture_guest_rate
from gameplay.models import JailInteractionLog
from gameplay.services.jail import list_held_prisoners, list_oath_bonds
from gameplay.services.jail_persuasion.eligibility import RECRUITMENT_MODES, recruitment_offer
from gameplay.services.jail_persuasion.interactions import daily_action_limit
from gameplay.services.jail_persuasion.milestones import pending_milestone
from gameplay.services.jail_persuasion.profiles import (
    METHOD_MIGHT,
    METHOD_ORDER,
    METHOD_REASON,
    get_clue_keys,
    load_jail_persuasion_profiles,
    render_copy,
)
from guests.models import Guest, GuestStatus
from guests.query_utils import guest_template_rarity_rank_case

RESOURCE_COST_LABELS = {
    "silver": "银两",
    "grain": "粮食",
    "gold_bar": "根金条",
}
RESOURCE_COST_ORDER = ("silver", "grain", "gold_bar")


def _format_method_cost(cost: dict[str, Any]) -> str:
    parts: list[str] = []
    for resource in RESOURCE_COST_ORDER:
        amount = int(cost.get(resource, 0) or 0)
        if amount <= 0:
            continue
        parts.append(f"{amount:,} {RESOURCE_COST_LABELS[resource]}")
    return " · ".join(parts) if parts else "无额外消耗"


def _speaker_options(manor: Any, prisoner: Any, *, today: Any) -> dict[str, list[dict[str, Any]]]:
    speakers = list(
        Guest.objects.filter(manor=manor, status=GuestStatus.IDLE).select_related("template").order_by("id")
    )
    used_speaker_ids = set(
        JailInteractionLog.objects.filter(captor=manor, usage_date=today, speaker__isnull=False).values_list(
            "speaker_id", flat=True
        )
    )
    used_methods = set(
        JailInteractionLog.objects.filter(
            prisoner=prisoner,
            usage_date=today,
            method__in=[METHOD_REASON, METHOD_MIGHT],
        ).values_list("method", flat=True)
    )
    result: dict[str, list[dict[str, Any]]] = {METHOD_REASON: [], METHOD_MIGHT: []}
    for method in (METHOD_REASON, METHOD_MIGHT):
        for speaker in speakers:
            if method == METHOD_REASON:
                speaker_value = int(speaker.template.base_intellect)
            else:
                speaker_value = int(speaker.template.base_attack)
            result[method].append(
                {
                    "id": int(speaker.id),
                    "name": speaker.display_name,
                    "archetype": str(speaker.template.archetype),
                    "speaker_base_value": speaker_value,
                    "used_today": speaker.id in used_speaker_ids,
                    "method_used_today": method in used_methods,
                    "available": speaker.id not in used_speaker_ids and method not in used_methods,
                }
            )
    return result


def _serialize_pending_milestone(prisoner: Any) -> dict[str, Any] | None:
    event = pending_milestone(prisoner)
    if event is None:
        return None
    return {
        "key": event.key,
        "title": event.title,
        "prompt": event.prompt,
        "method": event.method,
        "stage": event.stage,
        "threshold": event.threshold,
        "choices": [
            {
                "key": choice.key,
                "label": choice.label,
            }
            for choice in event.choices
        ],
    }


def build_prisoner_state(
    manor: Any,
    prisoner: Any,
    *,
    today: Any | None = None,
    known_recruitment_attempted_today: bool | None = None,
) -> dict[str, Any]:
    local_date = today or timezone.localdate()
    profile = load_jail_persuasion_profiles()
    observed = prisoner.observed_at is not None
    interactions_today = int(prisoner.interactions_today or 0) if prisoner.interaction_date == local_date else 0
    action_limit = daily_action_limit(manor)
    clue_keys = (
        get_clue_keys(
            stance_method=str(prisoner.stance_method or ""),
            revealed_level=int(prisoner.revealed_level or 0),
            prisoner_id=int(prisoner.id),
            template_key=str(prisoner.guest_template.key),
            captured_at=prisoner.captured_at,
        )
        if observed
        else []
    )
    copy_params = {"prisoner_name": prisoner.display_name}
    history = [
        {
            "id": int(log.id),
            "method": log.method,
            "outcome": log.outcome,
            "text": render_copy(log.copy_key, dict(log.copy_params or {})),
            "heart_delta": int(log.heart_after) - int(log.heart_before),
            "affinity_delta": int(log.affinity_after) - int(log.affinity_before),
            "speaker_name": log.speaker_name_snapshot,
            "speaker_loyalty_delta": (
                int(log.speaker_loyalty_after) - int(log.speaker_loyalty_before)
                if log.speaker_loyalty_before is not None and log.speaker_loyalty_after is not None
                else 0
            ),
            "created_at": log.created_at.isoformat() if log.created_at else "",
        }
        for log in JailInteractionLog.objects.filter(prisoner=prisoner).order_by("-created_at", "-id")[:3]
    ]
    offers = {
        mode: {
            "mode": mode,
            "eligible": offer.eligible,
            "gold_cost": offer.gold_cost,
            "initial_loyalty": offer.initial_loyalty,
            "heart_max": offer.heart_max,
            "affinity_min": offer.affinity_min,
            "label": {
                "standard": "普通收编",
                "negotiated": "权宜归附",
                "heartfelt": "心悦诚服",
            }[mode],
        }
        for mode in RECRUITMENT_MODES
        for offer in [recruitment_offer(prisoner, mode)]
    }
    pending = _serialize_pending_milestone(prisoner)
    recruitment_attempted_today = known_recruitment_attempted_today
    if recruitment_attempted_today is None:
        recruitment_attempted_today = JailInteractionLog.objects.filter(
            prisoner=prisoner,
            usage_date=local_date,
            attempt_scope="recruitment",
        ).exists()
    return {
        "id": int(prisoner.id),
        "name": prisoner.display_name,
        "template_key": str(prisoner.guest_template.key),
        "rarity": str(prisoner.guest_template.rarity),
        "rarity_label": str(prisoner.guest_template.get_rarity_display()),
        "archetype": str(prisoner.guest_template.archetype),
        "morality": int(prisoner.guest_template.default_morality),
        "original_level": int(prisoner.original_level),
        "captured_loyalty": int(prisoner.captured_loyalty),
        "heart": int(prisoner.loyalty),
        "affinity": int(prisoner.affinity),
        "captured_at": prisoner.captured_at.isoformat() if prisoner.captured_at else "",
        "original_manor": getattr(getattr(prisoner, "original_manor", None), "display_name", ""),
        "observed": observed,
        "revealed_level": int(prisoner.revealed_level or 0),
        "clues": [{"key": key, "text": render_copy(key, copy_params)} for key in clue_keys],
        "daily_limit": action_limit,
        "interactions_today": interactions_today,
        "remaining_actions": max(0, action_limit - interactions_today),
        "methods": {
            method: {
                "key": method,
                "label": profile["methods"][method]["label"],
                "cost": dict(profile["methods"][method]["cost"]),
                "cost_text": _format_method_cost(dict(profile["methods"][method]["cost"])),
            }
            for method in METHOD_ORDER
        },
        "speaker_options": _speaker_options(manor, prisoner, today=local_date),
        "pending_milestone": pending,
        "recruitment_offers": offers,
        "recruitment_attempted_today": recruitment_attempted_today,
        "history": history,
        "can_interact": observed and pending is None and interactions_today < action_limit,
    }


def build_prisoner_states(
    manor: Any,
    prisoners: list[Any],
    *,
    today: Any | None = None,
) -> list[dict[str, Any]]:
    local_date = today or timezone.localdate()
    prisoner_ids = [int(prisoner.id) for prisoner in prisoners]
    attempted_prisoner_ids = set(
        JailInteractionLog.objects.filter(
            prisoner_id__in=prisoner_ids,
            usage_date=local_date,
            attempt_scope="recruitment",
        ).values_list("prisoner_id", flat=True)
    )
    return [
        build_prisoner_state(
            manor,
            prisoner,
            today=local_date,
            known_recruitment_attempted_today=int(prisoner.id) in attempted_prisoner_ids,
        )
        for prisoner in prisoners
    ]


def get_jail_page_context(manor: Any) -> dict[str, Any]:
    prisoners = list_held_prisoners(manor)
    prisoner_states = build_prisoner_states(manor, prisoners)
    return {
        "jail_capacity": int(getattr(manor, "jail_capacity", 0) or 0),
        "prisoners": prisoners,
        "prisoner_states": prisoner_states,
        "capture_rate_percent": int(round(get_raid_capture_guest_rate() * 100)),
        "recruit_loyalty_threshold": int(PVPConstants.JAIL_RECRUIT_LOYALTY_THRESHOLD),
        "recruit_cost_gold_bar": int(PVPConstants.JAIL_RECRUIT_GOLD_BAR_COST),
    }


def get_oath_grove_page_context(manor: Any) -> dict[str, Any]:
    bonds = list_oath_bonds(manor)
    oathed_ids = {bond.guest_id for bond in bonds}
    available_guests = (
        manor.guests.select_related("template")
        .exclude(id__in=oathed_ids)
        .annotate(_template_rarity_rank=guest_template_rarity_rank_case("template__rarity"))
        .order_by("-_template_rarity_rank", "-level", "id")
    )
    return {
        "oath_capacity": int(getattr(manor, "oath_capacity", 0) or 0),
        "bonds": bonds,
        "available_guests": list(available_guests)[:50],
    }
