from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from battle.random_context import RNG_STREAM_TIE_BREAK
from core.exceptions import MessageError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from core.utils.side_effects import schedule_best_effort_after_commit
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaMatch,
    ArenaTournament,
    Manor,
    Message,
)
from gameplay.services.utils.messages import create_message
from guests.models import Guest, GuestStatus
from guests.services.status import persist_guest_status_transitions

from . import helpers as _arena_helpers
from .match_store import create_scheduled_match
from .replay import initialize_replay_metadata_locked, replay_context
from .rules import load_arena_rules

logger = logging.getLogger(__name__)

ARENA_SETTLEMENT_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def _send_arena_settlement_message(*, manor: Manor, title: str, body: str) -> None:
    create_message(manor=manor, kind=Message.Kind.REWARD, title=title, body=body)


def _build_round_pairings(
    tournament: ArenaTournament,
    entry_ids: list[int],
    round_number: int,
) -> list[tuple[int, int | None]]:
    random_context = replay_context(tournament)
    return _arena_helpers.build_round_pairings(
        entry_ids,
        rng=random_context.rng(
            RNG_STREAM_TIE_BREAK,
            discriminator=f"pairings:round:{int(round_number)}",
        ),
    )


def _round_interval_delta(tournament: ArenaTournament) -> timedelta:
    return _arena_helpers.round_interval_delta(tournament.round_interval_seconds)


def _reward_for_rank(rank: int) -> int:
    rewards = load_arena_rules()["rewards"]
    return _arena_helpers.reward_for_rank(
        rank,
        base_participation_coins=int(rewards["base_participation_coins"]),
        rank_bonus_coins=dict(rewards["rank_bonus_coins"]),
    )


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
        guests = list(
            Guest.objects.select_for_update()
            .filter(
                id__in=participating_guest_ids,
                status__in=[GuestStatus.ARENA, GuestStatus.DEPLOYED],
            )
            .order_by("id")
        )
        persist_guest_status_transitions(
            guests,
            GuestStatus.IDLE,
            source="arena_settlement",
        )

    winner = ranked_entries[0]
    tournament.status = ArenaTournament.Status.COMPLETED
    tournament.current_round = max(tournament.current_round, winner.eliminated_round or tournament.current_round)
    tournament.winner_entry = winner
    tournament.ended_at = now
    tournament.next_round_at = None
    tournament.save(
        update_fields=[
            "status",
            "current_round",
            "winner_entry",
            "ended_at",
            "next_round_at",
            "updated_at",
        ]
    )


def schedule_tournament_round_locked(
    tournament: ArenaTournament,
    *,
    round_number: int,
    now,
) -> bool:
    return schedule_round_locked(
        tournament,
        round_number=round_number,
        now=now,
        build_round_pairings=_build_round_pairings,
        create_scheduled_match=create_scheduled_match,
        round_interval_delta=_round_interval_delta,
        finalize_tournament_locked=lambda locked_tournament, *, winner_entry, now: finalize_tournament_locked(
            locked_tournament,
            winner_entry=winner_entry,
            now=now,
            calculate_ranked_entries=_arena_helpers.calculate_ranked_entries,
            reward_for_rank=_reward_for_rank,
            logger=logger,
        ),
    )


def start_tournament_locked(
    tournament: ArenaTournament,
    *,
    now=None,
) -> bool:
    if tournament.status != ArenaTournament.Status.RECRUITING:
        return False
    if tournament.entries.count() < tournament.player_limit:
        return False

    current_time = now or timezone.now()
    initialize_replay_metadata_locked(tournament)
    tournament.status = ArenaTournament.Status.RUNNING
    tournament.virtual_fill_completed = True
    tournament.started_at = current_time
    tournament.current_round = 0
    tournament.save(
        update_fields=[
            "status",
            "virtual_fill_completed",
            "started_at",
            "current_round",
            "updated_at",
        ]
    )
    schedule_tournament_round_locked(tournament, round_number=1, now=current_time)
    return True


def move_coop_event_to_preparing_locked(
    event: ArenaCoopEvent,
    *,
    now=None,
) -> bool:
    if event.status != ArenaCoopEvent.Status.RECRUITING:
        return False
    if event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED).count() < event.player_limit:
        return False

    current_time = now or timezone.now()
    event.status = ArenaCoopEvent.Status.PREPARING
    event.virtual_fill_completed = True
    event.prepare_ends_at = current_time + timedelta(seconds=event.prepare_duration_seconds)
    event.save(
        update_fields=[
            "status",
            "virtual_fill_completed",
            "prepare_ends_at",
            "updated_at",
        ]
    )
    return True
