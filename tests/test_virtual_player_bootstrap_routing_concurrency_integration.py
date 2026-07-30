from __future__ import annotations

import threading

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.utils import timezone

from gameplay.models import (
    BotInventoryDailyCounter,
    BotProfile,
    BotRuntimeRoutingState,
    Building,
    InventoryItem,
    Manor,
    PlayerTechnology,
    PlayerTroop,
)
from gameplay.services import runtime_configs, virtual_players
from gameplay.services.virtual_player_core import (
    bootstrap,
    gate_d1_exit_workflow,
    gate_e_cutover_workflow,
    population_runtime,
)
from gameplay.services.virtual_player_core.contracts import BotProjectionConfig, PopulationMutationStatus
from gameplay.services.virtual_player_core.gate_evidence import GateReadinessProof
from gameplay.services.virtual_player_core.profile_store import runtime_eligible_v1_profile_count
from guests.models import GearItem, Guest, GuestSkill
from tests.test_virtual_player_backfill import _bootstrap_building_types

pytestmark = pytest.mark.integration

D1_PROOF = GateReadinessProof(
    gate="d1",
    evidence_id="bootstrap-routing-concurrency-test",
    evidence_digest="d" * 64,
    recorded_at_utc="2026-07-28T00:00:00Z",
)
E_PROOF = GateReadinessProof(
    gate="e",
    evidence_id="bootstrap-routing-gate-e-concurrency-test",
    evidence_digest="e" * 64,
    recorded_at_utc="2026-07-28T00:00:00Z",
)


def _configure_minimal_v1_bootstrap(settings) -> None:
    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 1,
            "region_active_multiplier": 0,
            "global_floor": 4,
            "global_active_multiplier": 0,
            "hard_cap": 20,
        },
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "technology_keys": [],
        },
    }


def _bootstrap_graph_counts() -> dict[str, int]:
    return {
        "users": get_user_model().objects.count(),
        "manors": Manor.objects.count(),
        "profiles": BotProfile.objects.count(),
        "buildings": Building.objects.count(),
        "technologies": PlayerTechnology.objects.count(),
        "guests": Guest.objects.count(),
        "gear": GearItem.objects.count(),
        "skills": GuestSkill.objects.count(),
        "troops": PlayerTroop.objects.count(),
        "inventory": InventoryItem.objects.count(),
        "inventory_counters": BotInventoryDailyCounter.objects.count(),
    }


@pytest.mark.django_db(transaction=True)
def test_bootstrap_transition_waits_for_inflight_population_creation(
    settings,
    monkeypatch,
) -> None:
    if connection.vendor != "mysql":
        pytest.skip("Bootstrap routing serialization requires MySQL row locks")

    monkeypatch.setattr(
        gate_d1_exit_workflow,
        "verify_gate_d1_readiness",
        lambda: D1_PROOF,
    )
    monkeypatch.setattr(
        gate_d1_exit_workflow,
        "assert_current_evidence_environment",
        lambda _proof: None,
    )

    _configure_minimal_v1_bootstrap(settings)
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        revision=0,
    )

    creation_holds_routing = threading.Event()
    allow_creation = threading.Event()
    transition_started = threading.Event()
    transition_finished = threading.Event()
    completion_order: list[str] = []
    created_profile_ids: list[int] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()
    original_lock_population_capacity = population_runtime._lock_population_capacity

    def _block_after_routing_lock(*, now):
        creation_holds_routing.set()
        if not allow_creation.wait(timeout=10):
            raise TimeoutError("creation was not released by the concurrency test")
        return original_lock_population_capacity(now=now)

    monkeypatch.setattr(
        population_runtime,
        "_lock_population_capacity",
        _block_after_routing_lock,
    )

    def _create_worker() -> None:
        close_old_connections()
        try:
            mutation = population_runtime.create_virtual_player_with_capacity(
                region="north",
                prestige_band="newbie",
                growth_seed=930_001,
                now=timezone.now(),
                projection=BotProjectionConfig(0, 1, 0, 1),
                start_from_zero=True,
            )
            assert mutation.status is PopulationMutationStatus.CREATED
            assert mutation.profile is not None
            with results_guard:
                created_profile_ids.append(int(mutation.profile.id))
                completion_order.append("creation")
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _transition_worker() -> None:
        close_old_connections()
        transition_started.set()
        try:
            gate_d1_exit_workflow.exit_gate_d1_operation(
                expected_revision=0,
                authorization_basis="bootstrap-routing-concurrency-test",
                apply=True,
            )
            with results_guard:
                completion_order.append("transition")
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            transition_finished.set()
            close_old_connections()

    creation_thread = threading.Thread(target=_create_worker, daemon=True)
    transition_thread = threading.Thread(target=_transition_worker, daemon=True)
    creation_thread.start()
    assert creation_holds_routing.wait(timeout=10)
    transition_thread.start()
    assert transition_started.wait(timeout=10)
    assert not transition_finished.wait(timeout=0.5)

    allow_creation.set()
    creation_thread.join(timeout=30)
    transition_thread.join(timeout=30)

    assert not creation_thread.is_alive()
    assert not transition_thread.is_alive()
    assert errors == []
    assert completion_order == ["creation", "transition"]
    assert len(created_profile_ids) == 1
    assert BotProfile.objects.get(pk=created_profile_ids[0]).engine_version == 1
    routing = BotRuntimeRoutingState.objects.get()
    assert routing.bootstrap_mode == BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE
    assert routing.revision == 1


