from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaReserveTrainingAssignment,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotExternalStrengthReconciliation,
    BotMaintenanceExecution,
    BotMaintenanceRecovery,
    BotPopulationRecomputeDemand,
    BotProfile,
    BotRuntimeRoutingState,
    BotSafetyMetricEvent,
)
from gameplay.services import runtime_configs
from gameplay.services.arena import virtual_reserve_demand as reserve_demand_service
from gameplay.services.arena import virtual_reserve_fill as reserve_fill_service
from gameplay.services.arena import virtual_reserve_pool
from gameplay.services.arena import virtual_reserve_reconcile as reserve_reconcile_service
from gameplay.services.arena.virtual_lineups import BotLineupEvaluation
from gameplay.services.arena.virtual_reserve_fill import fill_due_coop_reserve, fill_due_tournament_reserve
from gameplay.services.arena.virtual_reserve_growth_budget import (
    ARENA_GROWTH_BUDGET_MAX_ATTEMPTS,
    ARENA_GROWTH_BUDGET_WINDOW,
    ArenaGrowthAttemptOutcome,
    ArenaGrowthBudgetEntry,
    parse_arena_growth_budget_entries,
    serialize_arena_growth_budget_entries,
)
from gameplay.services.arena.virtual_reserve_pool import (
    ARENA_REARM_JITTER_MAX,
    MAX_RESERVE_MEMBER_LEASE_AGE,
    ReserveReplenishmentResult,
    grow_due_virtual_reserves,
    pause_virtual_reserve_member_leases,
    reevaluate_existing_members,
    replenish_virtual_reserve,
    resume_virtual_reserve_member_leases,
)
from gameplay.services.arena.virtual_reserve_reconcile import reconcile_coop_demand, reconcile_tournament_demand
from gameplay.services.arena.virtual_reserve_references import (
    active_arena_population_activations,
    active_arena_population_funnel_snapshots,
)
from gameplay.services.arena.virtual_reserve_scan import scan_virtual_reserve_demands
from gameplay.services.virtual_player_core import maintenance, population_runtime, runtime_assessment
from gameplay.services.virtual_player_core.config import MaintenanceMode
from gameplay.services.virtual_player_core.contracts import (
    AcceleratedGrowthOutcome,
    ArenaGrowthObjective,
    MaintenanceTrigger,
)
from gameplay.services.virtual_player_core.safety_metrics import HARD_CONSTRAINT_METRIC
from gameplay.services.virtual_player_core.safety_provider import SafetyProviderError, record_safety_metric_event
from tests.arena_services.test_virtual_backfill import _add_real_arena_entry, _add_real_coop_entry
from tests.arena_services.test_virtual_backfill import _create_bot_profile as _create_bot_profile_unenrolled


def _set_runtime_routing(**fields) -> BotRuntimeRoutingState:
    """Replace the singleton routing snapshot without colliding with the V2 fixture."""

    key = fields.pop("key", BotRuntimeRoutingState.GLOBAL_KEY)
    return BotRuntimeRoutingState.objects.update_or_create(key=key, defaults=fields)[0]


def _create_tournament_demand(*, player_limit: int = 2) -> ArenaVirtualDemand:
    BotRuntimeRoutingState.objects.get_or_create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        defaults={
            "bootstrap_mode": BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
            "maintenance_mode": BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
            "calibration_routes": [],
        },
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=player_limit,
        virtual_fill_at=timezone.now() - timedelta(minutes=1),
    )
    _add_real_arena_entry(
        tournament,
        f"reserve_reference_{tournament.id}",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    demand = reconcile_tournament_demand(tournament.id)
    assert demand is not None
    return demand


def _enroll_profile_v2(profile: BotProfile, *, now=None) -> BotProfile:
    enrolled_at = now or timezone.now()
    profile.engine_version = 2
    profile.rng_version = 1
    profile.plan_schema_version = 1
    profile.policy_version = 2
    profile.policy_checksum = "a" * 64
    profile.last_strength_increase_at = enrolled_at
    profile.v2_enrolled_at = enrolled_at
    profile.save(
        update_fields=[
            "engine_version",
            "rng_version",
            "plan_schema_version",
            "policy_version",
            "policy_checksum",
            "last_strength_increase_at",
            "v2_enrolled_at",
        ]
    )
    return profile


def _create_bot_profile(
    username: str,
    *,
    state: str = BotProfile.State.ACTIVE,
    guest_stats: list[tuple[int, int, int]] | None = None,
) -> BotProfile:
    """Create the V2 fixture identity used by the post-cutover Arena suite."""

    return _enroll_profile_v2(
        _create_bot_profile_unenrolled(
            username,
            state=state,
            guest_stats=guest_stats,
        )
    )


def test_reachability_preflight_keeps_a_target_reachable_with_budget_only() -> None:
    guests = SimpleNamespace(count=lambda: 3)
    profile = SimpleNamespace(
        id=41,
        manor_id=91,
        manor=SimpleNamespace(guests=guests, guest_capacity=10),
        engine_version=2,
        policy_version=7,
        policy_checksum="a" * 64,
        current_prestige_band="newbie",
    )
    target = virtual_reserve_pool.ArenaVirtualGrowthTarget(
        critical_guest_count=3,
        preferred_guest_count=5,
        minimum_guest_level=20,
        recruitment_rarity_cap="gray",
        selected_power_lower_bound=1500,
        selected_power_upper_bound=2000,
    )
    assessment = virtual_reserve_pool._arena_growth_reachability(
        demand=SimpleNamespace(),
        profile=profile,
        selected_power=100,
        growth_target=target,
    )

    assert assessment.reachable is True
    assert assessment.reason == ""
    assert assessment.max_selected_power is None


def test_reachability_preflight_allows_juxianzhuang_capacity_provisioning() -> None:
    profile = SimpleNamespace(
        id=42,
        manor=SimpleNamespace(
            guests=SimpleNamespace(count=lambda: 3),
            guest_capacity=3,
            get_building_level=lambda _key: 1,
        ),
        engine_version=2,
    )
    target = virtual_reserve_pool.ArenaVirtualGrowthTarget(
        critical_guest_count=4,
        preferred_guest_count=4,
        minimum_guest_level=20,
        recruitment_rarity_cap="gray",
        selected_power_lower_bound=500,
        selected_power_upper_bound=750,
    )

    assessment = virtual_reserve_pool._arena_growth_reachability(
        demand=SimpleNamespace(),
        profile=profile,
        selected_power=100,
        available_growth_candidate_count=1,
        growth_target=target,
    )

    assert assessment.reachable is True
    assert assessment.reason == ""


def test_reachability_keeps_missing_guests_reachable_after_execution_window_is_exhausted() -> None:
    profile = SimpleNamespace(
        id=43,
        manor=SimpleNamespace(
            guests=SimpleNamespace(count=lambda: 1),
            guest_capacity=4,
        ),
        engine_version=2,
    )
    target = virtual_reserve_pool.ArenaVirtualGrowthTarget(
        critical_guest_count=2,
        preferred_guest_count=2,
        minimum_guest_level=1,
        recruitment_rarity_cap="gray",
        selected_power_lower_bound=500,
        selected_power_upper_bound=750,
    )

    assessment = virtual_reserve_pool._arena_growth_reachability(
        demand=SimpleNamespace(),
        profile=profile,
        selected_power=100,
        growth_target=target,
        growth_execution_attempt_count=20,
        available_growth_candidate_count=1,
    )

    assert assessment.reachable is True
    assert assessment.reason == ""


def test_reachability_keeps_temporarily_unavailable_or_assigned_guests_recheckable() -> None:
    idle_guests = []
    profile = SimpleNamespace(
        id=45,
        manor=SimpleNamespace(
            guests=SimpleNamespace(count=lambda: 2),
            arena_idle_guests=idle_guests,
            guest_capacity=4,
        ),
        engine_version=2,
    )
    target = virtual_reserve_pool.ArenaVirtualGrowthTarget(
        critical_guest_count=2,
        preferred_guest_count=2,
        minimum_guest_level=1,
        recruitment_rarity_cap="gray",
        selected_power_lower_bound=500,
        selected_power_upper_bound=750,
    )

    assessment = virtual_reserve_pool._arena_growth_reachability(
        demand=SimpleNamespace(),
        profile=profile,
        selected_power=100,
        growth_target=target,
        growth_round_training_guest_ids=(11, 12),
    )

    assert assessment.reachable is True


def test_reachability_honors_the_remaining_candidate_upper_bound() -> None:
    profile = SimpleNamespace(
        id=46,
        manor=SimpleNamespace(
            guests=SimpleNamespace(count=lambda: 1),
            guest_capacity=4,
        ),
        engine_version=2,
    )
    target = virtual_reserve_pool.ArenaVirtualGrowthTarget(
        critical_guest_count=2,
        preferred_guest_count=2,
        minimum_guest_level=1,
        recruitment_rarity_cap="gray",
        selected_power_lower_bound=500,
        selected_power_upper_bound=750,
    )

    assessment = virtual_reserve_pool._arena_growth_reachability(
        demand=SimpleNamespace(),
        profile=profile,
        selected_power=100,
        growth_target=target,
        available_growth_candidate_count=0,
    )

    assert assessment.reachable is False
    assert assessment.reason == "target_unreachable_by_cap"


@pytest.fixture
def reserve_demand() -> ArenaVirtualDemand:
    demand = _create_tournament_demand(player_limit=2)
    demand.reserve_target_count = 1
    demand.warm_target_count = 1
    demand.max_reserve_target_count = 1
    demand.save(update_fields=["reserve_target_count", "warm_target_count", "max_reserve_target_count"])
    return demand


@pytest.fixture
def training_member(reserve_demand: ArenaVirtualDemand) -> ArenaVirtualReserveMember:
    profile = _create_bot_profile(
        "reserve_training_member",
        guest_stats=[(150, 150, 25)],
    )
    return ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=timezone.now() - timedelta(minutes=1),
    )


def _single_growth_round_attempt_count() -> int:
    return virtual_reserve_pool.ARENA_GROWTH_BUDGET_MAX_ATTEMPTS


@pytest.mark.django_db
def test_arena_training_guest_is_reserved_before_a_receipt_exists(training_member) -> None:
    guest = training_member.profile.manor.guests.order_by("id").first()
    assert guest is not None
    plan = SimpleNamespace(
        trigger_policy=SimpleNamespace(trigger=MaintenanceTrigger.ARENA_ACCELERATION),
        action_kind="training",
        target_id=int(guest.id),
        manor_id=int(training_member.profile.manor_id),
    )

    maintenance._persist_arena_training_assignment_from_plan(
        plan,
        operation_id="arena-growth-training-slot-1",
        member_id=training_member.id,
        round_ordinal=1,
        action_ordinal_in_round=1,
    )

    assignment = ArenaReserveTrainingAssignment.objects.get(member=training_member, round_ordinal=1)
    assert assignment.guest_id == guest.id
    assert assignment.status == ArenaReserveTrainingAssignment.Status.ASSIGNED
    assert assignment.operation_id == "arena-growth-training-slot-1"
    with pytest.raises(maintenance.V2MaintenanceError, match="multiple slots"):
        maintenance._persist_arena_training_assignment_from_plan(
            plan,
            operation_id="arena-growth-training-slot-2",
            member_id=training_member.id,
            round_ordinal=1,
            action_ordinal_in_round=2,
        )


@dataclass(frozen=True)
class ReservePriorityRows:
    demand: ArenaVirtualDemand
    active: BotProfile
    abandoned: BotProfile
    retired: BotProfile
    weak: BotProfile


@pytest.fixture
def bot_profiles_for_reserve() -> ReservePriorityRows:
    demand = _create_tournament_demand(player_limit=3)
    demand.reserve_target_count = 4
    demand.warm_target_count = 4
    demand.max_reserve_target_count = 4
    demand.save(update_fields=["reserve_target_count", "warm_target_count", "max_reserve_target_count"])
    return ReservePriorityRows(
        demand=demand,
        active=_create_bot_profile("reserve_priority_active", state=BotProfile.State.ACTIVE),
        abandoned=_create_bot_profile("reserve_priority_abandoned", state=BotProfile.State.ABANDONED),
        retired=_create_bot_profile("reserve_priority_retired", state=BotProfile.State.RETIRED),
        weak=_create_bot_profile(
            "reserve_priority_weak",
            state=BotProfile.State.ACTIVE,
            guest_stats=[(150, 150, 25)],
        ),
    )


@pytest.fixture
def shared_ready_profile() -> SimpleNamespace:
    tournament_demand = _create_tournament_demand(player_limit=2)
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=2,
        guest_limit_per_entry=1,
    )
    _add_real_coop_entry(event, "reserve_shared_coop_reference")
    coop_demand = reconcile_coop_demand(event.id)
    assert coop_demand is not None
    return SimpleNamespace(
        demands=(tournament_demand, coop_demand),
        profile=_create_bot_profile("reserve_shared_ready"),
    )


@pytest.fixture
def ready_reserve_demand() -> SimpleNamespace:
    demand = _create_tournament_demand(player_limit=2)
    profiles = [
        _create_bot_profile("reserve_ready_one"),
        _create_bot_profile("reserve_ready_two"),
    ]
    members = [
        ArenaVirtualReserveMember.objects.create(
            demand=demand,
            profile=profile,
            state=ArenaVirtualReserveMember.State.READY,
            current_lineup_power=600,
        )
        for profile in profiles
    ]
    return SimpleNamespace(demand=demand, members=members)


@pytest.mark.django_db
def test_tournament_reconcile_persists_gap_target_and_reference():
    tournament = ArenaTournament.objects.create(player_limit=4)
    _add_real_arena_entry(
        tournament,
        "demand_reference",
        attack=200,
        defense=200,
        max_hp=2000,
    )

    demand = reconcile_tournament_demand(tournament.id)

    assert demand is not None
    assert demand.missing_entry_count == 3
    assert demand.reserve_target_count == 9
    assert demand.warm_target_count == 6
    assert demand.max_reserve_target_count == 9
    assert demand.target_guest_count == 1
    assert demand.target_team_power == 700


