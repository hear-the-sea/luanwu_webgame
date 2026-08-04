from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from gameplay.models import BotExternalStrengthReconciliation, BotPolicyRelease, BotProfile, BotRuntimeRoutingState
from gameplay.services import runtime_configs
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core import gate_e_cutover_workflow, policy_registry, profile_management
from gameplay.services.virtual_player_core.config import MaintenanceMode, V2RoutingConfig, policy_checksum
from gameplay.services.virtual_player_core.external_reconciliation import (
    ExternalReconciliationConflict,
    ReconciliationOperationSummary,
    requeue_quarantined_reconciliation_operation,
)
from gameplay.services.virtual_player_core.gate_evidence import GateReadinessProof
from gameplay.services.virtual_player_core.policy_registry import PolicyReleaseResult, release_policy
from gameplay.services.virtual_player_core.profile_management import BatchOperationSummary, ProfileManagementError
from gameplay.services.virtual_player_core.profile_store import ProfilePlanIdentity

pytestmark = [pytest.mark.integration]

E_PROOF = GateReadinessProof(
    gate="e",
    evidence_id="gate-c-concurrency-test-evidence",
    evidence_digest="e" * 64,
    recorded_at_utc="2026-07-30T00:00:00Z",
)


@pytest.fixture
def verified_gate_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        gate_e_cutover_workflow,
        "verify_gate_e_readiness",
        lambda **_kwargs: E_PROOF,
    )
    monkeypatch.setattr(
        gate_e_cutover_workflow,
        "assert_current_evidence_environment",
        lambda _proof: None,
    )


def _require_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("Gate C concurrency integration requires MySQL row locks")


def _create_quarantined_reconciliation() -> BotExternalStrengthReconciliation:
    now = timezone.now()
    return BotExternalStrengthReconciliation.objects.create(
        profile_id=91,
        domain_event_kind="gate_c_concurrency",
        domain_event_id="requeue-race",
        origin_committed_at=now - timedelta(minutes=5),
        pre_strength_summary={"score": 10},
        pre_prestige_band="newbie",
        status=BotExternalStrengthReconciliation.Status.QUARANTINED,
        profile_attempt_count=12,
        available_at=now + timedelta(hours=1),
        quarantined_at=now,
        quarantined_phase=BotExternalStrengthReconciliation.Phase.PROFILE,
        failure_code="profile_contract_error",
        last_error_digest="a" * 64,
    )


def _create_rng_repair_profile(django_user_model, *, username: str) -> BotProfile:
    now = timezone.now()
    payload = {"name": "gate-c-concurrency-repair"}
    checksum = policy_checksum(payload)
    release_policy(version=61, checksum=checksum, payload=payload)
    user = django_user_model(username=username)
    user.set_password("pass123")
    user._signup_region = "north"
    user.save()
    manor = ensure_manor(user)
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=961_001,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
        engine_version=2,
        rng_version=99,
        plan_schema_version=1,
        policy_version=61,
        policy_checksum=checksum,
        development_profile={},
        last_strength_increase_at=now,
        v2_enrolled_at=now,
    )


def _create_v1_enrollment_profile(django_user_model, *, username: str) -> BotProfile:
    now = timezone.now()
    user = django_user_model(username=username)
    user.set_password("pass123")
    user._signup_region = "north"
    user.save()
    manor = ensure_manor(user)
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=961_002,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )


def _create_cutover_routing() -> BotRuntimeRoutingState:
    return BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
        revision=0,
    )


def _activate_maintenance_v2() -> runtime_configs.RuntimeRoutingSnapshot:
    return gate_e_cutover_workflow.exit_gate_e_operation(
        expected_revision=0,
        authorization_basis="gate-c-concurrency-test",
        apply=True,
    ).snapshot


