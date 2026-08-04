from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from gameplay.models import BotExternalStrengthReconciliation, BotPolicyRelease, BotProfile, BotRuntimeRoutingState
from gameplay.services import runtime_configs
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core import gate_d1_exit_workflow, profile_management, profile_store
from gameplay.services.virtual_player_core.config import load_virtual_player_v2_config, policy_checksum
from gameplay.services.virtual_player_core.external_reconciliation import (
    ExternalReconciliationConflict,
    requeue_quarantined_reconciliation_operation,
)
from gameplay.services.virtual_player_core.gate_evidence import GateReadinessProof
from gameplay.services.virtual_player_core.policy_registry import (
    PolicyAssignmentError,
    PolicyReleaseConflict,
    PolicyRetirementBlocked,
    release_configured_policy_operation,
    release_policy,
    retire_policy_release,
    retire_policy_release_operation,
)
from gameplay.services.virtual_player_core.profile_management import (
    ProfileManagementError,
    enroll_virtual_players_batch,
    repair_virtual_player_plan,
    repair_virtual_player_rng,
    rollout_virtual_player_policy_batch,
    upgrade_virtual_player_policy_batch,
)
from gameplay.services.virtual_player_core.random_context import policy_rollout_bucket


def _create_v1_profile(django_user_model, *, username: str, growth_seed: int) -> BotProfile:
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
        growth_seed=growth_seed,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )


def _create_quarantined_reconciliation(
    *,
    phase: str,
    profile_attempt_count: int,
    population_attempt_count: int,
) -> BotExternalStrengthReconciliation:
    now = timezone.now()
    return BotExternalStrengthReconciliation.objects.create(
        profile_id=91,
        domain_event_kind="gate_c_test",
        domain_event_id=f"{phase}-{profile_attempt_count}-{population_attempt_count}",
        origin_committed_at=now - timedelta(minutes=5),
        pre_strength_summary={"score": 10},
        pre_prestige_band="newbie",
        status=BotExternalStrengthReconciliation.Status.QUARANTINED,
        profile_attempt_count=profile_attempt_count,
        population_attempt_count=population_attempt_count,
        available_at=now + timedelta(hours=1),
        profile_completed_at=(
            now - timedelta(minutes=3) if phase == BotExternalStrengthReconciliation.Phase.POPULATION else None
        ),
        quarantined_at=now,
        quarantined_phase=phase,
        failure_code=f"{phase}_contract_error",
        last_error_digest="a" * 64,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    (
        "phase",
        "expected_attempt_count",
        "pending_status",
        "reset_field",
        "preserved_field",
        "preserved_attempt_count",
    ),
    [
        (
            BotExternalStrengthReconciliation.Phase.PROFILE,
            12,
            BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
            "profile_attempt_count",
            "population_attempt_count",
            0,
        ),
        (
            BotExternalStrengthReconciliation.Phase.POPULATION,
            7,
            BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
            "population_attempt_count",
            "profile_attempt_count",
            12,
        ),
    ],
)
def test_requeue_quarantined_reconciliation_is_expected_current_and_phase_aware(
    phase: str,
    expected_attempt_count: int,
    pending_status: str,
    reset_field: str,
    preserved_field: str,
    preserved_attempt_count: int,
) -> None:
    reconciliation = _create_quarantined_reconciliation(
        phase=phase,
        profile_attempt_count=12,
        population_attempt_count=(7 if phase == BotExternalStrengthReconciliation.Phase.POPULATION else 0),
    )
    failure_code = f"{phase}_contract_error"
    original_available_at = reconciliation.available_at
    original_profile_completed_at = reconciliation.profile_completed_at

    preview = requeue_quarantined_reconciliation_operation(
        reconciliation_id=reconciliation.pk,
        expected_failure_code=failure_code,
        expected_attempt_count=expected_attempt_count,
        recovery_basis="incident-gate-c-001",
    )
    reconciliation.refresh_from_db()
    assert preview.changed == 1
    assert preview.locked == 0
    assert reconciliation.status == BotExternalStrengthReconciliation.Status.QUARANTINED
    assert reconciliation.available_at == original_available_at

    applied = requeue_quarantined_reconciliation_operation(
        reconciliation_id=reconciliation.pk,
        expected_failure_code=failure_code,
        expected_attempt_count=expected_attempt_count,
        recovery_basis="incident-gate-c-001",
        apply=True,
    )
    reconciliation.refresh_from_db()
    assert applied.changed == 1
    assert reconciliation.status == pending_status
    assert getattr(reconciliation, reset_field) == 0
    assert getattr(reconciliation, preserved_field) == preserved_attempt_count
    assert reconciliation.profile_completed_at == original_profile_completed_at
    assert reconciliation.available_at < original_available_at
    assert reconciliation.claim_token is None
    assert reconciliation.claimed_at is None
    assert reconciliation.claim_expires_at is None
    assert reconciliation.quarantined_at is None
    assert reconciliation.quarantined_phase == ""
    assert reconciliation.failure_code == ""
    assert reconciliation.last_error_digest == ""