@pytest.mark.django_db
def test_population_supply_wakes_matching_supply_blocked_arena_demand():
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=2)
    entry = demand.tournament.entries.get(source=ArenaEntry.Source.PLAYER)
    entry.manor.region = "overseas"
    entry.manor.save(update_fields=["region"])
    demand.next_retry_at = now + timedelta(hours=1)
    demand.last_failure_reason = "insufficient_ready_members"
    demand.save(update_fields=["next_retry_at", "last_failure_reason", "updated_at"])

    woken = reserve_demand_service.wake_active_arena_demands_for_population_region(
        region="overseas",
        now=now,
    )

    demand.refresh_from_db()
    assert woken == 1
    assert demand.next_retry_at == now


@pytest.mark.django_db
def test_reserve_creation_uses_bounded_warm_buffer():
    demand = _create_tournament_demand(player_limit=4)

    result = replenish_virtual_reserve(demand.id)

    assert demand.reserve_target_count == 9
    assert demand.warm_target_count == 6
    assert result.creation_needed == 6
    assert result.warm_target_count == 6


@pytest.mark.django_db
def test_population_activation_uses_warm_slots_bounded_by_monotonic_attempt_budget():
    demand = _create_tournament_demand(player_limit=4)
    states = [
        ArenaVirtualReserveMember.State.READY,
        ArenaVirtualReserveMember.State.TRAINING,
        ArenaVirtualReserveMember.State.TRAINING,
        ArenaVirtualReserveMember.State.EXHAUSTED,
        ArenaVirtualReserveMember.State.EXHAUSTED,
        ArenaVirtualReserveMember.State.EXHAUSTED,
        ArenaVirtualReserveMember.State.EXHAUSTED,
        ArenaVirtualReserveMember.State.EXHAUSTED,
    ]
    for index, state in enumerate(states):
        ArenaVirtualReserveMember.objects.create(
            demand=demand,
            profile=_create_bot_profile(f"population_attempt_{index}"),
            state=state,
        )

    activations = active_arena_population_activations()

    assert len(activations) == 1
    assert activations[0].needed == 1

    demand.admission_attempt_high_water = demand.max_reserve_target_count
    demand.save(update_fields=["admission_attempt_high_water", "updated_at"])

    assert active_arena_population_activations() == ()


@pytest.mark.django_db
def test_runtime_pause_leases_ready_only_and_preserves_existing_training_member():
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=2)
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        calibration_routes=[],
    )
    demand.reserve_target_count = 2
    demand.warm_target_count = 2
    demand.max_reserve_target_count = 3
    demand.save(update_fields=["reserve_target_count", "warm_target_count", "max_reserve_target_count"])
    existing_training = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_enroll_profile_v2(
            _create_bot_profile(
                "runtime_pause_existing_training",
                guest_stats=[(150, 150, 25)],
            ),
            now=now,
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now - timedelta(minutes=1),
    )
    unleased_training = _enroll_profile_v2(
        _create_bot_profile(
            "runtime_pause_unleased_training",
            guest_stats=[(150, 150, 25)],
        ),
        now=now,
    )
    unleased_ready = _enroll_profile_v2(
        _create_bot_profile("runtime_pause_unleased_ready", guest_stats=[(100, 100, 70)]),
        now=now,
    )

    result = replenish_virtual_reserve(demand.id, now=now)

    existing_training.refresh_from_db()
    demand.refresh_from_db()
    assert existing_training.state == ArenaVirtualReserveMember.State.TRAINING
    assert existing_training.lease_paused_at == now
    assert existing_training.next_acceleration_at <= now
    assert not demand.reserve_members.filter(profile=unleased_training).exists()
    assert demand.reserve_members.filter(profile=unleased_ready, state=ArenaVirtualReserveMember.State.READY).exists()
    assert result.ready_count == 1
    assert result.training_count == 1
    assert result.creation_needed == 0


@pytest.mark.django_db
def test_member_lease_pause_resume_is_exact_and_idempotent(training_member):
    paused_at = timezone.now()
    original_deadline = paused_at + timedelta(hours=4)
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        lease_expires_at=original_deadline,
        lease_paused_at=None,
    )

    assert pause_virtual_reserve_member_leases(now=paused_at) == 1
    assert pause_virtual_reserve_member_leases(now=paused_at + timedelta(minutes=20)) == 0

    training_member.refresh_from_db()
    assert training_member.lease_paused_at == paused_at
    assert training_member.lease_expires_at == original_deadline

    resumed_at = paused_at + timedelta(hours=3, minutes=15)
    assert resume_virtual_reserve_member_leases(now=resumed_at) == 1
    assert resume_virtual_reserve_member_leases(now=resumed_at + timedelta(minutes=5)) == 0

    training_member.refresh_from_db()
    assert training_member.lease_paused_at is None
    assert training_member.lease_expires_at == original_deadline + (resumed_at - paused_at)


@pytest.mark.django_db
def test_routing_pause_and_resume_owns_training_lease_transition(monkeypatch, training_member):
    original_deadline = timezone.now() + timedelta(hours=4)
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        lease_expires_at=original_deadline,
        lease_paused_at=None,
    )
    routing = _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        revision=3,
    )

    runtime_configs.transition_virtual_player_routing_operation(
        expected_revision=3,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="v2_active",
        bootstrap_mode="v2_active",
        maintenance_mode="v2_paused",
        calibration_routes=None,
        pause_reason="manual_incident:arena",
        apply=True,
    )

    training_member.refresh_from_db()
    paused_at = training_member.lease_paused_at
    assert paused_at is not None
    assert training_member.lease_expires_at == original_deadline

    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_preflight.check_v2_development_write_preflight",
        lambda: SimpleNamespace(allowed=True, reason=""),
    )
    monkeypatch.setattr(runtime_configs, "_rearm_arena_demands_for_active_routing", lambda: None)
    runtime_configs.transition_virtual_player_routing_operation(
        expected_revision=4,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="v2_paused",
        bootstrap_mode="v2_active",
        maintenance_mode="v2_active",
        calibration_routes=None,
        expected_pause_reason="manual_incident:arena",
        resume_paused=True,
        apply=True,
    )

    routing.refresh_from_db()
    training_member.refresh_from_db()
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert training_member.lease_paused_at is None
    assert training_member.lease_expires_at >= original_deadline


@pytest.mark.django_db
def test_routing_resume_rearms_demand_before_translating_member_leases(monkeypatch, training_member):
    paused_at = timezone.now() - timedelta(hours=1)
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        lease_paused_at=paused_at,
    )
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        pause_reason="aggregation_error:test",
        revision=3,
    )
    calls: list[str] = []
    original_resume = virtual_reserve_pool.resume_virtual_reserve_member_leases
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_preflight.check_v2_development_write_preflight",
        lambda: SimpleNamespace(allowed=True, reason=""),
    )
    monkeypatch.setattr(
        runtime_configs,
        "_rearm_arena_demands_for_active_routing",
        lambda: calls.append("rearm"),
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "resume_virtual_reserve_member_leases",
        lambda: calls.append("resume") or original_resume(),
    )

    runtime_configs.transition_virtual_player_routing_operation(
        expected_revision=3,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="v2_paused",
        bootstrap_mode="v2_active",
        maintenance_mode="v2_active",
        calibration_routes=None,
        expected_pause_reason="aggregation_error:test",
        resume_paused=True,
        apply=True,
    )

    training_member.refresh_from_db()
    assert calls == ["rearm", "resume"]
    assert training_member.lease_paused_at is None


@pytest.mark.django_db
def test_routing_resume_rolls_back_member_lease_translation_when_rearm_fails(monkeypatch, training_member):
    paused_at = timezone.now() - timedelta(hours=2)
    original_deadline = timezone.now() + timedelta(hours=1)
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        lease_expires_at=original_deadline,
        lease_paused_at=paused_at,
    )
    routing = _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        pause_reason="aggregation_error:test",
        revision=3,
    )
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_preflight.check_v2_development_write_preflight",
        lambda: SimpleNamespace(allowed=True, reason=""),
    )
    monkeypatch.setattr(
        runtime_configs,
        "_rearm_arena_demands_for_active_routing",
        lambda: (_ for _ in ()).throw(RuntimeError("rearm failed")),
    )

    with pytest.raises(RuntimeError, match="rearm failed"):
        runtime_configs.transition_virtual_player_routing_operation(
            expected_revision=3,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="v2_paused",
            bootstrap_mode="v2_active",
            maintenance_mode="v2_active",
            calibration_routes=None,
            expected_pause_reason="aggregation_error:test",
            resume_paused=True,
            apply=True,
        )

    routing.refresh_from_db()
    training_member.refresh_from_db()
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED
    assert routing.revision == 3
    assert training_member.lease_paused_at == paused_at
    assert training_member.lease_expires_at == original_deadline


@pytest.mark.django_db
def test_runtime_pause_does_not_trim_ready_member_behind_protected_training():
    now = timezone.now()
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        calibration_routes=[],
    )
    demand = _create_tournament_demand(player_limit=2)
    demand.reserve_target_count = 1
    demand.warm_target_count = 1
    demand.max_reserve_target_count = 3
    demand.save(update_fields=["reserve_target_count", "warm_target_count", "max_reserve_target_count"])
    training = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_enroll_profile_v2(
            _create_bot_profile(
                "runtime_pause_surplus_training",
                guest_stats=[(150, 150, 25)],
            ),
            now=now,
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now - timedelta(minutes=1),
    )
    ready = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_enroll_profile_v2(
            _create_bot_profile("runtime_pause_surplus_ready"),
            now=now,
        ),
        state=ArenaVirtualReserveMember.State.READY,
        current_lineup_power=600,
    )

    result = replenish_virtual_reserve(demand.id, now=now)

    assert ArenaVirtualReserveMember.objects.filter(pk=training.pk).exists()
    assert ArenaVirtualReserveMember.objects.filter(pk=ready.pk).exists()
    assert result.ready_count == 1
    assert result.training_count == 1
    assert result.creation_needed == 0


@pytest.mark.django_db
def test_routing_unavailable_preserves_reserve_and_recovers_automatically(monkeypatch):
    now = timezone.now()
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
    )
    demand = _create_tournament_demand(player_limit=2)
    demand.reserve_target_count = 2
    demand.warm_target_count = 2
    demand.max_reserve_target_count = 3
    demand.save(update_fields=["reserve_target_count", "warm_target_count", "max_reserve_target_count"])
    training = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_create_bot_profile(
            "routing_unavailable_existing_training",
            guest_stats=[(150, 150, 25)],
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now,
    )
    original_deadline = now + timedelta(minutes=30)
    ArenaVirtualReserveMember.objects.filter(pk=training.pk).update(
        lease_expires_at=original_deadline,
        lease_paused_at=None,
    )
    ready_profile = _create_bot_profile("routing_unavailable_ready_candidate")
    routing_available = False
    read_routing = runtime_assessment.read_virtual_player_routing

    def conditional_routing():
        if not routing_available:
            raise runtime_assessment.RuntimeRoutingError("routing unavailable")
        return read_routing()

    monkeypatch.setattr(runtime_assessment, "read_virtual_player_routing", conditional_routing)

    deferred = replenish_virtual_reserve(demand.id, now=now)

    training.refresh_from_db()
    demand.refresh_from_db()
    assert deferred.ready_count == 0
    assert deferred.training_count == 1
    assert deferred.creation_needed == 0
    assert training.state == ArenaVirtualReserveMember.State.TRAINING
    assert training.lease_paused_at == now
    assert training.lease_expires_at == original_deadline
    assert not demand.reserve_members.filter(profile=ready_profile).exists()

    routing_available = True
    resumed_at = now + timedelta(hours=2)
    resumed = replenish_virtual_reserve(demand.id, now=resumed_at)

    training.refresh_from_db()
    assert resumed.ready_count == 1
    assert resumed.training_count == 1
    assert training.lease_paused_at is None
    assert training.lease_expires_at == original_deadline + (resumed_at - now)
    assert demand.reserve_members.filter(
        profile=ready_profile,
        state=ArenaVirtualReserveMember.State.READY,
    ).exists()


@pytest.mark.django_db
def test_stalled_demand_pauses_only_new_admission_until_real_progress():
    demand = _create_tournament_demand(player_limit=2)
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_create_bot_profile("population_admission_guard_exhausted"),
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
    )
    now = timezone.now() + timedelta(minutes=11)
    stale_at = demand.created_at
    demand.last_progress_at = stale_at
    demand.last_input_change_at = stale_at
    demand.consecutive_failure_count = 2
    demand.last_failure_reason = "insufficient_ready_members"
    demand.save(
        update_fields=[
            "last_progress_at",
            "last_input_change_at",
            "consecutive_failure_count",
            "last_failure_reason",
            "updated_at",
        ]
    )

    with transaction.atomic():
        locked_demand = ArenaVirtualDemand.objects.select_for_update().get(pk=demand.pk)
        assessment = virtual_reserve_pool._refresh_admission_guard_locked(
            locked_demand,
            now=now,
        )

    demand.refresh_from_db()
    assert assessment.raw_materialization_needed == 5
    assert assessment.admitted_materialization_needed == 0
    assert demand.admission_pause_reason == "no_effective_progress"
    assert demand.admission_paused_at == now
    assert active_arena_population_activations() == ()
    snapshot = active_arena_population_funnel_snapshots(now=now)[0]
    assert snapshot.raw_materialization_need == 5
    assert snapshot.suppressed_materialization_need == 5
    assert snapshot.guard_reason_counts == (("no_effective_progress", 1),)

    with transaction.atomic():
        locked_demand = ArenaVirtualDemand.objects.select_for_update().get(pk=demand.pk)
        virtual_reserve_pool.record_demand_progress_locked(locked_demand, now=now)

    demand.refresh_from_db()
    assert demand.admission_pause_reason == ""
    assert demand.admission_paused_at is None
    assert active_arena_population_activations()[0].needed == 5


