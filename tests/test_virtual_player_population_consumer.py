from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.utils import timezone

import gameplay.tasks.virtual_players as virtual_player_tasks
from gameplay.models import BotPopulationRecomputeDemand, BotProfile, BotRuntimeRoutingState
from gameplay.services.virtual_player_core import bootstrap, population_runtime
from gameplay.services.virtual_player_core.contracts import PopulationMutationStatus
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.population_runtime import (
    PopulationCellReconcileStatus,
    PopulationMutationResult,
    merge_population_recompute_demand,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=1, apply=True)


@contextmanager
def _owned_population():
    yield lambda: None


def _activate_consumer(monkeypatch) -> None:
    BotRuntimeRoutingState.objects.update_or_create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        defaults={
            "bootstrap_mode": BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
            "maintenance_mode": (BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE),
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


def test_population_consumer_materializes_a_real_v2_profile_from_durable_demand(
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    now = timezone.now()
    _activate_consumer(monkeypatch)
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

    assert result.status is PopulationCellReconcileStatus.CONTINUED
    assert result.processed_count == 1
    assert result.created_count == 1
    profile = BotProfile.objects.get(engine_version=2)
    assert profile.manor.region == "north"
    assert profile.current_prestige_band == "newbie"
    assert profile.target_prestige_band == "newbie"


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
                "policy_version": 1,
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
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
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