@pytest.mark.django_db
def test_requeue_quarantined_reconciliation_rejects_stale_expectations() -> None:
    reconciliation = _create_quarantined_reconciliation(
        phase=BotExternalStrengthReconciliation.Phase.PROFILE,
        profile_attempt_count=3,
        population_attempt_count=0,
    )

    with pytest.raises(ExternalReconciliationConflict, match="failure code changed"):
        requeue_quarantined_reconciliation_operation(
            reconciliation_id=reconciliation.pk,
            expected_failure_code="different_failure",
            expected_attempt_count=3,
            recovery_basis="incident-gate-c-002",
            apply=True,
        )
    with pytest.raises(ExternalReconciliationConflict, match="attempt count changed"):
        requeue_quarantined_reconciliation_operation(
            reconciliation_id=reconciliation.pk,
            expected_failure_code="profile_contract_error",
            expected_attempt_count=2,
            recovery_basis="incident-gate-c-002",
            apply=True,
        )

    reconciliation.refresh_from_db()
    assert reconciliation.status == BotExternalStrengthReconciliation.Status.QUARANTINED
    assert reconciliation.profile_attempt_count == 3


@pytest.mark.django_db
def test_policy_release_is_idempotent_and_rejects_same_version_with_different_content() -> None:
    released_at = timezone.now()
    payload = {"name": "gate-c-v41", "max_development_actions": 1}
    checksum = policy_checksum(payload)

    first = release_policy(
        version=41,
        checksum=checksum,
        payload=payload,
        released_at=released_at,
    )
    second = release_policy(
        version=41,
        checksum=checksum.upper(),
        payload=payload,
        released_at=released_at + timedelta(hours=1),
    )

    assert first.created is True
    assert second.created is False
    assert second.release.pk == first.release.pk
    assert second.release.released_at == released_at
    assert BotPolicyRelease.objects.filter(version=41).count() == 1

    conflicting_payload = {"name": "gate-c-v41-conflict", "max_development_actions": 1}
    with pytest.raises(PolicyReleaseConflict, match="already has different content"):
        release_policy(
            version=41,
            checksum=policy_checksum(conflicting_payload),
            payload=conflicting_payload,
        )


@pytest.mark.django_db
def test_policy_release_translates_checksum_uniqueness_to_a_domain_conflict() -> None:
    payload = {"name": "shared-checksum"}
    checksum = policy_checksum(payload)
    release_policy(version=42, checksum=checksum, payload=payload)

    with pytest.raises(PolicyReleaseConflict, match="checksum already belongs"):
        release_policy(version=43, checksum=checksum, payload=payload)


