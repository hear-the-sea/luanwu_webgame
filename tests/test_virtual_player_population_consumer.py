from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import gameplay.tasks.virtual_players as virtual_player_tasks
from gameplay.models import (
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotPopulationRecomputeDemand,
    BotProfile,
    BotRuntimeRoutingState,
)
from gameplay.services.arena import virtual_reserve_pool
from gameplay.services.virtual_player_core import arena_population, bootstrap, population_runtime, runtime_assessment
from gameplay.services.virtual_player_core.contracts import PopulationMutationStatus
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.population import ArenaHandoffSupply, arena_materialization_deficit
from gameplay.services.virtual_player_core.population_runtime import (
    PopulationCellReconcileStatus,
    PopulationMutationResult,
    merge_population_recompute_demand,
)
from tests.arena_services.test_virtual_backfill import _add_real_arena_entry, _create_bot_profile

pytestmark = pytest.mark.django_db


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=2, apply=True)


@contextmanager
def _owned_population():
    yield lambda: None


def _activate_consumer(monkeypatch) -> None:
    BotRuntimeRoutingState.objects.update_or_create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        defaults={
            "bootstrap_mode": BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
            "maintenance_mode": BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        },
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_bootstrap_routing_is_active",
        lambda: True,
    )
    monkeypatch.setattr(
        population_runtime,
        "_population_ownership",
        _owned_population,
    )


def _bootstrap_plan():
    return SimpleNamespace(projection=None)


def _population_plan(*, deficit: int):
    cell = SimpleNamespace(structural_deficit=deficit)
    return SimpleNamespace(by_key={("north", "newbie"): cell})


def _patch_bootstrap_builder(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "build_virtual_player_v2_bootstrap_plan",
        lambda **_kwargs: _bootstrap_plan(),
    )


def _enroll_test_profile_v2(profile: BotProfile, *, region: str) -> BotProfile:
    now = timezone.now()
    profile.manor.region = region
    profile.manor.save(update_fields=["region"])
    profile.engine_version = 2
    profile.rng_version = 1
    profile.plan_schema_version = 1
    profile.policy_version = 2
    profile.policy_checksum = "a" * 64
    profile.last_strength_increase_at = now
    profile.v2_enrolled_at = now
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


def test_active_arena_shortage_expands_derived_v2_capacity_but_not_explicit_hard_cap(
    monkeypatch,
    settings,
) -> None:
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 0,
            "global_active_multiplier": 0,
        },
        "prestige_bands": {"newbie": [0, 500]},
    }
    _activate_consumer(monkeypatch)
    monkeypatch.setattr(
        arena_population,
        "active_arena_population_activations",
        lambda: (SimpleNamespace(region="overseas", prestige=100, needed=4),),
    )
    config = population_runtime._v2_population_runtime_config()
    # Remove the repository deployment guard so this first half exercises the
    # derived-capacity path; the second half below verifies an explicit cap.
    config["population"].pop("hard_cap", None)

    derived = population_runtime._build_population_plan(
        config,
        now=timezone.now(),
        target_based_membership=True,
        required_engine_version=2,
    )

    assert derived.hard_cap == 4
    assert derived.by_key[("overseas", "newbie")].target == 4

    settings.VIRTUAL_PLAYER_CONFIG["population"]["hard_cap"] = 2
    capped_config = population_runtime._v2_population_runtime_config()
    capped = population_runtime._build_population_plan(
        capped_config,
        now=timezone.now(),
        target_based_membership=True,
        required_engine_version=2,
    )

    assert capped.hard_cap == 2


def test_active_arena_population_recovery_creates_a_missing_overseas_cell_once(monkeypatch) -> None:
    now = timezone.now()
    _activate_consumer(monkeypatch)
    monkeypatch.setattr(
        arena_population,
        "active_arena_population_activations",
        lambda: (SimpleNamespace(region="overseas", prestige=100, needed=4),),
    )

    created = population_runtime.ensure_active_arena_population_recompute_demands(now=now)
    repeated = population_runtime.ensure_active_arena_population_recompute_demands(now=now)

    assert [(row.region, row.prestige_band, row.requested_revision) for row in created] == [("overseas", "newbie", 1)]
    assert repeated == ()
    demand = BotPopulationRecomputeDemand.objects.get(region="overseas", prestige_band="newbie")
    assert demand.requested_revision == 1