@pytest.mark.django_db(transaction=True)
def test_concurrent_requeue_applies_once_and_rejects_stale_expected_current() -> None:
    _require_mysql()
    reconciliation = _create_quarantined_reconciliation()

    start = threading.Barrier(2)
    results: list[ReconciliationOperationSummary] = []
    conflicts: list[ExternalReconciliationConflict] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = requeue_quarantined_reconciliation_operation(
                reconciliation_id=reconciliation.pk,
                expected_failure_code="profile_contract_error",
                expected_attempt_count=12,
                recovery_basis="incident-gate-c-requeue-race",
                apply=True,
            )
            with results_guard:
                results.append(result)
        except ExternalReconciliationConflict as exc:
            with results_guard:
                conflicts.append(exc)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    reconciliation.refresh_from_db()
    assert all(not thread.is_alive() for thread in threads)
    assert unexpected_errors == []
    assert len(results) == 1
    assert results[0].changed == 1
    assert len(conflicts) == 1
    assert "is not quarantined" in str(conflicts[0])
    assert reconciliation.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
    assert reconciliation.profile_attempt_count == 0
    assert reconciliation.failure_code == ""


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_policy_publish_is_idempotent(monkeypatch) -> None:
    _require_mysql()
    version = 62
    payload = {"name": "gate-c-concurrent-policy", "max_development_actions": 1}
    checksum = policy_checksum(payload)
    released_at = timezone.now()
    both_observed_absent = threading.Barrier(2)
    results: list[PolicyReleaseResult] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _concurrent_database_now():
        both_observed_absent.wait(timeout=10)
        return released_at

    monkeypatch.setattr(policy_registry, "_database_utc_now", _concurrent_database_now)

    def _worker() -> None:
        close_old_connections()
        try:
            result = release_policy(
                version=version,
                checksum=checksum,
                payload=payload,
            )
            with results_guard:
                results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads)
    assert unexpected_errors == []
    assert len(results) == 2
    assert sorted(result.created for result in results) == [False, True]
    assert {(result.release.pk, result.release.checksum) for result in results} == {(version, checksum)}
    assert BotPolicyRelease.objects.filter(version=version).count() == 1
    release = BotPolicyRelease.objects.get(version=version)
    assert release.checksum == checksum
    assert release.payload == payload
    assert release.released_at == released_at


@pytest.mark.django_db(transaction=True)
def test_enrollment_holds_routing_lock_until_the_batch_commits(
    django_user_model,
    monkeypatch,
    verified_gate_evidence,
) -> None:
    _require_mysql()
    routing = _create_cutover_routing()
    policy_registry.release_configured_policy_operation(version=1, apply=True)
    profile = _create_v1_enrollment_profile(
        django_user_model,
        username="gate_c_enrollment_holds_routing",
    )

    enrollment_reached_profile_write = threading.Event()
    allow_enrollment = threading.Event()
    transition_started = threading.Event()
    transition_finished = threading.Event()
    enrollment_results: list[BatchOperationSummary] = []
    transition_results: list[runtime_configs.RuntimeRoutingSnapshot] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()
    original_enroll_profile = profile_management.enroll_profile_v2

    def _block_before_profile_write(*args, **kwargs):
        enrollment_reached_profile_write.set()
        if not allow_enrollment.wait(timeout=10):
            raise TimeoutError("enrollment was not released by the concurrency test")
        return original_enroll_profile(*args, **kwargs)

    monkeypatch.setattr(
        profile_management,
        "enroll_profile_v2",
        _block_before_profile_write,
    )

    def _enrollment_worker() -> None:
        close_old_connections()
        try:
            result = profile_management.enroll_virtual_players_batch(
                batch_size=10,
                apply=True,
            )
            with results_guard:
                enrollment_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    def _transition_worker() -> None:
        close_old_connections()
        transition_started.set()
        try:
            result = _activate_maintenance_v2()
            with results_guard:
                transition_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            transition_finished.set()
            close_old_connections()

    enrollment_thread = threading.Thread(target=_enrollment_worker, daemon=True)
    transition_thread = threading.Thread(target=_transition_worker, daemon=True)
    enrollment_thread.start()
    assert enrollment_reached_profile_write.wait(timeout=10)
    transition_thread.start()
    assert transition_started.wait(timeout=10)
    assert not transition_finished.wait(timeout=0.5)

    allow_enrollment.set()
    enrollment_thread.join(timeout=30)
    transition_thread.join(timeout=30)

    profile.refresh_from_db()
    routing.refresh_from_db()
    assert not enrollment_thread.is_alive()
    assert not transition_thread.is_alive()
    assert unexpected_errors == []
    assert len(enrollment_results) == 1
    assert enrollment_results[0].changed == 1
    assert len(transition_results) == 1
    assert transition_results[0].maintenance_mode is MaintenanceMode.V2_ACTIVE
    assert profile.engine_version == 2
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert routing.revision == 1