@pytest.mark.django_db
def test_assignment_previews_reject_retired_policy_releases() -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
    )
    released_at = timezone.now() - timedelta(days=31)
    configured = load_virtual_player_v2_config()
    assert configured is not None
    configured_policy = configured.policy()
    release_policy(
        version=configured_policy.version,
        checksum=configured_policy.checksum,
        payload=configured_policy.payload,
        released_at=released_at,
    )
    retire_policy_release(
        version=configured_policy.version,
        expected_checksum=configured_policy.checksum,
    )

    with pytest.raises(PolicyAssignmentError, match="is retired"):
        enroll_virtual_players_batch(batch_size=1)

    expected_payload = {"name": "active-policy-for-upgrade-preview"}
    expected_checksum = policy_checksum(expected_payload)
    release_policy(
        version=44,
        checksum=expected_checksum,
        payload=expected_payload,
    )
    with pytest.raises(PolicyAssignmentError, match="is retired"):
        upgrade_virtual_player_policy_batch(
            expected_policy_version=44,
            expected_policy_checksum=expected_checksum,
            target_policy_version=configured_policy.version,
            target_policy_checksum=configured_policy.checksum,
        )


@pytest.mark.django_db
@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize(
    "maintenance_mode",
    [
        BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
    ],
)
def test_profile_enrollment_rejects_every_non_cutover_mode(
    django_user_model,
    maintenance_mode: str,
    apply: bool,
) -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=maintenance_mode,
    )
    profile = _create_v1_profile(
        django_user_model,
        username=f"gate_c_enrollment_blocked_{maintenance_mode}_{int(apply)}",
        growth_seed=73_101,
    )

    with pytest.raises(
        ProfileManagementError,
        match="requires maintenance_mode=v2_cutover",
    ):
        enroll_virtual_players_batch(batch_size=10, apply=apply)

    profile.refresh_from_db()
    assert profile.engine_version == 1
    assert profile.v2_enrolled_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("apply", [False, True])
def test_profile_enrollment_is_available_during_cutover(
    django_user_model,
    apply: bool,
) -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
    )
    release_configured_policy_operation(version=1, apply=True)
    profile = _create_v1_profile(
        django_user_model,
        username=f"gate_c_enrollment_cutover_{int(apply)}",
        growth_seed=73_102,
    )

    summary = enroll_virtual_players_batch(batch_size=10, apply=apply)

    profile.refresh_from_db()
    assert summary.changed == 1
    assert summary.failed == 0
    assert profile.engine_version == (2 if apply else 1)
    assert (profile.v2_enrolled_at is not None) is apply


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("identity_field", "concurrent_value"),
    [
        ("growth_seed", 73_104),
        ("archetype", BotProfile.Archetype.RICH),
        ("policy_version", 9),
        ("policy_checksum", "f" * 64),
    ],
)
def test_profile_enrollment_rejects_plan_built_from_stale_identity(
    django_user_model,
    monkeypatch: pytest.MonkeyPatch,
    identity_field: str,
    concurrent_value: Any,
) -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
    )
    release_configured_policy_operation(version=1, apply=True)
    profile = _create_v1_profile(
        django_user_model,
        username=f"gate_c_enrollment_stale_{identity_field}",
        growth_seed=73_103,
    )
    stale_identity = profile_store.get_profile_plan_identity(profile.pk)
    assert stale_identity is not None
    BotProfile.objects.filter(pk=profile.pk).update(**{identity_field: concurrent_value})
    monkeypatch.setattr(
        profile_management,
        "list_v1_enrollment_candidates",
        lambda **_kwargs: (stale_identity,),
    )

    summary = enroll_virtual_players_batch(batch_size=10, apply=True)

    profile.refresh_from_db()
    assert summary.changed == 0
    assert summary.failed == 1
    assert "enrollment identity changed" in summary.reasons[0]
    assert profile.engine_version == 1
    assert profile.development_profile == {}
    assert profile.v2_enrolled_at is None


@pytest.mark.django_db
def test_configured_policy_release_defaults_to_write_free_preview() -> None:
    preview = release_configured_policy_operation(version=1)
    assert preview.changed == 1
    assert preview.skipped == 0
    assert BotPolicyRelease.objects.filter(version=1).exists() is False

    applied = release_configured_policy_operation(version=1, apply=True)
    repeated_preview = release_configured_policy_operation(version=1)
    assert applied.changed == 1
    assert repeated_preview.changed == 0
    assert repeated_preview.skipped == 1
    assert BotPolicyRelease.objects.get(version=1).checksum == applied.checksum