@pytest.mark.django_db(transaction=True)
def test_gate_d1_exit_waits_for_raw_public_v1_creation(
    settings,
    monkeypatch,
) -> None:
    if connection.vendor != "mysql":
        pytest.skip("Bootstrap routing serialization requires MySQL row locks")

    monkeypatch.setattr(
        gate_d1_exit_workflow,
        "verify_gate_d1_readiness",
        lambda: D1_PROOF,
    )
    monkeypatch.setattr(
        gate_d1_exit_workflow,
        "assert_current_evidence_environment",
        lambda _proof: None,
    )
    _configure_minimal_v1_bootstrap(settings)
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        revision=0,
    )

    creation_holds_routing = threading.Event()
    allow_creation = threading.Event()
    transition_started = threading.Event()
    transition_finished = threading.Event()
    created_profile_ids: list[int] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()
    original_create_v1 = bootstrap._create_virtual_player_v1

    def _block_before_v1_materialization(*args, **kwargs):
        creation_holds_routing.set()
        if not allow_creation.wait(timeout=10):
            raise TimeoutError("raw creation was not released by the concurrency test")
        return original_create_v1(*args, **kwargs)

    monkeypatch.setattr(
        bootstrap,
        "_create_virtual_player_v1",
        _block_before_v1_materialization,
    )

    def _create_worker() -> None:
        close_old_connections()
        try:
            profile = virtual_players.create_virtual_player(
                region="north",
                prestige_band="newbie",
                growth_seed=930_002,
                now=timezone.now(),
                projection=BotProjectionConfig(0, 1, 0, 1),
                start_from_zero=True,
            )
            with results_guard:
                created_profile_ids.append(int(profile.id))
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _transition_worker() -> None:
        close_old_connections()
        transition_started.set()
        try:
            gate_d1_exit_workflow.exit_gate_d1_operation(
                expected_revision=0,
                authorization_basis="raw-bootstrap-routing-concurrency-test",
                apply=True,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            transition_finished.set()
            close_old_connections()

    creation_thread = threading.Thread(target=_create_worker, daemon=True)
    transition_thread = threading.Thread(target=_transition_worker, daemon=True)
    creation_thread.start()
    assert creation_holds_routing.wait(timeout=10)
    transition_thread.start()
    assert transition_started.wait(timeout=10)
    assert not transition_finished.wait(timeout=0.5)

    allow_creation.set()
    creation_thread.join(timeout=30)
    transition_thread.join(timeout=30)

    assert not creation_thread.is_alive()
    assert not transition_thread.is_alive()
    assert errors == []
    assert len(created_profile_ids) == 1
    assert BotProfile.objects.get(pk=created_profile_ids[0]).engine_version == 1
    routing = BotRuntimeRoutingState.objects.get()
    assert routing.bootstrap_mode == BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE
    assert routing.revision == 1


@pytest.mark.django_db(transaction=True)
def test_gate_e_exit_waits_for_raw_public_bootstrap_guard_without_v1_dml(
    monkeypatch,
) -> None:
    if connection.vendor != "mysql":
        pytest.skip("Bootstrap routing serialization requires MySQL row locks")

    monkeypatch.setattr(
        gate_e_cutover_workflow,
        "verify_gate_e_readiness",
        lambda: E_PROOF,
    )
    monkeypatch.setattr(
        gate_e_cutover_workflow,
        "assert_current_evidence_environment",
        lambda _proof: None,
    )
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
        revision=0,
    )
    before = _bootstrap_graph_counts()

    creation_holds_routing = threading.Event()
    allow_creation_guard = threading.Event()
    transition_started = threading.Event()
    transition_finished = threading.Event()
    blocked_errors: list[runtime_configs.RuntimeRoutingGateBlocked] = []
    transition_results: list[gate_e_cutover_workflow.GateECutoverSummary] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()
    original_lock_routing = bootstrap.lock_virtual_player_routing

    def _block_after_routing_lock():
        snapshot = original_lock_routing()
        creation_holds_routing.set()
        if not allow_creation_guard.wait(timeout=10):
            raise TimeoutError("raw creation guard was not released by the test")
        return snapshot

    monkeypatch.setattr(
        bootstrap,
        "lock_virtual_player_routing",
        _block_after_routing_lock,
    )

    def _create_worker() -> None:
        close_old_connections()
        try:
            virtual_players.create_virtual_player(
                region="north",
                prestige_band="newbie",
                growth_seed=930_003,
                now=timezone.now(),
                projection=BotProjectionConfig(0, 1, 0, 1),
                start_from_zero=True,
            )
        except runtime_configs.RuntimeRoutingGateBlocked as exc:
            with results_guard:
                blocked_errors.append(exc)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    def _transition_worker() -> None:
        close_old_connections()
        transition_started.set()
        try:
            result = gate_e_cutover_workflow.exit_gate_e_operation(
                expected_revision=0,
                authorization_basis="raw-bootstrap-gate-e-concurrency-test",
                apply=True,
            )
            with results_guard:
                transition_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            transition_finished.set()
            close_old_connections()

    creation_thread = threading.Thread(target=_create_worker, daemon=True)
    transition_thread = threading.Thread(target=_transition_worker, daemon=True)
    creation_thread.start()
    assert creation_holds_routing.wait(timeout=10)
    transition_thread.start()
    assert transition_started.wait(timeout=10)
    assert not transition_finished.wait(timeout=0.5)

    allow_creation_guard.set()
    creation_thread.join(timeout=30)
    transition_thread.join(timeout=30)

    assert not creation_thread.is_alive()
    assert not transition_thread.is_alive()
    assert unexpected_errors == []
    assert len(blocked_errors) == 1
    assert len(transition_results) == 1
    assert _bootstrap_graph_counts() == before
    assert runtime_eligible_v1_profile_count() == 0
    routing = BotRuntimeRoutingState.objects.get()
    assert routing.bootstrap_mode == BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert routing.revision == 1
