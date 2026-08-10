from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.services.virtual_player_core.archetype_pacing import (
    ArchetypeBudgetState,
    pacing_from_cycle_payload,
    resolve_archetype_pacing,
)
from gameplay.services.virtual_player_core.config import load_virtual_player_config
from gameplay.services.virtual_player_core.maintenance_cycle import (
    next_ordinary_slot_due_at,
    ordinary_slot_interval_minutes,
)


def test_archetype_pacing_is_typed_and_archetype_specific() -> None:
    config = load_virtual_player_config()

    balanced = resolve_archetype_pacing(config, "balanced")
    dojo = resolve_archetype_pacing(config, "dojo")
    abandoned = resolve_archetype_pacing(config, "abandoned")

    assert balanced.slot_interval_minutes == (10, 15)
    assert dojo.max_parallel_training == 2
    assert dojo.technology_targets == ("forging", "smelting")
    assert abandoned.max_parallel_training == 0
    assert abandoned.slot_interval_minutes == (14, 15)
    assert balanced.recruitment_pool_weights != dojo.recruitment_pool_weights


def test_cycle_pacing_payload_round_trips_without_configuration_lookup() -> None:
    pacing = resolve_archetype_pacing(load_virtual_player_config(), "rich")

    restored = pacing_from_cycle_payload({"archetype_pacing": pacing.to_payload()})

    assert restored == pacing


def test_typed_slot_interval_is_deterministic_and_stays_inside_contract() -> None:
    first = tuple(
        ordinary_slot_interval_minutes(
            "rich-cycle",
            ordinal,
            minimum_minutes=12,
            maximum_minutes=15,
        )
        for ordinal in range(1, 17)
    )
    second = tuple(
        ordinary_slot_interval_minutes(
            "rich-cycle",
            ordinal,
            minimum_minutes=12,
            maximum_minutes=15,
        )
        for ordinal in range(1, 17)
    )

    assert first == second
    assert all(12 <= value <= 15 for value in first)
    completed_at = timezone.now()
    due_at = next_ordinary_slot_due_at(
        "rich-cycle",
        completed_at=completed_at,
        next_slot_ordinal=1,
        interval_minutes=(12, 15),
    )
    assert due_at - completed_at == timedelta(minutes=first[0])


@pytest.mark.parametrize("archetype", ["balanced", "rich", "dojo", "guard", "abandoned"])
def test_archetype_pacing_payload_contains_a_complete_business_contract(archetype: str) -> None:
    pacing = resolve_archetype_pacing(load_virtual_player_config(), archetype)
    payload = pacing.to_payload()

    assert payload["archetype"] == archetype
    assert set(payload["recruitment_pool_weights"]) == {"dianshi", "xiangshi", "cunmu"}
    assert payload["building_targets"]
    assert payload["technology_targets"]


def test_archetype_budget_consumes_one_cycle_baseline_across_slots() -> None:
    pacing = resolve_archetype_pacing(load_virtual_player_config(), "balanced")
    state = ArchetypeBudgetState.from_spendable_resources({"silver": 1_000, "grain": 500})

    first_limits = dict(state.remaining_limits(pacing))
    state_after_first = state.consume({"silver": 200, "grain": 100})
    second_limits = dict(state_after_first.remaining_limits(pacing))

    assert first_limits == {"silver": 600, "grain": 300}
    assert second_limits == {"silver": 400, "grain": 200}
    assert ArchetypeBudgetState.from_payload(state_after_first.to_payload()) == state_after_first