@pytest.mark.django_db
def test_explicit_cap_stall_does_not_trip_admission_guard():
    demand = _create_tournament_demand(player_limit=2)
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_create_bot_profile("population_admission_guard_cap"),
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
        growth_retry_reason="target_cap_retry_limit",
    )
    now = timezone.now() + timedelta(minutes=11)
    stale_at = demand.created_at
    demand.last_progress_at = stale_at
    demand.last_input_change_at = stale_at
    demand.consecutive_failure_count = 2
    demand.last_failure_reason = "insufficient_ready_members"
    demand.save(
        update_fields=[
            "last_progress_at",
            "last_input_change_at",
            "consecutive_failure_count",
            "last_failure_reason",
            "updated_at",
        ]
    )

    with transaction.atomic():
        locked_demand = ArenaVirtualDemand.objects.select_for_update().get(pk=demand.pk)
        assessment = virtual_reserve_pool._refresh_admission_guard_locked(
            locked_demand,
            now=now,
        )

    demand.refresh_from_db()
    assert assessment.admitted_materialization_needed == 5
    assert demand.admission_pause_reason == ""


@pytest.mark.django_db
def test_admission_pause_fields_must_be_persisted_together():
    demand = _create_tournament_demand(player_limit=2)

    with pytest.raises(IntegrityError), transaction.atomic():
        ArenaVirtualDemand.objects.filter(pk=demand.pk).update(
            admission_pause_reason="no_effective_progress",
            admission_paused_at=None,
        )


@pytest.mark.django_db
def test_admission_guard_still_allows_existing_ready_profile_handoff(monkeypatch):
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=2)
    demand.admission_pause_reason = "no_effective_progress"
    demand.admission_paused_at = now
    demand.save(
        update_fields=[
            "admission_pause_reason",
            "admission_paused_at",
            "updated_at",
        ]
    )
    profile = _create_bot_profile("admission_guard_existing_ready_handoff")
    reference_region = demand.tournament.entries.filter(source=ArenaEntry.Source.PLAYER).first().manor.region
    profile.manor.region = reference_region
    profile.manor.save(update_fields=["region"])
    ready_assessment = virtual_reserve_pool.ArenaReserveCandidateAssessment(
        disposition=virtual_reserve_pool.ArenaReserveCandidateDisposition.READY,
        evaluation=BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
        roster_target_count=int(demand.target_guest_count),
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "assess_arena_reserve_candidate",
        lambda *_args, **_kwargs: ready_assessment,
    )

    result = replenish_virtual_reserve(demand.id, now=now)

    demand.refresh_from_db()
    member = ArenaVirtualReserveMember.objects.get(demand=demand, profile=profile)
    assert member.state == ArenaVirtualReserveMember.State.READY
    assert result.ready_count >= 1
    assert demand.admission_attempt_high_water == 1
    assert demand.admission_pause_reason == ""


@pytest.mark.django_db
def test_reserve_replenishment_uses_warm_buffer_before_full_replacement_budget():
    demand = _create_tournament_demand(player_limit=4)
    profiles = [_enroll_profile_v2(_create_bot_profile(f"reserve_warm_buffer_{index}")) for index in range(9)]

    result = replenish_virtual_reserve(demand.id)

    assert result.ready_count + result.training_count == 6
    assert result.ready_count + result.training_count < demand.reserve_target_count
    assert ArenaVirtualReserveMember.objects.filter(demand=demand, profile__in=profiles).count() == 6


@pytest.mark.django_db
def test_replenishment_initializes_roster_targets_even_during_retry_backoff(training_member):
    future = timezone.now() + timedelta(hours=1)
    ArenaVirtualDemand.objects.filter(pk=training_member.demand_id).update(next_retry_at=future)

    result = replenish_virtual_reserve(training_member.demand_id, now=timezone.now())

    training_member.refresh_from_db()
    assert result.creation_needed == 0
    assert training_member.roster_target_count is not None
    assert training_member.roster_target_count >= training_member.demand.target_guest_count


@pytest.mark.django_db
def test_reserve_reconciliation_preserves_growth_retry_state(training_member):
    # Keep the persisted strength summary aligned with the live guest snapshot;
    # this test is about retry preservation, not stale power repair.
    training_member.current_lineup_power = 550
    training_member.save(update_fields=["current_lineup_power"])
    retry_at = timezone.now() + timedelta(minutes=30)
    training_member.growth_retry_streak = 3
    training_member.growth_retry_reason = "domain_constraint"
    training_member.next_acceleration_at = retry_at
    training_member.save(update_fields=["growth_retry_streak", "growth_retry_reason", "next_acceleration_at"])

    replenish_virtual_reserve(training_member.demand_id, now=timezone.now())

    training_member.refresh_from_db()
    assert training_member.growth_retry_streak == 3
    assert training_member.growth_retry_reason == "domain_constraint"
    assert training_member.next_acceleration_at == retry_at


@pytest.mark.django_db
def test_reconcile_increments_version_only_when_inputs_change():
    now = timezone.now()
    tournament = ArenaTournament.objects.create(player_limit=3)
    _add_real_arena_entry(
        tournament,
        "version_reference",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    first = reconcile_tournament_demand(tournament.id)
    assert first is not None
    first_progress_at = first.last_progress_at
    first_input_change_at = first.last_input_change_at

    repeated = reconcile_tournament_demand(tournament.id)
    assert repeated is not None
    assert repeated.version == first.version
    first.admission_attempt_high_water = 1
    first.admission_paused_at = now
    first.admission_pause_reason = "no_effective_progress"
    first.admission_probe_target_ordinal = 1
    first.save(
        update_fields=[
            "admission_attempt_high_water",
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "updated_at",
        ]
    )

    _add_real_arena_entry(
        tournament,
        "version_second",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    changed = reconcile_tournament_demand(tournament.id)

    assert changed is not None
    assert changed.version == first.version + 1
    assert changed.missing_entry_count == 1
    assert changed.reserve_target_count == 6
    assert changed.last_progress_at == first_progress_at
    assert changed.last_input_change_at is not None
    assert changed.last_input_change_at > first_input_change_at
    assert changed.admission_pause_reason == ""
    assert changed.admission_paused_at is None
    assert changed.admission_probe_target_ordinal is None


@pytest.mark.django_db
def test_coop_reconcile_uses_registered_real_entries_only():
    event = ArenaCoopEvent.objects.create(player_limit=3, guest_limit_per_entry=1)
    _add_real_coop_entry(event, "coop_registered")
    _add_real_coop_entry(
        event,
        "coop_cancelled",
        status=ArenaCoopEntry.Status.CANCELLED,
    )

    demand = reconcile_coop_demand(event.id)

    assert demand is not None
    assert demand.missing_entry_count == 2
    assert demand.reserve_target_count == 6


@pytest.mark.django_db
def test_reconcile_closes_inactive_event_and_releases_members():
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=2)
    profile = _create_bot_profile("reserve_close_member")
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    demand.admission_attempt_high_water = 1
    demand.admission_paused_at = now
    demand.admission_pause_reason = "no_effective_progress"
    demand.admission_probe_target_ordinal = 1
    demand.save(
        update_fields=[
            "admission_attempt_high_water",
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "updated_at",
        ]
    )
    ArenaTournament.objects.filter(pk=demand.tournament_id).update(
        status=ArenaTournament.Status.RUNNING,
    )

    assert reconcile_tournament_demand(demand.tournament_id) is None

    demand.refresh_from_db()
    assert demand.status == ArenaVirtualDemand.Status.CLOSED
    assert demand.missing_entry_count == 0
    assert demand.reserve_target_count == 0
    assert demand.reserve_members.count() == 0
    assert demand.admission_pause_reason == ""
    assert demand.admission_paused_at is None
    assert demand.admission_probe_target_ordinal is None


@pytest.mark.django_db
def test_stalled_demand_becomes_blocked_and_reactivates_after_input_change():
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=4)
    profile = _create_bot_profile("reserve_blocked_member")
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
    )
    ArenaVirtualDemand.objects.filter(pk=demand.pk).update(
        last_progress_at=now - timedelta(hours=13),
        last_input_change_at=now - timedelta(hours=13),
    )

    assert reconcile_tournament_demand(demand.tournament_id, now=now) is None

    demand.refresh_from_db()
    assert demand.status == ArenaVirtualDemand.Status.BLOCKED
    assert demand.missing_entry_count == 3
    assert demand.reserve_target_count == 0
    assert demand.last_failure_reason == "no_progress_timeout"
    assert not demand.reserve_members.exists()

    idle_scan = scan_virtual_reserve_demands(now=now + timedelta(minutes=1))
    assert idle_scan["scanned"] == 0

    _add_real_arena_entry(
        demand.tournament,
        "reserve_blocked_new_real_entry",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    scan_result = scan_virtual_reserve_demands(now=now + timedelta(minutes=1))
    reactivated = ArenaVirtualDemand.objects.get(pk=demand.pk)

    assert scan_result["reconciled"] == 1
    assert reactivated.status == ArenaVirtualDemand.Status.ACTIVE
    assert reactivated.missing_entry_count == 2
    assert reactivated.reserve_members.filter(profile=profile).exists()


@pytest.mark.django_db
def test_routing_pause_does_not_consume_the_demand_no_progress_window():
    now = timezone.now()
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        pause_reason="maintenance_failure_rate",
    )
    demand = _create_tournament_demand(player_limit=3)
    ArenaVirtualDemand.objects.filter(pk=demand.pk).update(
        last_progress_at=now - timedelta(hours=13),
        last_input_change_at=now - timedelta(hours=13),
    )

    reconciled = reconcile_tournament_demand(demand.tournament_id, now=now)

    assert reconciled is not None
    demand.refresh_from_db()
    assert demand.status == ArenaVirtualDemand.Status.ACTIVE
    assert demand.last_failure_reason != "no_progress_timeout"


@pytest.mark.django_db
def test_routing_resume_wakes_active_and_timeout_blocked_demands(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    now = timezone.now()
    routing = _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        pause_reason="aggregation_error:test",
        revision=9,
    )
    active = _create_tournament_demand(player_limit=2)
    coop = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=3,
        guest_limit_per_entry=1,
        virtual_fill_at=now - timedelta(minutes=1),
    )
    _add_real_coop_entry(coop, "routing_resume_blocked_reference")
    blocked = reconcile_coop_demand(coop.id, now=now)
    assert blocked is not None
    blocked.admission_attempt_high_water = 2
    blocked.admission_paused_at = now
    blocked.admission_pause_reason = "no_effective_progress"
    blocked.admission_probe_target_ordinal = 2
    blocked.status = ArenaVirtualDemand.Status.BLOCKED
    blocked.reserve_target_count = 0
    blocked.warm_target_count = 0
    blocked.next_retry_at = None
    blocked.last_failure_reason = "no_progress_timeout"
    blocked.save(
        update_fields=[
            "admission_attempt_high_water",
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "status",
            "reserve_target_count",
            "warm_target_count",
            "next_retry_at",
            "last_failure_reason",
            "updated_at",
        ]
    )
    queued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        reserve_demand_service,
        "queue_virtual_reserve_reconcile",
        lambda mode, event_id: queued.append((mode, event_id)) or True,
    )
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_preflight.check_v2_development_write_preflight",
        lambda: SimpleNamespace(allowed=True, reason=""),
    )

    with django_capture_on_commit_callbacks(execute=True):
        runtime_configs.transition_virtual_player_routing_operation(
            expected_revision=9,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="v2_paused",
            bootstrap_mode="v2_active",
            maintenance_mode="v2_active",
            calibration_routes=None,
            expected_pause_reason="aggregation_error:test",
            resume_paused=True,
            apply=True,
        )

    routing.refresh_from_db()
    active.refresh_from_db()
    blocked.refresh_from_db()
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert active.next_retry_at is not None
    assert active.last_input_change_at is not None
    assert blocked.status == ArenaVirtualDemand.Status.ACTIVE
    assert blocked.reserve_target_count > 0
    assert blocked.warm_target_count > 0
    assert blocked.admission_attempt_high_water == 2
    assert blocked.last_failure_reason == ""
    assert blocked.admission_pause_reason == ""
    assert blocked.admission_paused_at is None
    assert blocked.admission_probe_target_ordinal is None
    assert {event_id for _mode, event_id in queued} == {
        int(active.tournament_id),
        int(blocked.coop_event_id),
    }


@pytest.mark.django_db
def test_routing_resume_rolls_back_when_arena_demand_rearm_fails(monkeypatch):
    now = timezone.now()
    routing = _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        pause_reason="aggregation_error:test",
        revision=9,
    )
    demand = _create_tournament_demand(player_limit=2)
    retry_at = now + timedelta(hours=1)
    ArenaVirtualDemand.objects.filter(pk=demand.pk).update(next_retry_at=retry_at)
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_preflight.check_v2_development_write_preflight",
        lambda: SimpleNamespace(allowed=True, reason=""),
    )
    monkeypatch.setattr(
        reserve_demand_service,
        "wake_arena_demands_after_routing_resume",
        lambda: (_ for _ in ()).throw(RuntimeError("arena demand rearm failed")),
    )

    with pytest.raises(RuntimeError, match="arena demand rearm failed"):
        runtime_configs.transition_virtual_player_routing_operation(
            expected_revision=9,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="v2_paused",
            bootstrap_mode="v2_active",
            maintenance_mode="v2_active",
            calibration_routes=None,
            expected_pause_reason="aggregation_error:test",
            resume_paused=True,
            apply=True,
        )

    routing.refresh_from_db()
    demand.refresh_from_db()
    assert routing.revision == 9
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED
    assert routing.paused_from_maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert routing.pause_reason == "aggregation_error:test"
    assert demand.next_retry_at == retry_at


