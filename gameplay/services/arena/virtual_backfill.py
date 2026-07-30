from __future__ import annotations

import logging
from collections.abc import Sequence

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    BotProfile,
)

from .virtual_lineups import validate_full_health_virtual_lineup_snapshots

logger = logging.getLogger(__name__)

LockedVirtualLineup = tuple[BotProfile, Sequence[dict]]


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


def backfill_tournament_locked(
    tournament: ArenaTournament,
    *,
    locked_lineups: Sequence[LockedVirtualLineup],
    target_team_power: int,
) -> int:
    registered_entries = tournament.entries.filter(status=ArenaEntry.Status.REGISTERED)
    needed = max(0, int(tournament.player_limit) - registered_entries.count())
    if needed <= 0:
        return 0
    if len(locked_lineups) < needed:
        return 0

    selected = list(locked_lineups[:needed])
    for _profile, lineup in selected:
        validate_full_health_virtual_lineup_snapshots(lineup)
    for profile, lineup in selected:
        entry = ArenaEntry.objects.create(
            tournament=tournament,
            manor=profile.manor,
            source=ArenaEntry.Source.VIRTUAL,
        )
        ArenaEntryGuest.objects.bulk_create(
            [ArenaEntryGuest(entry=entry, guest=None, snapshot=snapshot) for snapshot in lineup]
        )
    _log_backfill_completed(
        mode="tournament",
        event_id=tournament.id,
        real_entry_count=registered_entries.filter(source=ArenaEntry.Source.PLAYER).count(),
        virtual_entry_count=len(selected),
        target_team_power=int(target_team_power),
    )
    return len(selected)


def backfill_coop_event_locked(
    event: ArenaCoopEvent,
    *,
    locked_lineups: Sequence[LockedVirtualLineup],
    target_team_power: int,
) -> int:
    registered_entries = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED)
    needed = max(0, int(event.player_limit) - registered_entries.count())
    if needed <= 0:
        return 0
    if len(locked_lineups) < needed:
        return 0

    selected = list(locked_lineups[:needed])
    for _profile, lineup in selected:
        validate_full_health_virtual_lineup_snapshots(lineup)
    for profile, lineup in selected:
        entry = ArenaCoopEntry.objects.create(
            event=event,
            manor=profile.manor,
            source=ArenaCoopEntry.Source.VIRTUAL,
        )
        ArenaCoopEntryGuest.objects.bulk_create(
            [
                ArenaCoopEntryGuest(entry=entry, guest=None, slot_index=index, snapshot=snapshot)
                for index, snapshot in enumerate(lineup)
            ]
        )
    _log_backfill_completed(
        mode="coop",
        event_id=event.id,
        real_entry_count=registered_entries.filter(source=ArenaCoopEntry.Source.PLAYER).count(),
        virtual_entry_count=len(selected),
        target_team_power=int(target_team_power),
    )
    return len(selected)


__all__ = ["backfill_coop_event_locked", "backfill_tournament_locked"]
