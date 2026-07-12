from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from copy import deepcopy

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    BotProfile,
)

logger = logging.getLogger(__name__)


def _entry_power(entry: ArenaEntry | ArenaCoopEntry) -> int:
    return sum(
        int((link.snapshot or {}).get("attack") or 0)
        + int((link.snapshot or {}).get("defense") or 0)
        + int((link.snapshot or {}).get("max_hp") or 0) // 10
        for link in entry.entry_guests.all()
    )


def _median_entry(entries: Sequence[ArenaEntry | ArenaCoopEntry]) -> ArenaEntry | ArenaCoopEntry:
    return sorted(entries, key=_entry_power)[len(entries) // 2]


def _reference_snapshots(entry: ArenaEntry | ArenaCoopEntry) -> list[dict]:
    return [deepcopy(link.snapshot) for link in entry.entry_guests.all() if link.snapshot]


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


def _candidates(excluded_manor_ids: Iterable[int], needed: int) -> list[BotProfile]:
    return list(
        BotProfile.objects.select_for_update(skip_locked=True)
        .filter(state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING])
        .exclude(manor_id__in=set(excluded_manor_ids))
        .select_related("manor")
        .order_by("id")[:needed]
    )


def _tournament_reserved_manor_ids(tournament: ArenaTournament) -> set[int]:
    return set(
        ArenaEntry.objects.filter(
            tournament__status__in=[ArenaTournament.Status.RECRUITING, ArenaTournament.Status.RUNNING]
        )
        .exclude(tournament=tournament)
        .values_list("manor_id", flat=True)
    )


def _coop_reserved_manor_ids(event: ArenaCoopEvent) -> set[int]:
    return set(
        ArenaCoopEntry.objects.filter(
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ]
        )
        .exclude(event=event)
        .values_list("manor_id", flat=True)
    )


def backfill_tournament_locked(tournament: ArenaTournament) -> int:
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
    excluded_manor_ids = set(registered_entries.values_list("manor_id", flat=True))
    excluded_manor_ids.update(_tournament_reserved_manor_ids(tournament))
    candidates = _candidates(excluded_manor_ids, needed)
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
    for profile in candidates:
        entry = ArenaEntry.objects.create(tournament=tournament, manor=profile.manor, source=ArenaEntry.Source.VIRTUAL)
        ArenaEntryGuest.objects.bulk_create(
            [ArenaEntryGuest(entry=entry, guest=None, snapshot=snapshot) for snapshot in snapshots]
        )
    _log_backfill_completed(
        mode="tournament",
        event_id=tournament.id,
        real_entry_count=len(real_entries),
        virtual_entry_count=len(candidates),
        target_team_power=_entry_power(reference_entry),
    )
    return len(candidates)


def backfill_coop_event_locked(event: ArenaCoopEvent) -> int:
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
    excluded_manor_ids = set(registered_entries.values_list("manor_id", flat=True))
    excluded_manor_ids.update(_coop_reserved_manor_ids(event))
    candidates = _candidates(excluded_manor_ids, needed)
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
    for profile in candidates:
        entry = ArenaCoopEntry.objects.create(event=event, manor=profile.manor, source=ArenaCoopEntry.Source.VIRTUAL)
        ArenaCoopEntryGuest.objects.bulk_create(
            [
                ArenaCoopEntryGuest(entry=entry, guest=None, slot_index=index, snapshot=snapshot)
                for index, snapshot in enumerate(snapshots)
            ]
        )
    _log_backfill_completed(
        mode="coop",
        event_id=event.id,
        real_entry_count=len(real_entries),
        virtual_entry_count=len(candidates),
        target_team_power=_entry_power(reference_entry),
    )
    return len(candidates)
