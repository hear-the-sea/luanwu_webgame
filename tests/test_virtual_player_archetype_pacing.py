from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.services.virtual_player_core.archetype_pacing import pacing_from_cycle_payload, resolve_archetype_pacing
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
    assert dojo.technology_targets == ("architecture", "farming")
    assert abandoned.max_parallel_training == 0
    assert abandoned.slot_interval_minutes == (14, 15)
    assert balanced.recruitment_pool_weights != dojo.recruitment_pool_weights


@pytest.mark.parametrize("archetype", ["balanced", "rich", "dojo", "guard"])
def test_active_archetypes_include_all_core_resource_buildings(archetype: str) -> None:
    pacing = resolve_archetype_pacing(load_virtual_player_config(), archetype)

    assert {
        "tax_office",
        "latrine",
        "tavern",
        "bathhouse",
        "silver_vault",
    }.issubset(pacing.building_targets)
    assert pacing.building_targets.index("tax_office") < pacing.building_targets.index("farm")


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
    assert not {
        "silver_budget_ratio",
        "grain_budget_ratio",
        "high_cost_actions_per_cycle",
        "economic_recovery_actions_per_cycle",
    } & set(payload)
