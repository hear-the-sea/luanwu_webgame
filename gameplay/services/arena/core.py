from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Iterable

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from core.exceptions import ArenaBusyError, ArenaEntryStateError, ArenaParticipationLimitError
from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock
from gameplay.models import ArenaEntry, ArenaEntryGuest, ArenaMatch, ArenaTournament, Manor
from guests.models import Guest, GuestStatus
from guests.services.loyalty import increase_guest_loyalty_by_ids

from . import helpers as _arena_helpers
from .exchange_helpers import ArenaExchangeResult, exchange_arena_reward  # noqa: F401
from .lifecycle_helpers import cleanup_expired_tournaments as _cleanup_expired_tournaments
from .lifecycle_helpers import finalize_tournament_locked, schedule_round_locked
from .match_helpers import resolve_match_locked, save_resolved_match
from .registration_helpers import (
    collect_cancelable_recruiting_entries_locked,
    create_arena_entry_with_guests_locked,
    deduct_registration_silver_locked,
    load_selected_registration_guests_locked,
)
from .round_helpers import finalize_round_state_locked, load_round_entries_for_matches, resolve_pending_round_matches
from .rules import load_arena_rules
from .snapshots import build_entry_guest_snapshot
from .state_helpers import sync_daily_participation_counter_locked as _sync_daily_participation_counter_locked
from .state_helpers import update_daily_participation_counter_locked as _update_daily_participation_counter_locked
from .virtual_backfill import backfill_tournament_locked

logger = logging.getLogger(__name__)


_load_positive_int_setting = _arena_helpers.load_positive_int_setting


ARENA_RULES = load_arena_rules()
ARENA_DAILY_PARTICIPATION_LIMIT = _load_positive_int_setting(
    "ARENA_DAILY_PARTICIPATION_LIMIT", ARENA_RULES["registration"]["daily_participation_limit"], minimum=1
)
ARENA_MAX_GUESTS_PER_ENTRY = int(ARENA_RULES["registration"]["max_guests_per_entry"])
ARENA_TOURNAMENT_PLAYER_LIMIT = _load_positive_int_setting(
    "ARENA_TOURNAMENT_PLAYER_LIMIT", ARENA_RULES["registration"]["tournament_player_limit"], minimum=2
)
ARENA_ROUND_INTERVAL_SECONDS = int(ARENA_RULES["runtime"]["round_interval_seconds"])
ARENA_VIRTUAL_FILL_WAIT_SECONDS = int(ARENA_RULES["runtime"]["virtual_fill_wait_seconds"])
ARENA_COMPLETED_RETENTION_SECONDS = int(ARENA_RULES["runtime"]["completed_retention_seconds"])
ARENA_ROUND_RETRY_SECONDS = int(ARENA_RULES["runtime"]["round_retry_seconds"])
ARENA_REGISTRATION_SILVER_COST = int(ARENA_RULES["registration"]["registration_silver_cost"])
ARENA_BASE_PARTICIPATION_COINS = int(ARENA_RULES["rewards"]["base_participation_coins"])
ARENA_RANK_BONUS_COINS = dict(ARENA_RULES["rewards"]["rank_bonus_coins"])
ARENA_RECRUITING_LOCK_KEY = str(ARENA_RULES["runtime"]["recruiting_lock_key"])
ARENA_RECRUITING_LOCK_TIMEOUT = int(ARENA_RULES["runtime"]["recruiting_lock_timeout"])


