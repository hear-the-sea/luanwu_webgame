from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.db import transaction

from gameplay.services import runtime_configs

from .config import BootstrapMode, MaintenanceMode
from .gate_evidence import GateReadinessProof, assert_current_evidence_environment, verify_gate_d1_readiness


class GateD1ExitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GateD1ExitSummary:
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


def _authorization_basis_digest(value: object, *, apply: bool) -> str:
    normalized = str(value or "").strip()
    if apply and not normalized:
        raise GateD1ExitError("authorization_basis is required when apply=true")
    if len(normalized) > 512:
        raise GateD1ExitError("authorization_basis must not exceed 512 characters")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _assert_expected_routing(
    snapshot: runtime_configs.RuntimeRoutingSnapshot,
    *,
    expected_revision: int,
) -> None:
    if snapshot.revision != expected_revision:
        raise runtime_configs.RuntimeRoutingConflict(
            f"routing revision changed: expected {expected_revision}, found {snapshot.revision}"
        )
    if snapshot.bootstrap_mode is not BootstrapMode.LEGACY_BEFORE_GATE:
        raise GateD1ExitError("Gate D1 exit requires bootstrap_mode=legacy_before_gate")
    if snapshot.maintenance_mode is not MaintenanceMode.LEGACY_BEFORE_GATE:
        raise GateD1ExitError("Gate D1 exit requires maintenance_mode=legacy_before_gate")


@transaction.atomic
def _exit_gate_d1_locked(
    *,
    expected_revision: int,
    proof: GateReadinessProof,
    authorization_basis_digest: str,
    apply: bool,
) -> GateD1ExitSummary:
    current = runtime_configs.lock_virtual_player_routing()
    _assert_expected_routing(current, expected_revision=expected_revision)
    result = runtime_configs._transition_virtual_player_routing(
        expected_revision=expected_revision,
        expected_bootstrap_mode=current.bootstrap_mode,
        expected_maintenance_mode=current.maintenance_mode,
        bootstrap_mode=BootstrapMode.V2_ACTIVE,
        maintenance_mode=current.maintenance_mode,
        calibration_routes=current.calibration_routes,
        gate_d1_ready=True,
        pause_reason=current.pause_reason,
        apply=apply,
    )
    return GateD1ExitSummary(
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
    )


def exit_gate_d1_operation(
    *,
    expected_revision: int,
    authorization_basis: str = "",
    apply: bool = False,
) -> GateD1ExitSummary:
    proof = verify_gate_d1_readiness()
    authorization_digest = _authorization_basis_digest(
        authorization_basis,
        apply=apply,
    )
    if apply:
        assert_current_evidence_environment(proof)
    return _exit_gate_d1_locked(
        expected_revision=expected_revision,
        proof=proof,
        authorization_basis_digest=authorization_digest,
        apply=apply,
    )


__all__ = ["GateD1ExitError", "GateD1ExitSummary", "exit_gate_d1_operation"]
