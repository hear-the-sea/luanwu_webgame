from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.db import transaction

from gameplay.services import runtime_configs

from .config import BootstrapMode, MaintenanceMode
from .gate_evidence import GateReadinessProof, assert_current_evidence_environment, verify_gate_e_readiness
from .profile_store import runtime_eligible_v1_profile_count


class GateECutoverError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GateECutoverSummary:
    scanned: int
    locked: int
    changed: int
    skipped: int
    failed: int
    reasons: tuple[str, ...]
    snapshot: runtime_configs.RuntimeRoutingSnapshot
    evidence_id: str
    evidence_digest: str
    authorization_basis_digest: str
    runtime_eligible_v1_profiles: int


def _authorization_basis_digest(value: object, *, apply: bool) -> str:
    normalized = str(value or "").strip()
    if apply and not normalized:
        raise GateECutoverError("authorization_basis is required when apply=true")
    if len(normalized) > 512:
        raise GateECutoverError("authorization_basis must not exceed 512 characters")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _assert_expected_routing(
    snapshot: runtime_configs.RuntimeRoutingSnapshot,
    *,
    expected_revision: int,
    expected_maintenance_mode: MaintenanceMode,
) -> None:
    if snapshot.revision != expected_revision:
        raise runtime_configs.RuntimeRoutingConflict(
            f"routing revision changed: expected {expected_revision}, found {snapshot.revision}"
        )
    if snapshot.bootstrap_mode is not BootstrapMode.V2_ACTIVE:
        raise GateECutoverError("Gate E workflow requires bootstrap_mode=v2_active")
    if snapshot.maintenance_mode is not expected_maintenance_mode:
        raise GateECutoverError("Gate E workflow expected maintenance_mode=" f"{expected_maintenance_mode.value}")


def _assert_paused_from_cutover(snapshot: runtime_configs.RuntimeRoutingSnapshot) -> None:
    if snapshot.paused_from_maintenance_mode != MaintenanceMode.V2_CUTOVER.value:
        raise GateECutoverError("Gate E resume requires a safety pause originating from V2_CUTOVER")


def _summary(
    *,
    result: runtime_configs.RuntimeRoutingTransitionResult,
    proof: GateReadinessProof,
    authorization_basis_digest: str,
    eligible_v1_profiles: int,
) -> GateECutoverSummary:
    return GateECutoverSummary(
        scanned=1,
        locked=1,
        changed=int(result.changed),
        skipped=int(not result.changed),
        failed=0,
        reasons=() if result.changed else ("routing_unchanged",),
        snapshot=result.snapshot,
        evidence_id=proof.evidence_id,
        evidence_digest=proof.evidence_digest,
        authorization_basis_digest=authorization_basis_digest,
        runtime_eligible_v1_profiles=eligible_v1_profiles,
    )


@transaction.atomic
def _enter_gate_e_cutover_locked(
    *,
    expected_revision: int,
    proof: GateReadinessProof,
    authorization_basis_digest: str,
    apply: bool,
) -> GateECutoverSummary:
    current = runtime_configs.lock_virtual_player_routing()
    _assert_expected_routing(
        current,
        expected_revision=expected_revision,
        expected_maintenance_mode=MaintenanceMode.LEGACY_BEFORE_GATE,
    )
    eligible_v1_profiles = runtime_eligible_v1_profile_count()
    result = runtime_configs._transition_virtual_player_routing(
        expected_revision=expected_revision,
        expected_bootstrap_mode=current.bootstrap_mode,
        expected_maintenance_mode=current.maintenance_mode,
        bootstrap_mode=current.bootstrap_mode,
        maintenance_mode=MaintenanceMode.V2_CUTOVER,
        calibration_routes=current.calibration_routes,
        gate_e_ready=True,
        pause_reason=current.pause_reason,
        apply=apply,
    )
    return _summary(
        result=result,
        proof=proof,
        authorization_basis_digest=authorization_basis_digest,
        eligible_v1_profiles=eligible_v1_profiles,
    )


