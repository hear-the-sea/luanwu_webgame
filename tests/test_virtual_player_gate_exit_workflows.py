from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import BotProfile, BotRuntimeRoutingState
from gameplay.services import runtime_configs
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core import gate_d1_exit_workflow, gate_e_cutover_workflow
from gameplay.services.virtual_player_core.gate_evidence import GateReadinessProof

pytestmark = pytest.mark.skip(reason="manual pre-cutover Gate D1/Gate E workflows retired after the policy 2 cutover")

D1_PROOF = GateReadinessProof(
    gate="d1",
    evidence_id="gate-d1-test-evidence",
    evidence_digest="a" * 64,
    recorded_at_utc="2026-07-28T20:08:08Z",
)
E_PROOF = GateReadinessProof(
    gate="e",
    evidence_id="gate-e-test-evidence",
    evidence_digest="b" * 64,
    recorded_at_utc="2026-07-28T20:08:08Z",
)


@pytest.fixture
def verified_gate_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        gate_d1_exit_workflow,
        "verify_gate_d1_readiness",
        lambda: D1_PROOF,
    )
    monkeypatch.setattr(
        gate_e_cutover_workflow,
        "verify_gate_e_readiness",
        lambda **_kwargs: E_PROOF,
    )
    monkeypatch.setattr(
        gate_d1_exit_workflow,
        "assert_current_evidence_environment",
        lambda _proof: None,
    )
    monkeypatch.setattr(
        gate_e_cutover_workflow,
        "assert_current_evidence_environment",
        lambda _proof: None,
    )


def _create_v1_profile(django_user_model, *, username: str) -> BotProfile:
    now = timezone.now()
    manor = ensure_manor(django_user_model.objects.create_user(username=username, password="pass123"))
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        growth_seed=990_001,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )


@pytest.mark.django_db
def test_generic_routing_transition_cannot_self_approve_gate_activation() -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        revision=0,
    )

    with pytest.raises(
        runtime_configs.RuntimeRoutingGateBlocked,
        match="Gate D1 evidence is required",
    ):
        runtime_configs.transition_virtual_player_routing(
            expected_revision=0,
            expected_bootstrap_mode="legacy_before_gate",
            expected_maintenance_mode="legacy_before_gate",
            bootstrap_mode="v2_active",
            maintenance_mode="legacy_before_gate",
        )


@pytest.mark.django_db
def test_gate_d1_workflow_verifies_preview_and_requires_apply_authorization(
    verified_gate_evidence,
) -> None:
    routing = BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        revision=0,
    )

    preview = gate_d1_exit_workflow.exit_gate_d1_operation(expected_revision=0)
    routing.refresh_from_db()
    assert preview.snapshot.bootstrap_mode is runtime_configs.BootstrapMode.V2_ACTIVE
    assert preview.snapshot.revision == 1
    assert preview.evidence_digest == D1_PROOF.evidence_digest
    assert routing.bootstrap_mode == BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE
    assert routing.revision == 0

    with pytest.raises(
        gate_d1_exit_workflow.GateD1ExitError,
        match="authorization_basis is required",
    ):
        gate_d1_exit_workflow.exit_gate_d1_operation(
            expected_revision=0,
            apply=True,
        )

    basis = "approved test Gate D1 transition"
    applied = gate_d1_exit_workflow.exit_gate_d1_operation(
        expected_revision=0,
        authorization_basis=basis,
        apply=True,
    )
    routing.refresh_from_db()
    assert routing.bootstrap_mode == BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE
    assert routing.revision == 1
    assert applied.authorization_basis_digest == hashlib.sha256(basis.encode("utf-8")).hexdigest()
    assert basis not in applied.authorization_basis_digest


@pytest.mark.django_db
def test_gate_e_cutover_allows_v1_profiles_and_preserves_calibration_routes(
    django_user_model,
    verified_gate_evidence,
) -> None:
    route = {
        "policy_version": 1,
        "reference_snapshot_version": 2,
        "prestige_band": "newbie",
        "policy_checksum": "a" * 64,
        "reference_snapshot_digest": "b" * 64,
        "evidence_schema_version": 1,
        "evidence_digest": "c" * 64,
    }
    routing = BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        calibration_routes=[route],
        revision=4,
    )
    _create_v1_profile(django_user_model, username="gate_e_cutover_v1")

    preview = gate_e_cutover_workflow.enter_gate_e_cutover_operation(
        expected_revision=4,
    )
    routing.refresh_from_db()
    assert preview.runtime_eligible_v1_profiles == 1
    assert preview.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_CUTOVER
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE

    applied = gate_e_cutover_workflow.enter_gate_e_cutover_operation(
        expected_revision=4,
        authorization_basis="approved test Gate E cutover",
        apply=True,
    )
    routing.refresh_from_db()
    assert applied.runtime_eligible_v1_profiles == 1
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER
    assert routing.calibration_routes == [route]
    assert routing.revision == 5


@pytest.mark.django_db
def test_gate_e_exit_requires_transactional_zero_v1_count(
    django_user_model,
    verified_gate_evidence,
) -> None:
    routing = BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
        revision=7,
    )
    profile = _create_v1_profile(django_user_model, username="gate_e_exit_v1")

    with pytest.raises(
        runtime_configs.RuntimeRoutingGateBlocked,
        match="zero eligible V1 profiles",
    ):
        gate_e_cutover_workflow.exit_gate_e_operation(expected_revision=7)
    routing.refresh_from_db()
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER
    assert routing.revision == 7

    BotProfile.objects.filter(pk=profile.pk).update(state=BotProfile.State.STALE)
    applied = gate_e_cutover_workflow.exit_gate_e_operation(
        expected_revision=7,
        authorization_basis="approved test Gate E exit",
        apply=True,
    )
    routing.refresh_from_db()
    assert applied.runtime_eligible_v1_profiles == 0
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    assert routing.revision == 8


@pytest.mark.django_db
def test_gate_e_cutover_resume_requires_cutover_origin_and_uses_revision_cas(
    verified_gate_evidence,
) -> None:
    routing = BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
        revision=9,
    )

    preview = gate_e_cutover_workflow.resume_gate_e_cutover_operation(
        expected_revision=9,
    )
    routing.refresh_from_db()
    assert preview.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_CUTOVER
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED
    assert routing.revision == 9

    applied = gate_e_cutover_workflow.resume_gate_e_cutover_operation(
        expected_revision=9,
        authorization_basis="approved test Gate E resume",
        apply=True,
    )
    routing.refresh_from_db()
    assert applied.snapshot.maintenance_mode is runtime_configs.MaintenanceMode.V2_CUTOVER
    assert routing.maintenance_mode == BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER
    assert routing.paused_from_maintenance_mode == ""
    assert routing.revision == 10


@pytest.mark.django_db
def test_gate_e_cutover_resume_rejects_pause_originating_from_v2_active(
    verified_gate_evidence,
) -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
        paused_from_maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        revision=11,
    )

    with pytest.raises(
        gate_e_cutover_workflow.GateECutoverError,
        match="safety pause originating from V2_CUTOVER",
    ):
        gate_e_cutover_workflow.resume_gate_e_cutover_operation(
            expected_revision=11,
        )
