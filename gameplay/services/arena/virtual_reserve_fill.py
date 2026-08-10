from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from hashlib import blake2b
from typing import cast

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotProfile,
)
from gameplay.services.virtual_player_core.profile_store import record_arena_participation
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES
from guests.models import Guest, GuestStatus

from .coop_rules import load_arena_coop_rules
from .lifecycle_helpers import move_coop_event_to_preparing_locked, start_tournament_locked
from .rules import load_arena_rules
from .virtual_backfill import backfill_coop_event_locked, backfill_tournament_locked
from .virtual_lineups import lineup_power
from .virtual_protection import is_virtual_profile_arena_match_eligible, with_arena_reconciliation_state
from .virtual_reserve_observability import log_demand_event
from .virtual_reserve_policy import virtual_roster_target_count
from .virtual_reserve_pool import evaluate_bot_lineup, record_demand_failure_locked, replenish_virtual_reserve
from .virtual_reserve_reconcile import (
    reconcile_coop_demand,
    reconcile_coop_demand_locked,
    reconcile_tournament_demand,
    reconcile_tournament_demand_locked,
)
from .virtual_reserve_references import median_entry, reference_snapshots

logger = logging.getLogger(__name__)

PARTICIPATION_COOLDOWN = timedelta(hours=24)
CANDIDATE_SCAN_CHUNK_SIZE = 100
CANDIDATE_LOCK_BATCH_SIZE = 100


class _AtomicFillAborted(RuntimeError):
    def __init__(self, demand_id: int, reason: str):
        super().__init__(reason)
        self.demand_id = int(demand_id)
        self.reason = str(reason)


def _log_backfill_deferred(
    *,
    mode: str,
    event_id: int,
    reason: str,
    needed_entry_count: int,
    available_bot_count: int,
) -> None:
    logger.warning(
        "virtual arena backfill deferred: mode=%s event_id=%s reason=%s needed=%s available_bots=%s",
        mode,
        event_id,
        reason,
        needed_entry_count,
        available_bot_count,
        extra={
            "event": "arena_virtual_backfill_deferred",
            "mode": mode,
            "event_id": event_id,
            "reason": reason,
            "needed_entry_count": needed_entry_count,
            "available_bot_count": available_bot_count,
        },
    )


def _candidates(
    excluded_manor_ids: Iterable[int],
    *,
    profile_ids: Sequence[int] | None = None,
):
    queryset = (
        BotProfile.objects.filter(
            state__in=VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
            engine_version=2,
            policy_version=2,
        )
        .exclude(manor_id__in=set(excluded_manor_ids))
        .select_related("manor")
        .prefetch_related(
            Prefetch(
                "manor__guests",
                queryset=Guest.objects.filter(status=GuestStatus.IDLE).select_related("template").order_by("id"),
                to_attr="arena_idle_guests",
            )
        )
        .order_by("id")
    )
    queryset = with_arena_reconciliation_state(queryset)
    if profile_ids is not None:
        queryset = queryset.filter(id__in=list(dict.fromkeys(int(profile_id) for profile_id in profile_ids)))
    return queryset


def _lock_candidates(
    *,
    profile_ids: Sequence[int],
    excluded_manor_ids: Iterable[int],
    limit: int,
):
    queryset = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .filter(
            id__in=profile_ids,
            state__in=VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
            engine_version=2,
            policy_version=2,
        )
        .exclude(manor_id__in=set(excluded_manor_ids))
        .select_related("manor")
        .prefetch_related(
            Prefetch(
                "manor__guests",
                queryset=Guest.objects.filter(status=GuestStatus.IDLE).select_related("template").order_by("id"),
                to_attr="arena_idle_guests",
            )
        )
        .order_by("id")
    )
    return with_arena_reconciliation_state(queryset)[: max(0, int(limit))]