@pytest.mark.django_db
def test_routing_resume_dispatch_loss_falls_back_to_periodic_demand_scan(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    now = timezone.now()
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        pause_reason="aggregation_error:test",
        revision=9,
    )
    demand = _create_tournament_demand(player_limit=2)
    stale_at = now - timedelta(hours=13)
    ArenaVirtualDemand.objects.filter(pk=demand.pk).update(
        last_progress_at=stale_at,
        last_input_change_at=stale_at,
        next_retry_at=now + timedelta(hours=1),
    )
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_preflight.check_v2_development_write_preflight",
        lambda: SimpleNamespace(allowed=True, reason=""),
    )
    monkeypatch.setattr(
        reserve_demand_service,
        "queue_virtual_reserve_reconcile",
        lambda _mode, _event_id: False,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_scan.replenish_virtual_reserve",
        lambda _demand_id, *, now: ReserveReplenishmentResult(0, 0, 0, 0, 0),
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_scan.fill_due_tournament_reserve",
        lambda _event_id, **_kwargs: 0,
    )

    with django_capture_on_commit_callbacks(execute=True):
        runtime_configs.transition_virtual_player_routing_operation(
            expected_revision=9,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="v2_paused",
            bootstrap_mode="v2_active",
            maintenance_mode="v2_active",
            calibration_routes=None,
            expected_pause_reason="aggregation_error:test",
            resume_paused=True,
            apply=True,
        )

    demand.refresh_from_db()
    assert demand.status == ArenaVirtualDemand.Status.ACTIVE
    assert demand.last_input_change_at is not None
    assert demand.last_input_change_at > stale_at
    assert demand.next_retry_at is not None
    assert demand.next_retry_at <= timezone.now()

    result = scan_virtual_reserve_demands(now=timezone.now() + timedelta(minutes=5), limit=20)

    demand.refresh_from_db()
    assert result["scanned"] == 1
    assert result["reconciled"] == 1


@pytest.mark.django_db
def test_routing_resume_keeps_committed_state_when_reconcile_dispatch_raises(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    routing = _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        pause_reason="aggregation_error:test",
        revision=9,
    )
    demand = _create_tournament_demand(player_limit=2)
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_preflight.check_v2_development_write_preflight",
        lambda: SimpleNamespace(allowed=True, reason=""),
    )
    monkeypatch.setattr(
        reserve_demand_service,
        "queue_virtual_reserve_reconcile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    with django_capture_on_commit_callbacks(execute=True):
        runtime_configs.transition_virtual_player_routing_operation(
            expected_revision=9,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="v2_paused",
            bootstrap_mode="v2_active",
            maintenance_mode="v2_active",
            calibration_routes=None,
            expected_pause_reason="aggregation_error:test",
            resume_paused=True,
            apply=True,
        )

    routing.refresh_from_db()
    demand.refresh_from_db()
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert routing.revision == 10
    assert demand.next_retry_at is not None
    assert demand.next_retry_at <= timezone.now()
    assert demand.status == ArenaVirtualDemand.Status.ACTIVE
    assert demand.last_failure_reason != "no_progress_timeout"


@pytest.mark.django_db
def test_changed_reference_reevaluates_member_without_resetting_growth_rounds():
    demand = _create_tournament_demand(player_limit=2)
    profile = _create_bot_profile(
        "reserve_recheck_member",
        guest_stats=[(150, 150, 25)],
    )
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
        evaluated_version=demand.version,
        current_lineup_power=450,
        growth_rounds_started=4,
        growth_applied_action_count=4,
    )
    real_link = ArenaEntryGuest.objects.get(
        entry__tournament_id=demand.tournament_id,
        entry__source="player",
    )
    real_link.snapshot = {
        "display_name": "调整后真人门客",
        "attack": 150,
        "defense": 150,
        "max_hp": 1500,
        "agility": 100,
        "current_hp": 1500,
    }
    real_link.save(update_fields=["snapshot"])

    changed = reconcile_tournament_demand(demand.tournament_id)

    assert changed is not None
    member.refresh_from_db()
    assert changed.version == demand.version + 1
    assert member.evaluated_version == changed.version
    assert member.state == ArenaVirtualReserveMember.State.READY
    assert member.current_lineup_power == 550
    assert member.growth_rounds_started == 4


@pytest.mark.django_db
@pytest.mark.parametrize("population_region", ["north", "overseas"])
def test_active_arena_demand_merges_its_v2_population_cell_once_per_change(
    monkeypatch,
    django_capture_on_commit_callbacks,
    population_region,
):
    current_time = timezone.now()
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=current_time - timedelta(minutes=1),
    )
    entry = _add_real_arena_entry(
        tournament,
        "arena_population_reference",
        attack=200,
        defense=200,
        max_hp=2_000,
    )
    entry.manor.region = population_region
    entry.manor.prestige = 130_000
    entry.manor.last_active_at = current_time - timedelta(days=31)
    entry.manor.save(update_fields=["region", "prestige", "last_active_at"])
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        reserve_demand_service,
        "_queue_virtual_player_population_reconcile",
        lambda *, region, prestige_band: queued.append((region, prestige_band)) or True,
    )

    with django_capture_on_commit_callbacks(execute=True):
        demand = reconcile_tournament_demand(tournament.id)

    assert demand is not None
    population_demand = BotPopulationRecomputeDemand.objects.get(
        region=population_region,
        prestige_band="newbie",
    )
    assert population_demand.requested_revision == 1
    assert demand.arena_supply_prestige_band == "newbie"
    assert queued == [(population_region, "newbie")]
    population_plan = population_runtime._build_population_plan(
        population_runtime._v2_population_runtime_config(),
        now=current_time,
        target_based_membership=True,
        required_engine_version=2,
    )
    newbie_cell = population_plan.by_key[(population_region, "newbie")]
    assert newbie_cell.active_real == 0
    assert newbie_cell.search_demand == 0
    assert newbie_cell.arena_materialization_additional == demand.warm_target_count
    assert newbie_cell.target >= demand.warm_target_count

    with django_capture_on_commit_callbacks(execute=True):
        reconcile_tournament_demand(tournament.id)
    population_demand.refresh_from_db()
    assert population_demand.requested_revision == 1
    assert queued == [(population_region, "newbie")]

    link = ArenaEntryGuest.objects.get(entry=entry)
    link.snapshot = {
        "display_name": "arena_population_reference_changed",
        "attack": 300,
        "defense": 300,
        "max_hp": 3_000,
        "current_hp": 3_000,
    }
    link.save(update_fields=["snapshot"])
    with django_capture_on_commit_callbacks(execute=True):
        reconcile_tournament_demand(tournament.id)

    population_demand.refresh_from_db()
    assert population_demand.requested_revision == 2
    assert queued == [(population_region, "newbie"), (population_region, "newbie")]


@pytest.mark.django_db
def test_routing_unavailable_does_not_rollback_arena_demand_reconcile(monkeypatch):
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
    )
    _add_real_arena_entry(
        tournament,
        "routing_unavailable_demand_reference",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    monkeypatch.setattr(
        runtime_assessment,
        "read_virtual_player_routing",
        lambda: (_ for _ in ()).throw(runtime_assessment.RuntimeRoutingError("routing unavailable")),
    )

    demand = reconcile_tournament_demand(tournament.id)

    assert demand is not None
    assert demand.status == ArenaVirtualDemand.Status.ACTIVE
    assert demand.missing_entry_count == 1
    assert not BotPopulationRecomputeDemand.objects.exists()


@pytest.mark.django_db
def test_reserve_priority_uses_active_then_abandoned_then_retired_then_training(
    bot_profiles_for_reserve,
    caplog,
):
    rows = bot_profiles_for_reserve
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")

    reconcile_tournament_demand(rows.demand.tournament_id)
    rows.demand.refresh_from_db()

    result = replenish_virtual_reserve(rows.demand.id)

    members = list(rows.demand.reserve_members.select_related("profile").order_by("id"))
    assert result.ready_count == 3
    assert result.training_count == 1
    assert [member.profile_id for member in members[:3]] == [
        rows.active.id,
        rows.abandoned.id,
        rows.retired.id,
    ]
    assert members[-1].profile_id == rows.weak.id
    assert members[-1].state == ArenaVirtualReserveMember.State.TRAINING
    rows.abandoned.refresh_from_db()
    rows.retired.refresh_from_db()
    assert rows.abandoned.state == BotProfile.State.ACTIVE
    assert rows.retired.state == BotProfile.State.ACTIVE
    reconciled_record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_demand_reconciled"
    )
    replenished_record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_reserve_replenished"
    )
    recovered_records = [
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_profile_recovered"
    ]
    for record in [reconciled_record, replenished_record, *recovered_records]:
        assert record.mode == "tournament"
        assert record.event_id == rows.demand.tournament_id
        assert record.demand_id == rows.demand.id
        assert record.demand_version == rows.demand.version
        assert record.missing_entry_count == rows.demand.missing_entry_count
        assert record.reserve_target_count == rows.demand.reserve_target_count
        assert record.warm_target_count == rows.demand.warm_target_count
        assert isinstance(record.ready_count, int)
        assert isinstance(record.training_count, int)
    assert {record.previous_state for record in recovered_records} == {
        BotProfile.State.ABANDONED,
        BotProfile.State.RETIRED,
    }
    assert replenished_record.recovered_abandoned == 1
    assert replenished_record.recovered_retired == 1
    assert replenished_record.creation_needed == result.creation_needed


@pytest.mark.django_db
def test_zero_hard_cap_allows_retired_reserve_reactivation(settings, reserve_demand):
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "cell_floor": 0,
            "cell_active_multiplier": 0,
            "hard_cap": 0,
        },
    }
    retired = _create_bot_profile(
        "reserve_unlimited_retired",
        state=BotProfile.State.RETIRED,
    )

    result = replenish_virtual_reserve(reserve_demand.id, now=timezone.now())

    retired.refresh_from_db()
    assert result.recovered_retired == 1
    assert retired.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_profile_cannot_be_leased_by_tournament_and_coop_at_once(shared_ready_profile):
    tournament_demand, coop_demand = shared_ready_profile.demands

    replenish_virtual_reserve(tournament_demand.id)
    replenish_virtual_reserve(coop_demand.id)

    assert ArenaVirtualReserveMember.objects.filter(profile=shared_ready_profile.profile).count() == 1


@pytest.mark.django_db
def test_failed_final_evaluation_rolls_back_retired_reactivation(
    monkeypatch,
    reserve_demand,
):
    retired = _create_bot_profile(
        "reserve_reactivation_rollback",
        state=BotProfile.State.RETIRED,
    )
    evaluations = iter(
        [
            BotLineupEvaluation(({"attack": 200, "defense": 200, "max_hp": 2000},), 600, True),
            BotLineupEvaluation((), 0, False),
        ]
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda *_args, **_kwargs: next(evaluations),
    )

    replenish_virtual_reserve(reserve_demand.id)

    retired.refresh_from_db()
    assert retired.state == BotProfile.State.RETIRED
    assert not ArenaVirtualReserveMember.objects.filter(profile=retired).exists()


@pytest.mark.django_db
def test_unrelated_integrity_error_from_member_create_is_not_swallowed(
    monkeypatch,
    reserve_demand,
):
    _create_bot_profile("reserve_unrelated_integrity")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda *_args, **_kwargs: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )

    def _raise_integrity_error(**_kwargs):
        raise IntegrityError("unrelated member constraint")

    monkeypatch.setattr(ArenaVirtualReserveMember.objects, "create", _raise_integrity_error)

    with pytest.raises(IntegrityError, match="unrelated member constraint"):
        replenish_virtual_reserve(reserve_demand.id)


@pytest.mark.django_db
def test_reserve_slot_count_is_ready_plus_training_not_exhausted(reserve_demand):
    reserve_demand.max_reserve_target_count = 2
    reserve_demand.save(update_fields=["max_reserve_target_count", "updated_at"])
    exhausted_profile = _create_bot_profile("reserve_exhausted")
    ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=exhausted_profile,
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
    )
    _create_bot_profile("reserve_after_exhausted")

    result = replenish_virtual_reserve(reserve_demand.id)

    reserve_demand.refresh_from_db()
    assert result.ready_count + result.training_count == reserve_demand.reserve_target_count
    assert not reserve_demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.EXHAUSTED).exists()


@pytest.mark.django_db
def test_replenishment_leases_only_from_the_demand_population_cell():
    demand = _create_tournament_demand(player_limit=2)
    demand.reserve_target_count = 1
    demand.warm_target_count = 1
    demand.max_reserve_target_count = 1
    demand.save(update_fields=["reserve_target_count", "warm_target_count", "max_reserve_target_count"])
    reference_region = demand.tournament.entries.get(source=ArenaEntry.Source.PLAYER).manor.region
    other_region = "overseas" if reference_region != "overseas" else "north"
    wrong_cell = _create_bot_profile("reserve_wrong_population_cell")
    wrong_cell.manor.region = other_region
    wrong_cell.manor.save(update_fields=["region"])
    correct_cell = _create_bot_profile("reserve_correct_population_cell")

    result = replenish_virtual_reserve(demand.id)

    demand.refresh_from_db()
    assert result.ready_count == 1
    assert not demand.reserve_members.filter(profile=wrong_cell).exists()
    assert demand.reserve_members.filter(profile=correct_cell).exists()


@pytest.mark.django_db
def test_budget_block_result_reflects_terminal_member_release():
    demand = _create_tournament_demand(player_limit=3)
    demand.reserve_target_count = 1
    demand.warm_target_count = 1
    demand.max_reserve_target_count = 1
    demand.save(update_fields=["reserve_target_count", "warm_target_count", "max_reserve_target_count"])
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_create_bot_profile("reserve_terminal_ready"),
        state=ArenaVirtualReserveMember.State.READY,
    )

    result = replenish_virtual_reserve(demand.id)

    demand.refresh_from_db()
    assert demand.status == ArenaVirtualDemand.Status.BLOCKED
    assert result.ready_count == 0
    assert result.training_count == 0
    assert result.warm_target_count == 0
    assert not ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()


@pytest.mark.django_db
def test_replenish_trims_to_warm_target_before_creating_more(reserve_demand):
    reserve_demand.warm_target_count = 1
    reserve_demand.save(update_fields=["warm_target_count", "updated_at"])
    first = _create_bot_profile("reserve_warm_trim_first")
    second = _create_bot_profile("reserve_warm_trim_second")
    ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=first,
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=400,
    )
    ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=second,
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=500,
    )

    result = replenish_virtual_reserve(reserve_demand.id)

    assert result.ready_count + result.training_count == 1
    assert reserve_demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).count() == 1


