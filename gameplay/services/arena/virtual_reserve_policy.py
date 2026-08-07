from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b

__all__ = [
    "RESERVE_MINIMUM",
    "RESERVE_MULTIPLIER",
    "ReserveTargetPlan",
    "reserve_target_for_missing",
    "reserve_target_plan",
    "reserve_warm_target",
    "virtual_roster_target_count",
]

RESERVE_MULTIPLIER = 3
RESERVE_MINIMUM = 6
VIRTUAL_ROSTER_HARD_CAP = 10


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