def _select_bot_lineup(
    profile: BotProfile,
    *,
    mode: str,
    event_id: int,
    target_guest_count: int,
    target_team_power: int,
    max_lineup_size: int | None = None,
) -> list[dict]:
    resolved_max_lineup_size = max_lineup_size
    if resolved_max_lineup_size is None:
        resolved_max_lineup_size = int(
            load_arena_rules()["registration"]["max_guests_per_entry"]
            if str(mode) == "tournament"
            else load_arena_coop_rules()["registration"]["guest_limit_per_entry"]
        )
    preferred_guest_count = virtual_roster_target_count(
        reference_guest_count=int(target_guest_count),
        max_lineup_size=resolved_max_lineup_size,
        mode=str(mode),
        event_id=int(event_id),
        profile_id=int(profile.id),
    )
    evaluation = evaluate_bot_lineup(
        profile,
        mode=mode,
        event_id=event_id,
        target_guest_count=target_guest_count,
        target_team_power=target_team_power,
        max_lineup_size=resolved_max_lineup_size,
        preferred_guest_count=preferred_guest_count,
    )
    return list(evaluation.snapshots) if evaluation.is_ready else []


def _eligible_bot_profile_ids(
    *,
    excluded_manor_ids: Iterable[int],
    mode: str,
    event_id: int,
    target_guest_count: int,
    target_team_power: int,
    candidate_profile_ids: Sequence[int] | None = None,
    max_lineup_size: int | None = None,
    now=None,
) -> list[int]:
    current_time = now or timezone.now()
    selected: list[int] = []
    for profile in _candidates(
        excluded_manor_ids,
        profile_ids=candidate_profile_ids,
    ).iterator(chunk_size=CANDIDATE_SCAN_CHUNK_SIZE):
        if not is_virtual_profile_arena_match_eligible(
            profile,
            now=current_time,
        ):
            continue
        lineup = _select_bot_lineup(
            profile,
            mode=mode,
            event_id=event_id,
            target_guest_count=target_guest_count,
            target_team_power=target_team_power,
            max_lineup_size=max_lineup_size,
        )
        if not lineup:
            continue
        selected.append(profile.id)
    return selected


def _lock_eligible_bot_lineups(
    *,
    profile_ids: Sequence[int],
    excluded_manor_ids: Iterable[int],
    needed: int,
    mode: str,
    event_id: int,
    target_guest_count: int,
    target_team_power: int,
    max_lineup_size: int | None = None,
    now=None,
) -> list[tuple[BotProfile, list[dict]]]:
    current_time = now or timezone.now()
    selected: list[tuple[BotProfile, list[dict]]] = []
    excluded_manor_ids = set(excluded_manor_ids)
    for offset in range(0, len(profile_ids), CANDIDATE_LOCK_BATCH_SIZE):
        pending_profile_ids = list(profile_ids[offset : offset + CANDIDATE_LOCK_BATCH_SIZE])
        while pending_profile_ids and len(selected) < needed:
            locked_profiles = list(
                _lock_candidates(
                    profile_ids=pending_profile_ids,
                    excluded_manor_ids=excluded_manor_ids,
                    limit=needed - len(selected),
                )
            )
            if not locked_profiles:
                break
            locked_profile_ids = {profile.id for profile in locked_profiles}
            pending_profile_ids = [
                profile_id for profile_id in pending_profile_ids if profile_id not in locked_profile_ids
            ]
            for profile in locked_profiles:
                if not is_virtual_profile_arena_match_eligible(
                    profile,
                    now=current_time,
                ):
                    continue
                lineup = _select_bot_lineup(
                    profile,
                    mode=mode,
                    event_id=event_id,
                    target_guest_count=target_guest_count,
                    target_team_power=target_team_power,
                    max_lineup_size=max_lineup_size,
                )
                if lineup:
                    selected.append((profile, lineup))
    return selected


def _tournament_reserved_manor_ids(tournament: ArenaTournament) -> set[int]:
    return set(
        ArenaEntry.objects.filter(
            tournament__status__in=[
                ArenaTournament.Status.RECRUITING,
                ArenaTournament.Status.RUNNING,
            ]
        )
        .exclude(tournament=tournament)
        .values_list("manor_id", flat=True)
    )


def _tournament_excluded_manor_ids(tournament: ArenaTournament) -> set[int]:
    excluded = set(tournament.entries.filter(status=ArenaEntry.Status.REGISTERED).values_list("manor_id", flat=True))
    excluded.update(_tournament_reserved_manor_ids(tournament))
    return excluded


def _coop_reserved_manor_ids(event: ArenaCoopEvent) -> set[int]:
    return set(
        ArenaCoopEntry.objects.filter(
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ],
            status=ArenaCoopEntry.Status.REGISTERED,
        )
        .exclude(event=event)
        .values_list("manor_id", flat=True)
    )