@transaction.atomic
def _exit_gate_e_locked(
    *,
    expected_revision: int,
    proof: GateReadinessProof,
    authorization_basis_digest: str,
    apply: bool,
) -> GateECutoverSummary:
    current = runtime_configs.lock_virtual_player_routing()
    _assert_expected_routing(
        current,
        expected_revision=expected_revision,
        expected_maintenance_mode=MaintenanceMode.V2_CUTOVER,
    )
    eligible_v1_profiles = runtime_eligible_v1_profile_count()
    result = runtime_configs._transition_virtual_player_routing(
        expected_revision=expected_revision,
        expected_bootstrap_mode=current.bootstrap_mode,
        expected_maintenance_mode=current.maintenance_mode,
        bootstrap_mode=current.bootstrap_mode,
        maintenance_mode=MaintenanceMode.V2_ACTIVE,
        calibration_routes=current.calibration_routes,
        gate_e_ready=True,
        pause_reason=current.pause_reason,
        apply=apply,
    )
    return _summary(
        result=result,
        proof=proof,
        authorization_basis_digest=authorization_basis_digest,
        eligible_v1_profiles=eligible_v1_profiles,
    )


@transaction.atomic
def _resume_gate_e_cutover_locked(
    *,
    expected_revision: int,
    proof: GateReadinessProof,
    authorization_basis_digest: str,
    apply: bool,
) -> GateECutoverSummary:
    current = runtime_configs.lock_virtual_player_routing()
    _assert_expected_routing(
        current,
        expected_revision=expected_revision,
        expected_maintenance_mode=MaintenanceMode.V2_PAUSED,
    )
    _assert_paused_from_cutover(current)
    eligible_v1_profiles = runtime_eligible_v1_profile_count()
    result = runtime_configs._transition_virtual_player_routing(
        expected_revision=expected_revision,
        expected_bootstrap_mode=current.bootstrap_mode,
        expected_maintenance_mode=current.maintenance_mode,
        bootstrap_mode=current.bootstrap_mode,
        maintenance_mode=MaintenanceMode.V2_CUTOVER,
        calibration_routes=current.calibration_routes,
        gate_e_ready=True,
        pause_reason="",
        apply=apply,
    )
    return _summary(
        result=result,
        proof=proof,
        authorization_basis_digest=authorization_basis_digest,
        eligible_v1_profiles=eligible_v1_profiles,
    )


def enter_gate_e_cutover_operation(
    *,
    expected_revision: int,
    authorization_basis: str = "",
    apply: bool = False,
    expected_git_commit: str | None = None,
) -> GateECutoverSummary:
    proof = verify_gate_e_readiness(expected_git_commit=expected_git_commit)
    authorization_digest = _authorization_basis_digest(
        authorization_basis,
        apply=apply,
    )
    if apply:
        assert_current_evidence_environment(proof)
    return _enter_gate_e_cutover_locked(
        expected_revision=expected_revision,
        proof=proof,
        authorization_basis_digest=authorization_digest,
        apply=apply,
    )


def exit_gate_e_operation(
    *,
    expected_revision: int,
    authorization_basis: str = "",
    apply: bool = False,
    expected_git_commit: str | None = None,
) -> GateECutoverSummary:
    proof = verify_gate_e_readiness(expected_git_commit=expected_git_commit)
    authorization_digest = _authorization_basis_digest(
        authorization_basis,
        apply=apply,
    )
    if apply:
        assert_current_evidence_environment(proof)
    return _exit_gate_e_locked(
        expected_revision=expected_revision,
        proof=proof,
        authorization_basis_digest=authorization_digest,
        apply=apply,
    )


def resume_gate_e_cutover_operation(
    *,
    expected_revision: int,
    authorization_basis: str = "",
    apply: bool = False,
    expected_git_commit: str | None = None,
) -> GateECutoverSummary:
    proof = verify_gate_e_readiness(expected_git_commit=expected_git_commit)
    authorization_digest = _authorization_basis_digest(
        authorization_basis,
        apply=apply,
    )
    if apply:
        assert_current_evidence_environment(proof)
    return _resume_gate_e_cutover_locked(
        expected_revision=expected_revision,
        proof=proof,
        authorization_basis_digest=authorization_digest,
        apply=apply,
    )


__all__ = [
    "GateECutoverError",
    "GateECutoverSummary",
    "enter_gate_e_cutover_operation",
    "exit_gate_e_operation",
    "resume_gate_e_cutover_operation",
]
