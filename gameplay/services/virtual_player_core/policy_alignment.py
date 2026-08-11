"""Guarded reconciliation of the configured V2 policy runtime snapshot.

The normal policy registry treats a released policy as immutable.  This
operation is the deliberately narrow exception for the single-policy V2
runtime: when executable defaults or merged config inputs change, the
operator must explicitly fence writes, compare the old checksum, and update
the release and every V2 profile in one transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from django.db import transaction

from gameplay.models import ArenaVirtualReserveMember, BotPolicyRelease, BotProfile
from gameplay.services.runtime_configs import lock_virtual_player_routing
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES

from .config import (
    BootstrapMode,
    MaintenanceMode,
    canonical_policy_payload,
    load_virtual_player_v2_config,
    policy_checksum,
)
from .database_clock import database_utc_now as _database_utc_now
from .growth_control import refresh_growth_control_snapshots
from .policy_registry import POLICY_RETIREMENT_GUARD

logger = logging.getLogger(__name__)


class PolicyAlignmentError(ValueError):
    """Raised when the guarded alignment preconditions are not satisfied."""


@dataclass(frozen=True, slots=True)
class PolicyAlignmentOperationSummary:
    scanned: int
    locked: int
    changed: int
    skipped: int
    failed: int
    reasons: tuple[str, ...]
    version: int
    previous_checksum: str
    target_checksum: str
    profile_count: int
    historical_v2_profile_count: int
    updated_profile_count: int
    control_run_digest: str
    routing_revision: int
    alignment_id: str


def _normalize_checksum(value: object, *, field: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64:
        raise PolicyAlignmentError(f"{field} must be a SHA-256 checksum")
    try:
        bytes.fromhex(normalized)
    except ValueError as exc:
        raise PolicyAlignmentError(f"{field} must be a hexadecimal SHA-256 checksum") from exc
    return normalized


def _normalize_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyAlignmentError("expected_routing_revision must be a non-negative integer")
    return value


def _normalize_pause_reason(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyAlignmentError("expected_pause_reason must be a non-empty string")
    return value.strip()


def _target_policy_payload() -> tuple[int, str, dict[str, object]]:
    config = load_virtual_player_v2_config()
    if config is None:
        raise PolicyAlignmentError("bot_development_v2 is not configured")
    policy = config.policy(2)
    payload = canonical_policy_payload(policy.payload)
    checksum = _normalize_checksum(policy.checksum, field="configured policy checksum")
    if policy_checksum(payload) != checksum:
        raise PolicyAlignmentError("configured policy payload checksum is invalid")
    return int(policy.version), checksum, payload


@transaction.atomic
def align_configured_policy_runtime_operation(
    *,
    expected_routing_revision: int,
    expected_pause_reason: str,
    expected_policy_checksum: str,
    target_policy_checksum: str,
    apply: bool = False,
    aligned_at: datetime | None = None,
) -> PolicyAlignmentOperationSummary:
    """CAS-align the DB release, all V2 profiles, and the control snapshot.

    The routing row is locked first and must already be a V2-active-origin
    pause. Every V2 profile is then locked. Current policy-2 profiles must
    still carry the expected old checksum; retired profiles from the old
    policy are preserved as historical rows and are never rewritten.
    """

    normalized_revision = _normalize_revision(expected_routing_revision)
    normalized_pause_reason = _normalize_pause_reason(expected_pause_reason)
    normalized_previous_checksum = _normalize_checksum(
        expected_policy_checksum,
        field="expected_policy_checksum",
    )
    normalized_target_checksum = _normalize_checksum(
        target_policy_checksum,
        field="target_policy_checksum",
    )
    version, configured_checksum, target_payload = _target_policy_payload()
    if version != 2:
        raise PolicyAlignmentError("only policy 2 can be aligned")
    if configured_checksum != normalized_target_checksum:
        raise PolicyAlignmentError("target_policy_checksum does not match the currently configured policy")
    if normalized_previous_checksum == normalized_target_checksum:
        raise PolicyAlignmentError("target policy checksum must differ from the expected old checksum")

    routing = lock_virtual_player_routing()
    if not routing.persisted or routing.revision is None:
        raise PolicyAlignmentError("V2 routing state is not initialized")
    if int(routing.revision) != normalized_revision:
        raise PolicyAlignmentError(
            f"routing revision changed: expected {normalized_revision}, found {routing.revision}"
        )
    if routing.bootstrap_mode is not BootstrapMode.V2_ACTIVE:
        raise PolicyAlignmentError("policy alignment requires bootstrap_mode=v2_active")
    if routing.maintenance_mode is not MaintenanceMode.V2_PAUSED:
        raise PolicyAlignmentError("policy alignment requires maintenance_mode=v2_paused")
    if routing.paused_from_maintenance_mode != MaintenanceMode.V2_ACTIVE.value:
        raise PolicyAlignmentError("policy alignment requires a pause originating from v2_active")
    if routing.pause_reason != normalized_pause_reason:
        raise PolicyAlignmentError("routing pause reason changed before policy alignment")

    release = BotPolicyRelease.objects.select_for_update().filter(version=version).first()
    if release is None:
        raise PolicyAlignmentError("policy version 2 release is missing")
    if release.retired_at is not None:
        raise PolicyAlignmentError("policy version 2 release is retired")
    if release.checksum != normalized_previous_checksum:
        raise PolicyAlignmentError("policy version 2 checksum changed before policy alignment")
    if policy_checksum(release.payload) != release.checksum:
        raise PolicyAlignmentError("policy version 2 release payload is corrupt")
    if BotPolicyRelease.objects.filter(checksum=normalized_target_checksum).exclude(version=version).exists():
        raise PolicyAlignmentError("target policy checksum belongs to another release")

    all_v2_profiles = list(BotProfile.objects.select_for_update().filter(engine_version=2).order_by("id"))
    non_policy2_profiles = [profile for profile in all_v2_profiles if int(profile.policy_version) != version]
    maintained_non_policy2_profiles = [
        profile.id for profile in non_policy2_profiles if profile.state in VIRTUAL_PROFILE_MAINTAINED_STATES
    ]
    if maintained_non_policy2_profiles:
        raise PolicyAlignmentError(
            "V2 profile population contains maintained non-policy-2 rows: " f"{maintained_non_policy2_profiles[:5]}"
        )
    v2_profiles = [profile for profile in all_v2_profiles if int(profile.policy_version) == version]
    mismatched_profiles = [
        profile.id
        for profile in v2_profiles
        if str(profile.policy_checksum).strip().lower() != normalized_previous_checksum
    ]
    if mismatched_profiles:
        raise PolicyAlignmentError(f"V2 profile checksums are not at the expected old value: {mismatched_profiles[:5]}")

    active_claims = list(
        ArenaVirtualReserveMember.objects.select_for_update()
        .filter(growth_claim_token__isnull=False)
        .order_by("id")
        .values_list("id", flat=True)
    )
    if active_claims:
        raise PolicyAlignmentError(f"arena growth claims are still active: {list(active_claims[:5])}")

    alignment_id = f"policy-align-{uuid4().hex}"
    updated_profile_count = 0
    control_run_digest = ""
    if apply:
        event_time = aligned_at or _database_utc_now()
        if event_time.tzinfo is None or event_time.utcoffset() is None:
            raise PolicyAlignmentError("aligned_at must be timezone-aware")

        release.checksum = normalized_target_checksum
        release.payload = target_payload
        release.released_at = event_time
        release.retire_not_before = event_time + POLICY_RETIREMENT_GUARD
        release.retired_at = None
        release.save(
            update_fields=[
                "checksum",
                "payload",
                "released_at",
                "retire_not_before",
                "retired_at",
            ]
        )

        for profile in v2_profiles:
            profile.policy_checksum = normalized_target_checksum
            profile.updated_at = event_time
        if v2_profiles:
            BotProfile.objects.bulk_update(
                v2_profiles,
                ["policy_checksum", "updated_at"],
                batch_size=500,
            )
        updated_profile_count = len(v2_profiles)

        control_result = refresh_growth_control_snapshots(now=event_time)
        control_run_digest = str(control_result["run_digest"])

        def _log_committed_alignment() -> None:
            logger.info(
                "Aligned configured V2 policy runtime",
                extra={
                    "event": "virtual_player_policy_runtime_aligned",
                    "alignment_id": alignment_id,
                    "policy_version": version,
                    "previous_checksum": normalized_previous_checksum,
                    "target_checksum": normalized_target_checksum,
                    "profile_count": len(v2_profiles),
                    "control_run_digest": control_run_digest,
                    "routing_revision": normalized_revision,
                },
            )

        transaction.on_commit(_log_committed_alignment)

    reasons: list[str] = []
    if non_policy2_profiles:
        reasons.append("retired_non_policy2_profiles_excluded")
    if not apply:
        reasons.append("growth_control_snapshot_will_be_rebuilt")

    return PolicyAlignmentOperationSummary(
        scanned=1 + len(all_v2_profiles),
        locked=1 + len(all_v2_profiles),
        changed=(1 + len(v2_profiles)) if not apply else 1 + updated_profile_count,
        skipped=0,
        failed=0,
        reasons=tuple(reasons),
        version=version,
        previous_checksum=normalized_previous_checksum,
        target_checksum=normalized_target_checksum,
        profile_count=len(v2_profiles),
        historical_v2_profile_count=len(non_policy2_profiles),
        updated_profile_count=updated_profile_count,
        control_run_digest=control_run_digest,
        routing_revision=normalized_revision,
        alignment_id=alignment_id,
    )


__all__ = [
    "PolicyAlignmentError",
    "PolicyAlignmentOperationSummary",
    "align_configured_policy_runtime_operation",
]