def _coop_excluded_manor_ids(event: ArenaCoopEvent) -> set[int]:
    excluded = set(event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED).values_list("manor_id", flat=True))
    excluded.update(_coop_reserved_manor_ids(event))
    return excluded


def prepare_tournament_backfill_locked(
    tournament: ArenaTournament,
    *,
    candidate_profile_ids: Sequence[int] | None = None,
) -> int:
    current_time = timezone.now()
    registered_entries = tournament.entries.filter(status=ArenaEntry.Status.REGISTERED)
    real_entries = list(registered_entries.filter(source=ArenaEntry.Source.PLAYER).prefetch_related("entry_guests"))
    needed = max(0, int(tournament.player_limit) - registered_entries.count())
    reference_entry = median_entry(real_entries) if real_entries else None
    snapshots = reference_snapshots(reference_entry) if reference_entry else []
    if not needed:
        return 0
    if not real_entries:
        _log_backfill_deferred(
            mode="tournament",
            event_id=tournament.id,
            reason="missing_real_entries",
            needed_entry_count=needed,
            available_bot_count=0,
        )
        return 0
    if not snapshots:
        _log_backfill_deferred(
            mode="tournament",
            event_id=tournament.id,
            reason="missing_reference_snapshots",
            needed_entry_count=needed,
            available_bot_count=0,
        )
        return 0
    excluded_manor_ids = _tournament_excluded_manor_ids(tournament)
    target_team_power = lineup_power(snapshots)
    max_lineup_size = int(load_arena_rules()["registration"]["max_guests_per_entry"])
    eligible_profile_ids = _eligible_bot_profile_ids(
        excluded_manor_ids=excluded_manor_ids,
        mode="tournament",
        event_id=tournament.id,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
        max_lineup_size=max_lineup_size,
        candidate_profile_ids=candidate_profile_ids,
        now=current_time,
    )
    candidates = _lock_eligible_bot_lineups(
        profile_ids=eligible_profile_ids,
        excluded_manor_ids=_tournament_excluded_manor_ids(tournament),
        needed=needed,
        mode="tournament",
        event_id=tournament.id,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
        max_lineup_size=max_lineup_size,
        now=current_time,
    )
    if len(candidates) < needed:
        _log_backfill_deferred(
            mode="tournament",
            event_id=tournament.id,
            reason="insufficient_eligible_bots",
            needed_entry_count=needed,
            available_bot_count=len(candidates),
        )
        return 0
    return backfill_tournament_locked(
        tournament,
        locked_lineups=candidates,
        target_team_power=target_team_power,
    )


def prepare_coop_backfill_locked(
    event: ArenaCoopEvent,
    *,
    candidate_profile_ids: Sequence[int] | None = None,
) -> int:
    current_time = timezone.now()
    registered_entries = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED)
    real_entries = list(registered_entries.filter(source=ArenaCoopEntry.Source.PLAYER).prefetch_related("entry_guests"))
    needed = max(0, int(event.player_limit) - registered_entries.count())
    reference_entry = median_entry(real_entries) if real_entries else None
    snapshots = reference_snapshots(reference_entry)[: int(event.guest_limit_per_entry)] if reference_entry else []
    if not needed:
        return 0
    if not real_entries:
        _log_backfill_deferred(
            mode="coop",
            event_id=event.id,
            reason="missing_real_entries",
            needed_entry_count=needed,
            available_bot_count=0,
        )
        return 0
    if not snapshots:
        _log_backfill_deferred(
            mode="coop",
            event_id=event.id,
            reason="missing_reference_snapshots",
            needed_entry_count=needed,
            available_bot_count=0,
        )
        return 0
    target_team_power = lineup_power(snapshots)
    max_lineup_size = max(1, int(event.guest_limit_per_entry))
    eligible_profile_ids = _eligible_bot_profile_ids(
        excluded_manor_ids=_coop_excluded_manor_ids(event),
        mode="coop",
        event_id=event.id,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
        max_lineup_size=max_lineup_size,
        candidate_profile_ids=candidate_profile_ids,
        now=current_time,
    )
    candidates = _lock_eligible_bot_lineups(
        profile_ids=eligible_profile_ids,
        excluded_manor_ids=_coop_excluded_manor_ids(event),
        needed=needed,
        mode="coop",
        event_id=event.id,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
        max_lineup_size=max_lineup_size,
        now=current_time,
    )
    if len(candidates) < needed:
        _log_backfill_deferred(
            mode="coop",
            event_id=event.id,
            reason="insufficient_eligible_bots",
            needed_entry_count=needed,
            available_bot_count=len(candidates),
        )
        return 0
    return backfill_coop_event_locked(
        event,
        locked_lineups=candidates,
        target_team_power=target_team_power,
    )


