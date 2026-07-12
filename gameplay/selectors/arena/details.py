from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

import gameplay.services.arena.core as arena_core
from gameplay.models import (
    ArenaCoopContribution,
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaMatch,
    ArenaTournament,
    Manor,
)

from .common import build_common_context
from .registration import get_arena_coop_summary_context


def get_arena_event_detail_context(manor: Manor, tournament_id: int, selected_round: int | None = None) -> dict | None:
    context = build_common_context(manor)
    current_time = timezone.now()
    tournament = (
        ArenaTournament.objects.annotate(
            total_entries=Count("entries"),
            active_entries=Count("entries", filter=Q(entries__status=ArenaEntry.Status.REGISTERED)),
        )
        .filter(pk=tournament_id)
        .first()
    )
    if not tournament:
        return None

    if tournament.status != ArenaTournament.Status.RUNNING:
        visible_cutoff = current_time - timedelta(seconds=arena_core.ARENA_COMPLETED_RETENTION_SECONDS)
        is_recently_ended = (
            tournament.status in [ArenaTournament.Status.COMPLETED, ArenaTournament.Status.CANCELLED]
            and tournament.ended_at is not None
            and tournament.ended_at >= visible_cutoff
        )
        if not is_recently_ended:
            return None

    is_mine = ArenaEntry.objects.filter(tournament=tournament, manor=manor).exists()
    all_matches = list(
        ArenaMatch.objects.select_related(
            "attacker_entry__manor",
            "defender_entry__manor",
            "winner_entry__manor",
            "battle_report",
        )
        .filter(tournament=tournament)
        .order_by("round_number", "match_index", "id")
    )

    available_rounds = sorted({match.round_number for match in all_matches})
    current_round = (
        selected_round if selected_round in available_rounds else (available_rounds[-1] if available_rounds else None)
    )

    current_round_match_rows: list[dict] = []
    if current_round is not None:
        for match in all_matches:
            if match.round_number != current_round:
                continue
            left_name = match.attacker_entry.manor.display_name
            right_name = match.defender_entry.manor.display_name if match.defender_entry else "轮空"
            left_is_loser = (
                match.defender_entry_id is not None
                and match.winner_entry_id is not None
                and match.winner_entry_id != match.attacker_entry_id
            )
            right_is_loser = (
                match.defender_entry_id is not None
                and match.winner_entry_id is not None
                and match.winner_entry_id != match.defender_entry_id
            )
            left_outcome = None
            right_outcome = None
            if match.winner_entry_id is not None:
                if match.winner_entry_id == match.attacker_entry_id:
                    left_outcome = "胜利"
                elif match.defender_entry_id is not None:
                    left_outcome = "战败"
                if match.defender_entry_id is not None:
                    right_outcome = "胜利" if match.winner_entry_id == match.defender_entry_id else "战败"

            current_round_match_rows.append(
                {
                    "match": match,
                    "left_name": left_name,
                    "right_name": right_name,
                    "left_is_loser": left_is_loser,
                    "right_is_loser": right_is_loser,
                    "left_outcome": left_outcome,
                    "right_outcome": right_outcome,
                    "left_is_mine": match.attacker_entry.manor_id == manor.id,
                    "left_is_virtual": match.attacker_entry.source == ArenaEntry.Source.VIRTUAL,
                    "right_is_mine": match.defender_entry is not None and match.defender_entry.manor_id == manor.id,
                    "right_is_virtual": match.defender_entry is not None
                    and match.defender_entry.source == ArenaEntry.Source.VIRTUAL,
                    "report_id": match.battle_report_id,
                }
            )

    previous_round_number = None
    next_round_number = None
    if current_round is not None and available_rounds:
        current_index = available_rounds.index(current_round)
        if current_index > 0:
            previous_round_number = available_rounds[current_index - 1]
        if current_index < len(available_rounds) - 1:
            next_round_number = available_rounds[current_index + 1]

    context.update(
        {
            "tournament": tournament,
            "round_pages": [
                {"round": round_number, "is_active": round_number == current_round} for round_number in available_rounds
            ],
            "current_round_number": current_round,
            "current_round_match_rows": current_round_match_rows,
            "previous_round_number": previous_round_number,
            "next_round_number": next_round_number,
            "is_mine": is_mine,
        }
    )
    return context


def get_arena_coop_event_detail_context(manor: Manor, event_id: int) -> dict | None:
    context = build_common_context(manor)
    context.update(get_arena_coop_summary_context(manor))
    event = (
        ArenaCoopEvent.objects.select_related("battle_report")
        .filter(pk=event_id, entries__manor=manor)
        .distinct()
        .first()
    )
    if not event:
        return None

    contributions = list(
        ArenaCoopContribution.objects.select_related("entry__manor").filter(event=event).order_by("damage_rank", "id")
    )
    real_contributions = [row for row in contributions if row.entry.source == ArenaCoopEntry.Source.PLAYER]
    virtual_contributions = [row for row in contributions if row.entry.source == ArenaCoopEntry.Source.VIRTUAL]
    context.update(
        {
            "coop_event": event,
            "contribution_rows": [
                {
                    "damage_rank": row.damage_rank,
                    "manor_name": row.entry.manor.display_name,
                    "total_damage": row.total_damage,
                    "boss_damage": row.boss_damage,
                    "total_coins": row.total_coins,
                }
                for row in real_contributions
            ],
            "virtual_contribution_rows": [
                {
                    "manor_name": row.entry.manor.display_name,
                    "total_damage": row.total_damage,
                    "boss_damage": row.boss_damage,
                }
                for row in virtual_contributions
            ],
        }
    )
    return context