def refresh_arena_constants() -> None:
    """重新从 YAML 加载竞技场规则并更新模块级常量。"""
    global ARENA_RULES
    global ARENA_DAILY_PARTICIPATION_LIMIT, ARENA_MAX_GUESTS_PER_ENTRY, ARENA_TOURNAMENT_PLAYER_LIMIT
    global ARENA_ROUND_INTERVAL_SECONDS, ARENA_VIRTUAL_FILL_WAIT_SECONDS, ARENA_COMPLETED_RETENTION_SECONDS, ARENA_ROUND_RETRY_SECONDS
    global ARENA_REGISTRATION_SILVER_COST, ARENA_BASE_PARTICIPATION_COINS, ARENA_RANK_BONUS_COINS
    global ARENA_RECRUITING_LOCK_KEY, ARENA_RECRUITING_LOCK_TIMEOUT

    ARENA_RULES = load_arena_rules()
    ARENA_DAILY_PARTICIPATION_LIMIT = _load_positive_int_setting(
        "ARENA_DAILY_PARTICIPATION_LIMIT", ARENA_RULES["registration"]["daily_participation_limit"], minimum=1
    )
    ARENA_MAX_GUESTS_PER_ENTRY = int(ARENA_RULES["registration"]["max_guests_per_entry"])
    ARENA_TOURNAMENT_PLAYER_LIMIT = _load_positive_int_setting(
        "ARENA_TOURNAMENT_PLAYER_LIMIT", ARENA_RULES["registration"]["tournament_player_limit"], minimum=2
    )
    ARENA_ROUND_INTERVAL_SECONDS = int(ARENA_RULES["runtime"]["round_interval_seconds"])
    ARENA_VIRTUAL_FILL_WAIT_SECONDS = int(ARENA_RULES["runtime"]["virtual_fill_wait_seconds"])
    ARENA_COMPLETED_RETENTION_SECONDS = int(ARENA_RULES["runtime"]["completed_retention_seconds"])
    ARENA_ROUND_RETRY_SECONDS = int(ARENA_RULES["runtime"]["round_retry_seconds"])
    ARENA_REGISTRATION_SILVER_COST = int(ARENA_RULES["registration"]["registration_silver_cost"])
    ARENA_BASE_PARTICIPATION_COINS = int(ARENA_RULES["rewards"]["base_participation_coins"])
    ARENA_RANK_BONUS_COINS = dict(ARENA_RULES["rewards"]["rank_bonus_coins"])
    ARENA_RECRUITING_LOCK_KEY = str(ARENA_RULES["runtime"]["recruiting_lock_key"])
    ARENA_RECRUITING_LOCK_TIMEOUT = int(ARENA_RULES["runtime"]["recruiting_lock_timeout"])


@dataclass(frozen=True)
class ArenaRegistrationResult:
    entry: ArenaEntry
    tournament: ArenaTournament
    auto_started: bool
    entry_count: int


class ArenaMatchResolutionError(RuntimeError):
    """Raised when a round match cannot be resolved and should be retried."""


def _normalize_guest_ids(guest_ids: Iterable[int]) -> list[int]:
    return _arena_helpers.normalize_guest_ids(guest_ids, max_guests_per_entry=ARENA_MAX_GUESTS_PER_ENTRY)


def _round_interval_seconds() -> int:
    return _arena_helpers.round_interval_seconds(ARENA_ROUND_INTERVAL_SECONDS)


def _get_or_create_recruiting_tournament_locked() -> ArenaTournament:
    tournament = (
        ArenaTournament.objects.select_for_update()
        .filter(status=ArenaTournament.Status.RECRUITING)
        .annotate(entry_count=Count("entries"))
        .filter(entry_count__lt=F("player_limit"))
        .order_by("created_at")
        .first()
    )
    if tournament:
        return tournament

    acquired, from_cache, lock_token = acquire_best_effort_lock(
        ARENA_RECRUITING_LOCK_KEY,
        timeout_seconds=ARENA_RECRUITING_LOCK_TIMEOUT,
        logger=logger,
        log_context="arena recruiting tournament lock",
        allow_local_fallback=False,
    )
    if not acquired:
        existing = (
            ArenaTournament.objects.filter(status=ArenaTournament.Status.RECRUITING)
            .annotate(entry_count=Count("entries"))
            .filter(entry_count__lt=F("player_limit"))
            .order_by("created_at")
            .first()
        )
        if existing:
            return existing
        raise ArenaBusyError()

    try:
        existing = (
            ArenaTournament.objects.select_for_update()
            .filter(status=ArenaTournament.Status.RECRUITING)
            .annotate(entry_count=Count("entries"))
            .filter(entry_count__lt=F("player_limit"))
            .order_by("created_at")
            .first()
        )
        if existing:
            return existing

        return ArenaTournament.objects.create(
            status=ArenaTournament.Status.RECRUITING,
            player_limit=ARENA_TOURNAMENT_PLAYER_LIMIT,
            round_interval_seconds=_round_interval_seconds(),
            virtual_fill_at=timezone.now() + timedelta(seconds=ARENA_VIRTUAL_FILL_WAIT_SECONDS),
        )
    finally:
        release_best_effort_lock(
            ARENA_RECRUITING_LOCK_KEY,
            from_cache=from_cache,
            lock_token=lock_token,
            logger=logger,
            log_context="arena recruiting tournament lock",
        )