def test_paused_maintenance_suppresses_v2_population_activation_and_resume_requeues(
    monkeypatch,
    settings,
) -> None:
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 0,
            "global_active_multiplier": 0,
        },
        "prestige_bands": {"newbie": [0, 500]},
    }
    routing = BotRuntimeRoutingState.objects.create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        calibration_routes=[],
    )
    monkeypatch.setattr(
        arena_population,
        "active_arena_population_activations",
        lambda: (SimpleNamespace(region="overseas", prestige=100, needed=1),),
    )

    assert population_runtime.ensure_active_arena_population_recompute_demands(now=now) == ()
    assert not BotPopulationRecomputeDemand.objects.exists()

    routing.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    routing.save(update_fields=["maintenance_mode"])

    resumed = population_runtime.ensure_active_arena_population_recompute_demands(now=now)
    assert [(row.region, row.prestige_band) for row in resumed] == [("overseas", "newbie")]


def test_routing_unavailable_suppresses_population_revision_and_periodic_scan_recovers(
    monkeypatch,
    settings,
) -> None:
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 0,
            "global_active_multiplier": 0,
        },
        "prestige_bands": {"newbie": [0, 500]},
    }
    BotRuntimeRoutingState.objects.create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
    )
    monkeypatch.setattr(
        arena_population,
        "active_arena_population_activations",
        lambda: (SimpleNamespace(region="overseas", prestige=100, needed=1),),
    )
    routing_available = False
    read_routing = runtime_assessment.read_virtual_player_routing

    def conditional_routing():
        if not routing_available:
            raise runtime_assessment.RuntimeRoutingError("routing unavailable")
        return read_routing()

    monkeypatch.setattr(runtime_assessment, "read_virtual_player_routing", conditional_routing)

    assert population_runtime.ensure_active_arena_population_recompute_demands(now=now) == ()
    assert not BotPopulationRecomputeDemand.objects.exists()

    routing_available = True
    resumed = population_runtime.ensure_active_arena_population_recompute_demands(now=now)

    assert [(row.region, row.prestige_band, row.requested_revision) for row in resumed] == [("overseas", "newbie", 1)]


def test_arena_admission_funnel_observation_is_cell_scoped(monkeypatch, caplog) -> None:
    now = timezone.now()
    monkeypatch.setattr(
        arena_population,
        "active_arena_population_funnel_snapshots",
        lambda **_kwargs: (
            SimpleNamespace(
                region="overseas",
                prestige=100,
                demand_count=2,
                materialization_need=3,
                raw_materialization_need=5,
                suppressed_materialization_need=2,
                warm_target_count=8,
                replacement_target_count=12,
                admission_attempt_high_water=7,
                admission_high_water_lag_count=0,
                ready_count=1,
                training_count=3,
                exhausted_count=2,
                growth_attempt_count=9,
                growth_applied_count=4,
                effective_progress_count=2,
                selected_growth_bps_total=700,
                selected_growth_bps_max=300,
                invalid_growth_budget_count=0,
                oldest_ready_member_age_seconds=60,
                oldest_training_member_age_seconds=600,
                oldest_exhausted_member_age_seconds=900,
                guard_reason_counts=(("no_effective_progress", 1),),
                retry_reason_counts=(("profile_busy", 2),),
            ),
        ),
    )
    monkeypatch.setattr(
        arena_population,
        "arena_handoff_supply_by_cell",
        lambda *_args, **_kwargs: {
            ("overseas", "newbie"): ArenaHandoffSupply(available=2),
        },
    )
    caplog.set_level("INFO", logger=arena_population.logger.name)

    observations = arena_population.observe_arena_population_funnel(
        {"prestige_bands": {"newbie": [0, 500]}},
        maintained=object(),
        target_based=True,
        now=now,
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.population_materialization_additional == 1
    assert observation.guard_reason_counts == (("no_effective_progress", 1),)
    record = next(record for record in caplog.records if record.event == "arena_virtual_admission_funnel")
    assert record.region == "overseas"
    assert record.prestige_band == "newbie"
    assert record.effective_progress_ratio == 0.5
    assert not hasattr(record, "demand_id")
    assert not hasattr(record, "profile_id")


def test_arena_handoff_supply_counts_only_unleased_eligible_profiles(game_data) -> None:
    tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RECRUITING, player_limit=2)
    demand = ArenaVirtualDemand.objects.create(
        tournament=tournament,
        status=ArenaVirtualDemand.Status.ACTIVE,
        missing_entry_count=1,
        reserve_target_count=2,
        warm_target_count=2,
        max_reserve_target_count=2,
    )
    leased_profile = _enroll_test_profile_v2(
        _create_bot_profile("population_pipeline_leased"),
        region="overseas",
    )
    unleased_profile = _enroll_test_profile_v2(
        _create_bot_profile("population_pipeline_unleased"),
        region="overseas",
    )
    abandoned_profile = _enroll_test_profile_v2(
        _create_bot_profile("population_pipeline_abandoned"),
        region="overseas",
    )
    abandoned_profile.state = BotProfile.State.ABANDONED
    abandoned_profile.save(update_fields=["state"])
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=leased_profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
    )
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=abandoned_profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    terminal_tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.COMPLETED, player_limit=2)
    terminal_demand = ArenaVirtualDemand.objects.create(
        tournament=terminal_tournament,
        status=ArenaVirtualDemand.Status.SATISFIED,
        missing_entry_count=0,
        reserve_target_count=0,
        warm_target_count=0,
        max_reserve_target_count=0,
    )
    terminal_profile = _enroll_test_profile_v2(
        _create_bot_profile("population_pipeline_terminal"),
        region="overseas",
    )
    ArenaVirtualReserveMember.objects.create(
        demand=terminal_demand,
        profile=terminal_profile,
        state=ArenaVirtualReserveMember.State.READY,
    )

    config = {"prestige_bands": {"newbie": [0, 500]}}
    with CaptureQueriesContext(connection) as captured:
        supply = arena_population.arena_handoff_supply_by_cell(
            BotProfile.objects.filter(
                pk__in=[leased_profile.pk, unleased_profile.pk, abandoned_profile.pk, terminal_profile.pk]
            ),
            arena_demands={("overseas", "newbie"): 2},
            config=config,
            target_based=True,
        )

    # The handoff check validates active demand/member/reference data and two
    # bounded guest prefetches in fixed-size batches. Keep this bound
    # independent of the number of demands.
    assert 1 <= len(captured) <= 11
    assert supply[("overseas", "newbie")].available == 1


