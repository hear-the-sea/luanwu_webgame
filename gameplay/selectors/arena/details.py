from __future__ import annotations

from datetime import timedelta
from math import ceil

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
import gameplay.services.arena.core as arena_core
from gameplay.models import (
    ArenaCoopContribution,
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaMatch,
    ArenaTournament,
    ArenaWheelBout,
    ArenaWheelEntry,
    ArenaWheelEntryGuest,
    ArenaWheelEvent,
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
    event = ArenaCoopEvent.objects.select_related("battle_report").filter(pk=event_id).first()
    if not event:
        return None

    if event.status == ArenaCoopEvent.Status.RECRUITING:
        if not ArenaCoopEntry.objects.filter(event=event, manor=manor).exists():
            return None
    elif event.status not in [ArenaCoopEvent.Status.PREPARING, ArenaCoopEvent.Status.RUNNING]:
        visible_cutoff = timezone.now() - timedelta(seconds=arena_coop_core.ARENA_COOP_COMPLETED_RETENTION_SECONDS)
        if (
            event.status not in [ArenaCoopEvent.Status.COMPLETED, ArenaCoopEvent.Status.CANCELLED]
            or event.ended_at is None
            or event.ended_at < visible_cutoff
        ):
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


def get_arena_wheel_event_detail_context(manor: Manor, event_id: int) -> dict | None:
    from gameplay.services.arena import wheel_core

    context = build_common_context(manor)
    current_time = timezone.now()
    event = ArenaWheelEvent.objects.filter(pk=event_id).first()
    if event is None:
        return None

    is_mine = ArenaWheelEntry.objects.filter(event=event, manor=manor).exists()
    if event.status == ArenaWheelEvent.Status.RECRUITING and not is_mine:
        return None
    if event.status in [ArenaWheelEvent.Status.COMPLETED, ArenaWheelEvent.Status.CANCELLED]:
        visible_cutoff = current_time - timedelta(seconds=wheel_core.ARENA_WHEEL_COMPLETED_RETENTION_SECONDS)
        if event.ended_at is None or event.ended_at < visible_cutoff:
            return None

    entries = list(
        ArenaWheelEntry.objects.filter(event=event)
        .select_related("manor")
        .prefetch_related(
            Prefetch(
                "entry_guests",
                queryset=ArenaWheelEntryGuest.objects.select_related("guest").order_by("slot_index", "id"),
            )
        )
        .order_by("team", "team_order", "joined_at", "id")
    )

    def build_guest_rows(entry):
        guest_rows = []
        for link in entry.entry_guests.all():
            snapshot = link.snapshot if isinstance(link.snapshot, dict) else {}
            try:
                defense_stat = max(0, int(snapshot.get("defense_stat") or 0))
                original_max_hp = max(1, int(snapshot.get("max_hp") or 1))
                current_hp = max(1, int(snapshot.get("current_hp") or 1))
            except (TypeError, ValueError):
                defense_stat = 0
                original_max_hp = 1
                current_hp = 1
            _debuffed_defense_stat, max_hp = wheel_core.get_arena_wheel_debuffed_defense_and_max_hp(
                defense_stat=defense_stat,
                max_hp=original_max_hp,
                debuff_stacks=entry.debuff_stacks,
            )
            guest_rows.append(
                {
                    "display_name": snapshot.get("display_name") or "门客",
                    "wheel_knocked_out": bool(snapshot.get("wheel_knocked_out")),
                    "current_hp": min(current_hp, max_hp),
                    "max_hp": max_hp,
                }
            )
        return guest_rows

    team_a_rows = [
        {
            "entry": entry,
            "is_mine": entry.manor_id == manor.id,
            "is_active": entry.pk == event.team_a_active_entry_id,
            "is_winner": entry.team == event.winner_team,
            "guests": build_guest_rows(entry),
        }
        for entry in entries
        if entry.team == ArenaWheelEvent.Team.A
    ]
    team_b_rows = [
        {
            "entry": entry,
            "is_mine": entry.manor_id == manor.id,
            "is_active": entry.pk == event.team_b_active_entry_id,
            "is_winner": entry.team == event.winner_team,
            "guests": build_guest_rows(entry),
        }
        for entry in entries
        if entry.team == ArenaWheelEvent.Team.B
    ]
    bouts = list(
        ArenaWheelBout.objects.filter(event=event)
        .select_related(
            "team_a_entry__manor",
            "team_b_entry__manor",
            "winner_entry__manor",
            "battle_report",
        )
        .order_by("bout_number", "id")
    )
    bout_rows = [
        {
            "bout": bout,
            "team_a_name": bout.team_a_entry.manor.display_name,
            "team_b_name": bout.team_b_entry.manor.display_name,
            "winner_name": (
                bout.winner_entry.manor.display_name if bout.winner_entry is not None else "双方门客均倒下"
            ),
            "is_mine": manor.id in {bout.team_a_entry.manor_id, bout.team_b_entry.manor_id},
        }
        for bout in bouts
    ]
    if event.status == ArenaWheelEvent.Status.PREPARING:
        next_bout_at = event.prepare_ends_at
    elif event.status == ArenaWheelEvent.Status.RUNNING:
        latest_bout = bouts[-1] if bouts else None
        next_bout_at = (
            latest_bout.created_at + timedelta(seconds=wheel_core.ARENA_WHEEL_BOUT_INTERVAL_SECONDS)
            if latest_bout
            else current_time
        )
    else:
        next_bout_at = None
    refresh_after_seconds = max(10, ceil((next_bout_at - current_time).total_seconds()) + 15) if next_bout_at else None
    context.update(
        {
            "wheel_event": event,
            "wheel_win_reward": wheel_core.ARENA_WHEEL_WIN_REWARD,
            "wheel_loss_reward": wheel_core.ARENA_WHEEL_LOSS_REWARD,
            "wheel_team_a_rows": team_a_rows,
            "wheel_team_b_rows": team_b_rows,
            "wheel_bout_rows": bout_rows,
            "wheel_next_bout_at": next_bout_at,
            "wheel_refresh_after_seconds": refresh_after_seconds,
            "wheel_is_mine": is_mine,
            "wheel_team_a_reward": (
                wheel_core.ARENA_WHEEL_WIN_REWARD
                if event.winner_team == ArenaWheelEvent.Team.A
                else wheel_core.ARENA_WHEEL_LOSS_REWARD
            ),
            "wheel_team_b_reward": (
                wheel_core.ARENA_WHEEL_WIN_REWARD
                if event.winner_team == ArenaWheelEvent.Team.B
                else wheel_core.ARENA_WHEEL_LOSS_REWARD
            ),
        }
    )
    return context