@pytest.mark.django_db
def test_policy_retirement_dry_run_and_reference_guards(django_user_model) -> None:
    released_at = timezone.now() - timedelta(days=31)
    payload = {"name": "retirement-v91"}
    checksum = policy_checksum(payload)
    release_policy(
        version=91,
        checksum=checksum,
        payload=payload,
        released_at=released_at,
    )
    profile = _create_v1_profile(
        django_user_model,
        username="gate_c_policy_retirement",
        growth_seed=91_001,
    )
    BotProfile.objects.filter(pk=profile.pk).update(policy_version=91)

    with pytest.raises(PolicyRetirementBlocked, match="referenced by profiles"):
        retire_policy_release(version=91, expected_checksum=checksum)

    BotProfile.objects.filter(pk=profile.pk).update(policy_version=0)
    routing = BotRuntimeRoutingState.objects.create(
        calibration_routes=[
            {
                "policy_version": 91,
                "reference_snapshot_version": 1,
                "prestige_band": "newbie",
                "policy_checksum": checksum,
                "reference_snapshot_digest": "a" * 64,
                "evidence_schema_version": 1,
                "evidence_digest": "b" * 64,
            }
        ]
    )
    with pytest.raises(PolicyRetirementBlocked, match="referenced by routing"):
        retire_policy_release(version=91, expected_checksum=checksum)

    routing.calibration_routes = []
    routing.save(update_fields=["calibration_routes", "updated_at"])
    preview = retire_policy_release_operation(
        version=91,
        expected_checksum=checksum,
    )
    assert preview.changed == 1
    assert BotPolicyRelease.objects.get(version=91).retired_at is None

    applied = retire_policy_release_operation(
        version=91,
        expected_checksum=checksum,
        apply=True,
    )
    repeated = retire_policy_release_operation(
        version=91,
        expected_checksum=checksum,
        apply=True,
    )
    assert applied.changed == 1
    assert repeated.changed == 0
    assert repeated.skipped == 1
    assert BotPolicyRelease.objects.get(version=91).retired_at is not None


@pytest.mark.django_db
def test_routing_transition_preview_is_write_free_and_apply_uses_revision_cas(monkeypatch) -> None:
    preview = runtime_configs.transition_virtual_player_routing_operation(
        expected_revision=None,
        bootstrap_mode="legacy_before_gate",
        maintenance_mode="legacy_before_gate",
    )
    assert preview.changed == 1
    assert preview.snapshot.revision == 0
    assert preview.snapshot.persisted is False
    assert BotRuntimeRoutingState.objects.count() == 0

    initialized = runtime_configs.transition_virtual_player_routing_operation(
        expected_revision=None,
        bootstrap_mode="legacy_before_gate",
        maintenance_mode="legacy_before_gate",
        apply=True,
    )
    assert initialized.changed == 1
    assert initialized.snapshot.revision == 0
    assert initialized.snapshot.persisted is True

    with pytest.raises(
        runtime_configs.RuntimeRoutingGateBlocked,
        match="Gate D1 evidence is required",
    ):
        runtime_configs.transition_virtual_player_routing_operation(
            expected_revision=0,
            expected_bootstrap_mode="legacy_before_gate",
            expected_maintenance_mode="legacy_before_gate",
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
        )

    monkeypatch.setattr(
        gate_d1_exit_workflow,
        "verify_gate_d1_readiness",
        lambda: GateReadinessProof(
            gate="d1",
            evidence_id="test-gate-d1-evidence",
            evidence_digest="0" * 64,
            recorded_at_utc="2026-08-04T00:00:00Z",
        ),
    )
    activation_preview = gate_d1_exit_workflow.exit_gate_d1_operation(
        expected_revision=0,
    )
    assert activation_preview.changed == 1
    assert activation_preview.snapshot.revision == 1
    assert BotRuntimeRoutingState.objects.get().revision == 0

    monkeypatch.setattr(
        gate_d1_exit_workflow,
        "assert_current_evidence_environment",
        lambda _proof: None,
    )
    activated = gate_d1_exit_workflow.exit_gate_d1_operation(
        expected_revision=0,
        authorization_basis="test-approved-gate-d1-exit",
        apply=True,
    )
    assert activated.snapshot.revision == 1
    assert BotRuntimeRoutingState.objects.get().bootstrap_mode == "v2_active"

    with pytest.raises(runtime_configs.RuntimeRoutingConflict, match="revision changed"):
        gate_d1_exit_workflow.exit_gate_d1_operation(
            expected_revision=0,
            authorization_basis="test-stale-gate-d1-exit",
            apply=True,
        )