def test_arena_handoff_supply_batches_demands_and_assigns_each_profile_once(
    game_data,
    monkeypatch,
) -> None:
    for index in range(2):
        tournament = ArenaTournament.objects.create(
            status=ArenaTournament.Status.RUNNING,
            player_limit=2,
        )
        reference = _add_real_arena_entry(
            tournament,
            f"population_handoff_reference_{index}",
            attack=200,
            defense=200,
            max_hp=2000,
        )
        reference.manor.region = "overseas"
        reference.manor.save(update_fields=["region"])
        ArenaVirtualDemand.objects.create(
            tournament=tournament,
            status=ArenaVirtualDemand.Status.ACTIVE,
            target_guest_count=1,
            target_team_power=600,
            missing_entry_count=1,
            reserve_target_count=1,
            warm_target_count=1,
            max_reserve_target_count=1,
        )
    eligible = _enroll_test_profile_v2(
        _create_bot_profile(
            "population_handoff_live_cap_eligible",
            guest_stats=[(150, 150, 50)],
        ),
        region="overseas",
    )
    rejected = _enroll_test_profile_v2(
        _create_bot_profile(
            "population_handoff_live_cap_rejected",
            guest_stats=[(150, 150, 50)],
        ),
        region="overseas",
    )
    monkeypatch.setattr(
        arena_population,
        "is_virtual_profile_arena_match_eligible",
        lambda profile, **_kwargs: profile.id == eligible.id,
    )

    with CaptureQueriesContext(connection) as captured:
        supply = arena_population.arena_handoff_supply_by_cell(
            BotProfile.objects.filter(pk__in=[eligible.pk, rejected.pk]),
            arena_demands={("overseas", "newbie"): 2},
            config={"prestige_bands": {"newbie": [0, 500]}},
            target_based=True,
        )

    assert len(captured) <= 11
    assert supply[("overseas", "newbie")].available == 1


