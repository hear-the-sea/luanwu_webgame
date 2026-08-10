from __future__ import annotations

from dataclasses import dataclass

from gameplay.services.arena.virtual_lineups import LineupSelectionContext, evaluate_lineup_snapshots

from .contracts import ArenaGrowthObjective


class ArenaSelectedPowerProjectionError(ValueError):
    pass


def _canonical_guest_powers(
    rows: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    normalized: list[tuple[int, int]] = []
    seen_guest_ids: set[int] = set()
    for guest_id, power in rows:
        if isinstance(guest_id, bool) or not isinstance(guest_id, int) or guest_id < 1:
            raise ArenaSelectedPowerProjectionError("guest ids must be positive integers")
        if guest_id in seen_guest_ids:
            raise ArenaSelectedPowerProjectionError("guest powers must use unique guest ids")
        if isinstance(power, bool) or not isinstance(power, int) or power < 1:
            raise ArenaSelectedPowerProjectionError("guest powers must be positive integers")
        seen_guest_ids.add(guest_id)
        normalized.append((guest_id, power))
    return tuple(sorted(normalized))


def _power_snapshot(*, guest_id: int, power: int) -> dict[str, int]:
    # max_hp=10 contributes one power, so this minimal snapshot preserves the
    # exact canonical guest power while reusing the arena lineup selector.
    return {
        "guest_id": guest_id,
        "attack": power - 1,
        "defense": 0,
        "max_hp": 10,
        "agility": 0,
        "current_hp": 10,
    }


def _selected_power(
    guest_powers: tuple[tuple[int, int], ...],
    *,
    objective: ArenaGrowthObjective,
    profile_id: int,
) -> tuple[int, bool]:
    if len(guest_powers) < objective.critical_guest_count:
        return sum(power for _guest_id, power in guest_powers), False
    snapshots = tuple(_power_snapshot(guest_id=guest_id, power=power) for guest_id, power in guest_powers)
    evaluation = evaluate_lineup_snapshots(
        snapshots,
        context=LineupSelectionContext(
            mode=objective.lineup_mode,
            event_id=objective.lineup_event_id,
            profile_id=profile_id,
            max_lineup_size=objective.lineup_max_size,
        ),
        target_guest_count=objective.critical_guest_count,
        target_team_power=objective.target_team_power,
        preferred_guest_count=objective.preferred_guest_count,
    )
    if evaluation.snapshots:
        return int(evaluation.selected_power), True

    # The arena selector returns no lineup only when every legal reference-size
    # combination is above the event window. Preserve the smallest possible
    # legal power so CandidateAssessment can reject the overshoot explicitly.
    minimum_legal_power = sum(sorted(power for _guest_id, power in guest_powers)[: objective.critical_guest_count])
    return minimum_legal_power, True


@dataclass(frozen=True, slots=True)
class ArenaSelectedPowerProjection:
    selected_power_before: int
    projected_selected_power: int
    has_legal_lineup_after: bool


def project_arena_candidate_selected_power(
    *,
    objective: ArenaGrowthObjective,
    profile_id: int,
    eligible_guest_powers_before: tuple[tuple[int, int], ...],
    existing_guest_power_after: tuple[int, int] | None = None,
    newly_eligible_guest_power: tuple[int, int] | None = None,
    added_guest_powers: tuple[int, ...] = (),
    added_guest_id_start: int | None = None,
) -> ArenaSelectedPowerProjection:
    if not isinstance(objective, ArenaGrowthObjective):
        raise ArenaSelectedPowerProjectionError("objective must be an ArenaGrowthObjective")
    if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1:
        raise ArenaSelectedPowerProjectionError("profile_id must be a positive integer")
    before_rows = _canonical_guest_powers(eligible_guest_powers_before)
    projected = dict(before_rows)
    if existing_guest_power_after is not None:
        guest_id, power = _canonical_guest_powers((existing_guest_power_after,))[0]
        if guest_id in projected:
            projected[guest_id] = power
    if newly_eligible_guest_power is not None:
        guest_id, power = _canonical_guest_powers((newly_eligible_guest_power,))[0]
        projected[guest_id] = power
    for power in added_guest_powers:
        if isinstance(power, bool) or not isinstance(power, int) or power < 1:
            raise ArenaSelectedPowerProjectionError("added guest powers must be positive integers")
    if added_guest_powers and (
        isinstance(added_guest_id_start, bool) or not isinstance(added_guest_id_start, int) or added_guest_id_start < 1
    ):
        raise ArenaSelectedPowerProjectionError("added_guest_id_start must be positive when guests are added")
    if not added_guest_powers and added_guest_id_start is not None:
        raise ArenaSelectedPowerProjectionError("added_guest_id_start requires added guest powers")
    next_guest_id = int(added_guest_id_start or 0)
    for ordinal, power in enumerate(added_guest_powers):
        projected[next_guest_id + ordinal] = power

    before_power, _before_legal = _selected_power(
        before_rows,
        objective=objective,
        profile_id=profile_id,
    )
    after_power, after_legal = _selected_power(
        tuple(sorted(projected.items())),
        objective=objective,
        profile_id=profile_id,
    )
    return ArenaSelectedPowerProjection(
        selected_power_before=before_power,
        projected_selected_power=after_power,
        has_legal_lineup_after=after_legal,
    )


__all__ = [
    "ArenaSelectedPowerProjection",
    "ArenaSelectedPowerProjectionError",
    "project_arena_candidate_selected_power",
]