@pytest.mark.django_db
def test_routing_reference_removal_monotonically_extends_policy_retirement_deadline() -> None:
    released_at = timezone.now() - timedelta(days=40)
    payload = {"name": "routing-reference-removal"}
    checksum = policy_checksum(payload)
    release = release_policy(
        version=45,
        checksum=checksum,
        payload=payload,
        released_at=released_at,
    ).release
    original_deadline = release.retire_not_before
    route = {
        "policy_version": 45,
        "reference_snapshot_version": 1,
        "prestige_band": "newbie",
        "policy_checksum": checksum,
        "reference_snapshot_digest": "a" * 64,
        "evidence_schema_version": 1,
        "evidence_digest": "b" * 64,
    }
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        calibration_routes=[route],
    )

    runtime_configs.transition_virtual_player_routing_operation(
        expected_revision=0,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_routes=[],
    )
    release.refresh_from_db()
    assert release.retire_not_before == original_deadline

    runtime_configs.transition_virtual_player_routing_operation(
        expected_revision=0,
        expected_bootstrap_mode="v2_active",
        expected_maintenance_mode="legacy_before_gate",
        bootstrap_mode="v2_active",
        maintenance_mode="legacy_before_gate",
        calibration_routes=[],
        apply=True,
    )
    release.refresh_from_db()
    assert release.retire_not_before > original_deadline
    assert release.retire_not_before >= timezone.now() + timedelta(days=29)


@pytest.mark.django_db
def test_routing_preview_rejects_an_unconfigured_policy_before_assignment() -> None:
    released_at = timezone.now() - timedelta(days=31)
    payload = {"name": "retired-routing-target"}
    checksum = policy_checksum(payload)
    release_policy(
        version=46,
        checksum=checksum,
        payload=payload,
        released_at=released_at,
    )
    retire_policy_release(version=46, expected_checksum=checksum)
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
    )
    route = {
        "policy_version": 46,
        "reference_snapshot_version": 1,
        "prestige_band": "newbie",
    }

    with pytest.raises(
        runtime_configs.RuntimeRoutingGateBlocked,
        match="policy 46 is not configured",
    ):
        runtime_configs.transition_virtual_player_routing_operation(
            expected_revision=0,
            expected_bootstrap_mode="v2_active",
            expected_maintenance_mode="legacy_before_gate",
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
            calibration_routes=[route],
        )