@pytest.mark.parametrize(
    ("guest_stats", "target_guest_count"),
    (
        ([(80, 80, 20)], 1),
        ([(150, 150, 50)], 2),
    ),
    ids=("power-growth", "guest-recruitment"),
)
def test_arena_handoff_counts_reachable_training_without_materializing(
    game_data,
    monkeypatch,
    guest_stats,
    target_guest_count,
) -> None:
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
    )
    reference = _add_real_arena_entry(
        tournament,
        f"population_handoff_training_reference_{target_guest_count}",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    reference.manor.region = "overseas"
    reference.manor.save(update_fields=["region"])
    ArenaVirtualDemand.objects.create(
        tournament=tournament,
        status=ArenaVirtualDemand.Status.ACTIVE,
        target_guest_count=target_guest_count,
        target_team_power=600,
        missing_entry_count=1,
        reserve_target_count=1,
        warm_target_count=1,
        max_reserve_target_count=1,
    )
    profile = _enroll_test_profile_v2(
        _create_bot_profile(
            f"population_handoff_training_{target_guest_count}",
            guest_stats=guest_stats,
        ),
        region="overseas",
    )
    monkeypatch.setattr(
        arena_population,
        "is_virtual_profile_arena_match_eligible",
        lambda *_args, **_kwargs: True,
    )

    supply = arena_population.arena_handoff_supply_by_cell(
        BotProfile.objects.filter(pk=profile.pk),
        arena_demands={("overseas", "newbie"): 1},
        config={"prestige_bands": {"newbie": [0, 500]}},
        target_based=True,
        candidate_engine_version=2,
        training_admission_allowed=True,
    )

    assert supply[("overseas", "newbie")].available == 1
    assert arena_materialization_deficit(required_handoff=1, handoff_supply=supply[("overseas", "newbie")]) == 0


def test_arena_handoff_uses_maximum_matching_for_general_and_specialized_candidates(
    game_data,
    monkeypatch,
) -> None:
    demands: list[ArenaVirtualDemand] = []
    for index in range(2):
        tournament = ArenaTournament.objects.create(
            status=ArenaTournament.Status.RUNNING,
            player_limit=2,
        )
        reference = _add_real_arena_entry(
            tournament,
            f"population_handoff_matching_reference_{index}",
            attack=200,
            defense=200,
            max_hp=2000,
        )
        reference.manor.region = "overseas"
        reference.manor.save(update_fields=["region"])
        demands.append(
            ArenaVirtualDemand.objects.create(
                tournament=tournament,
                status=ArenaVirtualDemand.Status.ACTIVE,
                target_guest_count=1,
                target_team_power=600,
                missing_entry_count=1,
                reserve_target_count=1,
                warm_target_count=1,
                max_reserve_target_count=1,
            )
        )
    general = _enroll_test_profile_v2(
        _create_bot_profile("population_handoff_matching_general"),
        region="overseas",
    )
    specialized = _enroll_test_profile_v2(
        _create_bot_profile("population_handoff_matching_specialized"),
        region="overseas",
    )
    first_demand_id = demands[0].id

    def assess_candidate(demand, profile):
        eligible = profile.id == general.id or demand.id == first_demand_id
        return SimpleNamespace(
            disposition=(
                virtual_reserve_pool.ArenaReserveCandidateDisposition.READY
                if eligible
                else virtual_reserve_pool.ArenaReserveCandidateDisposition.REJECTED
            )
        )

    monkeypatch.setattr(
        virtual_reserve_pool,
        "assess_arena_reserve_candidate",
        assess_candidate,
    )
    monkeypatch.setattr(
        arena_population,
        "is_virtual_profile_arena_match_eligible",
        lambda *_args, **_kwargs: True,
    )

    supply = arena_population.arena_handoff_supply_by_cell(
        BotProfile.objects.filter(pk__in=[general.pk, specialized.pk]),
        arena_demands={("overseas", "newbie"): 2},
        config={"prestige_bands": {"newbie": [0, 500]}},
        target_based=True,
        candidate_engine_version=2,
    )

    assert supply[("overseas", "newbie")].available == 2


def test_arena_population_handoff_uses_region_scoped_deduplication(monkeypatch):
    dispatch = Mock(return_value=True)
    task = object()
    monkeypatch.setattr(arena_population.current_app, "signature", lambda name: task)
    monkeypatch.setattr(arena_population, "safe_apply_async_with_dedup", dispatch)

    assert arena_population.queue_arena_population_handoff(region="overseas") is True
    assert arena_population.queue_arena_population_handoff(region="overseas") is True

    assert dispatch.call_count == 2
    first_call = dispatch.call_args_list[0].kwargs
    second_call = dispatch.call_args_list[1].kwargs
    assert first_call["dedup_key"] == second_call["dedup_key"] == "virtual-player-arena-population-handoff:overseas"
    assert first_call["dedup_timeout"] == arena_population.ARENA_POPULATION_HANDOFF_DEDUP_SECONDS
    assert first_call["args"] == ["overseas"]


def test_routing_inactive_preserves_pending_demand() -> None:
    now = timezone.now()
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    assert result.status is PopulationCellReconcileStatus.ROUTING_INACTIVE
    demand = BotPopulationRecomputeDemand.objects.get()
    assert demand.requested_revision == 1
    assert demand.completed_revision == 0
    assert demand.claim_token is None
    assert demand.consecutive_failure_count == 0


def test_planned_creation_is_abandoned_when_cell_deficit_disappears(
    monkeypatch,
) -> None:
    now = timezone.now()
    created = []
    issue_permit = Mock(side_effect=AssertionError("a cell without a deficit cannot receive a permit"))
    _activate_consumer(monkeypatch)
    _patch_bootstrap_builder(monkeypatch)
    monkeypatch.setattr(
        bootstrap,
        "_issue_v2_bootstrap_population_permit",
        issue_permit,
    )
    monkeypatch.setattr(
        bootstrap,
        "create_virtual_player_v2",
        lambda **kwargs: created.append(kwargs),
    )
    monkeypatch.setattr(
        population_runtime,
        "_lock_population_capacity",
        lambda **_kwargs: (100, 0),
    )
    monkeypatch.setattr(
        population_runtime,
        "_build_population_plan",
        lambda *_args, **_kwargs: _population_plan(deficit=0),
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_population_cell_has_executable_deficit",
        lambda **_kwargs: False,
    )
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region="north",
        prestige_band="newbie",
        limit=4,
        now=now,
    )

    assert result.status is PopulationCellReconcileStatus.COMPLETED
    assert result.processed_count == 0
    assert created == []
    issue_permit.assert_not_called()