@pytest.mark.django_db
def test_invalid_growth_budget_is_permanent_and_cannot_be_revived_by_demand_change(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    training_member.arena_growth_budget_entries = {"invalid": "payload"}
    training_member.save(update_fields=["arena_growth_budget_entries", "updated_at"])

    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )

    assert claim is None
    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.growth_retry_reason == "invalid_growth_budget"
    demand = training_member.demand
    demand.version += 1
    demand.save(update_fields=["version", "updated_at"])
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_evaluate_profile_for_demand",
        lambda *_args, **_kwargs: pytest.fail("invalid growth budget must be terminal"),
    )

    reevaluate_existing_members(demand, now=now + timedelta(minutes=1))

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.growth_retry_reason == "invalid_growth_budget"


@pytest.mark.django_db
def test_target_unreachable_member_reopens_when_re_evaluation_finds_ready(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    demand = training_member.demand
    demand.admission_paused_at = now - timedelta(minutes=1)
    demand.admission_pause_reason = "no_effective_progress"
    demand.consecutive_failure_count = 2
    demand.last_failure_reason = "insufficient_ready_members"
    demand.save(
        update_fields=[
            "admission_paused_at",
            "admission_pause_reason",
            "consecutive_failure_count",
            "last_failure_reason",
            "updated_at",
        ]
    )
    training_member.state = ArenaVirtualReserveMember.State.EXHAUSTED
    training_member.growth_retry_reason = "target_unreachable_by_cap"
    training_member.next_acceleration_at = None
    training_member.save(
        update_fields=[
            "state",
            "growth_retry_reason",
            "next_acceleration_at",
            "updated_at",
        ]
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_evaluate_profile_for_demand",
        lambda *_args, **_kwargs: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )

    reevaluate_existing_members(demand, now=now)

    training_member.refresh_from_db()
    demand.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.READY
    assert training_member.growth_retry_reason == ""
    assert demand.admission_pause_reason == ""
    assert demand.admission_paused_at is None
    assert demand.consecutive_failure_count == 0
    assert demand.last_failure_reason == ""

    demand.version += 1
    demand.save(update_fields=["version", "updated_at"])
    reevaluate_existing_members(demand, now=now + timedelta(minutes=1))

    training_member.refresh_from_db()
    demand.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.READY
    assert training_member.growth_retry_reason == ""
    assert demand.admission_pause_reason == ""
    assert demand.admission_paused_at is None
    assert demand.consecutive_failure_count == 0
    assert demand.last_failure_reason == ""


@pytest.mark.django_db
def test_overpopulation_retirement_skips_active_reserve_members(reserve_demand):
    from gameplay.services.virtual_player_core import population_runtime

    reserved = _create_bot_profile("reserve_retirement_protected")
    normal = _create_bot_profile("reserve_retirement_normal")
    ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=reserved,
        state=ArenaVirtualReserveMember.State.READY,
    )

    assert (
        population_runtime._retire_excess_virtual_players(
            target=0,
            now=timezone.now(),
            required_engine_version=2,
        )
        == 1
    )

    reserved.refresh_from_db()
    normal.refresh_from_db()
    assert reserved.state == BotProfile.State.ACTIVE
    assert normal.state == BotProfile.State.RETIRED


@pytest.mark.django_db
def test_overpopulation_retirement_rechecks_lease_before_state_update(
    monkeypatch,
    reserve_demand,
):
    from django.db.models.query import QuerySet

    from gameplay.services.virtual_player_core import population_runtime

    profile = _create_bot_profile("reserve_retirement_race")
    original_update = QuerySet.update
    injected = False

    def _inject_lease_before_retirement(queryset, **kwargs):
        nonlocal injected
        if not injected and queryset.model is BotProfile and kwargs.get("state") == BotProfile.State.RETIRED:
            injected = True
            ArenaVirtualReserveMember.objects.create(
                demand=reserve_demand,
                profile=profile,
                state=ArenaVirtualReserveMember.State.READY,
            )
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", _inject_lease_before_retirement)

    assert (
        population_runtime._retire_excess_virtual_players(
            target=0,
            now=timezone.now(),
            required_engine_version=2,
        )
        == 0
    )

    profile.refresh_from_db()
    assert injected is True
    assert profile.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_population_retargeting_rechecks_lease_before_band_update(
    monkeypatch,
    reserve_demand,
):
    from django.db.models.query import QuerySet

    from gameplay.services.virtual_player_core import population_runtime
    from gameplay.services.virtual_player_population import PlannedPopulationCell, PopulationPlan

    profile = _create_bot_profile("reserve_retarget_race")
    region = profile.manor.region
    plan = PopulationPlan(
        cells=(
            PlannedPopulationCell(region, "newbie", 0, 1, 1, 0, 0),
            PlannedPopulationCell(region, "junior", 1, 0, 0, 0, 1),
        ),
        hard_cap=1,
        region_target_rows=((region, 1),),
    )
    original_update = QuerySet.update
    injected = False

    def _inject_lease_before_retarget(queryset, **kwargs):
        nonlocal injected
        if not injected and queryset.model is BotProfile and kwargs.get("target_prestige_band") == "junior":
            injected = True
            ArenaVirtualReserveMember.objects.create(
                demand=reserve_demand,
                profile=profile,
                state=ArenaVirtualReserveMember.State.READY,
            )
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", _inject_lease_before_retarget)

    assert (
        population_runtime.rebalance_virtual_player_target_bands(
            plan,
            limit=1,
            required_engine_version=2,
        )
        == 0
    )

    profile.refresh_from_db()
    assert injected is True
    assert profile.target_prestige_band == "newbie"


@pytest.mark.django_db
def test_lifecycle_retirement_skips_leased_profile_and_resumes_after_release(reserve_demand, monkeypatch):
    from gameplay.services.virtual_players import maintain_due_virtual_players

    now = timezone.now()
    monkeypatch.setattr(
        maintenance,
        "check_v2_development_write_preflight",
        lambda **_kwargs: SimpleNamespace(
            allowed=True,
            reason="",
            checked_at=now,
            monitor_heartbeat_at=now,
        ),
    )
    profile = _create_bot_profile("reserve_lifecycle_retirement_protected")
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        retire_at=now - timedelta(minutes=1),
    )

    assert maintain_due_virtual_players(now=now, limit=10) == 0

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ACTIVE
    assert profile.next_growth_at <= now
    assert ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()

    member.delete()

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.RETIRED


@pytest.mark.django_db
def test_overpopulation_retirement_skips_bot_in_live_arena_entry():
    from gameplay.services.virtual_player_core import population_runtime

    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
    )
    participating = _create_bot_profile("reserve_live_entry_protected")
    normal = _create_bot_profile("reserve_live_entry_normal")
    ArenaEntry.objects.create(
        tournament=tournament,
        manor=participating.manor,
        source=ArenaEntry.Source.VIRTUAL,
    )

    assert (
        population_runtime._retire_excess_virtual_players(
            target=0,
            now=timezone.now(),
            required_engine_version=2,
        )
        == 1
    )

    participating.refresh_from_db()
    normal.refresh_from_db()
    assert participating.state == BotProfile.State.ACTIVE
    assert normal.state == BotProfile.State.RETIRED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [BotProfile.State.ABANDONED, BotProfile.State.RETIRED, BotProfile.State.STALE],
)
def test_member_reevaluation_releases_profile_that_is_not_arena_eligible(reserve_demand, state):
    profile = _create_bot_profile("reserve_reevaluation_ineligible")
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    BotProfile.objects.filter(pk=profile.pk).update(state=state)

    reevaluate_existing_members(reserve_demand, now=timezone.now())

    assert not ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
        BotExternalStrengthReconciliation.Status.QUARANTINED,
    ],
)
def test_replenish_releases_existing_member_with_unresolved_reconciliation(
    reserve_demand,
    status,
):
    profile = _create_bot_profile(f"reserve_reconciliation_{status}")
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
        current_lineup_power=600,
    )
    now = timezone.now()
    quarantine_fields = (
        {
            "quarantined_at": now,
            "quarantined_phase": BotExternalStrengthReconciliation.Phase.PROFILE,
            "failure_code": "manual_review_required",
        }
        if status == BotExternalStrengthReconciliation.Status.QUARANTINED
        else {}
    )
    BotExternalStrengthReconciliation.objects.create(
        profile_id=profile.id,
        domain_event_kind="arena_member_test",
        domain_event_id=f"member:{member.id}",
        origin_committed_at=now,
        pre_strength_summary={},
        pre_prestige_band="newbie",
        status=status,
        available_at=now,
        **quarantine_fields,
    )

    result = replenish_virtual_reserve(reserve_demand.id, now=now)

    assert not ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()
    assert result.ready_count == 0
    assert result.training_count == 0
    assert result.creation_needed == 0
    reserve_demand.refresh_from_db()
    assert reserve_demand.status == ArenaVirtualDemand.Status.BLOCKED
    assert reserve_demand.last_failure_reason == "replacement_budget_exhausted"


@pytest.mark.django_db
def test_overdue_member_reevaluation_pulls_distant_growth_schedule_forward(
    training_member,
):
    now = timezone.now()
    training_member.next_acceleration_at = now + timedelta(hours=1)
    training_member.save(update_fields=["next_acceleration_at"])

    reevaluate_existing_members(training_member.demand, now=now)

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.next_acceleration_at == now


@pytest.mark.django_db
def test_growth_uses_reference_targets_and_marks_member_ready(monkeypatch, training_member, caplog):
    now = timezone.now()
    reference_guest = ArenaEntryGuest.objects.get(
        entry__tournament_id=training_member.demand.tournament_id,
        entry__source=ArenaEntry.Source.PLAYER,
    )
    reference_guest.snapshot = {
        **reference_guest.snapshot,
        "level": 100,
        "rarity": "purple",
    }
    reference_guest.save(update_fields=["snapshot"])
    calls: list[tuple[int, dict]] = []
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: calls.append((profile_id, kwargs)) or AcceleratedGrowthOutcome.GROWN,
    )
    evaluations = iter(
        (
            BotLineupEvaluation(
                ({"attack": 150, "defense": 150, "max_hp": 1500},),
                450,
                False,
            ),
            BotLineupEvaluation(
                ({"attack": 200, "defense": 200, "max_hp": 2000},),
                600,
                True,
            ),
        )
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: next(evaluations),
    )

    result = grow_due_virtual_reserves(now=now, limit=10)

    training_member.refresh_from_db()
    assert result == 1
    assert len(calls) == 1
    called_profile_id, called_kwargs = calls[0]
    assert called_profile_id == training_member.profile_id
    assert called_kwargs.pop("operation_id").startswith("arena-growth-")
    assert called_kwargs.pop("attempt_ordinal") == 1
    assert called_kwargs.pop("request_digest_schema") == 3
    expected_minimum_guest_count = training_member.demand.target_guest_count
    assert called_kwargs["now"] == now
    objective = called_kwargs["arena_growth_objective"]
    assert isinstance(objective, ArenaGrowthObjective)
    assert objective.critical_guest_count == expected_minimum_guest_count
    assert objective.preferred_guest_count == training_member.roster_target_count
    assert objective.minimum_guest_level == 100
    assert objective.recruitment_rarity_cap == "purple"
    assert objective.max_guest_level_step == 10
    assert objective.critical_guest_count >= training_member.demand.target_guest_count
    assert training_member.growth_applied_action_count == 1
    assert training_member.state == ArenaVirtualReserveMember.State.READY
    assert training_member.next_acceleration_at is None
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_profile_grown"
    )
    assert record.profile_id == training_member.profile_id
    assert record.power_before == 450
    assert record.power_after == 600
    assert record.growth_rounds == 1
    assert record.member_state == ArenaVirtualReserveMember.State.READY
    budget_entries = parse_arena_growth_budget_entries(
        training_member.arena_growth_budget_entries,
        now=now,
    )
    assert len(budget_entries) == 1
    assert budget_entries[0].outcome is ArenaGrowthAttemptOutcome.APPLIED
    assert budget_entries[0].effective_progress is True
    assert budget_entries[0].selected_growth_bps == 2679


