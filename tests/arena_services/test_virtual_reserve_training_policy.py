from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from gameplay.models import (
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotProfile,
    BotRuntimeRoutingState,
)
from gameplay.services.arena import virtual_reserve_pool
from gameplay.services.arena.virtual_lineups import BotLineupEvaluation
from gameplay.services.arena.virtual_reserve_training_policy import (
    demand_supply_prestige_band_priority,
    resolve_configured_arena_training_policy,
)
from gameplay.services.virtual_player_core import arena_population, maintenance
from gameplay.services.virtual_player_core.config import BootstrapMode, MaintenanceMode
from gameplay.services.virtual_player_core.contracts import MaintenanceOutcome
from gameplay.services.virtual_player_core.runtime_assessment import VirtualPlayerRuntimeAssessment
from tests.arena_services.test_virtual_backfill import _add_real_arena_entry, _create_bot_profile


def _ready_assessment(demand: ArenaVirtualDemand):
    return virtual_reserve_pool.ArenaReserveCandidateAssessment(
        disposition=virtual_reserve_pool.ArenaReserveCandidateDisposition.READY,
        evaluation=BotLineupEvaluation(
            snapshots=({"attack": 200, "defense": 200, "max_hp": 2_000},),
            selected_power=600,
            is_ready=True,
        ),
        roster_target_count=int(demand.target_guest_count),
    )


def _priority_demand(*, priority: list[str]) -> ArenaVirtualDemand:
    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=now,
    )
    reference = _add_real_arena_entry(
        tournament,
        "arena_priority_reference",
        attack=200,
        defense=200,
        max_hp=2_000,
    )
    reference.manor.region = "north"
    reference.manor.save(update_fields=["region"])
    return ArenaVirtualDemand.objects.create(
        tournament=tournament,
        status=ArenaVirtualDemand.Status.ACTIVE,
        target_guest_count=1,
        target_team_power=600,
        arena_training_policy_version=2,
        arena_training_policy_checksum="a" * 64,
        arena_strength_segment="middle",
        arena_strength_envelope_digest="b" * 64,
        arena_supply_prestige_band=priority[0],
        arena_supply_prestige_band_priority=priority,
        arena_supply_prestige=2_000,
        missing_entry_count=1,
        reserve_target_count=1,
        warm_target_count=1,
        max_reserve_target_count=1,
        next_retry_at=now,
    )


def _profile_in_band(*, username: str, band: str) -> BotProfile:
    profile = _create_bot_profile(username)
    enrolled_at = timezone.now()
    profile.engine_version = 2
    profile.rng_version = 1
    profile.plan_schema_version = 1
    profile.policy_version = 2
    profile.policy_checksum = "a" * 64
    profile.v2_enrolled_at = enrolled_at
    profile.last_strength_increase_at = enrolled_at
    profile.save(
        update_fields=[
            "engine_version",
            "rng_version",
            "plan_schema_version",
            "policy_version",
            "policy_checksum",
            "v2_enrolled_at",
            "last_strength_increase_at",
        ]
    )
    profile.manor.region = "north"
    profile.manor.save(update_fields=["region"])
    profile.prestige_band = band
    profile.target_prestige_band = band
    profile.current_prestige_band = band
    profile.save(update_fields=["prestige_band", "target_prestige_band", "current_prestige_band"])
    return profile


@pytest.fixture
def priority_runtime(monkeypatch):
    BotRuntimeRoutingState.objects.update_or_create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        defaults={
            "bootstrap_mode": BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
            "maintenance_mode": BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
            "calibration_routes": [],
            "policy_rollout_target_version": 2,
            "policy_rollout_enabled": False,
            "policy_rollout_percent": 0,
        },
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "assess_virtual_player_runtime",
        lambda: VirtualPlayerRuntimeAssessment(
            routing_available=True,
            bootstrap_mode=BootstrapMode.V2_ACTIVE,
            maintenance_mode=MaintenanceMode.V2_ACTIVE,
        ),
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "is_virtual_profile_arena_match_eligible",
        lambda *_args, **_kwargs: True,
    )


def test_configured_middle_envelope_uses_ordered_all_band_supply() -> None:
    decision = resolve_configured_arena_training_policy(target_team_power=3_354)

    assert decision.available is True
    assert decision.policy_version == 2
    assert decision.required_ready_power == 2_684
    assert decision.strength_segment == "middle"
    assert decision.supply_prestige_band == "middle"
    assert decision.supply_prestige_band_priority == (
        "middle",
        "senior",
        "junior",
        "veteran",
        "newbie",
        "elite",
        "legend",
        "mythic",
    )


