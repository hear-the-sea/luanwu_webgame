from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

ARENA_GROWTH_ATTEMPT_HISTORY_WINDOW = timedelta(hours=24)
# Bound same-member work within one worker invocation. This is deliberately
# independent of rolling attempt history and does not cap long-term growth.
ARENA_GROWTH_MAX_ATTEMPTS_PER_TASK = 48
# Keep the JSON field a bounded diagnostic tail after the rolling attempt cap
# is removed. This is storage protection only; it is never consulted for
# admission, retry scheduling or growth eligibility.
ARENA_GROWTH_HISTORY_MAX_ENTRIES = 256
ARENA_GROWTH_MAX_SLOT_ATTEMPTS = 5
ARENA_GROWTH_BUDGET_MAX_FUTURE_SKEW = timedelta(minutes=5)


class ArenaGrowthBudgetError(ValueError):
    pass


class InvalidArenaGrowthBudgetError(ArenaGrowthBudgetError):
    pass


class ArenaGrowthAttemptOutcome(StrEnum):
    PENDING = "pending"
    BUSY = "busy"
    NO_ACTION = "no_action"
    APPLIED = "applied"


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidArenaGrowthBudgetError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _attempt_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidArenaGrowthBudgetError(f"{field} must be a UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise InvalidArenaGrowthBudgetError(f"{field} must be a UUID string") from exc
    return str(parsed)


@dataclass(frozen=True, slots=True)
class ArenaGrowthBudgetEntry:
    attempt_id: str
    attempted_at: datetime
    outcome: ArenaGrowthAttemptOutcome
    effective_progress: bool
    selected_growth_bps: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _attempt_id(self.attempt_id, field="attempt_id"))
        object.__setattr__(self, "attempted_at", _aware_utc(self.attempted_at, field="attempted_at"))
        object.__setattr__(self, "outcome", ArenaGrowthAttemptOutcome(self.outcome))
        if type(self.effective_progress) is not bool:
            raise InvalidArenaGrowthBudgetError("effective_progress must be a boolean")
        if (
            isinstance(self.selected_growth_bps, bool)
            or not isinstance(self.selected_growth_bps, int)
            or self.selected_growth_bps < 0
        ):
            raise InvalidArenaGrowthBudgetError("selected_growth_bps must be a non-negative integer")
        if self.outcome is not ArenaGrowthAttemptOutcome.APPLIED and (
            self.effective_progress or self.selected_growth_bps != 0
        ):
            raise InvalidArenaGrowthBudgetError("only applied attempts may record progress or selected growth")

    def to_payload(self) -> dict[str, bool | int | str]:
        return {
            "attempt_id": self.attempt_id,
            "attempted_at": self.attempted_at.isoformat().replace("+00:00", "Z"),
            "outcome": self.outcome.value,
            "effective_progress": self.effective_progress,
            "selected_growth_bps": self.selected_growth_bps,
        }