@pytest.mark.django_db
def test_growth_isolates_claimed_member_business_error_and_continues(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    training_member.next_acceleration_at = now - timedelta(minutes=2)
    training_member.save(update_fields=["next_acceleration_at", "updated_at"])
    peer = ArenaVirtualReserveMember.objects.create(
        demand=training_member.demand,
        profile=_create_bot_profile(
            "reserve_training_business_error_peer",
            guest_stats=[(150, 150, 25)],
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now - timedelta(minutes=1),
    )
    attempted_profile_ids: list[int] = []

    def grow_profile(profile_id, **_kwargs):
        attempted_profile_ids.append(profile_id)
        if profile_id == training_member.profile_id:
            raise maintenance.V2MaintenanceError("profile policy is invalid")
        return AcceleratedGrowthOutcome.BUSY

    monkeypatch.setattr(virtual_reserve_pool, "accelerate_virtual_player_growth", grow_profile)

    expected_round_attempts = _single_growth_round_attempt_count()
    assert grow_due_virtual_reserves(now=now, limit=10) == 1 + expected_round_attempts

    training_member.refresh_from_db()
    peer.refresh_from_db()
    training_member.profile.refresh_from_db()
    assert attempted_profile_ids == [
        training_member.profile_id,
        *([peer.profile_id] * expected_round_attempts),
    ]
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.growth_claim_token is None
    assert training_member.growth_retry_reason == "growth_business_error"
    assert training_member.next_acceleration_at is not None
    assert training_member.next_acceleration_at > now
    assert training_member.growth_applied_action_count == 0
    assert training_member.profile.maintenance_sequence == 0
    first_budget = parse_arena_growth_budget_entries(
        training_member.arena_growth_budget_entries,
        now=timezone.now(),
    )
    assert len(first_budget) == 1
    assert first_budget[0].outcome is ArenaGrowthAttemptOutcome.NO_ACTION
    assert peer.growth_claim_token is None
    assert peer.growth_retry_reason == "arena_attempt_budget_exhausted"
    peer_budget = parse_arena_growth_budget_entries(
        peer.arena_growth_budget_entries,
        now=timezone.now(),
    )
    assert len(peer_budget) == expected_round_attempts
    assert peer_budget[0].outcome is ArenaGrowthAttemptOutcome.BUSY


@pytest.mark.django_db
def test_growth_backs_off_unclaimed_member_business_error_and_continues(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    training_member.next_acceleration_at = now - timedelta(minutes=2)
    training_member.save(update_fields=["next_acceleration_at", "updated_at"])
    peer = ArenaVirtualReserveMember.objects.create(
        demand=training_member.demand,
        profile=_create_bot_profile(
            "reserve_training_claim_error_peer",
            guest_stats=[(150, 150, 25)],
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now - timedelta(minutes=1),
    )
    evaluate_profile = virtual_reserve_pool._evaluate_profile_for_demand

    def evaluate_or_fail(demand, profile, **kwargs):
        if profile.id == training_member.profile_id:
            raise virtual_reserve_pool.InvalidVirtualLineupSnapshot("invalid member lineup")
        return evaluate_profile(demand, profile, **kwargs)

    attempted_profile_ids: list[int] = []
    monkeypatch.setattr(virtual_reserve_pool, "_evaluate_profile_for_demand", evaluate_or_fail)
    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        lambda profile_id, **_kwargs: attempted_profile_ids.append(profile_id) or AcceleratedGrowthOutcome.BUSY,
    )

    expected_round_attempts = _single_growth_round_attempt_count()
    assert grow_due_virtual_reserves(now=now, limit=10) == expected_round_attempts

    training_member.refresh_from_db()
    peer.refresh_from_db()
    assert attempted_profile_ids == [peer.profile_id] * expected_round_attempts
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.growth_claim_token is None
    assert training_member.arena_growth_budget_entries == []
    assert training_member.growth_retry_streak == 1
    assert training_member.growth_retry_reason == "growth_business_error"
    assert training_member.next_acceleration_at is not None
    assert (
        now
        < training_member.next_acceleration_at
        <= (training_member.created_at + virtual_reserve_pool.MAX_RESERVE_MEMBER_LEASE_AGE)
    )
    assert peer.growth_claim_token is None
    assert peer.growth_retry_reason == "arena_attempt_budget_exhausted"


@pytest.mark.parametrize("error_type", (DatabaseError, SafetyProviderError))
@pytest.mark.django_db
def test_growth_propagates_infrastructure_errors_with_claim_intact(
    monkeypatch,
    training_member,
    error_type,
):
    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error_type("growth infrastructure unavailable")),
    )

    with pytest.raises(error_type, match="growth infrastructure unavailable"):
        grow_due_virtual_reserves(now=timezone.now(), limit=1)

    training_member.refresh_from_db()
    assert training_member.growth_claim_token is not None
    assert training_member.growth_applied_action_count == 0
    budget_entries = parse_arena_growth_budget_entries(
        training_member.arena_growth_budget_entries,
        now=timezone.now(),
    )
    assert len(budget_entries) == 1
    assert budget_entries[0].outcome is ArenaGrowthAttemptOutcome.PENDING


@pytest.mark.django_db
def test_first_growth_claim_refreshes_stale_selected_lineup_baseline(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    training_member.current_lineup_power = 1
    training_member.save(update_fields=["current_lineup_power", "updated_at"])
    monkeypatch.setattr(
        virtual_reserve_pool,
        "evaluate_bot_lineup",
        lambda *_args, **_kwargs: BotLineupEvaluation(
            ({"attack": 150, "defense": 150, "max_hp": 1500},),
            450,
            False,
        ),
    )

    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )

    assert claim is not None
    assert claim.power_before == 450
    training_member.refresh_from_db()
    assert training_member.current_lineup_power == 450
    assert training_member.growth_power_before == 450


@pytest.mark.django_db(transaction=True)
def test_growth_executes_maintenance_without_an_arena_transaction(
    monkeypatch,
    training_member,
):
    atomic_states: list[bool] = []

    def observe_transaction_state(*_args, **_kwargs):
        atomic_states.append(connection.in_atomic_block)
        return AcceleratedGrowthOutcome.BUSY

    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        observe_transaction_state,
    )

    expected_round_attempts = _single_growth_round_attempt_count()
    assert grow_due_virtual_reserves(now=timezone.now(), limit=1) == expected_round_attempts
    assert atomic_states == [False] * expected_round_attempts


@pytest.mark.django_db(transaction=True)
def test_growth_finalize_failure_cannot_rollback_maintenance_or_safety_event(
    monkeypatch,
    training_member,
):
    now = timezone.now()

    def commit_external_growth(profile_id, **_kwargs):
        BotProfile.objects.filter(pk=profile_id).update(maintenance_sequence=1)
        record_safety_metric_event(
            event_id="arena-growth-finalize-failure",
            metric_name=HARD_CONSTRAINT_METRIC,
            occurred_at=now,
            dimensions={},
            value=1,
        )
        return AcceleratedGrowthOutcome.GROWN

    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        commit_external_growth,
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_finalize_virtual_reserve_growth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("arena finalize failed")),
    )

    assert grow_due_virtual_reserves(now=now, limit=1) == 0

    training_member.profile.refresh_from_db()
    training_member.refresh_from_db()
    assert training_member.profile.maintenance_sequence == 1
    assert training_member.growth_claim_token is not None
    assert BotSafetyMetricEvent.objects.filter(event_id="arena-growth-finalize-failure").exists()
    recovery = BotMaintenanceRecovery.objects.get(
        scope=BotMaintenanceRecovery.Scope.ARENA_MEMBER,
        entity_key=f"member:{training_member.id}",
    )
    assert recovery.failure_code == "programmer_error"
    assert recovery.payload["phase"] == "finalize"


@pytest.mark.django_db(transaction=True)
def test_committed_growth_receipt_recovers_finalize_without_repeating_execution(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    execution_count = 0

    monkeypatch.setattr(
        maintenance,
        "read_virtual_player_routing",
        lambda: SimpleNamespace(maintenance_mode=MaintenanceMode.V2_ACTIVE),
    )
    monkeypatch.setattr(maintenance, "_run_arena_v2_healing_sweep", lambda *_args, **_kwargs: None)

    def commit_growth(
        profile_id, *, operation_id, attempt_ordinal, now, _execution_request_digest, _execution_requested_at, **_kwargs
    ):
        nonlocal execution_count
        execution_count += 1
        profile = BotProfile.objects.get(pk=profile_id)
        sequence_before = int(profile.maintenance_sequence)
        profile.growth_stage += 1
        profile.maintenance_sequence = sequence_before + 1
        profile.save(update_fields=["growth_stage", "maintenance_sequence", "updated_at"])
        BotMaintenanceExecution.objects.create(
            operation_id=str(operation_id),
            profile=profile,
            attempt_ordinal=int(attempt_ordinal),
            trigger=BotMaintenanceExecution.Trigger.ARENA_ACCELERATION,
            outcome=BotMaintenanceExecution.Outcome.APPLIED,
            schedule_disposition=BotMaintenanceExecution.ScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
            maintenance_sequence_before=sequence_before,
            maintenance_sequence_after=sequence_before + 1,
            next_growth_at_before=profile.next_growth_at,
            next_growth_at_after=profile.next_growth_at,
            action_kind="training",
            shadow_cost={},
            request_digest=str(_execution_request_digest),
            requested_at=_execution_requested_at,
            safety_started_at=now,
        )

    monkeypatch.setattr(maintenance, "maintain_virtual_player_v2", commit_growth)
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_evaluate_member",
        lambda _member: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )
    original_finalize = virtual_reserve_pool._finalize_virtual_reserve_growth
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_finalize_virtual_reserve_growth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated crash after maintenance commit")),
    )

    assert grow_due_virtual_reserves(now=now, limit=1) == 0

    training_member.refresh_from_db()
    operation_id = training_member.growth_operation_id
    assert execution_count == 1
    assert operation_id
    assert BotMaintenanceExecution.objects.filter(operation_id=operation_id).exists()
    assert BotMaintenanceRecovery.objects.filter(
        scope=BotMaintenanceRecovery.Scope.ARENA_MEMBER,
        entity_key=f"member:{training_member.id}",
        failure_code="programmer_error",
    ).exists()

    monkeypatch.setattr(
        virtual_reserve_pool,
        "_finalize_virtual_reserve_growth",
        original_finalize,
    )
    demand = training_member.demand
    demand.version += 1
    demand.target_team_power += 100
    demand.save(update_fields=["version", "target_team_power", "updated_at"])
    retry_at = now + virtual_reserve_pool.GROWTH_CLAIM_LEASE + timedelta(seconds=1)
    assert grow_due_virtual_reserves(now=retry_at, limit=1) == 1

    training_member.refresh_from_db()
    assert execution_count == 1
    assert training_member.growth_claim_token is None
    assert training_member.growth_applied_action_count == 1
    assert training_member.state == ArenaVirtualReserveMember.State.READY


@pytest.mark.django_db
def test_expired_growth_claim_reuses_operation_and_fences_stale_finalize(
    training_member,
):
    now = timezone.now()
    growth_targets: dict[tuple[int, int], object] = {}
    first = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets=growth_targets,
    )
    assert first is not None
    reclaimed_at = now + virtual_reserve_pool.GROWTH_CLAIM_LEASE + timedelta(seconds=1)
    second = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=reclaimed_at,
        growth_targets=growth_targets,
    )
    assert second is not None
    assert second.claim_token != first.claim_token
    assert second.operation_id == first.operation_id
    assert second.attempt_ordinal == 2

    assert not virtual_reserve_pool._finalize_virtual_reserve_growth(
        first,
        growth_outcome=AcceleratedGrowthOutcome.BUSY,
        now=reclaimed_at,
    )
    training_member.refresh_from_db()
    assert training_member.growth_claim_token == second.claim_token
    budget_entries = parse_arena_growth_budget_entries(
        training_member.arena_growth_budget_entries,
        now=reclaimed_at,
    )
    # Reclaiming an expired claim cancels its old PENDING reservation before
    # creating the replacement attempt, so the 24-hour budget is not double
    # charged while a worker is being recovered.
    assert len(budget_entries) == 1
    assert all(entry.outcome is ArenaGrowthAttemptOutcome.PENDING for entry in budget_entries)


@pytest.mark.django_db
def test_schema_three_growth_claim_rejects_missing_objective(training_member):
    now = timezone.now()
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None
    assert claim.request_digest_schema == 3
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        growth_objective_payload={},
    )

    assert not virtual_reserve_pool._finalize_virtual_reserve_growth(
        claim,
        growth_outcome=AcceleratedGrowthOutcome.BUSY,
        now=now,
    )
    training_member.refresh_from_db()
    assert training_member.growth_claim_token == claim.claim_token


@pytest.mark.django_db
def test_growth_attempt_budget_defers_claim_beyond_active_budget(training_member, caplog):
    now = timezone.now()
    oldest = now - timedelta(hours=23)
    entries = tuple(
        ArenaGrowthBudgetEntry(
            attempt_id=str(uuid4()),
            attempted_at=oldest + timedelta(minutes=index),
            outcome=ArenaGrowthAttemptOutcome.NO_ACTION,
            effective_progress=False,
            selected_growth_bps=0,
        )
        for index in range(ARENA_GROWTH_BUDGET_MAX_ATTEMPTS)
    )
    training_member.arena_growth_budget_entries = serialize_arena_growth_budget_entries(entries)
    training_member.save(update_fields=["arena_growth_budget_entries", "updated_at"])
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")

    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )

    assert claim is None
    training_member.refresh_from_db()
    assert training_member.growth_claim_token is None
    assert training_member.next_acceleration_at == oldest + ARENA_GROWTH_BUDGET_WINDOW
    assert training_member.growth_retry_reason == "arena_attempt_budget_exhausted"
    record = next(
        record
        for record in caplog.records
        if getattr(record, "failure_reason", None) == "arena_attempt_budget_exhausted"
    )
    assert record.attempt_count == ARENA_GROWTH_BUDGET_MAX_ATTEMPTS


@pytest.mark.django_db
def test_growth_claim_can_finalize_after_lease_when_it_was_not_reclaimed(
    training_member,
):
    now = timezone.now()
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None

    assert virtual_reserve_pool._finalize_virtual_reserve_growth(
        claim,
        growth_outcome=AcceleratedGrowthOutcome.BUSY,
        now=claim.claim_expires_at + timedelta(seconds=1),
    )

    training_member.refresh_from_db()
    assert training_member.growth_claim_token is None
    assert training_member.next_acceleration_at > now
    assert training_member.growth_retry_reason == "profile_busy"


@pytest.mark.django_db
def test_paused_member_lease_prevents_claim_finalize_from_expiring_training_member(training_member):
    now = timezone.now()
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        lease_expires_at=now + timedelta(minutes=30),
        lease_paused_at=now,
    )

    assert virtual_reserve_pool._finalize_virtual_reserve_growth(
        claim,
        growth_outcome=AcceleratedGrowthOutcome.BUSY,
        now=now + timedelta(hours=1),
    )

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.lease_paused_at == now
    assert training_member.growth_claim_token is None
    assert (
        virtual_reserve_pool._claim_due_virtual_reserve_growth(
            member_id=training_member.id,
            demand_id=training_member.demand_id,
            now=now + timedelta(hours=1),
            growth_targets={},
        )
        is None
    )


@pytest.mark.django_db
def test_blocked_demand_active_claim_is_released_during_finalize(training_member):
    now = timezone.now()
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None
    ArenaVirtualDemand.objects.filter(pk=training_member.demand_id).update(
        status=ArenaVirtualDemand.Status.BLOCKED,
    )

    assert virtual_reserve_pool._finalize_virtual_reserve_growth(
        claim,
        growth_outcome=AcceleratedGrowthOutcome.NO_ACTION,
        now=now,
    )
    assert not ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).exists()


@pytest.mark.django_db
def test_blocked_demand_expired_claim_is_released_by_periodic_growth_scan(training_member):
    now = timezone.now()
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None
    ArenaVirtualDemand.objects.filter(pk=training_member.demand_id).update(
        status=ArenaVirtualDemand.Status.BLOCKED,
    )

    assert (
        grow_due_virtual_reserves(
            now=claim.claim_expires_at + timedelta(seconds=1),
            limit=10,
        )
        == 0
    )
    assert not ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).exists()