def _start_tournament_locked(tournament: ArenaTournament, *, now: datetime | None = None) -> bool:
    if tournament.status != ArenaTournament.Status.RECRUITING:
        return False
    entry_count = tournament.entries.count()
    if entry_count < tournament.player_limit:
        return False

    current_time = now or timezone.now()
    tournament.status = ArenaTournament.Status.RUNNING
    tournament.started_at = current_time
    tournament.current_round = 0
    tournament.save(update_fields=["status", "started_at", "current_round", "updated_at"])
    _schedule_round_locked(tournament, round_number=1, now=current_time)
    return True


def _round_interval_delta(tournament: ArenaTournament) -> timedelta:
    return _arena_helpers.round_interval_delta(tournament.round_interval_seconds)


_build_round_pairings = _arena_helpers.build_round_pairings


def _schedule_round_locked(tournament: ArenaTournament, *, round_number: int, now: datetime) -> bool:
    return schedule_round_locked(
        tournament,
        round_number=round_number,
        now=now,
        build_round_pairings=_build_round_pairings,
        round_interval_delta=_round_interval_delta,
        finalize_tournament_locked=partial(
            finalize_tournament_locked,
            calculate_ranked_entries=_arena_helpers.calculate_ranked_entries,
            reward_for_rank=_reward_for_rank,
            logger=logger,
        ),
    )


@transaction.atomic
def register_arena_entry(manor: Manor, guest_ids: Iterable[int]) -> ArenaRegistrationResult:
    selected_guest_ids = _normalize_guest_ids(guest_ids)
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)

    if _sync_daily_participation_counter_locked(locked_manor) >= ARENA_DAILY_PARTICIPATION_LIMIT:
        raise ArenaParticipationLimitError(ARENA_DAILY_PARTICIPATION_LIMIT)

    if ArenaEntry.objects.filter(
        manor=locked_manor,
        tournament__status__in=[ArenaTournament.Status.RECRUITING, ArenaTournament.Status.RUNNING],
    ).exists():
        raise ArenaEntryStateError("您已有进行中的竞技场报名，请等待本场结束")

    selected_guests = load_selected_registration_guests_locked(locked_manor, selected_guest_ids)
    deduct_registration_silver_locked(locked_manor, silver_cost=ARENA_REGISTRATION_SILVER_COST)
    tournament = _get_or_create_recruiting_tournament_locked()
    entry = create_arena_entry_with_guests_locked(
        tournament=tournament,
        locked_manor=locked_manor,
        selected_guests=selected_guests,
        build_entry_guest_snapshot=build_entry_guest_snapshot,
    )

    entry_count = tournament.entries.count()
    auto_started = False
    if entry_count >= tournament.player_limit:
        auto_started = _start_tournament_locked(tournament)
    _update_daily_participation_counter_locked(locked_manor, delta=1)

    return ArenaRegistrationResult(
        entry=entry,
        tournament=tournament,
        auto_started=auto_started,
        entry_count=entry_count,
    )


@transaction.atomic
def cancel_arena_entry(manor: Manor) -> int:
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    recruiting_entries, participant_guest_ids = collect_cancelable_recruiting_entries_locked(locked_manor)

    entry_ids = [entry.id for entry in recruiting_entries]
    ArenaEntry.objects.filter(id__in=entry_ids).delete()
    if participant_guest_ids:
        Guest.objects.filter(
            id__in=participant_guest_ids,
            status__in=[GuestStatus.ARENA, GuestStatus.DEPLOYED],
        ).update(status=GuestStatus.IDLE)

    _update_daily_participation_counter_locked(locked_manor, delta=-len(entry_ids))
    return len(entry_ids)


@transaction.atomic
def start_tournament_if_ready(tournament: ArenaTournament, *, now: datetime | None = None) -> bool:
    locked = ArenaTournament.objects.select_for_update().filter(pk=tournament.pk).first()
    if not locked:
        return False
    return _start_tournament_locked(locked, now=now)