@pytest.mark.django_db(transaction=True)
def test_policy_rollout_batch_holds_routing_lock_until_commit(
    django_user_model,
    monkeypatch,
) -> None:
    _require_mysql()
    routing = _create_cutover_routing()
    profile = _create_rng_repair_profile(
        django_user_model,
        username="gate_c_rollout_holds_routing",
    )
    target_payload = {"name": "gate-c-concurrency-rollout-target"}
    target_checksum = policy_checksum(target_payload)
    release_policy(version=62, checksum=target_checksum, payload=target_payload)
    runtime_configs.transition_virtual_player_policy_rollout(
        expected_revision=0,
        expected_target_version=1,
        expected_enabled=False,
        expected_rollout_percent=0,
        target_version=62,
        enabled=True,
        rollout_percent=100,
    )

    rollout_reached_profile_write = threading.Event()
    allow_rollout = threading.Event()
    transition_started = threading.Event()
    transition_finished = threading.Event()
    rollout_results: list[BatchOperationSummary] = []
    transition_results: list[runtime_configs.PolicyRolloutSnapshot] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()
    original_upgrade_profile = profile_management.upgrade_profile_policy

    def _block_before_profile_write(*args, **kwargs):
        rollout_reached_profile_write.set()
        if not allow_rollout.wait(timeout=10):
            raise TimeoutError("policy rollout was not released by the concurrency test")
        return original_upgrade_profile(*args, **kwargs)

    monkeypatch.setattr(
        profile_management,
        "upgrade_profile_policy",
        _block_before_profile_write,
    )

    def _rollout_worker() -> None:
        close_old_connections()
        try:
            result = profile_management.rollout_virtual_player_policy_batch(
                expected_revision=1,
                expected_policy_version=61,
                expected_policy_checksum=profile.policy_checksum,
                target_policy_checksum=target_checksum,
                batch_size=10,
                apply=True,
            )
            with results_guard:
                rollout_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    def _transition_worker() -> None:
        close_old_connections()
        transition_started.set()
        try:
            result = runtime_configs.transition_virtual_player_policy_rollout(
                expected_revision=1,
                expected_target_version=62,
                expected_enabled=True,
                expected_rollout_percent=100,
                target_version=62,
                enabled=False,
                rollout_percent=0,
            )
            with results_guard:
                transition_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            transition_finished.set()
            close_old_connections()

    rollout_thread = threading.Thread(target=_rollout_worker, daemon=True)
    transition_thread = threading.Thread(target=_transition_worker, daemon=True)
    rollout_thread.start()
    assert rollout_reached_profile_write.wait(timeout=10)
    transition_thread.start()
    assert transition_started.wait(timeout=10)
    assert not transition_finished.wait(timeout=0.5)

    allow_rollout.set()
    rollout_thread.join(timeout=30)
    transition_thread.join(timeout=30)

    profile.refresh_from_db()
    routing.refresh_from_db()
    assert not rollout_thread.is_alive()
    assert not transition_thread.is_alive()
    assert unexpected_errors == []
    assert len(rollout_results) == 1
    assert rollout_results[0].changed == 1
    assert len(transition_results) == 1
    assert transition_results[0].enabled is False
    assert profile.policy_version == 62
    assert routing.policy_rollout_enabled is False
    assert routing.revision == 2