@pytest.mark.django_db
def test_replenish_prefers_primary_band_before_lower_id_fallback(monkeypatch, priority_runtime) -> None:
    demand = _priority_demand(priority=["middle", "senior", "junior"])
    fallback = _profile_in_band(username="arena_priority_fallback", band="senior")
    primary = _profile_in_band(username="arena_priority_primary", band="middle")
    monkeypatch.setattr(
        virtual_reserve_pool,
        "assess_arena_reserve_candidate",
        lambda current_demand, _profile, **_kwargs: _ready_assessment(current_demand),
    )

    result = virtual_reserve_pool.replenish_virtual_reserve(demand.id, now=timezone.now())

    assert result.ready_count == 1
    assert ArenaVirtualReserveMember.objects.filter(demand=demand, profile=primary).exists()
    assert not ArenaVirtualReserveMember.objects.filter(demand=demand, profile=fallback).exists()


@pytest.mark.django_db
def test_replenish_falls_back_to_next_configured_band(monkeypatch, priority_runtime) -> None:
    demand = _priority_demand(priority=["middle", "senior", "junior"])
    fallback = _profile_in_band(username="arena_priority_only_fallback", band="senior")
    monkeypatch.setattr(
        virtual_reserve_pool,
        "assess_arena_reserve_candidate",
        lambda current_demand, _profile, **_kwargs: _ready_assessment(current_demand),
    )

    result = virtual_reserve_pool.replenish_virtual_reserve(demand.id, now=timezone.now())

    assert result.ready_count == 1
    assert ArenaVirtualReserveMember.objects.filter(demand=demand, profile=fallback).exists()


@pytest.mark.django_db
def test_replenish_does_not_lease_a_band_outside_the_snapshot(monkeypatch, priority_runtime) -> None:
    demand = _priority_demand(priority=["middle", "senior"])
    outside = _profile_in_band(username="arena_priority_outside", band="legend")
    monkeypatch.setattr(
        virtual_reserve_pool,
        "assess_arena_reserve_candidate",
        lambda current_demand, _profile, **_kwargs: _ready_assessment(current_demand),
    )

    virtual_reserve_pool.replenish_virtual_reserve(demand.id, now=timezone.now())

    assert not ArenaVirtualReserveMember.objects.filter(demand=demand, profile=outside).exists()
    demand.refresh_from_db()
    assert demand_supply_prestige_band_priority(demand) == ("middle", "senior")


@pytest.mark.django_db
def test_population_handoff_counts_a_priority_fallback_for_the_primary_cell(monkeypatch) -> None:
    _priority_demand(priority=["middle", "senior"])
    fallback = _profile_in_band(username="arena_handoff_priority_fallback", band="senior")
    monkeypatch.setattr(
        arena_population,
        "is_virtual_profile_arena_match_eligible",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "assess_arena_reserve_candidate",
        lambda current_demand, _profile, **_kwargs: _ready_assessment(current_demand),
    )

    supply = arena_population.arena_handoff_supply_by_cell(
        BotProfile.objects.filter(pk=fallback.pk),
        arena_demands={("north", "middle"): 1},
        config={
            "prestige_bands": {
                "middle": [2_000, 8_000],
                "senior": [8_000, None],
            }
        },
        target_based=True,
        candidate_engine_version=2,
    )

    assert supply[("north", "middle")].available == 1


@pytest.mark.django_db
def test_v2_scheduled_maintenance_skips_a_lease_and_resumes_after_release(monkeypatch) -> None:
    now = timezone.now()
    demand = _priority_demand(priority=["middle", "senior"])
    profile = _profile_in_band(username="arena_lease_scheduler", band="middle")
    profile.engine_version = 2
    profile.rng_version = 1
    profile.plan_schema_version = 1
    profile.policy_version = 2
    profile.policy_checksum = "a" * 64
    profile.next_growth_at = now
    profile.last_strength_increase_at = now
    profile.v2_enrolled_at = now
    profile.save(
        update_fields=[
            "engine_version",
            "rng_version",
            "plan_schema_version",
            "policy_version",
            "policy_checksum",
            "next_growth_at",
            "last_strength_increase_at",
            "v2_enrolled_at",
        ]
    )
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
    )
    calls: list[int] = []
    monkeypatch.setattr(
        maintenance,
        "check_v2_development_write_preflight",
        lambda **_kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(
        maintenance,
        "start_maintenance_attempts",
        lambda **_kwargs: (object(),),
    )
    monkeypatch.setattr(
        maintenance,
        "_scheduled_planning_snapshots_with_profile_isolation",
        lambda profiles, **_kwargs: {int(candidate.id): object() for candidate in profiles},
    )
    monkeypatch.setattr(
        maintenance,
        "maintain_virtual_player_v2",
        lambda profile_id, **_kwargs: calls.append(profile_id) or SimpleNamespace(outcome=MaintenanceOutcome.NO_ACTION),
    )

    assert (
        maintenance._maintain_due_virtual_players_v2(
            current_time=now,
            limit=1,
            routing=object(),
        )
        == 0
    )
    assert calls == []

    member.delete()

    assert (
        maintenance._maintain_due_virtual_players_v2(
            current_time=now,
            limit=1,
            routing=object(),
        )
        == 1
    )
    assert calls == [profile.id]