def test_consumer_revalidates_deficit_before_each_creation(monkeypatch) -> None:
    now = timezone.now()
    deficits = iter((1, 0))
    created = []
    _activate_consumer(monkeypatch)
    _patch_bootstrap_builder(monkeypatch)
    monkeypatch.setattr(
        bootstrap,
        "create_virtual_player_v2",
        lambda **kwargs: created.append(kwargs) or SimpleNamespace(id=101),
    )
    monkeypatch.setattr(
        population_runtime,
        "_lock_population_capacity",
        lambda **_kwargs: (100, 0),
    )
    monkeypatch.setattr(
        population_runtime,
        "_build_population_plan",
        lambda *_args, **_kwargs: _population_plan(deficit=next(deficits)),
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_population_cell_has_executable_deficit",
        lambda **_kwargs: False,
    )
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region="north",
        prestige_band="newbie",
        limit=8,
        now=now,
    )

    assert result.status is PopulationCellReconcileStatus.COMPLETED
    assert result.processed_count == 1
    assert result.created_count == 1
    assert len(created) == 1


@pytest.mark.parametrize("region", ["north", "overseas"])
def test_population_consumer_materializes_a_real_v2_profile_from_durable_demand(
    released_v2_policy,
    game_data,
    monkeypatch,
    region,
) -> None:
    now = timezone.now()
    _activate_consumer(monkeypatch)
    arena_handoff = Mock(return_value=True)
    monkeypatch.setattr(
        arena_population,
        "queue_arena_population_handoff",
        arena_handoff,
    )
    merge_population_recompute_demand(
        region=region,
        prestige_band="newbie",
        now=now,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region=region,
        prestige_band="newbie",
        limit=1,
        now=now,
    )

    assert result.status is PopulationCellReconcileStatus.CONTINUED
    assert result.processed_count == 1
    assert result.created_count == 1
    profile = BotProfile.objects.get(engine_version=2)
    assert profile.manor.region == region
    assert profile.current_prestige_band == "newbie"
    assert profile.target_prestige_band == "newbie"
    arena_handoff.assert_called_once_with(region=region)