@pytest.mark.django_db
def test_growth_finalize_revalidates_demand_version(
    monkeypatch,
    training_member,
):
    now = timezone.now()

    def change_demand_version(*_args, **_kwargs):
        ArenaVirtualDemand.objects.filter(pk=training_member.demand_id).update(version=2)
        return AcceleratedGrowthOutcome.GROWN

    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        change_demand_version,
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_evaluate_member",
        lambda *_args, **_kwargs: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=1) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.READY
    assert training_member.evaluated_version == 2
    assert training_member.growth_applied_action_count == 1
    assert training_member.growth_claim_token is None


@pytest.mark.django_db
def test_reevaluation_and_trim_preserve_member_with_growth_claim(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None
    demand = training_member.demand
    demand.version += 1
    demand.reserve_target_count = 1
    demand.warm_target_count = 1
    demand.save(update_fields=["version", "reserve_target_count", "warm_target_count"])
    monkeypatch.setattr(
        virtual_reserve_pool,
        "evaluate_bot_lineup",
        lambda *_args, **_kwargs: pytest.fail("claimed member must not be reevaluated"),
    )

    reevaluate_existing_members(demand, now=now)
    removable = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_create_bot_profile(
            "reserve_growth_claim_removable",
            guest_stats=[(150, 150, 25)],
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now,
    )
    virtual_reserve_pool._trim_surplus_members(demand)
    assert virtual_reserve_pool.release_virtual_reserve_members_for_demand(demand) == 0

    training_member.refresh_from_db()
    assert training_member.growth_claim_token == claim.claim_token
    assert training_member.evaluated_version == claim.member_version
    assert not ArenaVirtualReserveMember.objects.filter(pk=removable.pk).exists()


@pytest.mark.django_db
def test_successful_fill_preserves_in_flight_growth_claim(ready_reserve_demand):
    now = timezone.now()
    claimed_member = ArenaVirtualReserveMember.objects.create(
        demand=ready_reserve_demand.demand,
        profile=_create_bot_profile(
            "reserve_fill_growth_claim",
            guest_stats=[(150, 150, 25)],
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now,
    )
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=claimed_member.id,
        demand_id=claimed_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None

    assert (
        fill_due_tournament_reserve(
            ready_reserve_demand.demand.tournament_id,
            now=now,
        )
        == 1
    )

    claimed_member.refresh_from_db()
    claimed_member.demand.refresh_from_db()
    assert claimed_member.growth_claim_token == claim.claim_token
    assert claimed_member.demand.status == ArenaVirtualDemand.Status.SATISFIED

    assert (
        grow_due_virtual_reserves(
            now=claim.claim_expires_at + timedelta(seconds=1),
            limit=1,
        )
        == 0
    )
    assert not ArenaVirtualReserveMember.objects.filter(pk=claimed_member.pk).exists()


@pytest.mark.django_db
def test_growth_continues_after_many_rounds_without_lifetime_cap(monkeypatch, training_member, caplog):
    training_member.growth_rounds_started = 9
    training_member.save(update_fields=["growth_rounds_started"])
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 10, "defense": 10, "max_hp": 100},),
            30,
            False,
        ),
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == virtual_reserve_pool.ARENA_SLOTS_PER_ROUND

    training_member.refresh_from_db()
    assert training_member.growth_rounds_started == 10
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.next_acceleration_at is not None
    assert training_member.growth_retry_reason == ""
    assert not any(
        getattr(record, "event", None) == "arena_virtual_profile_exhausted"
        and getattr(record, "failure_reason", None) == "target_cap_retry_limit"
        for record in caplog.records
    )


@pytest.mark.django_db
def test_applied_action_without_readiness_progress_consumes_round_only(
    monkeypatch,
    training_member,
    caplog,
):
    now = timezone.now()
    previous_progress_at = now - timedelta(hours=2)
    demand = training_member.demand
    demand.last_progress_at = previous_progress_at
    demand.consecutive_failure_count = 2
    demand.last_failure_reason = "previous_failure"
    demand.save(
        update_fields=[
            "last_progress_at",
            "consecutive_failure_count",
            "last_failure_reason",
        ]
    )
    power_before = int(training_member.current_lineup_power)
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda *_args, **_kwargs: BotLineupEvaluation(
            ({"attack": 100, "defense": 100, "max_hp": 1000},),
            power_before,
            False,
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=1) == 8

    training_member.refresh_from_db()
    demand.refresh_from_db()
    assert training_member.growth_applied_action_count == 8
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert demand.last_progress_at == previous_progress_at
    assert demand.consecutive_failure_count == 2
    assert demand.last_failure_reason == "previous_failure"
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_profile_grown"
    )
    assert record.readiness_progress is False
    assert record.selected_lineup_gap_before == record.selected_lineup_gap_after


@pytest.mark.django_db
def test_post_fill_growth_waits_fifteen_minutes_before_repeating(monkeypatch, training_member):
    now = timezone.now()
    calls: list[int] = []
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: calls.append(profile_id) or AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 10, "defense": 10, "max_hp": 100},),
            30,
            False,
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 8
    assert grow_due_virtual_reserves(now=now, limit=10) == 0
    assert calls == [training_member.profile_id] * 8
    training_member.refresh_from_db()
    assert (
        now + timedelta(minutes=15)
        <= training_member.next_acceleration_at
        <= (now + timedelta(minutes=15) + ARENA_REARM_JITTER_MAX)
    )


@pytest.mark.django_db
def test_growth_consumes_the_remaining_round_slots_in_order(monkeypatch, training_member):
    now = timezone.now()
    BotProfile.objects.filter(pk=training_member.profile_id).update(policy_version=2)
    calls: list[dict[str, object]] = []

    def grow_profile(profile_id, **kwargs):
        calls.append({"profile_id": profile_id, **kwargs})
        return AcceleratedGrowthOutcome.GROWN

    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        grow_profile,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 10, "defense": 10, "max_hp": 100},),
            30,
            False,
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=1) == 8
    assert len(calls) == 8
    assert [int(call["_arena_action_ordinal"]) for call in calls] == list(range(1, 9))
    assert {int(call["_arena_round_ordinal"]) for call in calls} == {1}
    assert {int(call["_arena_slot_attempt_ordinal"]) for call in calls} == {1}

    training_member.refresh_from_db()
    assert training_member.growth_rounds_started == 1
    assert training_member.growth_applied_action_count == 8
    assert training_member.growth_round_id == ""
    assert (
        now + timedelta(minutes=15)
        <= training_member.next_acceleration_at
        <= (now + timedelta(minutes=15) + ARENA_REARM_JITTER_MAX)
    )


@pytest.mark.django_db
def test_pre_fill_growth_waits_one_hour_before_repeating(monkeypatch, training_member):
    now = timezone.now()
    tournament = training_member.demand.tournament
    tournament.virtual_fill_at = now + timedelta(hours=2)
    tournament.save(update_fields=["virtual_fill_at"])
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 10, "defense": 10, "max_hp": 100},),
            30,
            False,
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 8

    training_member.refresh_from_db()
    assert (
        now + timedelta(hours=1)
        <= training_member.next_acceleration_at
        <= (now + timedelta(hours=1) + ARENA_REARM_JITTER_MAX)
    )


@pytest.mark.django_db
def test_busy_growth_keeps_training_member(monkeypatch, training_member):
    now = timezone.now()
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.BUSY,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == _single_growth_round_attempt_count()

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.growth_applied_action_count == 0
    assert training_member.next_acceleration_at > now
    assert training_member.growth_retry_reason == "arena_attempt_budget_exhausted"


@pytest.mark.django_db
def test_busy_growth_stops_at_the_member_lease_deadline(monkeypatch, training_member):
    now = timezone.now()
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        created_at=now - MAX_RESERVE_MEMBER_LEASE_AGE,
        lease_expires_at=now,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.BUSY,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.next_acceleration_at is None
    assert training_member.growth_retry_reason == "growth_busy_lease_deadline"


@pytest.mark.django_db
def test_no_action_growth_retries_without_consuming_a_growth_round(monkeypatch, training_member, caplog):
    now = timezone.now()
    created_at = now - timedelta(hours=11)
    ArenaVirtualReserveMember.objects.filter(
        pk=training_member.pk,
    ).update(
        created_at=created_at,
        lease_expires_at=created_at + MAX_RESERVE_MEMBER_LEASE_AGE,
    )
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.NO_ACTION,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.growth_applied_action_count == 0
    assert training_member.next_acceleration_at == now + timedelta(minutes=15)
    record = next(record for record in caplog.records if getattr(record, "failure_reason", None) == "growth_no_action")
    assert record.lease_deadline == (created_at + MAX_RESERVE_MEMBER_LEASE_AGE).isoformat()
    budget_entries = parse_arena_growth_budget_entries(
        training_member.arena_growth_budget_entries,
        now=now,
    )
    assert len(budget_entries) == 1
    assert budget_entries[0].outcome is ArenaGrowthAttemptOutcome.NO_ACTION
    assert budget_entries[0].effective_progress is False


@pytest.mark.django_db
def test_no_action_growth_uses_exponential_member_backoff(monkeypatch, training_member):
    now = timezone.now()
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.NO_ACTION,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1
    training_member.refresh_from_db()
    first_retry_at = training_member.next_acceleration_at
    assert first_retry_at == now + timedelta(minutes=15)
    assert training_member.growth_retry_streak == 1
    assert training_member.growth_retry_reason == "growth_no_action"

    assert grow_due_virtual_reserves(now=first_retry_at, limit=10) == 1
    training_member.refresh_from_db()
    assert training_member.next_acceleration_at == first_retry_at + timedelta(minutes=30)
    assert training_member.growth_retry_streak == 2


@pytest.mark.django_db
def test_no_action_retry_stops_at_the_absolute_lease_deadline(monkeypatch, training_member):
    deadline = timezone.now() + timedelta(minutes=10)
    created_at = deadline - MAX_RESERVE_MEMBER_LEASE_AGE
    ArenaVirtualReserveMember.objects.filter(
        pk=training_member.pk,
    ).update(
        created_at=created_at,
        lease_expires_at=deadline,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.NO_ACTION,
    )

    assert grow_due_virtual_reserves(now=deadline - timedelta(minutes=10), limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.next_acceleration_at == deadline
    assert training_member.growth_applied_action_count == 0

    assert grow_due_virtual_reserves(now=deadline, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.next_acceleration_at is None
    assert training_member.growth_applied_action_count == 0
    assert training_member.created_at == created_at


@pytest.mark.django_db
def test_no_action_at_deadline_frees_active_capacity(monkeypatch, training_member, caplog):
    now = timezone.now()
    ArenaVirtualReserveMember.objects.filter(
        pk=training_member.pk,
    ).update(
        created_at=now - MAX_RESERVE_MEMBER_LEASE_AGE,
        lease_expires_at=now,
    )
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.NO_ACTION,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.growth_applied_action_count == 0
    assert training_member.next_acceleration_at is None
    assert training_member.growth_retry_reason == "no_action_lease_deadline"
    assert training_member.demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).count() == 0
    record = next(
        record for record in caplog.records if getattr(record, "failure_reason", None) == "no_action_lease_deadline"
    )
    assert record.growth_rounds == 1


@pytest.mark.django_db
def test_expired_exhausted_terminal_lease_is_released():
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=2)
    demand.status = ArenaVirtualDemand.Status.BLOCKED
    demand.save(update_fields=["status", "updated_at"])
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_create_bot_profile("reserve_expired_exhausted_terminal"),
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
        last_checked_at=now - timedelta(hours=1),
    )

    assert virtual_reserve_pool.release_expired_exhausted_virtual_reserve_members(now=now, limit=10) == 1
    assert not ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()


@pytest.mark.django_db
def test_reevaluation_does_not_reactivate_a_no_action_expired_member(monkeypatch, training_member):
    now = timezone.now()
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
        created_at=now - MAX_RESERVE_MEMBER_LEASE_AGE,
        lease_expires_at=now,
        next_acceleration_at=None,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda *_args, **_kwargs: pytest.fail("expired member must not be reevaluated"),
    )

    reevaluate_existing_members(training_member.demand, now=now)

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.growth_applied_action_count == 0
    assert training_member.next_acceleration_at is None
    assert training_member.growth_retry_reason == "no_action_lease_deadline"


@pytest.mark.django_db
def test_paused_growth_result_preserves_training_member_and_refunds_attempt_budget(
    monkeypatch,
    training_member,
    caplog,
):
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.PAUSED,
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.growth_claim_token is None
    assert training_member.arena_growth_budget_entries == []
    record = next(record for record in caplog.records if getattr(record, "failure_reason", None) == "growth_paused")
    assert record.growth_rounds == 1
    assert record.member_state == ArenaVirtualReserveMember.State.TRAINING


@pytest.mark.django_db
def test_runtime_paused_preserves_training_and_resumes_growth_automatically(monkeypatch, training_member):
    _enroll_profile_v2(training_member.profile)
    routing = _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        calibration_routes=[],
    )

    def growth_attempt(*_args, **_kwargs):
        return AcceleratedGrowthOutcome.NO_ACTION

    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        growth_attempt,
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 0

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.growth_claim_token is None
    assert training_member.arena_growth_budget_entries == []

    routing.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    routing.save(update_fields=["maintenance_mode"])

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 1
    training_member.refresh_from_db()
    budget_entries = parse_arena_growth_budget_entries(
        training_member.arena_growth_budget_entries,
        now=timezone.now(),
    )
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.growth_claim_token is None
    assert len(budget_entries) == 1
    assert budget_entries[0].outcome is ArenaGrowthAttemptOutcome.NO_ACTION


