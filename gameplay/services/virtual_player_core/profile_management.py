from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction

from gameplay.services.runtime_configs import lock_virtual_player_policy_rollout, lock_virtual_player_routing

from .config import MaintenanceMode, VirtualPlayerV2Config, load_virtual_player_v2_config
from .policy_registry import get_assignable_policy_release, get_policy_release
from .profile_store import (
    ProfilePlanIdentity,
    ProfileStoreError,
    get_profile_plan_identity,
    list_prestige_band_reclassification_candidates,
    list_v2_policy_candidates,
    repair_profile_plan,
    repair_profile_rng,
    sync_current_prestige_band_from_manor,
    upgrade_profile_policy,
)
from .random_context import SUPPORTED_RNG_VERSIONS, RandomContext, policy_rollout_bucket
from .strategy import BotDevelopmentPlan, development_plan_catalog_v1, generate_development_plan

logger = logging.getLogger(__name__)


class ProfileManagementError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BatchOperationSummary:
    scanned: int
    changed: int
    skipped: int
    failed: int
    last_profile_id: int | None
    reasons: tuple[str, ...]
    locked: int = 0


def _required_config(
    config: VirtualPlayerV2Config | None = None,
) -> VirtualPlayerV2Config:
    resolved = config or load_virtual_player_v2_config()
    if resolved is None:
        raise ProfileManagementError("bot_development_v2 is not configured")
    return resolved


def _required_recovery_basis(recovery_basis: object) -> str:
    normalized = str(recovery_basis).strip()
    if not normalized:
        raise ProfileManagementError("recovery_basis must not be blank")
    return normalized


def _require_v2_development_stopped() -> None:
    routing = lock_virtual_player_routing()
    if routing.maintenance_mode is MaintenanceMode.V2_ACTIVE:
        raise ProfileManagementError("V2 profile repair requires Maintenance development writes to be stopped")


def _require_v2_enrollment_cutover() -> None:
    routing = lock_virtual_player_routing()
    if routing.maintenance_mode is not MaintenanceMode.V2_CUTOVER:
        raise ProfileManagementError("V2 profile enrollment requires maintenance_mode=v2_cutover")


def _plan_for_identity(
    identity: ProfilePlanIdentity,
    *,
    rng_version: int,
    plan_schema_version: int,
    policy_version: int,
) -> BotDevelopmentPlan:
    if plan_schema_version != 1:
        raise ProfileManagementError(f"unsupported plan schema {plan_schema_version}")
    context = RandomContext(
        rng_version=rng_version,
        growth_seed=identity.growth_seed,
        engine_version=2,
        plan_schema_version=plan_schema_version,
        policy_version=policy_version,
        maintenance_sequence=0,
    )
    return generate_development_plan(
        context=context,
        archetype=identity.archetype,
        catalog=development_plan_catalog_v1(),
    )


@transaction.atomic
def enroll_virtual_players_batch(
    *,
    after_id: int = 0,
    batch_size: int = 100,
    apply: bool = False,
    enrolled_at: datetime | None = None,
    config: VirtualPlayerV2Config | None = None,
) -> BatchOperationSummary:
    raise ProfileManagementError(
        "legacy V1 enrollment is retired; materialize new profiles through the active policy-2 population path"
    )


def reclassify_virtual_player_prestige_bands_batch(
    *,
    after_id: int = 0,
    batch_size: int = 100,
    apply: bool = False,
    config: VirtualPlayerV2Config | None = None,
) -> BatchOperationSummary:
    """Reclassify only current band metadata from persisted Manor prestige."""
    resolved = _required_config(config)
    candidates = list_prestige_band_reclassification_candidates(
        after_id=after_id,
        limit=batch_size,
    )
    changed = 0
    skipped = 0
    locked = 0
    failures: list[str] = []
    for candidate in candidates:
        try:
            if not apply:
                expected_band = resolved.band_for_prestige(candidate.manor_prestige).name
                if candidate.current_prestige_band == expected_band:
                    skipped += 1
                else:
                    changed += 1
                continue
            result = sync_current_prestige_band_from_manor(
                candidate.profile_id,
                config=resolved,
                skip_locked=True,
            )
            if result.changed:
                changed += 1
            elif result.reason == "missing_or_locked":
                locked += 1
            else:
                skipped += 1
        except (ValueError, ProfileStoreError) as exc:
            failures.append(f"profile={candidate.profile_id}:{type(exc).__name__}:{exc}")
    return BatchOperationSummary(
        scanned=len(candidates),
        changed=changed,
        skipped=skipped,
        failed=len(failures),
        last_profile_id=candidates[-1].profile_id if candidates else None,
        reasons=tuple(failures),
        locked=locked,
    )