def test_ownership_loss_after_materialization_rolls_back_the_cell_write(
    monkeypatch,
) -> None:
    now = timezone.now()
    _activate_consumer(monkeypatch)
    monkeypatch.setattr(
        population_runtime,
        "_lock_population_capacity",
        lambda **_kwargs: (100, 0),
    )
    monkeypatch.setattr(
        population_runtime,
        "_build_population_plan",
        lambda *_args, **_kwargs: _population_plan(deficit=1),
    )
    guard_calls = 0

    def ownership_guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 3:
            raise population_runtime.VirtualPlayerPopulationLockLostError("forced ownership loss")

    def materialize(population_permit):
        population_permit.consume(region="north", prestige_band="newbie")
        BotPopulationRecomputeDemand.objects.create(
            region="south",
            prestige_band="junior",
            requested_revision=1,
            available_at=now,
        )
        return SimpleNamespace(id=101)

    with pytest.raises(
        population_runtime.VirtualPlayerPopulationLockLostError,
        match="forced ownership loss",
    ):
        population_runtime._reactivate_or_create_virtual_player(
            region="north",
            prestige_band="newbie",
            low=0,
            high=500,
            archetype=BotProfile.Archetype.BALANCED,
            growth_seed=771_001,
            now=now,
            config={},
            projection_factory=lambda: None,
            evaluated_profile_ids=set(),
            ownership_guard=ownership_guard,
            require_population_deficit=True,
            required_engine_version=2,
            creation_factory=materialize,
            target_based_membership=True,
        )

    assert guard_calls == 3
    assert not BotPopulationRecomputeDemand.objects.filter(
        region="south",
        prestige_band="junior",
    ).exists()


def test_routing_pause_after_materialization_rolls_back_the_cell_write(
    monkeypatch,
) -> None:
    now = timezone.now()
    _activate_consumer(monkeypatch)
    monkeypatch.setattr(
        population_runtime,
        "_lock_population_capacity",
        lambda **_kwargs: (100, 0),
    )
    monkeypatch.setattr(
        population_runtime,
        "_build_population_plan",
        lambda *_args, **_kwargs: _population_plan(deficit=1),
    )

    def materialize(population_permit):
        population_permit.consume(region="north", prestige_band="newbie")
        BotPopulationRecomputeDemand.objects.create(
            region="south",
            prestige_band="junior",
            requested_revision=1,
            available_at=now,
        )
        BotRuntimeRoutingState.objects.filter(key=BotRuntimeRoutingState.GLOBAL_KEY).update(
            bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_PAUSED
        )
        return SimpleNamespace(id=102)

    with pytest.raises(
        population_runtime.PopulationRecomputeDemandError,
        match="routing stopped before materialization committed",
    ):
        population_runtime._reactivate_or_create_virtual_player(
            region="north",
            prestige_band="newbie",
            low=0,
            high=500,
            archetype=BotProfile.Archetype.BALANCED,
            growth_seed=771_002,
            now=now,
            config={},
            projection_factory=lambda: None,
            evaluated_profile_ids=set(),
            ownership_guard=lambda: None,
            require_population_deficit=True,
            required_engine_version=2,
            creation_factory=materialize,
            target_based_membership=True,
        )

    assert not BotPopulationRecomputeDemand.objects.filter(
        region="south",
        prestige_band="junior",
    ).exists()
    routing = BotRuntimeRoutingState.objects.get(key=BotRuntimeRoutingState.GLOBAL_KEY)
    assert routing.bootstrap_mode == BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE


def test_bounded_batch_adds_continuation_for_only_the_claimed_cell(
    monkeypatch,
) -> None:
    now = timezone.now()
    mutation_cells = []
    _activate_consumer(monkeypatch)
    _patch_bootstrap_builder(monkeypatch)

    def create_mutation(**kwargs):
        mutation_cells.append((kwargs["region"], kwargs["prestige_band"]))
        return PopulationMutationResult(
            status=PopulationMutationStatus.CREATED,
            profile=SimpleNamespace(id=len(mutation_cells)),
            hard_cap=100,
            maintained_count=len(mutation_cells) - 1,
        )

    monkeypatch.setattr(
        population_runtime,
        "_reactivate_or_create_virtual_player",
        create_mutation,
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_population_cell_has_executable_deficit",
        lambda **_kwargs: True,
    )
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )
    merge_population_recompute_demand(
        region="south",
        prestige_band="junior",
        now=now,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region="north",
        prestige_band="newbie",
        limit=2,
        now=now,
    )

    assert result.status is PopulationCellReconcileStatus.CONTINUED
    assert result.processed_count == 2
    assert result.created_count == 2
    assert mutation_cells == [("north", "newbie"), ("north", "newbie")]
    north = BotPopulationRecomputeDemand.objects.get(
        region="north",
        prestige_band="newbie",
    )
    south = BotPopulationRecomputeDemand.objects.get(
        region="south",
        prestige_band="junior",
    )
    assert (north.requested_revision, north.completed_revision) == (2, 1)
    assert (south.requested_revision, south.completed_revision) == (1, 0)


