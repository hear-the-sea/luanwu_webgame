from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import blake2b

__all__ = [
    "RESERVE_MINIMUM",
    "RESERVE_MULTIPLIER",
    "RESERVE_ADMISSION_STALL_AGE",
    "RESERVE_ADMISSION_STALL_FAILURES",
    "RESERVE_ADMISSION_PROBE_COOLDOWN",
    "ReserveAdmissionAssessment",
    "ReserveTargetPlan",
    "assess_reserve_admission",
    "reserve_target_for_missing",
    "reserve_target_plan",
    "reserve_admission_attempt_high_water",
    "reserve_materialization_needed",
    "reserve_warm_target",
    "virtual_roster_target_count",
]

RESERVE_MULTIPLIER = 3
RESERVE_MINIMUM = 6
VIRTUAL_ROSTER_HARD_CAP = 10
RESERVE_ADMISSION_STALL_AGE = timedelta(minutes=10)
RESERVE_ADMISSION_STALL_FAILURES = 2
RESERVE_ADMISSION_PROBE_COOLDOWN = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class ReserveAdmissionAssessment:
    raw_materialization_needed: int
    admitted_materialization_needed: int
    attempt_high_water: int
    guard_reasons: tuple[str, ...] = ()

    @property
    def suppressed_materialization_needed(self) -> int:
        return self.raw_materialization_needed - self.admitted_materialization_needed

    @property
    def admission_guard_active(self) -> bool:
        return bool(self.guard_reasons)

    @property
    def admission_probe_allowed(self) -> bool:
        return self.admission_guard_active and self.admitted_materialization_needed == 1


@dataclass(frozen=True, slots=True)
class ReserveTargetPlan:
    replacement_target_count: int
    warm_target_count: int


def reserve_target_for_missing(missing: int) -> int:
    normalized = max(0, int(missing))
    return 0 if normalized == 0 else max(normalized * RESERVE_MULTIPLIER, RESERVE_MINIMUM)


def reserve_warm_target(*, missing: int, reserve_target: int) -> int:
    normalized_missing = max(0, int(missing))
    if normalized_missing == 0:
        return 0
    warm_buffer = max(1, (normalized_missing + 1) // 2)
    bounded_warm_target = max(RESERVE_MINIMUM, normalized_missing + warm_buffer)
    return min(max(0, int(reserve_target)), bounded_warm_target)


def reserve_target_plan(missing: int) -> ReserveTargetPlan:
    replacement_target = reserve_target_for_missing(missing)
    return ReserveTargetPlan(
        replacement_target_count=replacement_target,
        warm_target_count=reserve_warm_target(
            missing=missing,
            reserve_target=replacement_target,
        ),
    )


def reserve_materialization_needed(
    *,
    warm_target: int,
    ready_count: int,
    training_count: int,
    attempt_count: int,
    replacement_target: int,
) -> int:
    """Return how many new reserve leases may still enter this demand.

    READY and TRAINING occupy warm slots. The caller supplies the canonical
    attempt count, where every post-migration attempt including EXHAUSTED
    members consumes the replacement budget.
    """

    active_count = max(0, int(ready_count)) + max(0, int(training_count))
    warm_slots = max(0, int(warm_target) - active_count)
    remaining_attempts = max(0, int(replacement_target) - max(0, int(attempt_count)))
    return min(warm_slots, remaining_attempts)


def reserve_admission_attempt_high_water(
    *,
    leased_attempts: int,
    admission_attempt_high_water: int,
) -> int:
    """Read the monotonic admission high-water for the current reserve model."""

    return max(0, int(leased_attempts), int(admission_attempt_high_water))


def assess_reserve_admission(
    *,
    warm_target: int,
    ready_count: int,
    training_count: int,
    leased_attempts: int,
    admission_attempt_high_water: int,
    replacement_target: int,
    stalled_without_explained_constraint: bool = False,
    active_pause_reason: str = "",
    admission_probe_target_ordinal: int | None = None,
) -> ReserveAdmissionAssessment:
    """Apply demand-local admission guards without stopping existing leases."""

    attempt_high_water = reserve_admission_attempt_high_water(
        leased_attempts=leased_attempts,
        admission_attempt_high_water=admission_attempt_high_water,
    )
    raw_materialization_needed = reserve_materialization_needed(
        warm_target=warm_target,
        ready_count=ready_count,
        training_count=training_count,
        attempt_count=attempt_high_water,
        replacement_target=replacement_target,
    )
    guard_reasons: list[str] = []
    normalized_pause_reason = str(active_pause_reason).strip()
    if normalized_pause_reason:
        guard_reasons.append(normalized_pause_reason)
    if attempt_high_water > max(0, int(replacement_target)):
        guard_reasons.append("admission_high_water_exceeded")
    if raw_materialization_needed > 0 and stalled_without_explained_constraint:
        guard_reasons.append("no_effective_progress")
    probe_reserved = bool(
        normalized_pause_reason == "no_effective_progress"
        and admission_probe_target_ordinal is not None
        and int(admission_probe_target_ordinal) == attempt_high_water + 1
        and raw_materialization_needed > 0
    )
    admitted_materialization_needed = raw_materialization_needed
    if guard_reasons:
        admitted_materialization_needed = min(1, raw_materialization_needed) if probe_reserved else 0
    return ReserveAdmissionAssessment(
        raw_materialization_needed=raw_materialization_needed,
        admitted_materialization_needed=admitted_materialization_needed,
        attempt_high_water=attempt_high_water,
        guard_reasons=tuple(dict.fromkeys(guard_reasons)),
    )


def virtual_roster_target_count(
    *,
    reference_guest_count: int,
    max_lineup_size: int,
    mode: str,
    event_id: int,
    profile_id: int,
) -> int:
    """Choose one stable virtual roster target above the human reference when possible.

    The human roster remains the lower bound for fairness. The target is persisted
    by the reserve member, so retries and demand scans never redraw a profile's
    roster size. The hard cap mirrors the arena rule instead of creating a second
    unbounded source of strength.
    """

    upper = min(VIRTUAL_ROSTER_HARD_CAP, max(1, int(max_lineup_size)))
    lower = min(upper, max(1, int(reference_guest_count)))
    if upper <= lower:
        return lower
    payload = f"{str(mode)}:{int(event_id)}:{int(profile_id)}:{lower}:{upper}".encode("utf-8")
    bucket = int.from_bytes(blake2b(payload, digest_size=8).digest(), "big")
    return lower + 1 + bucket % (upper - lower)