@pytest.mark.django_db
def test_routing_unavailable_preserves_due_growth_and_recovers_automatically(monkeypatch, training_member):
    now = timezone.now()
    _enroll_profile_v2(training_member.profile)
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
    )
    routing_available = False
    read_routing = runtime_assessment.read_virtual_player_routing

    def conditional_routing():
        if not routing_available:
            raise runtime_assessment.RuntimeRoutingError("routing unavailable")
        return read_routing()

    monkeypatch.setattr(runtime_assessment, "read_virtual_player_routing", conditional_routing)
    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.NO_ACTION,
    )

    original_deadline = now + timedelta(minutes=30)
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        lease_expires_at=original_deadline,
        lease_paused_at=None,
    )
    due_at = training_member.next_acceleration_at
    assert grow_due_virtual_reserves(now=now, limit=10) == 0
    training_member.refresh_from_db()
    assert training_member.next_acceleration_at == due_at
    assert training_member.growth_claim_token is None
    assert training_member.arena_growth_budget_entries == []
    assert training_member.lease_paused_at == now
    assert training_member.lease_expires_at == original_deadline

    routing_available = True
    resumed_at = now + timedelta(hours=2)
    assert grow_due_virtual_reserves(now=resumed_at, limit=10) == 1
    training_member.refresh_from_db()
    budget_entries = parse_arena_growth_budget_entries(
        training_member.arena_growth_budget_entries,
        now=resumed_at,
    )
    assert training_member.growth_claim_token is None
    assert len(budget_entries) == 1
    assert budget_entries[0].outcome is ArenaGrowthAttemptOutcome.NO_ACTION
    assert training_member.lease_paused_at is None
    assert training_member.lease_expires_at == original_deadline + (resumed_at - now)


@pytest.mark.django_db
def test_demand_reconcile_preserves_failure_backoff_and_scan_skips_until_due():
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=2)
    reserve_fill_service._record_fill_deferred(
        demand_id=demand.id,
        reason="insufficient_ready_members",
        now=now,
    )
    demand.refresh_from_db()
    retry_at = demand.next_retry_at
    assert retry_at is not None
    assert demand.consecutive_failure_count == 1

    reconciled = reconcile_tournament_demand(demand.tournament_id, now=now + timedelta(minutes=1))
    assert reconciled is not None
    reconciled.refresh_from_db()
    assert reconciled.next_retry_at == retry_at
    assert reconciled.consecutive_failure_count == 1

    result = scan_virtual_reserve_demands(now=now + timedelta(minutes=1), limit=20)
    assert result["scanned"] == 0


@pytest.mark.django_db
def test_same_demand_failure_is_coalesced_until_its_retry_window_expires(reserve_demand):
    now = timezone.now()

    with transaction.atomic():
        demand = ArenaVirtualDemand.objects.select_for_update().get(pk=reserve_demand.pk)
        virtual_reserve_pool.record_demand_failure_locked(
            demand,
            reason="target_unreachable_by_cap",
            now=now,
        )

    reserve_demand.refresh_from_db()
    first_retry_at = reserve_demand.next_retry_at
    assert first_retry_at == now + timedelta(minutes=5)
    assert reserve_demand.consecutive_failure_count == 1

    with transaction.atomic():
        demand = ArenaVirtualDemand.objects.select_for_update().get(pk=reserve_demand.pk)
        virtual_reserve_pool.record_demand_failure_locked(
            demand,
            reason="target_unreachable_by_cap",
            now=now + timedelta(seconds=1),
        )

    reserve_demand.refresh_from_db()
    assert reserve_demand.next_retry_at == first_retry_at
    assert reserve_demand.consecutive_failure_count == 1

    with transaction.atomic():
        demand = ArenaVirtualDemand.objects.select_for_update().get(pk=reserve_demand.pk)
        virtual_reserve_pool.record_demand_failure_locked(
            demand,
            reason="target_unreachable_by_cap",
            now=first_retry_at,
        )

    reserve_demand.refresh_from_db()
    assert reserve_demand.consecutive_failure_count == 2
    assert reserve_demand.next_retry_at == first_retry_at + timedelta(minutes=10)


@pytest.mark.django_db
def test_same_demand_failure_coalesces_after_a_different_prior_failure_episode(reserve_demand):
    now = timezone.now()
    reserve_demand.consecutive_failure_count = 2
    reserve_demand.last_failure_reason = "insufficient_ready_members"
    reserve_demand.save(
        update_fields=[
            "consecutive_failure_count",
            "last_failure_reason",
            "updated_at",
        ]
    )

    with transaction.atomic():
        demand = ArenaVirtualDemand.objects.select_for_update().get(pk=reserve_demand.pk)
        virtual_reserve_pool.record_demand_failure_locked(
            demand,
            reason="target_unreachable_by_cap",
            now=now,
        )

    reserve_demand.refresh_from_db()
    retry_at = reserve_demand.next_retry_at
    assert retry_at == now + timedelta(minutes=20)
    assert reserve_demand.consecutive_failure_count == 3

    with transaction.atomic():
        demand = ArenaVirtualDemand.objects.select_for_update().get(pk=reserve_demand.pk)
        virtual_reserve_pool.record_demand_failure_locked(
            demand,
            reason="target_unreachable_by_cap",
            now=now + timedelta(seconds=1),
        )

    reserve_demand.refresh_from_db()
    assert reserve_demand.next_retry_at == retry_at
    assert reserve_demand.consecutive_failure_count == 3


@pytest.mark.django_db
def test_growth_scan_coalesces_batch_unreachable_members_into_one_demand_failure(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    demand = training_member.demand
    peer_profile = _create_bot_profile(
        "batch_unreachable_growth_peer",
        guest_stats=[(150, 150, 25)],
    )
    peer = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=peer_profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now,
    )
    training_member.next_acceleration_at = now
    training_member.save(update_fields=["next_acceleration_at", "updated_at"])
    demand.consecutive_failure_count = 2
    demand.last_failure_reason = "insufficient_ready_members"
    demand.save(
        update_fields=[
            "consecutive_failure_count",
            "last_failure_reason",
            "updated_at",
        ]
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_evaluate_profile_for_demand",
        lambda *_args, **_kwargs: BotLineupEvaluation(
            ({"attack": 150, "defense": 150, "max_hp": 25},),
            450,
            False,
        ),
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_arena_growth_reachability",
        lambda **_kwargs: virtual_reserve_pool._ArenaReachabilityAssessment(
            False,
            max_selected_power=900,
            reason="target_unreachable_by_cap",
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 0

    demand.refresh_from_db()
    training_member.refresh_from_db()
    peer.refresh_from_db()
    assert demand.consecutive_failure_count == 3
    assert demand.last_failure_reason == "target_unreachable_by_cap"
    assert demand.next_retry_at == now + timedelta(minutes=20)
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert peer.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.growth_claim_token is None
    assert peer.growth_claim_token is None


@pytest.mark.django_db
def test_ineligible_growth_releases_training_member(monkeypatch, training_member):
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.INELIGIBLE,
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 1

    assert not ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).exists()


@pytest.mark.django_db
def test_unknown_growth_outcome_enters_recovery_without_consuming_round(monkeypatch, training_member):
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: "unexpected",
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 0

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.growth_applied_action_count == 0
    assert BotMaintenanceRecovery.objects.filter(
        scope=BotMaintenanceRecovery.Scope.ARENA_MEMBER,
        entity_key=f"member:{training_member.id}",
        failure_code="programmer_error",
    ).exists()


@pytest.mark.django_db
def test_due_fill_prefers_profiles_outside_shared_24_hour_cooldown(
    ready_reserve_demand,
):
    now = timezone.now()
    recent = ready_reserve_demand.members[0].profile
    old = ready_reserve_demand.members[1].profile
    BotProfile.objects.filter(pk=recent.pk).update(
        last_arena_participated_at=now - timedelta(hours=1),
    )
    BotProfile.objects.filter(pk=old.pk).update(
        last_arena_participated_at=now - timedelta(days=2),
    )

    filled = fill_due_tournament_reserve(
        ready_reserve_demand.demand.tournament_id,
        now=now,
    )

    assert filled == 1
    virtual_entry = ready_reserve_demand.demand.tournament.entries.get(
        source=ArenaEntry.Source.VIRTUAL,
    )
    assert virtual_entry.manor_id == old.manor_id


@pytest.mark.django_db
def test_due_fill_falls_back_to_oldest_recent_profile(ready_reserve_demand):
    now = timezone.now()
    older_recent = ready_reserve_demand.members[0].profile
    newer_recent = ready_reserve_demand.members[1].profile
    BotProfile.objects.filter(pk=older_recent.pk).update(
        last_arena_participated_at=now - timedelta(hours=3),
    )
    BotProfile.objects.filter(pk=newer_recent.pk).update(
        last_arena_participated_at=now - timedelta(hours=1),
    )

    assert (
        fill_due_tournament_reserve(
            ready_reserve_demand.demand.tournament_id,
            now=now,
        )
        == 1
    )

    virtual_entry = ready_reserve_demand.demand.tournament.entries.get(
        source=ArenaEntry.Source.VIRTUAL,
    )
    assert virtual_entry.manor_id == older_recent.manor_id


@pytest.mark.django_db
def test_successful_fill_updates_shared_participation_history(ready_reserve_demand, caplog):
    now = timezone.now()
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    demand = ready_reserve_demand.demand
    demand.admission_attempt_high_water = 2
    demand.admission_paused_at = now
    demand.admission_pause_reason = "no_effective_progress"
    demand.admission_probe_target_ordinal = 2
    demand.save(
        update_fields=[
            "admission_attempt_high_water",
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "updated_at",
        ]
    )

    assert (
        fill_due_tournament_reserve(
            ready_reserve_demand.demand.tournament_id,
            now=now,
        )
        == 1
    )

    virtual_entry = ready_reserve_demand.demand.tournament.entries.get(
        source=ArenaEntry.Source.VIRTUAL,
    )
    selected_profile = BotProfile.objects.get(manor_id=virtual_entry.manor_id)
    selected_profile.refresh_from_db()
    demand.refresh_from_db()
    assert selected_profile.last_arena_participated_at == now
    assert selected_profile.arena_participation_count == 1
    assert demand.admission_pause_reason == ""
    assert demand.admission_paused_at is None
    assert demand.admission_probe_target_ordinal is None
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_fill_completed"
    )
    assert record.mode == "tournament"
    assert record.event_id == ready_reserve_demand.demand.tournament_id
    assert record.selected_profile_ids == [selected_profile.id]
    assert record.used_cooldown is False
    assert record.failure_reason == ""


@pytest.mark.django_db
def test_fill_rolls_back_all_virtual_entries_when_ready_member_becomes_invalid(
    monkeypatch,
    ready_reserve_demand,
    caplog,
):
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_fill._select_bot_lineup",
        lambda *args, **kwargs: [],
    )

    assert fill_due_tournament_reserve(ready_reserve_demand.demand.tournament_id) == 0
    assert (
        ready_reserve_demand.demand.tournament.entries.filter(
            source=ArenaEntry.Source.VIRTUAL,
        ).count()
        == 0
    )
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_fill_deferred"
    )
    assert record.failure_reason == "ready_member_revalidation_failed"
    assert record.demand_id == ready_reserve_demand.demand.id


@pytest.mark.django_db
def test_due_coop_fill_uses_shared_reserve_and_moves_to_preparing():
    now = timezone.now()
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=2,
        guest_limit_per_entry=1,
        prepare_duration_seconds=120,
        virtual_fill_at=now - timedelta(minutes=1),
    )
    _add_real_coop_entry(event, "reserve_due_coop_reference")
    demand = reconcile_coop_demand(event.id, now=now)
    assert demand is not None
    profile = _create_bot_profile("reserve_due_coop_ready")
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
        current_lineup_power=600,
    )

    assert fill_due_coop_reserve(event.id, now=now) == 1

    event.refresh_from_db()
    demand.refresh_from_db()
    assert event.status == ArenaCoopEvent.Status.PREPARING
    assert event.virtual_fill_completed is True
    assert event.prepare_ends_at == now + timedelta(seconds=120)
    assert event.entries.filter(source=ArenaCoopEntry.Source.VIRTUAL).count() == 1
    assert demand.status == ArenaVirtualDemand.Status.SATISFIED


@pytest.mark.django_db
def test_periodic_scan_replenishes_and_fills_due_reserve():
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=2)
    _create_bot_profile("reserve_periodic_scan_ready")

    result = scan_virtual_reserve_demands(now=now, limit=20)

    demand.tournament.refresh_from_db()
    assert result == {
        "scanned": 1,
        "reconciled": 1,
        "ready": 1,
        "training": 0,
        "filled_entries": 1,
    }
    assert demand.tournament.status == ArenaTournament.Status.RUNNING
    assert demand.tournament.entries.filter(source=ArenaEntry.Source.VIRTUAL).count() == 1


@pytest.mark.django_db
def test_periodic_scan_observes_arena_shortage_once_when_fill_reconciles_again(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    now = timezone.now()
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=now - timedelta(minutes=1),
    )
    _add_real_arena_entry(
        tournament,
        "reserve_single_shortage_observation_reference",
        attack=200,
        defense=200,
        max_hp=2_000,
    )
    _create_bot_profile("reserve_single_shortage_observation_ready")
    observations: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        reserve_reconcile_service,
        "emit_arena_shortage_after_commit",
        lambda *args, **kwargs: observations.append((args, kwargs)),
    )

    with django_capture_on_commit_callbacks(execute=True):
        result = scan_virtual_reserve_demands(now=now, limit=20)

    assert result["filled_entries"] == 1
    assert len(observations) == 1


@pytest.mark.django_db
def test_periodic_scan_discovers_recruiting_event_without_persisted_demand():
    now = timezone.now()
    _set_runtime_routing(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=now - timedelta(minutes=1),
    )
    _add_real_arena_entry(
        tournament,
        "reserve_periodic_discovery_reference",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    _create_bot_profile("reserve_periodic_discovery_ready")
    assert not ArenaVirtualDemand.objects.filter(tournament=tournament).exists()

    result = scan_virtual_reserve_demands(now=now, limit=20)

    tournament.refresh_from_db()
    demand = ArenaVirtualDemand.objects.get(tournament=tournament)
    assert result["scanned"] == 1
    assert result["reconciled"] == 1
    assert result["filled_entries"] == 1
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert demand.status == ArenaVirtualDemand.Status.SATISFIED