@pytest.mark.django_db
def test_policy_rollout_transition_is_persisted_cas_and_extends_removed_references() -> None:
    released_at = timezone.now() - timedelta(days=40)
    old_payload = {"name": "rollout-old"}
    old_checksum = policy_checksum(old_payload)
    old_release = release_policy(
        version=47,
        checksum=old_checksum,
        payload=old_payload,
        released_at=released_at,
    ).release
    new_payload = {"name": "rollout-new"}
    new_checksum = policy_checksum(new_payload)
    new_release = release_policy(
        version=48,
        checksum=new_checksum,
        payload=new_payload,
        released_at=released_at,
    ).release
    old_deadline = old_release.retire_not_before
    new_deadline = new_release.retire_not_before
    routing = BotRuntimeRoutingState.objects.create()

    preview = runtime_configs.transition_virtual_player_policy_rollout_operation(
        expected_revision=0,
        expected_target_version=1,
        expected_enabled=False,
        expected_rollout_percent=0,
        target_version=47,
        enabled=True,
        rollout_percent=25,
    )
    routing.refresh_from_db()
    old_release.refresh_from_db()
    assert preview.changed == 1
    assert preview.snapshot.revision == 1
    assert routing.revision == 0
    assert routing.policy_rollout_enabled is False
    assert old_release.retire_not_before == old_deadline

    enabled = runtime_configs.transition_virtual_player_policy_rollout_operation(
        expected_revision=0,
        expected_target_version=1,
        expected_enabled=False,
        expected_rollout_percent=0,
        target_version=47,
        enabled=True,
        rollout_percent=25,
        apply=True,
    )
    routing.refresh_from_db()
    assert enabled.snapshot.revision == 1
    assert routing.policy_rollout_target_version == 47
    assert routing.policy_rollout_enabled is True
    assert routing.policy_rollout_percent == 25

    with pytest.raises(PolicyRetirementBlocked, match="referenced by rollout"):
        retire_policy_release(
            version=47,
            expected_checksum=old_checksum,
            retired_at=timezone.now() + timedelta(days=31),
        )

    switched = runtime_configs.transition_virtual_player_policy_rollout_operation(
        expected_revision=1,
        expected_target_version=47,
        expected_enabled=True,
        expected_rollout_percent=25,
        target_version=48,
        enabled=True,
        rollout_percent=50,
        apply=True,
    )
    old_release.refresh_from_db()
    assert switched.snapshot.revision == 2
    assert old_release.retire_not_before > old_deadline
    assert old_release.retire_not_before >= timezone.now() + timedelta(days=29)

    disabled = runtime_configs.transition_virtual_player_policy_rollout_operation(
        expected_revision=2,
        expected_target_version=48,
        expected_enabled=True,
        expected_rollout_percent=50,
        target_version=48,
        enabled=False,
        rollout_percent=0,
        apply=True,
    )
    new_release.refresh_from_db()
    assert disabled.snapshot.revision == 3
    assert new_release.retire_not_before > new_deadline

    with pytest.raises(runtime_configs.RuntimeRoutingConflict, match="revision changed"):
        runtime_configs.transition_virtual_player_policy_rollout_operation(
            expected_revision=2,
            expected_target_version=48,
            expected_enabled=True,
            expected_rollout_percent=50,
            target_version=48,
            enabled=False,
            rollout_percent=0,
            apply=True,
        )


@pytest.mark.django_db
def test_policy_rollout_transition_rejects_a_retired_target() -> None:
    released_at = timezone.now() - timedelta(days=31)
    payload = {"name": "retired-rollout-target"}
    checksum = policy_checksum(payload)
    release_policy(
        version=49,
        checksum=checksum,
        payload=payload,
        released_at=released_at,
    )
    retire_policy_release(version=49, expected_checksum=checksum)
    BotRuntimeRoutingState.objects.create()

    with pytest.raises(runtime_configs.RuntimeRoutingGateBlocked, match="is retired"):
        runtime_configs.transition_virtual_player_policy_rollout_operation(
            expected_revision=0,
            expected_target_version=1,
            expected_enabled=False,
            expected_rollout_percent=0,
            target_version=49,
            enabled=True,
            rollout_percent=10,
        )


