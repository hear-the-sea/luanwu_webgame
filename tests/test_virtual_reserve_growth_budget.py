from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from gameplay.services.arena.virtual_reserve_growth_budget import (
    ARENA_GROWTH_BUDGET_MAX_ATTEMPTS,
    ARENA_GROWTH_BUDGET_WINDOW,
    ArenaGrowthAttemptBudgetExceeded,
    ArenaGrowthAttemptOutcome,
    ArenaGrowthBudgetEntry,
    InvalidArenaGrowthBudgetError,
    cancel_arena_growth_attempt,
    finalize_arena_growth_attempt,
    parse_arena_growth_budget_entries,
    reserve_arena_growth_attempt,
    selected_growth_bps,
    serialize_arena_growth_budget_entries,
)


def _entry(*, attempted_at: datetime, outcome: ArenaGrowthAttemptOutcome) -> ArenaGrowthBudgetEntry:
    return ArenaGrowthBudgetEntry(
        attempt_id=str(uuid4()),
        attempted_at=attempted_at,
        outcome=outcome,
        effective_progress=False,
        selected_growth_bps=0,
    )


def test_arena_growth_budget_round_trips_canonical_entries() -> None:
    now = datetime(2026, 8, 8, 8, tzinfo=UTC)
    entries = (
        _entry(
            attempted_at=now - timedelta(hours=1),
            outcome=ArenaGrowthAttemptOutcome.NO_ACTION,
        ),
    )

    payload = serialize_arena_growth_budget_entries(entries)

    assert parse_arena_growth_budget_entries(payload, now=now) == entries
    assert payload[0]["attempted_at"].endswith("Z")


def test_arena_growth_budget_rejects_attempt_beyond_active_budget() -> None:
    now = datetime(2026, 8, 8, 8, tzinfo=UTC)
    oldest = now - timedelta(hours=23)
    entries = tuple(
        _entry(
            attempted_at=oldest + timedelta(minutes=index),
            outcome=ArenaGrowthAttemptOutcome.NO_ACTION,
        )
        for index in range(ARENA_GROWTH_BUDGET_MAX_ATTEMPTS)
    )

    with pytest.raises(ArenaGrowthAttemptBudgetExceeded) as exc_info:
        reserve_arena_growth_attempt(
            entries,
            now=now,
            attempt_id=str(uuid4()),
        )

    assert exc_info.value.retry_at == oldest + ARENA_GROWTH_BUDGET_WINDOW


def test_arena_growth_budget_prunes_expired_attempt_before_reservation() -> None:
    now = datetime(2026, 8, 8, 8, tzinfo=UTC)
    entries = tuple(
        _entry(
            attempted_at=now - ARENA_GROWTH_BUDGET_WINDOW,
            outcome=ArenaGrowthAttemptOutcome.NO_ACTION,
        )
        for _index in range(ARENA_GROWTH_BUDGET_MAX_ATTEMPTS)
    )

    reserved = reserve_arena_growth_attempt(
        entries,
        now=now,
        attempt_id=str(uuid4()),
    )

    assert len(reserved) == 1
    assert reserved[0].outcome is ArenaGrowthAttemptOutcome.PENDING


def test_arena_growth_budget_finalizes_one_pending_attempt_with_shadow_progress() -> None:
    now = datetime(2026, 8, 8, 8, tzinfo=UTC)
    attempt_id = str(uuid4())
    reserved = reserve_arena_growth_attempt((), now=now, attempt_id=attempt_id)

    finalized = finalize_arena_growth_attempt(
        reserved,
        attempt_id=attempt_id,
        outcome=ArenaGrowthAttemptOutcome.APPLIED,
        effective_progress=True,
        selected_growth_bps=125,
    )

    assert finalized[0].outcome is ArenaGrowthAttemptOutcome.APPLIED
    assert finalized[0].effective_progress is True
    assert finalized[0].selected_growth_bps == 125


def test_arena_growth_budget_rejects_progress_on_non_applied_attempt() -> None:
    now = datetime(2026, 8, 8, 8, tzinfo=UTC)
    attempt_id = str(uuid4())
    reserved = reserve_arena_growth_attempt((), now=now, attempt_id=attempt_id)

    with pytest.raises(InvalidArenaGrowthBudgetError):
        finalize_arena_growth_attempt(
            reserved,
            attempt_id=attempt_id,
            outcome=ArenaGrowthAttemptOutcome.BUSY,
            effective_progress=True,
        )


def test_arena_growth_budget_cancels_only_an_unattempted_pending_reservation() -> None:
    now = datetime(2026, 8, 8, 8, tzinfo=UTC)
    attempt_id = str(uuid4())
    reserved = reserve_arena_growth_attempt((), now=now, attempt_id=attempt_id)

    assert cancel_arena_growth_attempt(reserved, attempt_id=attempt_id) == ()

    finalized = finalize_arena_growth_attempt(
        reserved,
        attempt_id=attempt_id,
        outcome=ArenaGrowthAttemptOutcome.NO_ACTION,
    )
    with pytest.raises(InvalidArenaGrowthBudgetError):
        cancel_arena_growth_attempt(finalized, attempt_id=attempt_id)


def test_selected_growth_bps_uses_the_ready_target_as_denominator_and_rounds_up() -> None:
    assert (
        selected_growth_bps(
            selected_power_before=450,
            selected_power_after=451,
            ready_lower_bound=480,
        )
        == 21
    )
    assert (
        selected_growth_bps(
            selected_power_before=451,
            selected_power_after=450,
            ready_lower_bound=480,
        )
        == 0
    )