def _stable_member_rank(*, mode: str, event_id: int, version: int, profile_id: int) -> bytes:
    payload = f"{mode}:{event_id}:{version}:{profile_id}".encode("utf-8")
    return blake2b(payload, digest_size=16).digest()


def _ordered_ready_members(
    demand: ArenaVirtualDemand,
    *,
    now,
) -> list[ArenaVirtualReserveMember]:
    members = list(
        demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.READY).select_related(
            "profile", "profile__manor"
        )
    )
    mode = "tournament" if demand.tournament_id is not None else "coop"
    event_id = int(demand.tournament_id or demand.coop_event_id or 0)
    cutoff = now - PARTICIPATION_COOLDOWN
    fresh = [
        member
        for member in members
        if member.profile.last_arena_participated_at is None or member.profile.last_arena_participated_at < cutoff
    ]
    recent = [
        member
        for member in members
        if member.profile.last_arena_participated_at is not None and member.profile.last_arena_participated_at >= cutoff
    ]

    fresh.sort(
        key=lambda member: _stable_member_rank(
            mode=mode,
            event_id=event_id,
            version=demand.version,
            profile_id=member.profile_id,
        )
    )
    recent.sort(
        key=lambda member: (
            member.profile.last_arena_participated_at,
            _stable_member_rank(
                mode=mode,
                event_id=event_id,
                version=demand.version,
                profile_id=member.profile_id,
            ),
        )
    )
    return fresh + recent


def _lock_selected_ready_members(
    demand: ArenaVirtualDemand,
    *,
    selected_members: Sequence[ArenaVirtualReserveMember],
) -> list[ArenaVirtualReserveMember]:
    selected_ids = [int(member.id) for member in selected_members]
    locked = list(
        ArenaVirtualReserveMember.objects.select_for_update(skip_locked=True)
        .filter(
            id__in=selected_ids,
            demand=demand,
            state=ArenaVirtualReserveMember.State.READY,
        )
        .select_related("profile", "profile__manor")
        .order_by("id")
    )
    locked_by_id = {int(member.id): member for member in locked}
    if set(locked_by_id) != set(selected_ids):
        return []
    return [locked_by_id[member_id] for member_id in selected_ids]


def _lock_ready_member_lineups(
    demand: ArenaVirtualDemand,
    *,
    members: Sequence[ArenaVirtualReserveMember],
    now=None,
) -> list[tuple[BotProfile, list[dict]]]:
    current_time = now or timezone.now()
    mode = "tournament" if demand.tournament_id is not None else "coop"
    event_id = int(demand.tournament_id or demand.coop_event_id or 0)
    if mode == "tournament":
        assert demand.tournament is not None
        excluded_manor_ids = _tournament_excluded_manor_ids(demand.tournament)
        max_lineup_size = int(load_arena_rules()["registration"]["max_guests_per_entry"])
    else:
        assert demand.coop_event is not None
        excluded_manor_ids = _coop_excluded_manor_ids(demand.coop_event)
        max_lineup_size = max(1, int(demand.coop_event.guest_limit_per_entry))

    profile_ids = [int(member.profile_id) for member in members]
    eligible_profile_ids = _eligible_bot_profile_ids(
        excluded_manor_ids=excluded_manor_ids,
        mode=mode,
        event_id=event_id,
        target_guest_count=int(demand.target_guest_count),
        target_team_power=int(demand.target_team_power),
        max_lineup_size=max_lineup_size,
        candidate_profile_ids=profile_ids,
        now=current_time,
    )
    if set(eligible_profile_ids) != set(profile_ids):
        return []
    return _lock_eligible_bot_lineups(
        profile_ids=eligible_profile_ids,
        excluded_manor_ids=excluded_manor_ids,
        needed=len(profile_ids),
        mode=mode,
        event_id=event_id,
        target_guest_count=int(demand.target_guest_count),
        target_team_power=int(demand.target_team_power),
        max_lineup_size=max_lineup_size,
        now=current_time,
    )