def test_merge_during_consumer_claim_remains_pending(monkeypatch) -> None:
    now = timezone.now()
    _activate_consumer(monkeypatch)
    _patch_bootstrap_builder(monkeypatch)

    def merge_while_processing(**_kwargs):
        merge_population_recompute_demand(
            region="north",
            prestige_band="newbie",
            now=now,
        )
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=100,
            maintained_count=0,
        )

    monkeypatch.setattr(
        population_runtime,
        "_reactivate_or_create_virtual_player",
        merge_while_processing,
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_population_cell_has_executable_deficit",
        lambda **_kwargs: False,
    )
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    assert result.status is PopulationCellReconcileStatus.COMPLETED
    demand = BotPopulationRecomputeDemand.objects.get()
    assert (demand.requested_revision, demand.completed_revision) == (2, 1)
    assert demand.claim_token is None


def test_ownership_unavailable_releases_claim_with_backoff(monkeypatch) -> None:
    now = timezone.now()
    monkeypatch.setattr(
        population_runtime,
        "_v2_bootstrap_routing_is_active",
        lambda: True,
    )

    @contextmanager
    def unavailable_population_ownership():
        yield None

    monkeypatch.setattr(
        population_runtime,
        "_population_ownership",
        unavailable_population_ownership,
    )
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    assert result.status is PopulationCellReconcileStatus.DEFERRED
    demand = BotPopulationRecomputeDemand.objects.get()
    assert demand.completed_revision == 0
    assert demand.claim_token is None
    assert demand.consecutive_failure_count == 1
    assert demand.available_at == now + timedelta(seconds=60)


def test_periodic_scan_recovers_expired_claim(monkeypatch) -> None:
    now = timezone.now()
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )
    old_claim = population_runtime.claim_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )
    assert old_claim is not None
    monkeypatch.setattr(
        population_runtime,
        "_v2_bootstrap_routing_is_active",
        lambda: True,
    )

    @contextmanager
    def unavailable_population_ownership():
        yield None

    monkeypatch.setattr(
        population_runtime,
        "_population_ownership",
        unavailable_population_ownership,
    )

    results = population_runtime.scan_virtual_player_population_demands(
        limit=1,
        now=now + timedelta(minutes=5, seconds=1),
    )

    assert [result.status for result in results] == [PopulationCellReconcileStatus.DEFERRED]
    demand = BotPopulationRecomputeDemand.objects.get()
    assert demand.claim_token is None
    assert demand.completed_revision == 0
    assert demand.consecutive_failure_count == 1


def _create_retired_profile(
    django_user_model,
    *,
    username: str,
    region: str,
    prestige: int,
    target_band: str,
    current_band: str,
    engine_version: int,
    now,
) -> BotProfile:
    user = django_user_model(username=username, is_active=False)
    user.set_unusable_password()
    user._signup_region = region
    user._virtual_player_internal = True
    user.save()
    manor = user.manor
    manor.prestige = prestige
    manor.save(update_fields=["prestige"])
    fields = {
        "manor": manor,
        "state": BotProfile.State.RETIRED,
        "prestige_band": target_band,
        "target_prestige_band": target_band,
        "current_prestige_band": current_band,
        "growth_seed": 73_100 + int(manor.id),
        "next_growth_at": now,
        "abandon_at": now + timedelta(days=30),
        "retire_at": now + timedelta(days=60),
        "maintenance_stopped_at": now,
        "engine_version": engine_version,
    }
    if engine_version == 2:
        fields.update(
            {
                "rng_version": 1,
                "plan_schema_version": 1,
                "policy_version": 2,
                "policy_checksum": "a" * 64,
                "last_strength_increase_at": now,
                "v2_enrolled_at": now,
            }
        )
    return BotProfile.objects.create(**fields)


