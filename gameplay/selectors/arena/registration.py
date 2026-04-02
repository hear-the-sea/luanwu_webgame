from __future__ import annotations

from django.db.models import Count

import gameplay.services.arena.coop_core as arena_coop_core
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaExchangeRecord, ArenaTournament, Manor
from guests.models import GuestStatus

from .common import build_common_context, build_reward_rows, today_coop_participation_stats


def get_active_entry(manor: Manor) -> ArenaEntry | None:
    return (
        ArenaEntry.objects.select_related("tournament")
        .prefetch_related("entry_guests__guest")
        .filter(manor=manor, tournament__status__in=[ArenaTournament.Status.RECRUITING, ArenaTournament.Status.RUNNING])
        .order_by("-joined_at")
        .first()
    )


def get_active_coop_entry(manor: Manor):
    return (
        ArenaCoopEntry.objects.select_related("event")
        .prefetch_related("entry_guests__guest")
        .filter(
            manor=manor,
            status=ArenaCoopEntry.Status.REGISTERED,
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ],
        )
        .order_by("-joined_at", "-id")
        .first()
    )


def get_arena_coop_summary_context(manor: Manor) -> dict:
    coop_today_participations, coop_remaining_daily = today_coop_participation_stats(manor)
    active_entry = get_active_coop_entry(manor)
    available_guests = manor.guests.none()
    selected_guest_ids: set[int] = set()

    if active_entry:
        selected_guest_ids = set(active_entry.entry_guests.values_list("guest_id", flat=True))
    elif coop_remaining_daily > 0:
        available_guests = (
            manor.guests.select_related("template").filter(status=GuestStatus.IDLE).order_by("-level", "id")
        )

    return {
        "arena_coop_today_participations": coop_today_participations,
        "arena_coop_remaining_daily": coop_remaining_daily,
        "arena_coop_daily_limit": arena_coop_core.ARENA_COOP_DAILY_PARTICIPATION_LIMIT,
        "arena_coop_event": {
            "name": "围攻光明顶",
            "subtitle": "5 人共斗",
            "description": "张无忌率队镇守光明顶，只有大后期满配队伍才有机会通关。",
            "player_limit": arena_coop_core.ARENA_COOP_PLAYER_LIMIT,
            "guest_limit_per_entry": arena_coop_core.ARENA_COOP_MAX_GUESTS_PER_ENTRY,
            "daily_limit": arena_coop_core.ARENA_COOP_DAILY_PARTICIPATION_LIMIT,
        },
        "arena_coop_active_entry": active_entry,
        "arena_coop_recruiting_event": ArenaCoopEvent.objects.filter(status=ArenaCoopEvent.Status.RECRUITING).first(),
        "arena_coop_available_guests": available_guests,
        "arena_coop_selected_guest_ids": selected_guest_ids,
    }


def get_arena_registration_context(manor: Manor) -> dict:
    context = build_common_context(manor)
    context.update(get_arena_coop_summary_context(manor))
    active_entry = get_active_entry(manor)

    context["active_entry"] = active_entry
    context["recruiting_tournament"] = (
        ArenaTournament.objects.filter(status=ArenaTournament.Status.RECRUITING)
        .annotate(entry_count=Count("entries"))
        .order_by("created_at")
        .first()
    )
    selected_guest_ids: set[int] = set()
    available_guests = manor.guests.none()

    if active_entry:
        selected_guest_ids = set(active_entry.entry_guests.values_list("guest_id", flat=True))
    elif context["remaining_daily"] > 0:
        available_guests = (
            manor.guests.select_related("template").filter(status=GuestStatus.IDLE).order_by("-level", "id")
        )

    context["available_guests"] = available_guests
    context["selected_guest_ids"] = selected_guest_ids
    return context


def get_arena_exchange_context(manor: Manor) -> dict:
    context = build_common_context(manor)
    context["reward_rows"] = build_reward_rows(manor)
    context["recent_exchange_records"] = ArenaExchangeRecord.objects.filter(manor=manor).order_by("-created_at")[:15]
    return context
