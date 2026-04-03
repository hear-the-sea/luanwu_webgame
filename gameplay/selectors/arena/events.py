from __future__ import annotations

from django.db.models import Count, Q
from django.urls import reverse

from gameplay.models import ArenaEntry, ArenaTournament, Manor

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
    running_tournaments = list(
        ArenaTournament.objects.filter(status=ArenaTournament.Status.RUNNING)
        .annotate(
            total_entries=Count("entries"),
            active_entries=Count("entries", filter=Q(entries__status=ArenaEntry.Status.REGISTERED)),
        )
        .order_by("next_round_at", "id")[:20]
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
    return context