def _record_fill_deferred(*, demand_id: int, reason: str, now) -> None:
    with transaction.atomic():
        demand = (
            ArenaVirtualDemand.objects.select_for_update()
            .filter(pk=demand_id, status=ArenaVirtualDemand.Status.ACTIVE)
            .first()
        )
        if demand is None:
            return
        record_demand_failure_locked(demand, reason=reason, now=now)
        log_demand_event(
            "arena_virtual_fill_deferred",
            demand,
            message="arena virtual fill deferred",
            level=logging.WARNING,
            failure_reason=reason,
        )


def _complete_demand_fill(
    *,
    demand: ArenaVirtualDemand,
    profile_ids: list[int] | tuple[int, ...],
    now,
    used_cooldown: bool = False,
) -> None:
    wait_seconds = max(0.0, (now - demand.created_at).total_seconds())
    record_arena_participation(profile_ids, participated_at=now)
    demand.reserve_members.filter(growth_claim_token__isnull=True).delete()
    demand.status = ArenaVirtualDemand.Status.SATISFIED
    demand.missing_entry_count = 0
    demand.reserve_target_count = 0
    demand.warm_target_count = 0
    demand.next_retry_at = None
    demand.last_checked_at = now
    demand.last_failure_reason = ""
    demand.consecutive_failure_count = 0
    demand.last_progress_at = now
    demand.admission_paused_at = None
    demand.admission_pause_reason = ""
    demand.admission_probe_target_ordinal = None
    demand.save(
        update_fields=[
            "status",
            "missing_entry_count",
            "reserve_target_count",
            "warm_target_count",
            "next_retry_at",
            "last_checked_at",
            "last_failure_reason",
            "consecutive_failure_count",
            "last_progress_at",
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "updated_at",
        ]
    )
    log_demand_event(
        "arena_virtual_fill_completed",
        demand,
        message="arena virtual fill completed",
        selected_profile_ids=[int(profile_id) for profile_id in profile_ids],
        used_cooldown=bool(used_cooldown),
        wait_seconds=wait_seconds,
    )


def _fill_due_reserve(
    *,
    mode: str,
    event_id: int,
    now,
    emit_shortage_observation: bool,
) -> int:
    event_model = ArenaTournament if mode == "tournament" else ArenaCoopEvent
    event = event_model.objects.select_for_update().filter(pk=event_id).first()
    if event is None:
        return 0
    if (
        event.status != event_model.Status.RECRUITING
        or event.virtual_fill_completed
        or event.virtual_fill_at is None
        or event.virtual_fill_at > now
    ):
        return 0

    if mode == "tournament":
        demand = reconcile_tournament_demand_locked(
            cast(ArenaTournament, event),
            now=now,
            emit_shortage_observation=emit_shortage_observation,
        )
    else:
        demand = reconcile_coop_demand_locked(
            cast(ArenaCoopEvent, event),
            now=now,
            emit_shortage_observation=emit_shortage_observation,
        )
    if demand is None:
        return 0
    demand = ArenaVirtualDemand.objects.select_for_update().select_related("tournament", "coop_event").get(pk=demand.pk)
    if demand.next_retry_at is not None and demand.next_retry_at > now + timedelta(seconds=1):
        return 0
    gap = max(0, int(demand.missing_entry_count))
    if gap <= 0:
        return 0

    ordered_members = _ordered_ready_members(demand, now=now)
    if len(ordered_members) < gap:
        record_demand_failure_locked(demand, reason="insufficient_ready_members", now=now)
        log_demand_event(
            "arena_virtual_fill_deferred",
            demand,
            message="arena virtual fill deferred",
            level=logging.WARNING,
            failure_reason="insufficient_ready_members",
        )
        return 0

    selected_members = _lock_selected_ready_members(
        demand,
        selected_members=ordered_members[:gap],
    )
    if len(selected_members) != gap:
        raise _AtomicFillAborted(demand.id, "ready_member_lock_failed")

    locked_lineups = _lock_ready_member_lineups(
        demand,
        members=selected_members,
        now=now,
    )
    if len(locked_lineups) != gap:
        raise _AtomicFillAborted(demand.id, "ready_member_revalidation_failed")

    cooldown_cutoff = now - PARTICIPATION_COOLDOWN
    used_cooldown = any(
        profile.last_arena_participated_at is not None and profile.last_arena_participated_at >= cooldown_cutoff
        for profile, _lineup in locked_lineups
    )
    profile_ids = [int(profile.id) for profile, _lineup in locked_lineups]
    if mode == "tournament":
        filled = backfill_tournament_locked(
            cast(ArenaTournament, event),
            locked_lineups=locked_lineups,
            target_team_power=int(demand.target_team_power),
        )
    else:
        filled = backfill_coop_event_locked(
            cast(ArenaCoopEvent, event),
            locked_lineups=locked_lineups,
            target_team_power=int(demand.target_team_power),
        )
    if filled != gap:
        raise _AtomicFillAborted(demand.id, "ready_member_revalidation_failed")

    if mode == "tournament":
        if not start_tournament_locked(cast(ArenaTournament, event), now=now):
            raise _AtomicFillAborted(demand.id, "tournament_start_failed")
    elif not move_coop_event_to_preparing_locked(cast(ArenaCoopEvent, event), now=now):
        raise _AtomicFillAborted(demand.id, "coop_prepare_failed")

    _complete_demand_fill(
        demand=demand,
        profile_ids=profile_ids,
        now=now,
        used_cooldown=used_cooldown,
    )
    return filled


