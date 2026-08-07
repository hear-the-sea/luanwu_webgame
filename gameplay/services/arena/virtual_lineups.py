from __future__ import annotations

import random
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations
from math import comb

MIN_LINEUP_POWER_PERCENT = 80
MAX_LINEUP_POWER_PERCENT = 120
MAX_RANDOM_LINEUP_COMBINATIONS = 64


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


@dataclass(frozen=True)
class LineupSelectionContext:
    mode: str
    event_id: int
    profile_id: int
    max_lineup_size: int | None = None

    def random(self) -> random.Random:
        return random.Random(f"{self.mode}:{self.event_id}:{self.profile_id}")


class InvalidVirtualLineupSnapshot(ValueError):
    pass


def normalize_virtual_lineup_snapshots(
    snapshots: Sequence[dict],
) -> tuple[dict, ...]:
    """Return full-health virtual Entry snapshots without mutating the inputs."""

    normalized: list[dict] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            raise InvalidVirtualLineupSnapshot("virtual lineup snapshots must be dictionaries")
        max_hp = snapshot.get("max_hp")
        if isinstance(max_hp, bool) or not isinstance(max_hp, int) or max_hp < 1:
            raise InvalidVirtualLineupSnapshot("virtual lineup snapshot max_hp must be a positive integer")
        copied = deepcopy(snapshot)
        copied["current_hp"] = max_hp
        normalized.append(copied)
    return tuple(normalized)


def validate_full_health_virtual_lineup_snapshots(
    snapshots: Sequence[dict],
) -> None:
    for snapshot in snapshots:
        normalized = normalize_virtual_lineup_snapshots((snapshot,))[0]
        current_hp = snapshot.get("current_hp")
        if isinstance(current_hp, bool) or not isinstance(current_hp, int) or current_hp != normalized["current_hp"]:
            raise InvalidVirtualLineupSnapshot("virtual lineup snapshot must be normalized to full health")


def snapshot_power(snapshot: dict) -> int:
    return int(snapshot.get("attack") or 0) + int(snapshot.get("defense") or 0) + int(snapshot.get("max_hp") or 0) // 10


def lineup_power(snapshots: Sequence[dict]) -> int:
    return sum(snapshot_power(snapshot) for snapshot in snapshots)


def _random_lineup_indexes(
    *,
    guest_count: int,
    lineup_size: int,
    rng: random.Random,
    max_combinations: int = MAX_RANDOM_LINEUP_COMBINATIONS,
) -> list[tuple[int, ...]]:
    total_combinations = comb(guest_count, lineup_size)
    combination_budget = max(1, int(max_combinations))
    indexes: list[tuple[int, ...]]
    if total_combinations <= combination_budget:
        indexes = list(combinations(range(guest_count), lineup_size))
        rng.shuffle(indexes)
        return indexes

    indexes = []
    seen: set[tuple[int, ...]] = set()
    max_attempts = combination_budget * 8
    for _attempt in range(max_attempts):
        candidate = tuple(sorted(rng.sample(range(guest_count), lineup_size)))
        if candidate in seen:
            continue
        seen.add(candidate)
        indexes.append(candidate)
        if len(indexes) >= combination_budget:
            break
    return indexes


def evaluate_lineup_snapshots(
    snapshots: Sequence[dict],
    *,
    context: LineupSelectionContext,
    target_guest_count: int,
    target_team_power: int,
    preferred_guest_count: int | None = None,
) -> BotLineupEvaluation:
    """Choose a deterministic, legal-size lineup, then normalize health.

    ``target_guest_count`` is the real-player reference size, not a hard virtual
    player size. When the arena allows more guests, larger sizes are tried first
    and the reference size remains the safe fallback if no larger lineup fits
    the target power band.
    """
    if target_guest_count <= 0 or target_team_power <= 0 or not snapshots:
        return BotLineupEvaluation((), 0, False)

    maximum_lineup_size = len(snapshots)
    if context.max_lineup_size is not None:
        maximum_lineup_size = min(maximum_lineup_size, max(0, int(context.max_lineup_size)))
    if maximum_lineup_size <= 0:
        return BotLineupEvaluation((), 0, False)

    reference_size = min(max(1, int(target_guest_count)), maximum_lineup_size)
    preferred_size = reference_size
    if preferred_guest_count is not None:
        preferred_size = min(
            maximum_lineup_size,
            max(reference_size, int(preferred_guest_count)),
        )
    lineup_sizes = list(range(reference_size, maximum_lineup_size + 1))
    rng = context.random()
    if len(lineup_sizes) > 1:
        lineup_sizes.remove(reference_size)
        if preferred_size != reference_size:
            lineup_sizes.remove(preferred_size)
        rng.shuffle(lineup_sizes)
        if preferred_size != reference_size:
            lineup_sizes.insert(0, preferred_size)
        lineup_sizes.append(reference_size)

    best_below: tuple[tuple[dict, ...], int] | None = None
    reference_size_index = len(lineup_sizes) - 1
    remaining_variable_budget = MAX_RANDOM_LINEUP_COMBINATIONS
    for size_index, lineup_size in enumerate(lineup_sizes):
        rows: list[tuple[tuple[dict, ...], int]] = []
        if size_index == reference_size_index:
            # Preserve the old fallback coverage for the real-player reference size.
            size_budget = MAX_RANDOM_LINEUP_COMBINATIONS
        else:
            remaining_size_count = reference_size_index - size_index
            size_budget = max(1, remaining_variable_budget // remaining_size_count)
        sampled_indexes = _random_lineup_indexes(
            guest_count=len(snapshots),
            lineup_size=lineup_size,
            rng=rng,
            max_combinations=size_budget,
        )
        if size_index != reference_size_index:
            remaining_variable_budget = max(0, remaining_variable_budget - len(sampled_indexes))
        for indexes in sampled_indexes:
            lineup = tuple(deepcopy(snapshots[index]) for index in indexes)
            rows.append((lineup, lineup_power(lineup)))

        ready = [
            row
            for row in rows
            if target_team_power * MIN_LINEUP_POWER_PERCENT
            <= row[1] * 100
            <= target_team_power * MAX_LINEUP_POWER_PERCENT
        ]
        if ready:
            lineup, power = rng.choice(ready)
            return BotLineupEvaluation(
                normalize_virtual_lineup_snapshots(lineup),
                power,
                True,
            )

        below = [row for row in rows if row[1] * 100 < target_team_power * MIN_LINEUP_POWER_PERCENT]
        if below:
            candidate = max(below, key=lambda row: row[1])
            if best_below is None or candidate[1] > best_below[1]:
                best_below = candidate

    if best_below is None:
        return BotLineupEvaluation((), 0, False)
    lineup, power = best_below
    return BotLineupEvaluation(
        normalize_virtual_lineup_snapshots(lineup),
        power,
        False,
    )


__all__ = [
    "ArenaReferenceTarget",
    "BotLineupEvaluation",
    "InvalidVirtualLineupSnapshot",
    "LineupSelectionContext",
    "evaluate_lineup_snapshots",
    "lineup_power",
    "normalize_virtual_lineup_snapshots",
    "snapshot_power",
    "validate_full_health_virtual_lineup_snapshots",
]