def test_consumer_reactivates_only_same_region_same_band_v2_profile(
    monkeypatch,
    django_user_model,
) -> None:
    now = timezone.now()
    _activate_consumer(monkeypatch)
    _patch_bootstrap_builder(monkeypatch)
    monkeypatch.setattr(
        bootstrap,
        "create_virtual_player_v2",
        Mock(side_effect=AssertionError("consumer must reactivate the eligible profile")),
    )
    monkeypatch.setattr(
        population_runtime,
        "_lock_population_capacity",
        lambda **_kwargs: (100, 0),
    )
    monkeypatch.setattr(
        population_runtime,
        "_build_population_plan",
        lambda *_args, **_kwargs: _population_plan(deficit=1),
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_population_cell_has_executable_deficit",
        lambda **_kwargs: False,
    )
    v1_profile = _create_retired_profile(
        django_user_model,
        username="population_consumer_v1",
        region="north",
        prestige=100,
        target_band="newbie",
        current_band="newbie",
        engine_version=1,
        now=now,
    )
    cross_band_profile = _create_retired_profile(
        django_user_model,
        username="population_consumer_cross_band",
        region="north",
        prestige=500,
        target_band="newbie",
        current_band="junior",
        engine_version=2,
        now=now,
    )
    other_region_profile = _create_retired_profile(
        django_user_model,
        username="population_consumer_other_region",
        region="south",
        prestige=100,
        target_band="newbie",
        current_band="newbie",
        engine_version=2,
        now=now,
    )
    eligible_profile = _create_retired_profile(
        django_user_model,
        username="population_consumer_eligible",
        region="north",
        prestige=100,
        target_band="newbie",
        current_band="newbie",
        engine_version=2,
        now=now,
    )
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region="north",
        prestige_band="newbie",
        limit=1,
        now=now,
    )

    assert result.status is PopulationCellReconcileStatus.COMPLETED
    assert result.reactivated_count == 1
    for profile in (
        v1_profile,
        cross_band_profile,
        other_region_profile,
        eligible_profile,
    ):
        profile.refresh_from_db()
    assert eligible_profile.state == BotProfile.State.ACTIVE
    assert v1_profile.state == BotProfile.State.RETIRED
    assert cross_band_profile.state == BotProfile.State.RETIRED
    assert other_region_profile.state == BotProfile.State.RETIRED


def test_population_reconcile_task_never_runs_global_roll_or_maintenance(
    monkeypatch,
) -> None:
    payload = {
        "status": "completed",
        "region": "north",
        "prestige_band": "newbie",
    }
    reconcile = Mock(return_value=SimpleNamespace(to_payload=lambda: payload))
    monkeypatch.setattr(
        virtual_player_tasks,
        "reconcile_virtual_player_population_cell",
        reconcile,
    )
    monkeypatch.setattr(
        virtual_player_tasks,
        "maintain_due_virtual_players",
        Mock(side_effect=AssertionError("dedicated task must not run Maintenance")),
    )
    monkeypatch.setattr(
        virtual_player_tasks,
        "roll_virtual_player_population",
        Mock(side_effect=AssertionError("dedicated task must not run the global roll")),
    )

    result = virtual_player_tasks.reconcile_virtual_player_population_cell_task.run(
        "north",
        "newbie",
        limit=3,
    )

    assert result == payload
    reconcile.assert_called_once_with(
        region="north",
        prestige_band="newbie",
        limit=3,
    )


def test_population_scan_task_is_transport_only(monkeypatch) -> None:
    first = SimpleNamespace(to_payload=lambda: {"status": "completed"})
    second = SimpleNamespace(to_payload=lambda: {"status": "continued"})
    scan = Mock(return_value=(first, second))
    monkeypatch.setattr(
        virtual_player_tasks,
        "scan_virtual_player_population_demands",
        scan,
    )

    result = virtual_player_tasks.scan_virtual_player_population_demands_task.run(
        limit=7,
        cell_limit=2,
    )

    assert result == [{"status": "completed"}, {"status": "continued"}]
    scan.assert_called_once_with(limit=7, cell_limit=2)


def test_periodic_current_band_sync_selects_only_mismatched_v2_profiles(
    django_user_model,
) -> None:
    now = timezone.now()
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
    )
    correct = _create_retired_profile(
        django_user_model,
        username="population_periodic_correct",
        region="north",
        prestige=100,
        target_band="newbie",
        current_band="newbie",
        engine_version=2,
        now=now,
    )
    mismatched = _create_retired_profile(
        django_user_model,
        username="population_periodic_mismatched",
        region="north",
        prestige=500,
        target_band="newbie",
        current_band="newbie",
        engine_version=2,
        now=now,
    )
    legacy = _create_retired_profile(
        django_user_model,
        username="population_periodic_legacy",
        region="north",
        prestige=500,
        target_band="newbie",
        current_band="newbie",
        engine_version=1,
        now=now,
    )
    correct_updated_at = correct.updated_at

    changed = population_runtime.sync_mismatched_v2_current_prestige_bands(limit=1)

    for profile in (correct, mismatched, legacy):
        profile.refresh_from_db()
    assert changed == 1
    assert correct.current_prestige_band == "newbie"
    assert correct.updated_at == correct_updated_at
    assert mismatched.current_prestige_band == "junior"
    assert legacy.current_prestige_band == "newbie"