@transaction.atomic
def repair_virtual_player_plan(
    *,
    profile_id: int,
    expected_plan_schema_version: int,
    recovery_basis: str,
    apply: bool = False,
) -> BatchOperationSummary:
    normalized_recovery_basis = _required_recovery_basis(recovery_basis)
    _require_v2_development_stopped()
    identity = get_profile_plan_identity(profile_id)
    if identity is None:
        return BatchOperationSummary(1, 0, 1, 0, int(profile_id), ("missing",))
    if identity.engine_version != 2:
        raise ProfileManagementError(f"profile {profile_id} is not V2")
    if identity.plan_schema_version != int(expected_plan_schema_version):
        raise ProfileManagementError(
            f"profile {profile_id} plan schema changed: expected {expected_plan_schema_version}, "
            f"found {identity.plan_schema_version}"
        )
    get_policy_release(version=identity.policy_version, expected_checksum=identity.policy_checksum)
    plan = _plan_for_identity(
        identity,
        rng_version=identity.rng_version,
        plan_schema_version=expected_plan_schema_version,
        policy_version=identity.policy_version,
    )
    result = repair_profile_plan(
        identity.profile_id,
        expected_plan_schema_version=expected_plan_schema_version,
        expected_identity=identity,
        development_profile=plan,
        apply=apply,
    )
    if apply and result.changed:

        def _log_committed_plan_repair() -> None:
            logger.info(
                "Repaired virtual-player development plan",
                extra={
                    "event": "virtual_player_plan_repaired",
                    "profile_id": identity.profile_id,
                    "expected_plan_schema_version": int(expected_plan_schema_version),
                    "recovery_basis": normalized_recovery_basis,
                },
            )

        transaction.on_commit(_log_committed_plan_repair)
    return BatchOperationSummary(
        1,
        int(result.changed),
        int(not result.changed),
        0,
        identity.profile_id,
        (result.reason,),
    )


@transaction.atomic
def repair_virtual_player_rng(
    *,
    profile_id: int,
    expected_rng_version: int,
    target_rng_version: int,
    recovery_basis: str,
    apply: bool = False,
) -> BatchOperationSummary:
    normalized_recovery_basis = _required_recovery_basis(recovery_basis)
    _require_v2_development_stopped()
    if int(target_rng_version) not in SUPPORTED_RNG_VERSIONS:
        raise ProfileManagementError(f"rng_version {target_rng_version} is not supported")
    identity = get_profile_plan_identity(profile_id)
    if identity is None:
        return BatchOperationSummary(1, 0, 1, 0, int(profile_id), ("missing",))
    if identity.engine_version != 2 or identity.rng_version != int(expected_rng_version):
        raise ProfileManagementError(f"profile {profile_id} does not match expected V2 RNG assignment")
    if identity.rng_version == int(target_rng_version):
        return BatchOperationSummary(1, 0, 1, 0, identity.profile_id, ("already_repaired",))
    if not apply:
        return BatchOperationSummary(1, 1, 0, 0, identity.profile_id, ())
    result = repair_profile_rng(
        identity.profile_id,
        expected_rng_version=expected_rng_version,
        target_rng_version=target_rng_version,
    )
    if result.changed:

        def _log_committed_rng_repair() -> None:
            logger.info(
                "Repaired virtual-player RNG assignment",
                extra={
                    "event": "virtual_player_rng_repaired",
                    "profile_id": identity.profile_id,
                    "expected_rng_version": int(expected_rng_version),
                    "target_rng_version": int(target_rng_version),
                    "recovery_basis": normalized_recovery_basis,
                },
            )

        transaction.on_commit(_log_committed_rng_repair)
    return BatchOperationSummary(
        1,
        int(result.changed),
        int(not result.changed),
        0,
        identity.profile_id,
        (result.reason,),
    )


