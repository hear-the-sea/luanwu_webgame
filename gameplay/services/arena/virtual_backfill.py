from __future__ import annotations

import logging
import random
from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations
from math import comb

from django.db.models import Prefetch

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    BotProfile,
)
from gameplay.services.arena.snapshots import build_entry_guest_snapshot
from guests.models import Guest, GuestStatus

logger = logging.getLogger(__name__)

MIN_LINEUP_POWER_PERCENT = 80
MAX_LINEUP_POWER_PERCENT = 120
MAX_RANDOM_LINEUP_COMBINATIONS = 64
CANDIDATE_SCAN_CHUNK_SIZE = 100
CANDIDATE_LOCK_BATCH_SIZE = 100


@dataclass(frozen=True)
class ArenaReferenceTarget:
    guest_count: int
    team_power: int
    prestige_band: str


@dataclass(frozen=True)
class BotLineupEvaluation:
    snapshots: tuple[dict, ...]
    selected_power: int
    is_ready: bool


def _snapshot_power(snapshot: dict) -> int:
    return int(snapshot.get("attack") or 0) + int(snapshot.get("defense") or 0) + int(snapshot.get("max_hp") or 0) // 10


def _lineup_power(snapshots: Sequence[dict]) -> int:
    return sum(_snapshot_power(snapshot) for snapshot in snapshots)


def _entry_power(entry: ArenaEntry | ArenaCoopEntry) -> int:
    return _lineup_power(_reference_snapshots(entry))


def _median_entry(entries: Sequence[ArenaEntry | ArenaCoopEntry]) -> ArenaEntry | ArenaCoopEntry:
    return sorted(entries, key=_entry_power)[len(entries) // 2]


def _reference_snapshots(entry: ArenaEntry | ArenaCoopEntry) -> list[dict]:
    snapshots = []
    for link in entry.entry_guests.all():
        snapshot = link.snapshot or (build_entry_guest_snapshot(link.guest) if link.guest else None)
        if snapshot:
            snapshots.append(deepcopy(snapshot))
    return snapshots


def _log_backfill_completed(
    *,
    mode: str,
    event_id: int,
    real_entry_count: int,
    virtual_entry_count: int,
    target_team_power: int,
) -> None:
    logger.info(
        "virtual arena backfill completed: mode=%s event_id=%s real_entries=%s virtual_entries=%s target_power=%s",
        mode,
        event_id,
        real_entry_count,
        virtual_entry_count,
        target_team_power,
        extra={
            "event": "arena_virtual_backfill_completed",
            "mode": mode,
            "event_id": event_id,
            "real_entry_count": real_entry_count,
            "virtual_entry_count": virtual_entry_count,
            "target_team_power": target_team_power,
        },
    )


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
        BotProfile.objects.filter(state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING])
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
    if profile_ids is not None:
        queryset = queryset.filter(id__in=list(dict.fromkeys(int(profile_id) for profile_id in profile_ids)))
    return queryset


def _lock_candidates(
    *,
    profile_ids: Sequence[int],
    excluded_manor_ids: Iterable[int],
    limit: int,
):
    return (
        BotProfile.objects.select_for_update(skip_locked=True)
        .filter(
            id__in=profile_ids,
            state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING],
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
        .order_by("id")[: max(0, int(limit))]
    )


def _random_lineup_indexes(
    *,
    guest_count: int,
    lineup_size: int,
    rng: random.Random,
) -> list[tuple[int, ...]]:
    total_combinations = comb(guest_count, lineup_size)
    indexes: list[tuple[int, ...]]
    if total_combinations <= MAX_RANDOM_LINEUP_COMBINATIONS:
        indexes = list(combinations(range(guest_count), lineup_size))
        rng.shuffle(indexes)
        return indexes

    indexes = []
    seen: set[tuple[int, ...]] = set()
    max_attempts = MAX_RANDOM_LINEUP_COMBINATIONS * 8
    for _attempt in range(max_attempts):
        candidate = tuple(sorted(rng.sample(range(guest_count), lineup_size)))
        if candidate in seen:
            continue
        seen.add(candidate)
        indexes.append(candidate)
        if len(indexes) >= MAX_RANDOM_LINEUP_COMBINATIONS:
            break
    return indexes


def evaluate_bot_lineup(
    profile: BotProfile,
    *,
    mode: str,
    event_id: int,
    target_guest_count: int,
    target_team_power: int,
) -> BotLineupEvaluation:
    if target_guest_count <= 0 or target_team_power <= 0:
        return BotLineupEvaluation((), 0, False)
    prefetched_guests = getattr(profile.manor, "arena_idle_guests", None)
    guests = (
        list(prefetched_guests)
        if prefetched_guests is not None
        else list(profile.manor.guests.filter(status=GuestStatus.IDLE).select_related("template").order_by("id"))
    )
    if not guests:
        return BotLineupEvaluation((), 0, False)

    snapshots = [build_entry_guest_snapshot(guest) for guest in guests]
    lineup_size = min(target_guest_count, len(snapshots))
    rng = random.Random(f"{mode}:{event_id}:{profile.id}")
    rows: list[tuple[tuple[dict, ...], int]] = []
    for indexes in _random_lineup_indexes(
        guest_count=len(snapshots),
        lineup_size=lineup_size,
        rng=rng,
    ):
        lineup = tuple(deepcopy(snapshots[index]) for index in indexes)
        rows.append((lineup, _lineup_power(lineup)))

    ready = [
        row
        for row in rows
        if target_team_power * MIN_LINEUP_POWER_PERCENT <= row[1] * 100 <= target_team_power * MAX_LINEUP_POWER_PERCENT
    ]
    if ready:
        lineup, power = rng.choice(ready)
        return BotLineupEvaluation(lineup, power, True)

    below = [row for row in rows if row[1] * 100 < target_team_power * MIN_LINEUP_POWER_PERCENT]
    if not below:
        return BotLineupEvaluation((), 0, False)
    lineup, power = max(below, key=lambda row: row[1])
    return BotLineupEvaluation(lineup, power, False)


