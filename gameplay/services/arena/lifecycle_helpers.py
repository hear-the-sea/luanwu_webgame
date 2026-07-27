from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from django.db.models import F

from core.exceptions import MessageError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from core.utils.side_effects import schedule_best_effort_after_commit
from gameplay.models import ArenaEntry, ArenaEntryGuest, ArenaMatch, ArenaTournament, Manor, Message
from gameplay.services.utils.messages import create_message
from guests.models import Guest, GuestStatus

ARENA_SETTLEMENT_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def _send_arena_settlement_message(*, manor: Manor, title: str, body: str) -> None:
    create_message(manor=manor, kind=Message.Kind.REWARD, title=title, body=body)


def schedule_round_locked(
    tournament: ArenaTournament,
    *,
    round_number: int,
    now,
    build_round_pairings: Callable[[ArenaTournament, list[int], int], list[tuple[int, int | None]]],
    create_scheduled_match: Callable[..., ArenaMatch],
    round_interval_delta: Callable[[ArenaTournament], timedelta],
    finalize_tournament_locked: Callable[..., None],
) -> bool:
    if tournament.status != ArenaTournament.Status.RUNNING:
        return False
    if round_number <= 0:
        return False
    if ArenaMatch.objects.filter(tournament=tournament, round_number=round_number).exists():
        return False

    active_entry_ids = list(
        tournament.entries.filter(status=ArenaEntry.Status.REGISTERED).order_by("id").values_list("id", flat=True)
    )
    if len(active_entry_ids) <= 1:
        winner = None
        if active_entry_ids:
            winner = (
                ArenaEntry.objects.select_related("manor", "manor__user")
                .select_for_update()
                .filter(pk=active_entry_ids[0])
                .first()
            )
        finalize_tournament_locked(tournament, winner_entry=winner, now=now)
        return False

    entry_map = {
        entry.pk: entry
        for entry in ArenaEntry.objects.select_for_update().filter(pk__in=active_entry_ids).order_by("pk")
    }
    pairings = build_round_pairings(tournament, active_entry_ids, round_number)
    for match_index, (attacker_id, defender_id) in enumerate(pairings):
        create_scheduled_match(
            tournament=tournament,
            round_number=round_number,
            match_index=match_index,
            attacker_entry=entry_map[attacker_id],
            defender_entry=entry_map.get(defender_id) if defender_id is not None else None,
        )

    tournament.current_round = round_number
    tournament.next_round_at = now + round_interval_delta(tournament)
    tournament.save(update_fields=["current_round", "next_round_at", "updated_at"])
    return True


def finalize_tournament_locked(
    tournament: ArenaTournament,
    *,
    winner_entry: ArenaEntry | None,
    now,
    calculate_ranked_entries: Callable[[list[ArenaEntry], ArenaEntry | None], list[ArenaEntry]],
    reward_for_rank: Callable[[int], int],
    logger: logging.Logger,
) -> None:
    entries = list(tournament.entries.select_related("manor", "manor__user").select_for_update().order_by("id"))
    if not entries:
        tournament.status = ArenaTournament.Status.CANCELLED
        tournament.ended_at = now
        tournament.next_round_at = None
        tournament.save(update_fields=["status", "ended_at", "next_round_at", "updated_at"])
        return

    ranked_entries = calculate_ranked_entries(entries, winner_entry)
    for idx, entry in enumerate(ranked_entries, start=1):
        entry.final_rank = idx
        entry.coin_reward = reward_for_rank(idx) if entry.source == ArenaEntry.Source.PLAYER else 0
        if idx == 1:
            entry.status = ArenaEntry.Status.WINNER
        elif entry.status != ArenaEntry.Status.ELIMINATED:
            entry.status = ArenaEntry.Status.ELIMINATED

    ArenaEntry.objects.bulk_update(ranked_entries, ["final_rank", "coin_reward", "status"])

    for entry in ranked_entries:
        if entry.source != ArenaEntry.Source.PLAYER:
            continue
        Manor.objects.filter(pk=entry.manor_id).update(arena_coins=F("arena_coins") + entry.coin_reward)
        title = "竞技场结算奖励"
        body = f"本场排名第 {entry.final_rank}，获得角斗币 {entry.coin_reward}。"

        def _send_settlement_message(
            *,
            manor=entry.manor,
            title=title,
            body=body,
        ) -> None:
            _send_arena_settlement_message(
                manor=manor,
                title=title,
                body=body,
            )

        schedule_best_effort_after_commit(
            _send_settlement_message,
            logger=logger,
            log_message=(
                "arena settlement message failed: "
                f"tournament_id={tournament.id} entry_id={entry.id} manor_id={entry.manor_id}"
            ),
            expected_exceptions=ARENA_SETTLEMENT_MESSAGE_EXCEPTIONS,
            degraded_component="arena_settlement_messages",
        )

    participating_guest_ids = list(
        ArenaEntryGuest.objects.filter(entry_id__in=[entry.id for entry in entries]).values_list("guest_id", flat=True)
    )
    if participating_guest_ids:
        Guest.objects.filter(
            id__in=participating_guest_ids, status__in=[GuestStatus.ARENA, GuestStatus.DEPLOYED]
        ).update(status=GuestStatus.IDLE)

    winner = ranked_entries[0]
    tournament.status = ArenaTournament.Status.COMPLETED
    tournament.current_round = max(tournament.current_round, winner.eliminated_round or tournament.current_round)
    tournament.winner_entry = winner
    tournament.ended_at = now
    tournament.next_round_at = None
    tournament.save(
        update_fields=["status", "current_round", "winner_entry", "ended_at", "next_round_at", "updated_at"]
    )


def cleanup_expired_tournaments(*, now, grace_seconds: int, limit: int = 50) -> int:
    retention_seconds = max(0, int(grace_seconds))
    cutoff_time = now - timedelta(seconds=retention_seconds)
    stale_ids = list(
        ArenaTournament.objects.filter(
            status__in=[ArenaTournament.Status.COMPLETED, ArenaTournament.Status.CANCELLED],
            ended_at__isnull=False,
            ended_at__lte=cutoff_time,
        )
        .order_by("ended_at", "id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    if not stale_ids:
        return 0

    ArenaTournament.objects.filter(id__in=stale_ids).delete()
    return len(stale_ids)
