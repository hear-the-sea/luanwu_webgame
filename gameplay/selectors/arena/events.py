from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
import gameplay.services.arena.core as arena_core
import gameplay.services.arena.wheel_core as arena_wheel_core
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaTournament,
    ArenaWheelEntry,
    ArenaWheelEvent,
    Manor,
)

from .common import build_common_context, running_row_sort_key


def build_running_tournament_card_row(row: dict) -> dict:
    tournament = row["tournament"]
    return {
        "title": f"天下布武 #{tournament.id}",
        "summary": f"第 {tournament.current_round} 轮 | 存活 {tournament.active_entries}/{tournament.total_entries}",
        "badge_text": "我已参赛" if row["is_mine"] else "",
        "badge_class": "rarity-blue",
        "countdown_at": tournament.next_round_at,
        "countdown_label": "下一轮：",
        "action_url": reverse("gameplay:arena_event_detail", args=[tournament.id]),
        "action_label": "查看赛事详情",
    }


def get_arena_events_context(manor: Manor) -> dict:
    context = build_common_context(manor)
    current_time = timezone.now()
    running_tournaments = list(
        ArenaTournament.objects.filter(status=ArenaTournament.Status.RUNNING)
        .annotate(
            total_entries=Count("entries"),
            active_entries=Count("entries", filter=Q(entries__status=ArenaEntry.Status.REGISTERED)),
        )
        .order_by("next_round_at", "id")
    )

    my_tournament_ids = set(
        ArenaEntry.objects.filter(
            manor=manor,
            tournament_id__in=[tournament.id for tournament in running_tournaments],
        ).values_list("tournament_id", flat=True)
    )

    running_rows = [
        {"tournament": tournament, "is_mine": tournament.id in my_tournament_ids} for tournament in running_tournaments
    ]
    running_rows.sort(key=running_row_sort_key)
    context["running_tournaments"] = running_rows
    context["running_tournament_card_rows"] = [build_running_tournament_card_row(row) for row in running_rows]
    coop_events = list(
        ArenaCoopEvent.objects.filter(
            Q(status__in=[ArenaCoopEvent.Status.PREPARING, ArenaCoopEvent.Status.RUNNING])
            | Q(
                status__in=[ArenaCoopEvent.Status.COMPLETED, ArenaCoopEvent.Status.CANCELLED],
                ended_at__gte=current_time - timedelta(seconds=arena_coop_core.ARENA_COOP_COMPLETED_RETENTION_SECONDS),
            )
        ).order_by("-created_at", "-id")
    )
    my_coop_ids = set(
        ArenaCoopEntry.objects.filter(manor=manor, event_id__in=[event.id for event in coop_events])
        .exclude(status=ArenaCoopEntry.Status.CANCELLED)
        .values_list("event_id", flat=True)
    )
    running_coop_cards = []
    completed_cards = []
    for coop_event in coop_events:
        is_running = coop_event.status in [ArenaCoopEvent.Status.PREPARING, ArenaCoopEvent.Status.RUNNING]
        card = {
            "title": f"围攻光明顶 #{coop_event.id}",
            "summary": f"{coop_event.get_status_display()} | 首领：{coop_event.boss_name}",
            "badge_text": "我已参赛" if coop_event.id in my_coop_ids else "",
            "countdown_at": (
                coop_event.prepare_ends_at if coop_event.status == ArenaCoopEvent.Status.PREPARING else None
            ),
            "countdown_label": "开战：",
            "meta_datetime": coop_event.ended_at,
            "meta_label": "结束",
            "action_url": reverse("gameplay:arena_coop_detail", args=[coop_event.id]),
            "action_label": "查看共斗详情",
        }
        if is_running:
            running_coop_cards.append(card)
        else:
            completed_cards.append(card)

    wheel_events = list(
        ArenaWheelEvent.objects.filter(
            Q(status__in=[ArenaWheelEvent.Status.PREPARING, ArenaWheelEvent.Status.RUNNING])
            | Q(
                status__in=[ArenaWheelEvent.Status.COMPLETED, ArenaWheelEvent.Status.CANCELLED],
                ended_at__gte=current_time
                - timedelta(seconds=arena_wheel_core.ARENA_WHEEL_COMPLETED_RETENTION_SECONDS),
            )
        )
        .annotate(
            entry_count=Count(
                "entries",
                filter=Q(
                    entries__status__in=[
                        ArenaWheelEntry.Status.REGISTERED,
                        ArenaWheelEntry.Status.ACTIVE,
                        ArenaWheelEntry.Status.ELIMINATED,
                        ArenaWheelEntry.Status.COMPLETED,
                    ]
                ),
            )
        )
        .order_by("-created_at", "-id")
    )
    my_wheel_event_ids = set(
        ArenaWheelEntry.objects.filter(manor=manor, event_id__in=[event.id for event in wheel_events])
        .exclude(status=ArenaWheelEntry.Status.CANCELLED)
        .values_list("event_id", flat=True)
    )
    running_wheel_cards = []
    for wheel_event in wheel_events:
        is_running = wheel_event.status in [ArenaWheelEvent.Status.PREPARING, ArenaWheelEvent.Status.RUNNING]
        winner_text = f" | 获胜队伍：{wheel_event.get_winner_team_display()}" if wheel_event.winner_team else ""
        card = {
            "title": f"金羽擂台 #{wheel_event.id}",
            "summary": (
                f"{wheel_event.get_status_display()} | 参赛 "
                f"{wheel_event.entry_count}/{wheel_event.player_limit}{winner_text}"
            ),
            "badge_text": "我已参赛" if wheel_event.id in my_wheel_event_ids else "",
            "countdown_at": (
                wheel_event.prepare_ends_at if wheel_event.status == ArenaWheelEvent.Status.PREPARING else None
            ),
            "countdown_label": "开战：",
            "meta_datetime": wheel_event.ended_at,
            "meta_label": "结束",
            "action_url": reverse("gameplay:arena_wheel_detail", args=[wheel_event.id]),
            "action_label": (
                "进入逐场观战" if wheel_event.status == ArenaWheelEvent.Status.RUNNING else "查看车轮战详情"
            ),
        }
        if is_running:
            running_wheel_cards.append(card)
        else:
            completed_cards.append(card)

    completed_tournaments = list(
        ArenaTournament.objects.filter(
            status__in=[ArenaTournament.Status.COMPLETED, ArenaTournament.Status.CANCELLED],
            ended_at__gte=current_time - timedelta(seconds=arena_core.ARENA_COMPLETED_RETENTION_SECONDS),
        ).order_by("-ended_at", "-id")
    )
    my_completed_ids = set(
        ArenaEntry.objects.filter(
            manor=manor, tournament_id__in=[event.id for event in completed_tournaments]
        ).values_list("tournament_id", flat=True)
    )
    completed_cards.extend(
        {
            "title": f"天下布武 #{tournament.id}",
            "summary": tournament.get_status_display(),
            "badge_text": "我已参赛" if tournament.id in my_completed_ids else "",
            "meta_datetime": tournament.ended_at,
            "meta_label": "结束",
            "action_url": reverse("gameplay:arena_event_detail", args=[tournament.id]),
            "action_label": "查看赛事详情",
        }
        for tournament in completed_tournaments
    )
    completed_cards.sort(key=lambda card: cast(datetime, card["meta_datetime"]), reverse=True)
    context["running_coop_card_rows"] = running_coop_cards
    context["running_wheel_card_rows"] = running_wheel_cards
    context["completed_event_card_rows"] = completed_cards
    return context
