"""Read-only inputs shared by virtual arena reserve planning and leasing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaTournament, ArenaVirtualDemand
from gameplay.services.virtual_player_core.population_runtime import virtual_player_prestige_bands

from .virtual_reserve_references import median_entry, reference_entries_for_demand
from .virtual_reserve_references import reference_snapshots as build_reference_snapshots
from .virtual_reserve_training_policy import demand_supply_prestige_band_priority, demand_uses_arena_training_policy


@dataclass(slots=True)
class ArenaReserveCandidateContext:
    """Memoized read-only inputs shared by one reserve replenishment pass."""

    _reference_entries: tuple[ArenaEntry | ArenaCoopEntry, ...] | None = None
    _reference_snapshots: tuple[dict[str, Any], ...] | None = None
    _occupied_manor_ids: frozenset[int] | None = None

    def reference_entries_for(self, demand: ArenaVirtualDemand) -> tuple[ArenaEntry | ArenaCoopEntry, ...]:
        if self._reference_entries is None:
            self._reference_entries = reference_entries_for_demand(demand)
        return self._reference_entries

    def target_population_cell_for(self, demand: ArenaVirtualDemand) -> tuple[str, str] | None:
        return target_population_cell_for_demand(
            demand,
            reference_entries=self.reference_entries_for(demand),
        )

    def reference_snapshots_for(self, demand: ArenaVirtualDemand) -> tuple[dict[str, Any], ...]:
        if self._reference_snapshots is None:
            entries = self.reference_entries_for(demand)
            self._reference_snapshots = (
                tuple(build_reference_snapshots(median_entry(entries)))[: max(0, int(demand.target_guest_count))]
                if entries
                else ()
            )
        return self._reference_snapshots

    def occupied_manor_ids(self) -> frozenset[int]:
        if self._occupied_manor_ids is None:
            self._occupied_manor_ids = frozenset(occupied_arena_manor_ids())
        return self._occupied_manor_ids


def occupied_arena_manor_ids() -> set[int]:
    """Return manor ids currently occupying an active arena entry."""

    occupied = set(
        ArenaEntry.objects.filter(
            status=ArenaEntry.Status.REGISTERED,
            tournament__status__in=[
                ArenaTournament.Status.RECRUITING,
                ArenaTournament.Status.RUNNING,
            ],
        ).values_list("manor_id", flat=True)
    )
    occupied.update(
        ArenaCoopEntry.objects.filter(
            status=ArenaCoopEntry.Status.REGISTERED,
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ],
        ).values_list("manor_id", flat=True)
    )
    return occupied


def supply_band_priority_for_demand(
    demand: ArenaVirtualDemand,
    *,
    target_cell: tuple[str, str] | None,
) -> tuple[str, ...] | None:
    """Return the persisted lease order for the demand's supply cell."""

    if target_cell is None:
        return None
    if not demand_uses_arena_training_policy(demand):
        return (target_cell[1],)
    priority = demand_supply_prestige_band_priority(demand)
    if priority is None:
        return None
    # The demand stores the validated, immutable borrowing order.  Do not
    # consult the live population activation gate here: V2_PAUSED must still
    # be able to hand off already-materialized candidates in this cell.
    return priority


def reference_population_context_for_demand(
    demand: ArenaVirtualDemand,
    *,
    reference_entries: Sequence[ArenaEntry | ArenaCoopEntry] | None = None,
) -> tuple[str, int] | None:
    real_entries = reference_entries if reference_entries is not None else reference_entries_for_demand(demand)
    if not real_entries:
        return None
    reference_entry = median_entry(real_entries)
    return str(reference_entry.manor.region), int(reference_entry.manor.prestige or 0)


def target_population_cell_for_demand(
    demand: ArenaVirtualDemand,
    *,
    reference_entries: Sequence[ArenaEntry | ArenaCoopEntry] | None = None,
) -> tuple[str, str] | None:
    reference_context = reference_population_context_for_demand(
        demand,
        reference_entries=reference_entries,
    )
    if reference_context is None:
        return None
    region, prestige = reference_context
    supply_band_priority = demand_supply_prestige_band_priority(demand)
    if supply_band_priority is not None:
        # The policy snapshot was validated when the demand was reconciled;
        # keep using that persisted primary band during a runtime pause.
        return region, supply_band_priority[0]
    if demand_uses_arena_training_policy(demand):
        return None
    bands = virtual_player_prestige_bands()
    for band_name, (low, high) in bands.items():
        if prestige >= low and (high is None or prestige < high):
            return region, band_name
    return None


__all__ = [
    "ArenaReserveCandidateContext",
    "occupied_arena_manor_ids",
    "reference_population_context_for_demand",
    "supply_band_priority_for_demand",
    "target_population_cell_for_demand",
]