def parse_arena_growth_budget_entries(
    value: object,
    *,
    now: datetime,
    max_future_skew: timedelta = ARENA_GROWTH_BUDGET_MAX_FUTURE_SKEW,
) -> tuple[ArenaGrowthBudgetEntry, ...]:
    current_time = _aware_utc(now, field="now")
    if max_future_skew < timedelta(0):
        raise InvalidArenaGrowthBudgetError("max_future_skew must be non-negative")
    if not isinstance(value, list):
        raise InvalidArenaGrowthBudgetError("arena_growth_budget_entries must be a list")
    expected_fields = {
        "attempt_id",
        "attempted_at",
        "outcome",
        "effective_progress",
        "selected_growth_bps",
    }
    entries: list[ArenaGrowthBudgetEntry] = []
    seen_attempt_ids: set[str] = set()
    for index, raw_entry in enumerate(value):
        if not isinstance(raw_entry, dict) or set(raw_entry) != expected_fields:
            raise InvalidArenaGrowthBudgetError(
                f"arena_growth_budget_entries[{index}] must contain exactly the budget entry fields"
            )
        attempted_at_value = raw_entry["attempted_at"]
        if not isinstance(attempted_at_value, str) or not attempted_at_value:
            raise InvalidArenaGrowthBudgetError(
                f"arena_growth_budget_entries[{index}].attempted_at must be an ISO-8601 string"
            )
        try:
            attempted_at = datetime.fromisoformat(attempted_at_value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise InvalidArenaGrowthBudgetError(
                f"arena_growth_budget_entries[{index}].attempted_at must be an ISO-8601 datetime"
            ) from exc
        try:
            entry = ArenaGrowthBudgetEntry(
                attempt_id=raw_entry["attempt_id"],
                attempted_at=attempted_at,
                outcome=raw_entry["outcome"],
                effective_progress=raw_entry["effective_progress"],
                selected_growth_bps=raw_entry["selected_growth_bps"],
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, InvalidArenaGrowthBudgetError):
                raise
            raise InvalidArenaGrowthBudgetError(f"arena_growth_budget_entries[{index}].outcome is invalid") from exc
        if entry.attempt_id in seen_attempt_ids:
            raise InvalidArenaGrowthBudgetError("arena_growth_budget_entries contains duplicate attempt ids")
        if entries and entry.attempted_at < entries[-1].attempted_at:
            raise InvalidArenaGrowthBudgetError("arena_growth_budget_entries must be sorted by attempted_at")
        if entry.attempted_at > current_time + max_future_skew:
            raise InvalidArenaGrowthBudgetError("arena_growth_budget_entries contains a future attempt")
        seen_attempt_ids.add(entry.attempt_id)
        entries.append(entry)
    return tuple(entries)


def prune_arena_growth_budget_entries(
    entries: tuple[ArenaGrowthBudgetEntry, ...],
    *,
    now: datetime,
) -> tuple[ArenaGrowthBudgetEntry, ...]:
    """Keep only recent attempt history; the window is not a growth throttle."""

    current_time = _aware_utc(now, field="now")
    cutoff = current_time - ARENA_GROWTH_ATTEMPT_HISTORY_WINDOW
    retained = tuple(entry for entry in entries if entry.attempted_at > cutoff)
    pending = tuple(entry for entry in retained if entry.outcome is ArenaGrowthAttemptOutcome.PENDING)
    if len(pending) > 1:
        raise InvalidArenaGrowthBudgetError("arena growth budget contains multiple pending attempts")
    completed = tuple(entry for entry in retained if entry.outcome is not ArenaGrowthAttemptOutcome.PENDING)
    completed_capacity = ARENA_GROWTH_HISTORY_MAX_ENTRIES - len(pending)
    kept = (*completed[-completed_capacity:], *pending) if completed_capacity else pending
    # A pending reservation is normally the newest entry, but sorting here
    # keeps the parser invariant intact even when an old row is being
    # reconciled after a clock adjustment.
    return tuple(sorted(kept, key=lambda entry: entry.attempted_at))


def reserve_arena_growth_attempt(
    entries: tuple[ArenaGrowthBudgetEntry, ...],
    *,
    now: datetime,
    attempt_id: str,
) -> tuple[ArenaGrowthBudgetEntry, ...]:
    """Record an attempt reservation without applying a per-member count cap."""

    active_entries = prune_arena_growth_budget_entries(entries, now=now)
    candidate = ArenaGrowthBudgetEntry(
        attempt_id=attempt_id,
        attempted_at=now,
        outcome=ArenaGrowthAttemptOutcome.PENDING,
        effective_progress=False,
        selected_growth_bps=0,
    )
    if active_entries and candidate.attempted_at < active_entries[-1].attempted_at:
        raise InvalidArenaGrowthBudgetError("new arena growth attempt predates the active budget window")
    # Keep the new pending reservation inside the same bound.  Completed
    # history may be evicted, but a pending row must survive until finalize or
    # cancel can settle it; silently dropping it would strand the member claim.
    return prune_arena_growth_budget_entries((*active_entries, candidate), now=now)


def finalize_arena_growth_attempt(
    entries: tuple[ArenaGrowthBudgetEntry, ...],
    *,
    attempt_id: str,
    outcome: ArenaGrowthAttemptOutcome,
    effective_progress: bool = False,
    selected_growth_bps: int = 0,
) -> tuple[ArenaGrowthBudgetEntry, ...]:
    normalized_attempt_id = _attempt_id(attempt_id, field="attempt_id")
    normalized_outcome = ArenaGrowthAttemptOutcome(outcome)
    matches = [index for index, entry in enumerate(entries) if entry.attempt_id == normalized_attempt_id]
    if len(matches) != 1:
        raise InvalidArenaGrowthBudgetError("arena growth attempt is missing from the budget window")
    index = matches[0]
    if entries[index].outcome is not ArenaGrowthAttemptOutcome.PENDING:
        raise InvalidArenaGrowthBudgetError("arena growth attempt was already finalized")
    updated = replace(
        entries[index],
        outcome=normalized_outcome,
        effective_progress=effective_progress,
        selected_growth_bps=selected_growth_bps,
    )
    return (*entries[:index], updated, *entries[index + 1 :])


def cancel_arena_growth_attempt(
    entries: tuple[ArenaGrowthBudgetEntry, ...],
    *,
    attempt_id: str,
) -> tuple[ArenaGrowthBudgetEntry, ...]:
    """Remove an explicitly paused reservation that never attempted growth."""

    normalized_attempt_id = _attempt_id(attempt_id, field="attempt_id")
    matches = [index for index, entry in enumerate(entries) if entry.attempt_id == normalized_attempt_id]
    if len(matches) != 1:
        raise InvalidArenaGrowthBudgetError("arena growth attempt is missing from the budget window")
    index = matches[0]
    if entries[index].outcome is not ArenaGrowthAttemptOutcome.PENDING:
        raise InvalidArenaGrowthBudgetError("only a pending arena growth attempt may be cancelled")
    return (*entries[:index], *entries[index + 1 :])


def selected_growth_bps(
    *,
    selected_power_before: int,
    selected_power_after: int,
    ready_lower_bound: int,
) -> int:
    delta = max(0, int(selected_power_after) - int(selected_power_before))
    return (delta * 10_000 + max(1, int(ready_lower_bound)) - 1) // max(1, int(ready_lower_bound))


def serialize_arena_growth_budget_entries(
    entries: tuple[ArenaGrowthBudgetEntry, ...],
) -> list[dict[str, bool | int | str]]:
    return [entry.to_payload() for entry in entries]


def actual_arena_growth_attempt_count(
    entries: tuple[ArenaGrowthBudgetEntry, ...],
) -> int:
    """Count business calls in the retained diagnostic tail."""

    return sum(entry.outcome is not ArenaGrowthAttemptOutcome.PENDING for entry in entries)


def applied_arena_growth_attempt_count(
    entries: tuple[ArenaGrowthBudgetEntry, ...],
) -> int:
    """Count successful receipts without conflating them with execution calls."""

    return sum(entry.outcome is ArenaGrowthAttemptOutcome.APPLIED for entry in entries)


__all__ = [
    "ARENA_GROWTH_ATTEMPT_HISTORY_WINDOW",
    "ARENA_GROWTH_HISTORY_MAX_ENTRIES",
    "ARENA_GROWTH_MAX_ATTEMPTS_PER_TASK",
    "ARENA_GROWTH_MAX_SLOT_ATTEMPTS",
    "ArenaGrowthAttemptOutcome",
    "ArenaGrowthBudgetEntry",
    "InvalidArenaGrowthBudgetError",
    "cancel_arena_growth_attempt",
    "actual_arena_growth_attempt_count",
    "applied_arena_growth_attempt_count",
    "finalize_arena_growth_attempt",
    "parse_arena_growth_budget_entries",
    "prune_arena_growth_budget_entries",
    "reserve_arena_growth_attempt",
    "selected_growth_bps",
    "serialize_arena_growth_budget_entries",
]
