from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaTournament, Manor

from .common import build_common_context, running_row_sort_key
from .registration import get_arena_coop_summary_context


def get_arena_events_context(manor: Manor) -> dict:
    context = build_common_context(manor)
    context.update(get_arena_coop_summary_context(manor))
    current_time = timezone.now()
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
    context["running_coop_events"] = list(
        ArenaCoopEvent.objects.filter(status__in=[ArenaCoopEvent.Status.PREPARING, ArenaCoopEvent.Status.RUNNING])
        .annotate(
            total_entries=Count("entries"),
            active_entries=Count("entries", filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED)),
        )
        .order_by("prepare_ends_at", "started_at", "id")[:20]
    )
    coop_visible_cutoff = current_time - timedelta(seconds=arena_coop_core.ARENA_COOP_COMPLETED_RETENTION_SECONDS)
    context["recent_coop_events"] = list(
        ArenaCoopEvent.objects.filter(
            status__in=[ArenaCoopEvent.Status.COMPLETED, ArenaCoopEvent.Status.CANCELLED],
            ended_at__isnull=False,
            ended_at__gte=coop_visible_cutoff,
            entries__manor=manor,
        )
        .distinct()
        .order_by("-ended_at", "-id")[:20]
    )
    return context