def fill_due_tournament_reserve(
    tournament_id: int,
    *,
    now=None,
    emit_shortage_observation: bool = True,
) -> int:
    current_time = now or timezone.now()
    try:
        with transaction.atomic():
            return _fill_due_reserve(
                mode="tournament",
                event_id=int(tournament_id),
                now=current_time,
                emit_shortage_observation=emit_shortage_observation,
            )
    except _AtomicFillAborted as exc:
        _record_fill_deferred(demand_id=exc.demand_id, reason=exc.reason, now=current_time)
        return 0


def fill_due_coop_reserve(
    event_id: int,
    *,
    now=None,
    emit_shortage_observation: bool = True,
) -> int:
    current_time = now or timezone.now()
    try:
        with transaction.atomic():
            return _fill_due_reserve(
                mode="coop",
                event_id=int(event_id),
                now=current_time,
                emit_shortage_observation=emit_shortage_observation,
            )
    except _AtomicFillAborted as exc:
        _record_fill_deferred(demand_id=exc.demand_id, reason=exc.reason, now=current_time)
        return 0


def start_due_virtual_backfill_tournaments(*, now: datetime | None = None, limit: int = 20, manor=None) -> int:
    current_time = now or timezone.now()
    candidates = ArenaTournament.objects.filter(
        status=ArenaTournament.Status.RECRUITING,
        virtual_fill_completed=False,
        virtual_fill_at__lte=current_time,
    )
    if manor is not None:
        candidates = candidates.filter(entries__manor=manor).distinct()
    candidate_ids = list(
        candidates.order_by("virtual_fill_at", "id").values_list("id", flat=True)[: max(1, int(limit))]
    )
    started = 0
    for tournament_id in candidate_ids:
        demand = reconcile_tournament_demand(tournament_id, now=current_time)
        if demand is None:
            continue
        replenish_virtual_reserve(demand.id, now=current_time)
        started += int(
            fill_due_tournament_reserve(
                tournament_id,
                now=current_time,
                emit_shortage_observation=False,
            )
            > 0
        )
    return started


def start_due_virtual_backfill_coop_events(*, now: datetime | None = None, limit: int = 20, manor=None) -> int:
    current_time = now or timezone.now()
    candidates = ArenaCoopEvent.objects.filter(
        status=ArenaCoopEvent.Status.RECRUITING,
        virtual_fill_completed=False,
        virtual_fill_at__lte=current_time,
    )
    if manor is not None:
        candidates = candidates.filter(entries__manor=manor).distinct()
    event_ids = list(candidates.order_by("virtual_fill_at", "id").values_list("id", flat=True)[: max(1, int(limit))])
    prepared = 0
    for event_id in event_ids:
        demand = reconcile_coop_demand(event_id, now=current_time)
        if demand is None:
            continue
        replenish_virtual_reserve(demand.id, now=current_time)
        prepared += int(
            fill_due_coop_reserve(
                event_id,
                now=current_time,
                emit_shortage_observation=False,
            )
            > 0
        )
    return prepared


__all__ = [
    "fill_due_coop_reserve",
    "fill_due_tournament_reserve",
    "prepare_coop_backfill_locked",
    "prepare_tournament_backfill_locked",
    "start_due_virtual_backfill_coop_events",
    "start_due_virtual_backfill_tournaments",
]
