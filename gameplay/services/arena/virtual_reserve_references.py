from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from django.db.models import Prefetch

from gameplay.models import ArenaCoopEntry, ArenaCoopEntryGuest, ArenaEntry, ArenaEntryGuest, ArenaVirtualDemand
from gameplay.services.arena.snapshots import build_entry_guest_snapshot

from .virtual_lineups import lineup_power


@dataclass(frozen=True, slots=True)
class ArenaPopulationActivation:
    region: str
    prestige: int
    needed: int


def reference_snapshots(entry: ArenaEntry | ArenaCoopEntry) -> list[dict]:
    snapshots: list[dict] = []
    for link in entry.entry_guests.all():
        snapshot = link.snapshot or (build_entry_guest_snapshot(link.guest) if link.guest else None)
        if snapshot:
            snapshots.append(deepcopy(snapshot))
    return snapshots


def median_entry(entries: Sequence[ArenaEntry | ArenaCoopEntry]) -> ArenaEntry | ArenaCoopEntry:
    return sorted(entries, key=lambda entry: lineup_power(reference_snapshots(entry)))[len(entries) // 2]


def reference_snapshots_for_demand(demand: ArenaVirtualDemand) -> list[dict]:
    real_entries: Sequence[ArenaEntry | ArenaCoopEntry]
    if demand.tournament_id is not None:
        real_entries = list(
            ArenaEntry.objects.filter(
                tournament_id=demand.tournament_id,
                status=ArenaEntry.Status.REGISTERED,
                source=ArenaEntry.Source.PLAYER,
            ).prefetch_related("entry_guests")
        )
    else:
        real_entries = list(
            ArenaCoopEntry.objects.filter(
                event_id=demand.coop_event_id,
                status=ArenaCoopEntry.Status.REGISTERED,
                source=ArenaCoopEntry.Source.PLAYER,
            ).prefetch_related("entry_guests")
        )
    if not real_entries:
        return []
    snapshots = reference_snapshots(median_entry(real_entries))
    return snapshots[: max(0, int(demand.target_guest_count))]


def active_arena_population_activations() -> tuple[ArenaPopulationActivation, ...]:
    """Return bounded V2 population demand derived from active Arena shortages."""
    demands = list(
        ArenaVirtualDemand.objects.filter(
            status=ArenaVirtualDemand.Status.ACTIVE,
            missing_entry_count__gt=0,
        )
        .only("id", "tournament_id", "coop_event_id", "missing_entry_count")
        .order_by("id")
    )
    if not demands:
        return ()

    tournament_ids = {int(demand.tournament_id) for demand in demands if demand.tournament_id is not None}
    coop_event_ids = {int(demand.coop_event_id) for demand in demands if demand.coop_event_id is not None}
    tournament_entries: dict[int, list[ArenaEntry]] = defaultdict(list)
    if tournament_ids:
        tournament_entry_rows = (
            ArenaEntry.objects.filter(
                tournament_id__in=sorted(tournament_ids),
                status=ArenaEntry.Status.REGISTERED,
                source=ArenaEntry.Source.PLAYER,
            )
            .select_related("manor")
            .prefetch_related(
                Prefetch(
                    "entry_guests",
                    queryset=ArenaEntryGuest.objects.select_related("guest").order_by("id"),
                )
            )
            .order_by("tournament_id", "id")
        )
        for tournament_entry in tournament_entry_rows:
            tournament_entries[int(tournament_entry.tournament_id)].append(tournament_entry)

    coop_entries: dict[int, list[ArenaCoopEntry]] = defaultdict(list)
    if coop_event_ids:
        coop_entry_rows = (
            ArenaCoopEntry.objects.filter(
                event_id__in=sorted(coop_event_ids),
                status=ArenaCoopEntry.Status.REGISTERED,
                source=ArenaCoopEntry.Source.PLAYER,
            )
            .select_related("manor")
            .prefetch_related(
                Prefetch(
                    "entry_guests",
                    queryset=ArenaCoopEntryGuest.objects.select_related("guest").order_by("id"),
                )
            )
            .order_by("event_id", "id")
        )
        for coop_entry in coop_entry_rows:
            coop_entries[int(coop_entry.event_id)].append(coop_entry)

    totals: dict[tuple[str, int], int] = defaultdict(int)
    for demand in demands:
        real_entries: Sequence[ArenaEntry | ArenaCoopEntry]
        if demand.tournament_id is not None:
            real_entries = tournament_entries.get(int(demand.tournament_id), [])
        else:
            real_entries = coop_entries.get(int(demand.coop_event_id or 0), [])
        if not real_entries:
            continue
        reference_entry = median_entry(real_entries)
        key = (
            str(reference_entry.manor.region),
            max(0, int(reference_entry.manor.prestige or 0)),
        )
        totals[key] += max(0, int(demand.missing_entry_count or 0))

    return tuple(
        ArenaPopulationActivation(region=region, prestige=prestige, needed=needed)
        for (region, prestige), needed in sorted(totals.items())
        if needed > 0
    )


__all__ = [
    "ArenaPopulationActivation",
    "active_arena_population_activations",
    "median_entry",
    "reference_snapshots",
    "reference_snapshots_for_demand",
]