def _select_bot_lineup(
    profile: BotProfile,
    *,
    mode: str,
    event_id: int,
    target_guest_count: int,
    target_team_power: int,
) -> list[dict]:
    evaluation = evaluate_bot_lineup(
        profile,
        mode=mode,
        event_id=event_id,
        target_guest_count=target_guest_count,
        target_team_power=target_team_power,
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
) -> list[int]:
    selected: list[int] = []
    for profile in _candidates(
        excluded_manor_ids,
        profile_ids=candidate_profile_ids,
    ).iterator(chunk_size=CANDIDATE_SCAN_CHUNK_SIZE):
        lineup = _select_bot_lineup(
            profile,
            mode=mode,
            event_id=event_id,
            target_guest_count=target_guest_count,
            target_team_power=target_team_power,
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
) -> list[tuple[BotProfile, list[dict]]]:
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
                lineup = _select_bot_lineup(
                    profile,
                    mode=mode,
                    event_id=event_id,
                    target_guest_count=target_guest_count,
                    target_team_power=target_team_power,
                )
                if lineup:
                    selected.append((profile, lineup))
    return selected


def _tournament_reserved_manor_ids(tournament: ArenaTournament) -> set[int]:
    return set(
        ArenaEntry.objects.filter(
            tournament__status__in=[ArenaTournament.Status.RECRUITING, ArenaTournament.Status.RUNNING]
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


def backfill_tournament_locked(
    tournament: ArenaTournament,
    *,
    candidate_profile_ids: Sequence[int] | None = None,
) -> int:
    registered_entries = tournament.entries.filter(status=ArenaEntry.Status.REGISTERED)
    real_entries = list(registered_entries.filter(source=ArenaEntry.Source.PLAYER).prefetch_related("entry_guests"))
    needed = max(0, tournament.player_limit - registered_entries.count())
    reference_entry = _median_entry(real_entries) if real_entries else None
    snapshots = _reference_snapshots(reference_entry) if reference_entry else []
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
    target_team_power = _lineup_power(snapshots)
    eligible_profile_ids = _eligible_bot_profile_ids(
        excluded_manor_ids=excluded_manor_ids,
        mode="tournament",
        event_id=tournament.id,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
        candidate_profile_ids=candidate_profile_ids,
    )
    candidates = _lock_eligible_bot_lineups(
        profile_ids=eligible_profile_ids,
        excluded_manor_ids=_tournament_excluded_manor_ids(tournament),
        needed=needed,
        mode="tournament",
        event_id=tournament.id,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
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
    assert reference_entry is not None
    for profile, lineup in candidates:
        entry = ArenaEntry.objects.create(tournament=tournament, manor=profile.manor, source=ArenaEntry.Source.VIRTUAL)
        ArenaEntryGuest.objects.bulk_create(
            [ArenaEntryGuest(entry=entry, guest=None, snapshot=snapshot) for snapshot in lineup]
        )
    _log_backfill_completed(
        mode="tournament",
        event_id=tournament.id,
        real_entry_count=len(real_entries),
        virtual_entry_count=len(candidates),
        target_team_power=target_team_power,
    )
    return len(candidates)


def backfill_coop_event_locked(
    event: ArenaCoopEvent,
    *,
    candidate_profile_ids: Sequence[int] | None = None,
) -> int:
    registered_entries = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED)
    real_entries = list(registered_entries.filter(source=ArenaCoopEntry.Source.PLAYER).prefetch_related("entry_guests"))
    needed = max(0, event.player_limit - registered_entries.count())
    reference_entry = _median_entry(real_entries) if real_entries else None
    snapshots = _reference_snapshots(reference_entry)[: event.guest_limit_per_entry] if reference_entry else []
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
    excluded_manor_ids = _coop_excluded_manor_ids(event)
    target_team_power = _lineup_power(snapshots)
    eligible_profile_ids = _eligible_bot_profile_ids(
        excluded_manor_ids=excluded_manor_ids,
        mode="coop",
        event_id=event.id,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
        candidate_profile_ids=candidate_profile_ids,
    )
    candidates = _lock_eligible_bot_lineups(
        profile_ids=eligible_profile_ids,
        excluded_manor_ids=_coop_excluded_manor_ids(event),
        needed=needed,
        mode="coop",
        event_id=event.id,
        target_guest_count=len(snapshots),
        target_team_power=target_team_power,
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
    assert reference_entry is not None
    for profile, lineup in candidates:
        entry = ArenaCoopEntry.objects.create(event=event, manor=profile.manor, source=ArenaCoopEntry.Source.VIRTUAL)
        ArenaCoopEntryGuest.objects.bulk_create(
            [
                ArenaCoopEntryGuest(entry=entry, guest=None, slot_index=index, snapshot=snapshot)
                for index, snapshot in enumerate(lineup)
            ]
        )
    _log_backfill_completed(
        mode="coop",
        event_id=event.id,
        real_entry_count=len(real_entries),
        virtual_entry_count=len(candidates),
        target_team_power=target_team_power,
    )
    return len(candidates)