@pytest.mark.django_db
def test_policy_rollout_batch_uses_persisted_revision_and_stable_bucket(
    django_user_model,
) -> None:
    routing = BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
    )
    source_release = release_configured_policy_operation(version=1, apply=True)
    profiles = [
        _create_v1_profile(
            django_user_model,
            username=f"gate_c_policy_rollout_{index}",
            growth_seed=74_000 + index,
        )
        for index in range(1, 13)
    ]
    enrolled = enroll_virtual_players_batch(batch_size=20, apply=True)
    assert enrolled.changed == len(profiles)

    target_payload = {"name": "gate-c-rollout-v2", "max_development_actions": 1}
    target_checksum = policy_checksum(target_payload)
    release_policy(version=2, checksum=target_checksum, payload=target_payload)

    with pytest.raises(ProfileManagementError, match="policy rollout is disabled"):
        rollout_virtual_player_policy_batch(
            expected_revision=0,
            expected_policy_version=1,
            expected_policy_checksum=source_release.checksum,
            target_policy_checksum=target_checksum,
        )

    runtime_configs.transition_virtual_player_policy_rollout(
        expected_revision=0,
        expected_target_version=1,
        expected_enabled=False,
        expected_rollout_percent=0,
        target_version=2,
        enabled=True,
        rollout_percent=50,
    )
    eligible_ids = {
        profile.pk
        for profile in profiles
        if policy_rollout_bucket(
            profile_id=profile.pk,
            target_policy_version=2,
        )
        < 50
    }
    assert eligible_ids
    assert eligible_ids != {profile.pk for profile in profiles}

    preview = rollout_virtual_player_policy_batch(
        expected_revision=1,
        expected_policy_version=1,
        expected_policy_checksum=source_release.checksum,
        target_policy_checksum=target_checksum,
        batch_size=20,
    )
    assert preview.scanned == len(profiles)
    assert preview.changed == len(eligible_ids)
    assert preview.skipped == len(profiles) - len(eligible_ids)
    assert set(BotProfile.objects.values_list("policy_version", flat=True)) == {1}

    applied = rollout_virtual_player_policy_batch(
        expected_revision=1,
        expected_policy_version=1,
        expected_policy_checksum=source_release.checksum,
        target_policy_checksum=target_checksum,
        batch_size=20,
        apply=True,
    )
    assert applied.changed == len(eligible_ids)
    assert set(BotProfile.objects.filter(policy_version=2).values_list("id", flat=True)) == eligible_ids

    runtime_configs.transition_virtual_player_policy_rollout(
        expected_revision=1,
        expected_target_version=2,
        expected_enabled=True,
        expected_rollout_percent=50,
        target_version=2,
        enabled=True,
        rollout_percent=100,
    )
    with pytest.raises(ProfileManagementError, match="routing revision changed"):
        rollout_virtual_player_policy_batch(
            expected_revision=1,
            expected_policy_version=1,
            expected_policy_checksum=source_release.checksum,
            target_policy_checksum=target_checksum,
            batch_size=20,
            apply=True,
        )
    completed = rollout_virtual_player_policy_batch(
        expected_revision=2,
        expected_policy_version=1,
        expected_policy_checksum=source_release.checksum,
        target_policy_checksum=target_checksum,
        batch_size=20,
        apply=True,
    )
    routing.refresh_from_db()
    assert completed.changed == len(profiles) - len(eligible_ids)
    assert not BotProfile.objects.filter(policy_version=1).exists()
    assert routing.revision == 2