def start_ready_tournaments(limit: int = 20) -> int:
    candidate_ids = list(
        ArenaTournament.objects.filter(status=ArenaTournament.Status.RECRUITING)
        .annotate(entry_count=Count("entries"))
        .filter(entry_count__gte=F("player_limit"))
        .order_by("created_at")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    started_count = 0
    for tournament_id in candidate_ids:
        if start_tournament_if_ready(ArenaTournament(id=tournament_id)):
            started_count += 1
    return started_count


def start_due_virtual_backfill_tournaments(*, now: datetime | None = None, limit: int = 20) -> int:
    now = now or timezone.now()
    candidate_ids = list(
        ArenaTournament.objects.filter(
            status=ArenaTournament.Status.RECRUITING,
            virtual_fill_completed=False,
            virtual_fill_at__lte=now,
        ).values_list("id", flat=True)[: max(1, int(limit))]
    )
    started = 0
    for tournament_id in candidate_ids:
        with transaction.atomic():
            tournament = ArenaTournament.objects.select_for_update().filter(pk=tournament_id).first()
            if not tournament or tournament.status != ArenaTournament.Status.RECRUITING:
                continue
            if tournament.virtual_fill_completed or not tournament.virtual_fill_at or tournament.virtual_fill_at > now:
                continue
            if backfill_tournament_locked(tournament) <= 0:
                continue
            tournament.virtual_fill_completed = True
            tournament.save(update_fields=["virtual_fill_completed", "updated_at"])
            started += int(_start_tournament_locked(tournament, now=now))
    return started


def _reward_for_rank(rank: int) -> int:
    return _arena_helpers.reward_for_rank(
        rank,
        base_participation_coins=ARENA_BASE_PARTICIPATION_COINS,
        rank_bonus_coins=ARENA_RANK_BONUS_COINS,
    )


def _schedule_round_retry_locked(tournament: ArenaTournament, *, now: datetime) -> None:
    retry_seconds = max(1, min(ARENA_ROUND_RETRY_SECONDS, _round_interval_seconds()))
    tournament.next_round_at = now + timedelta(seconds=retry_seconds)
    tournament.save(update_fields=["next_round_at", "updated_at"])


_collect_round_outcome_entry_ids = _arena_helpers.collect_round_outcome_entry_ids


def _run_tournament_round(tournament_id: int, *, now: datetime) -> bool:
    with transaction.atomic():
        tournament = ArenaTournament.objects.select_for_update().filter(pk=tournament_id).first()
        if not tournament:
            return False
        if tournament.status != ArenaTournament.Status.RUNNING:
            return False
        if not tournament.next_round_at or tournament.next_round_at > now:
            return False
        pending_matches = list(
            ArenaMatch.objects.select_for_update()
            .filter(
                tournament=tournament,
                round_number=tournament.current_round,
                status=ArenaMatch.Status.SCHEDULED,
            )
            .order_by("match_index", "id")
        )
        has_existing_round_matches = (
            tournament.current_round > 0
            and ArenaMatch.objects.filter(tournament=tournament, round_number=tournament.current_round).exists()
        )

        if not pending_matches:
            if not has_existing_round_matches:
                next_round_number = max(1, tournament.current_round + 1)
                return _schedule_round_locked(tournament, round_number=next_round_number, now=now)
            pending_match_ids: list[int] = []
        else:
            pending_match_ids = [match.id for match in pending_matches]

        round_number = tournament.current_round
        # 避免并发 worker 重复处理本轮，先把下次扫描时间推后。
        tournament.next_round_at = now + _round_interval_delta(tournament)
        tournament.save(update_fields=["next_round_at", "updated_at"])

    pending_matches, entry_map = load_round_entries_for_matches(
        arena_match_model=ArenaMatch,
        arena_entry_model=ArenaEntry,
        pending_match_ids=pending_match_ids,
    )
    resolution_failed = resolve_pending_round_matches(
        pending_matches=pending_matches,
        entry_map=entry_map,
        round_number=round_number,
        now=now,
        bye_status=ArenaMatch.Status.BYE,
        forfeit_status=ArenaMatch.Status.FORFEIT,
        save_resolved_match=save_resolved_match,
        resolve_match_locked=partial(
            resolve_match_locked,
            max_guests_per_entry=ARENA_MAX_GUESTS_PER_ENTRY,
            arena_match_resolution_error=ArenaMatchResolutionError,
            logger=logger,
        ),
        arena_match_resolution_error=ArenaMatchResolutionError,
    )

    with transaction.atomic():
        return finalize_round_state_locked(
            arena_tournament_model=ArenaTournament,
            arena_match_model=ArenaMatch,
            arena_entry_model=ArenaEntry,
            arena_entry_guest_model=ArenaEntryGuest,
            tournament_id=tournament_id,
            round_number=round_number,
            now=now,
            running_status=ArenaTournament.Status.RUNNING,
            scheduled_status=ArenaMatch.Status.SCHEDULED,
            registered_status=ArenaEntry.Status.REGISTERED,
            eliminated_status=ArenaEntry.Status.ELIMINATED,
            resolution_failed=resolution_failed,
            collect_round_outcome_entry_ids=_collect_round_outcome_entry_ids,
            schedule_round_retry_locked=_schedule_round_retry_locked,
            finalize_tournament_locked=partial(
                finalize_tournament_locked,
                calculate_ranked_entries=_arena_helpers.calculate_ranked_entries,
                reward_for_rank=_reward_for_rank,
                logger=logger,
            ),
            schedule_round_locked=_schedule_round_locked,
            increase_guest_loyalty_by_ids=increase_guest_loyalty_by_ids,
        )


def _collect_due_arena_tournament_ids_for_manor(
    manor: Manor,
    *,
    now: datetime,
    limit: int,
) -> tuple[list[int], list[int]]:
    normalized_limit = max(1, int(limit))
    recruiting_ids = list(
        ArenaTournament.objects.filter(status=ArenaTournament.Status.RECRUITING)
        .annotate(
            total_entry_count=Count("entries", distinct=True),
            manor_entry_count=Count("entries", filter=Q(entries__manor=manor), distinct=True),
        )
        .filter(manor_entry_count__gt=0, total_entry_count__gte=F("player_limit"))
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:normalized_limit]
    )
    running_ids = list(
        ArenaTournament.objects.filter(
            status=ArenaTournament.Status.RUNNING,
            next_round_at__isnull=False,
            next_round_at__lte=now,
        )
        .annotate(manor_entry_count=Count("entries", filter=Q(entries__manor=manor), distinct=True))
        .filter(manor_entry_count__gt=0)
        .order_by("next_round_at", "id")
        .values_list("id", flat=True)[:normalized_limit]
    )
    return recruiting_ids, running_ids