@pytest.mark.django_db(transaction=True)
def test_rng_repair_holds_routing_lock_until_repair_commits(
    django_user_model,
    monkeypatch,
    verified_gate_evidence,
) -> None:
    _require_mysql()
    routing = _create_cutover_routing()
    profile = _create_rng_repair_profile(
        django_user_model,
        username="gate_c_repair_holds_routing",
    )

    repair_holds_routing = threading.Event()
    allow_repair = threading.Event()
    transition_started = threading.Event()
    transition_finished = threading.Event()
    repair_results: list[BatchOperationSummary] = []
    transition_results: list[runtime_configs.RuntimeRoutingSnapshot] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()
    original_get_identity = profile_management.get_profile_plan_identity

    def _block_after_routing_lock(profile_id: int) -> ProfilePlanIdentity | None:
        repair_holds_routing.set()
        if not allow_repair.wait(timeout=10):
            raise TimeoutError("repair was not released by the concurrency test")
        return original_get_identity(profile_id)

    monkeypatch.setattr(
        profile_management,
        "get_profile_plan_identity",
        _block_after_routing_lock,
    )

    def _repair_worker() -> None:
        close_old_connections()
        try:
            result = profile_management.repair_virtual_player_rng(
                profile_id=profile.pk,
                expected_rng_version=99,
                target_rng_version=1,
                recovery_basis="incident-gate-c-repair-lock",
                apply=True,
            )
            with results_guard:
                repair_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    def _transition_worker() -> None:
        close_old_connections()
        transition_started.set()
        try:
            result = _activate_maintenance_v2()
            with results_guard:
                transition_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            transition_finished.set()
            close_old_connections()

    repair_thread = threading.Thread(target=_repair_worker, daemon=True)
    transition_thread = threading.Thread(target=_transition_worker, daemon=True)
    repair_thread.start()
    assert repair_holds_routing.wait(timeout=10)
    transition_thread.start()
    assert transition_started.wait(timeout=10)
    assert not transition_finished.wait(timeout=0.5)

    allow_repair.set()
    repair_thread.join(timeout=30)
    transition_thread.join(timeout=30)

    profile.refresh_from_db()
    routing.refresh_from_db()
    assert not repair_thread.is_alive()
    assert not transition_thread.is_alive()
    assert unexpected_errors == []
    assert len(repair_results) == 1
    assert repair_results[0].changed == 1
    assert len(transition_results) == 1
    assert transition_results[0].maintenance_mode is MaintenanceMode.V2_ACTIVE
    assert profile.rng_version == 1
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert routing.revision == 1


@pytest.mark.django_db(transaction=True)
def test_rng_repair_waits_for_transition_and_rejects_final_v2_active_mode(
    django_user_model,
    monkeypatch,
    verified_gate_evidence,
) -> None:
    _require_mysql()
    routing = _create_cutover_routing()
    profile = _create_rng_repair_profile(
        django_user_model,
        username="gate_c_transition_blocks_repair",
    )

    transition_holds_routing = threading.Event()
    allow_transition = threading.Event()
    repair_started = threading.Event()
    repair_finished = threading.Event()
    transition_results: list[runtime_configs.RuntimeRoutingSnapshot] = []
    repair_conflicts: list[ProfileManagementError] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()
    original_validate_transition = runtime_configs.validate_routing_transition

    def _block_transition_after_routing_lock(
        current: V2RoutingConfig,
        proposed: V2RoutingConfig,
    ) -> None:
        original_validate_transition(current, proposed)
        transition_holds_routing.set()
        if not allow_transition.wait(timeout=10):
            raise TimeoutError("transition was not released by the concurrency test")

    monkeypatch.setattr(
        runtime_configs,
        "validate_routing_transition",
        _block_transition_after_routing_lock,
    )

    def _transition_worker() -> None:
        close_old_connections()
        try:
            result = _activate_maintenance_v2()
            with results_guard:
                transition_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    def _repair_worker() -> None:
        close_old_connections()
        repair_started.set()
        try:
            profile_management.repair_virtual_player_rng(
                profile_id=profile.pk,
                expected_rng_version=99,
                target_rng_version=1,
                recovery_basis="incident-gate-c-final-mode",
                apply=True,
            )
        except ProfileManagementError as exc:
            with results_guard:
                repair_conflicts.append(exc)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            repair_finished.set()
            close_old_connections()

    transition_thread = threading.Thread(target=_transition_worker, daemon=True)
    repair_thread = threading.Thread(target=_repair_worker, daemon=True)
    transition_thread.start()
    assert transition_holds_routing.wait(timeout=10)
    repair_thread.start()
    assert repair_started.wait(timeout=10)
    assert not repair_finished.wait(timeout=0.5)

    allow_transition.set()
    transition_thread.join(timeout=30)
    repair_thread.join(timeout=30)

    profile.refresh_from_db()
    routing.refresh_from_db()
    assert not transition_thread.is_alive()
    assert not repair_thread.is_alive()
    assert unexpected_errors == []
    assert len(transition_results) == 1
    assert transition_results[0].maintenance_mode is MaintenanceMode.V2_ACTIVE
    assert len(repair_conflicts) == 1
    assert "development writes to be stopped" in str(repair_conflicts[0])
    assert profile.rng_version == 99
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert routing.revision == 1