@pytest.mark.django_db
def test_profile_enrollment_repairs_and_policy_upgrade_require_expected_current(
    django_user_model,
) -> None:
    routing = BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
    )
    configured_release = release_configured_policy_operation(version=1, apply=True)
    profile = _create_v1_profile(
        django_user_model,
        username="gate_c_profile_management",
        growth_seed=73_001,
    )

    enrollment_preview = enroll_virtual_players_batch(batch_size=10)
    profile.refresh_from_db()
    assert enrollment_preview.changed == 1
    assert profile.engine_version == 1

    enrolled = enroll_virtual_players_batch(batch_size=10, apply=True)
    profile.refresh_from_db()
    assert enrolled.changed == 1
    assert profile.engine_version == 2
    assert profile.rng_version == 1
    assert profile.plan_schema_version == 1
    assert profile.policy_version == configured_release.version
    assert profile.policy_checksum == configured_release.checksum
    assert profile.development_profile

    original_plan = profile.development_profile
    original_policy = (profile.policy_version, profile.policy_checksum)
    original_sequence = profile.maintenance_sequence
    routing.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    routing.save(update_fields=["maintenance_mode", "updated_at"])
    BotProfile.objects.filter(pk=profile.pk).update(rng_version=99)
    with pytest.raises(ProfileManagementError, match="development writes to be stopped"):
        repair_virtual_player_rng(
            profile_id=profile.pk,
            expected_rng_version=99,
            target_rng_version=1,
            recovery_basis="incident-gate-c-rng",
        )
    routing.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER
    routing.save(update_fields=["maintenance_mode", "updated_at"])
    with pytest.raises(ProfileManagementError, match="expected V2 RNG assignment"):
        repair_virtual_player_rng(
            profile_id=profile.pk,
            expected_rng_version=98,
            target_rng_version=1,
            recovery_basis="incident-gate-c-rng",
            apply=True,
        )
    rng_preview = repair_virtual_player_rng(
        profile_id=profile.pk,
        expected_rng_version=99,
        target_rng_version=1,
        recovery_basis="incident-gate-c-rng",
    )
    profile.refresh_from_db()
    assert rng_preview.changed == 1
    assert profile.rng_version == 99
    repair_virtual_player_rng(
        profile_id=profile.pk,
        expected_rng_version=99,
        target_rng_version=1,
        recovery_basis="incident-gate-c-rng",
        apply=True,
    )
    profile.refresh_from_db()
    assert profile.rng_version == 1
    assert profile.development_profile == original_plan
    assert (profile.policy_version, profile.policy_checksum) == original_policy
    assert profile.maintenance_sequence == original_sequence

    BotProfile.objects.filter(pk=profile.pk).update(plan_schema_version=2, development_profile={})
    with pytest.raises(ProfileManagementError, match="plan schema changed"):
        repair_virtual_player_plan(
            profile_id=profile.pk,
            expected_plan_schema_version=1,
            recovery_basis="incident-gate-c-plan",
        )
    BotProfile.objects.filter(pk=profile.pk).update(plan_schema_version=1)
    plan_preview = repair_virtual_player_plan(
        profile_id=profile.pk,
        expected_plan_schema_version=1,
        recovery_basis="incident-gate-c-plan",
    )
    profile.refresh_from_db()
    assert plan_preview.changed == 1
    assert profile.development_profile == {}
    repair_virtual_player_plan(
        profile_id=profile.pk,
        expected_plan_schema_version=1,
        recovery_basis="incident-gate-c-plan",
        apply=True,
    )
    profile.refresh_from_db()
    assert profile.development_profile == original_plan
    assert profile.rng_version == 1
    assert (profile.policy_version, profile.policy_checksum) == original_policy
    assert profile.maintenance_sequence == original_sequence
    routing.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE
    routing.save(update_fields=["maintenance_mode", "updated_at"])
    valid_plan_preview = repair_virtual_player_plan(
        profile_id=profile.pk,
        expected_plan_schema_version=1,
        recovery_basis="incident-gate-c-plan",
    )
    assert valid_plan_preview.changed == 0
    assert valid_plan_preview.skipped == 1

    target_payload = {"name": "gate-c-upgrade-v2", "max_development_actions": 1}
    target_checksum = policy_checksum(target_payload)
    release_policy(version=2, checksum=target_checksum, payload=target_payload)
    with pytest.raises(PolicyAssignmentError, match="checksum does not match"):
        upgrade_virtual_player_policy_batch(
            expected_policy_version=1,
            expected_policy_checksum="0" * 64,
            target_policy_version=2,
            target_policy_checksum=target_checksum,
            apply=True,
        )

    policy_preview = upgrade_virtual_player_policy_batch(
        expected_policy_version=1,
        expected_policy_checksum=configured_release.checksum.upper(),
        target_policy_version=2,
        target_policy_checksum=target_checksum.upper(),
    )
    profile.refresh_from_db()
    assert policy_preview.changed == 1
    assert profile.policy_version == 1
    plan_before_upgrade = profile.development_profile
    upgrade_virtual_player_policy_batch(
        expected_policy_version=1,
        expected_policy_checksum=configured_release.checksum,
        target_policy_version=2,
        target_policy_checksum=target_checksum,
        apply=True,
    )
    profile.refresh_from_db()
    assert (profile.policy_version, profile.policy_checksum) == (2, target_checksum)
    assert profile.engine_version == 2
    assert profile.rng_version == 1
    assert profile.development_profile == plan_before_upgrade
    assert profile.maintenance_sequence == original_sequence
