"""Read-only strength projections shared by virtual-player maintenance paths."""

from __future__ import annotations

from gameplay.models import Building, Manor
from gameplay.services.virtual_player_core.contracts import ArenaGrowthObjective
from guests.models import Guest

from .projection import StrengthSummary, calculate_guest_arena_power
from .reference_snapshots import CORE_BUILDING_KEYS, build_strength_summary


def guest_arena_power(
    guest: Guest,
    *,
    force: int,
    intellect: int,
    defense: int,
    agility: int,
) -> int:
    """Calculate arena power from already-loaded guest and template values."""

    return calculate_guest_arena_power(
        force=force,
        intellect=intellect,
        defense=defense,
        agility=agility,
        hp_bonus=int(guest.hp_bonus),
        archetype=str(guest.template.archetype),
        base_hp=int(guest.template.base_hp),
    )


def arena_growth_priority_guests(
    guests: tuple[Guest, ...],
    objective: ArenaGrowthObjective | None,
) -> tuple[Guest, ...]:
    """Return the strongest guests selected for an arena growth objective."""

    if objective is None or not guests:
        return guests
    target_count = min(
        len(guests),
        max(objective.critical_guest_count, objective.preferred_guest_count),
    )
    if target_count <= 0:
        return guests
    return tuple(
        sorted(
            guests,
            key=lambda guest: (
                -guest_arena_power(
                    guest,
                    force=int(guest.force),
                    intellect=int(guest.intellect),
                    defense=int(guest.defense_stat),
                    agility=int(guest.agility),
                ),
                int(guest.id),
            ),
        )[:target_count]
    )


def build_locked_snapshot_strength(
    *,
    manor: Manor,
    guests: tuple[Guest, ...],
    buildings: tuple[Building, ...],
    troop_total: int,
) -> StrengthSummary:
    """Build strength from the locked in-memory rows without issuing queries."""

    return build_strength_summary_from_locked_values(
        manor=manor,
        guests=guests,
        core_building_level=max(
            (
                int(building.level or 0)
                for building in buildings
                if str(building.building_type.key) in CORE_BUILDING_KEYS
            ),
            default=0,
        ),
        troop_total=troop_total,
    )


def build_strength_summary_from_locked_values(
    *,
    manor: Manor,
    guests: tuple[Guest, ...],
    core_building_level: int,
    troop_total: int,
) -> StrengthSummary:
    """Build the maintenance strength summary from an immutable read set."""

    return build_strength_summary(
        prestige=int(manor.prestige or 0),
        core_building_level=int(core_building_level),
        guest_count=len(guests),
        max_guest_level=max((int(guest.level or 0) for guest in guests), default=0),
        arena_lineup_power=sum(
            guest_arena_power(
                guest,
                force=int(guest.force or 0),
                intellect=int(guest.intellect or 0),
                defense=int(guest.defense_stat or 0),
                agility=int(guest.agility or 0),
            )
            for guest in guests
        ),
        troop_total=troop_total,
    )


__all__ = [
    "arena_growth_priority_guests",
    "build_locked_snapshot_strength",
    "build_strength_summary_from_locked_values",
    "guest_arena_power",
]