def _release_orphaned_arena_guests(manor: Manor) -> int:
    active_guest_ids = ArenaEntryGuest.objects.filter(
        guest__manor=manor,
        entry__status=ArenaEntry.Status.REGISTERED,
        entry__tournament__status__in=[ArenaTournament.Status.RECRUITING, ArenaTournament.Status.RUNNING],
    ).values_list("guest_id", flat=True)
    return (
        Guest.objects.filter(manor=manor, status=GuestStatus.ARENA)
        .exclude(id__in=active_guest_ids)
        .update(status=GuestStatus.IDLE)
    )


def refresh_arena_activity(manor: Manor, *, now: datetime | None = None, limit: int = 20) -> int:
    current_time = now or timezone.now()
    recruiting_ids, running_ids = _collect_due_arena_tournament_ids_for_manor(
        manor,
        now=current_time,
        limit=limit,
    )

    started_count = 0
    processed_count = 0
    for tournament_id in recruiting_ids:
        if start_tournament_if_ready(ArenaTournament(id=tournament_id), now=current_time):
            started_count += 1
    for tournament_id in running_ids:
        if _run_tournament_round(tournament_id, now=current_time):
            processed_count += 1

    _release_orphaned_arena_guests(manor)
    return started_count + processed_count


def run_due_arena_rounds(*, now: datetime | None = None, limit: int = 20) -> int:
    current_time = now or timezone.now()
    due_ids = list(
        ArenaTournament.objects.filter(
            status=ArenaTournament.Status.RUNNING,
            next_round_at__isnull=False,
            next_round_at__lte=current_time,
        )
        .order_by("next_round_at")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )

    processed = 0
    for tournament_id in due_ids:
        if _run_tournament_round(tournament_id, now=current_time):
            processed += 1
    return processed


def cleanup_expired_tournaments(
    *, now: datetime | None = None, grace_seconds: int = ARENA_COMPLETED_RETENTION_SECONDS, limit: int = 50
) -> int:
    current_time = now or timezone.now()
    return _cleanup_expired_tournaments(now=current_time, grace_seconds=grace_seconds, limit=limit)


# exchange_arena_reward 及 ArenaExchangeResult 已移至 exchange_helpers，通过顶部 import 重新导出。