def upgrade_virtual_player_policy_batch(
    *,
    expected_policy_version: int,
    expected_policy_checksum: str,
    target_policy_version: int,
    target_policy_checksum: str,
    after_id: int = 0,
    batch_size: int = 100,
    apply: bool = False,
) -> BatchOperationSummary:
    raise ProfileManagementError("multi-policy upgrade is retired; policy 2 is the only virtual-player release")
    normalized_expected_checksum = str(expected_policy_checksum).strip().lower()
    normalized_target_checksum = str(target_policy_checksum).strip().lower()
    get_policy_release(version=expected_policy_version, expected_checksum=normalized_expected_checksum)
    get_assignable_policy_release(
        version=target_policy_version,
        expected_checksum=normalized_target_checksum,
    )
    if (
        int(expected_policy_version) == int(target_policy_version)
        and normalized_expected_checksum == normalized_target_checksum
    ):
        raise ProfileManagementError("target policy assignment must differ from expected policy assignment")
    identities = list_v2_policy_candidates(
        policy_version=expected_policy_version,
        after_id=after_id,
        limit=batch_size,
    )
    changed = 0
    skipped = 0
    failures: list[str] = []
    for identity in identities:
        if identity.policy_checksum != normalized_expected_checksum:
            failures.append(f"profile={identity.profile_id}:policy_checksum_mismatch")
            continue
        if not apply:
            changed += 1
            continue
        try:
            result = upgrade_profile_policy(
                identity.profile_id,
                expected_policy_version=expected_policy_version,
                expected_policy_checksum=normalized_expected_checksum,
                target_policy_version=target_policy_version,
                target_policy_checksum=normalized_target_checksum,
            )
            if result.changed:
                changed += 1
            else:
                skipped += 1
        except (ValueError, ProfileStoreError) as exc:
            failures.append(f"profile={identity.profile_id}:{type(exc).__name__}:{exc}")
    return BatchOperationSummary(
        scanned=len(identities),
        changed=changed,
        skipped=skipped,
        failed=len(failures),
        last_profile_id=identities[-1].profile_id if identities else None,
        reasons=tuple(failures),
    )


@transaction.atomic
def rollout_virtual_player_policy_batch(
    *,
    expected_revision: int,
    expected_policy_version: int,
    expected_policy_checksum: str,
    target_policy_checksum: str,
    after_id: int = 0,
    batch_size: int = 100,
    apply: bool = False,
) -> BatchOperationSummary:
    raise ProfileManagementError("policy rollout is retired; policy 2 is the only virtual-player release")
    normalized_revision = int(expected_revision)
    if isinstance(expected_revision, bool) or normalized_revision < 0:
        raise ProfileManagementError("expected_revision must be non-negative")
    rollout = lock_virtual_player_policy_rollout()
    if rollout.revision != normalized_revision:
        raise ProfileManagementError(
            f"routing revision changed: expected {normalized_revision}, found {rollout.revision}"
        )
    if not rollout.enabled:
        raise ProfileManagementError("policy rollout is disabled")

    normalized_expected_checksum = str(expected_policy_checksum).strip().lower()
    normalized_target_checksum = str(target_policy_checksum).strip().lower()
    get_policy_release(
        version=expected_policy_version,
        expected_checksum=normalized_expected_checksum,
    )
    target = get_assignable_policy_release(
        version=rollout.target_version,
        expected_checksum=normalized_target_checksum,
    )
    if int(expected_policy_version) == target.version:
        raise ProfileManagementError("rollout target policy must differ from expected policy")

    identities = list_v2_policy_candidates(
        policy_version=expected_policy_version,
        after_id=after_id,
        limit=batch_size,
    )
    changed = 0
    skipped = 0
    failures: list[str] = []
    for identity in identities:
        if identity.policy_checksum != normalized_expected_checksum:
            failures.append(f"profile={identity.profile_id}:policy_checksum_mismatch")
            continue
        bucket = policy_rollout_bucket(
            profile_id=identity.profile_id,
            target_policy_version=target.version,
        )
        if bucket >= rollout.rollout_percent:
            skipped += 1
            continue
        if not apply:
            changed += 1
            continue
        try:
            result = upgrade_profile_policy(
                identity.profile_id,
                expected_policy_version=expected_policy_version,
                expected_policy_checksum=normalized_expected_checksum,
                target_policy_version=target.version,
                target_policy_checksum=target.checksum,
            )
            if result.changed:
                changed += 1
            else:
                skipped += 1
        except (ValueError, ProfileStoreError) as exc:
            failures.append(f"profile={identity.profile_id}:{type(exc).__name__}:{exc}")
    return BatchOperationSummary(
        scanned=len(identities),
        changed=changed,
        skipped=skipped,
        failed=len(failures),
        last_profile_id=identities[-1].profile_id if identities else None,
        reasons=tuple(failures),
    )


__all__ = [
    "BatchOperationSummary",
    "ProfileManagementError",
    "enroll_virtual_players_batch",
    "reclassify_virtual_player_prestige_bands_batch",
    "repair_virtual_player_plan",
    "repair_virtual_player_rng",
    "rollout_virtual_player_policy_batch",
    "upgrade_virtual_player_policy_batch",
]
