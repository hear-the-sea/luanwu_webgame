from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from gameplay.models import BotPolicyRelease, BotProfile, BotRuntimeRoutingState

from .config import canonical_policy_payload, load_virtual_player_v2_config, policy_checksum
from .database_clock import database_utc_now as _database_utc_now

POLICY_RETIREMENT_GUARD = timedelta(hours=720)


class PolicyRegistryError(ValueError):
    pass


class PolicyReleaseConflict(PolicyRegistryError):
    pass


class PolicyAssignmentError(PolicyRegistryError):
    pass


class PolicyRetirementBlocked(PolicyRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class PolicyReleaseResult:
    release: BotPolicyRelease
    created: bool


@dataclass(frozen=True, slots=True)
class PolicyOperationSummary:
    scanned: int
    locked: int
    changed: int
    skipped: int
    failed: int
    reasons: tuple[str, ...]
    version: int
    checksum: str


@dataclass(frozen=True, slots=True)
class _PolicyRetirementResult:
    release: BotPolicyRelease
    changed: bool


def _normalized_release_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = canonical_policy_payload(payload)
    calculated = policy_checksum(normalized)
    if not calculated:
        raise PolicyRegistryError("policy checksum cannot be empty")
    return normalized


def _normalized_release_request(
    *,
    version: int,
    checksum: str,
    payload: Mapping[str, Any],
) -> tuple[int, str, dict[str, Any]]:
    normalized_version = int(version)
    if isinstance(version, bool) or normalized_version < 1:
        raise PolicyRegistryError("policy version must be a positive integer")
    normalized_payload = _normalized_release_payload(payload)
    calculated_checksum = policy_checksum(normalized_payload)
    normalized_checksum = str(checksum).strip().lower()
    if normalized_checksum != calculated_checksum:
        raise PolicyRegistryError("policy checksum does not match canonical payload")
    return normalized_version, normalized_checksum, normalized_payload


def _assert_matching_existing_release(
    existing: BotPolicyRelease,
    *,
    version: int,
    checksum: str,
    payload: Mapping[str, Any],
) -> None:
    if existing.checksum != checksum or existing.payload != payload:
        raise PolicyReleaseConflict(f"policy version {version} already has different content")


@transaction.atomic
def release_policy(
    *,
    version: int,
    checksum: str,
    payload: Mapping[str, Any],
    released_at: datetime | None = None,
) -> PolicyReleaseResult:
    if isinstance(version, bool) or int(version) != 2:
        raise PolicyRegistryError("policy 2 is the only releasable virtual-player policy")
    normalized_version, normalized_checksum, normalized_payload = _normalized_release_request(
        version=version,
        checksum=checksum,
        payload=payload,
    )

    existing = BotPolicyRelease.objects.filter(version=normalized_version).first()
    if existing is not None:
        _assert_matching_existing_release(
            existing,
            version=normalized_version,
            checksum=normalized_checksum,
            payload=normalized_payload,
        )
        return PolicyReleaseResult(release=existing, created=False)

    now = released_at or _database_utc_now()
    if timezone.is_naive(now):
        raise PolicyRegistryError("released_at must be timezone-aware")
    try:
        with transaction.atomic():
            release = BotPolicyRelease.objects.create(
                version=normalized_version,
                checksum=normalized_checksum,
                payload=normalized_payload,
                released_at=now,
                retire_not_before=now + POLICY_RETIREMENT_GUARD,
            )
    except IntegrityError as exc:
        existing = BotPolicyRelease.objects.select_for_update().filter(version=normalized_version).first()
        if existing is not None:
            _assert_matching_existing_release(
                existing,
                version=normalized_version,
                checksum=normalized_checksum,
                payload=normalized_payload,
            )
            return PolicyReleaseResult(release=existing, created=False)
        checksum_owner = BotPolicyRelease.objects.filter(checksum=normalized_checksum).first()
        if checksum_owner is not None:
            raise PolicyReleaseConflict(f"policy checksum already belongs to version {checksum_owner.version}") from exc
        raise PolicyReleaseConflict(f"policy version {normalized_version} could not be published atomically") from exc
    return PolicyReleaseResult(release=release, created=True)


@transaction.atomic
def release_configured_policy_operation(
    *,
    version: int,
    apply: bool = False,
) -> PolicyOperationSummary:
    config = load_virtual_player_v2_config()
    if config is None:
        raise PolicyRegistryError("bot_development_v2 is not configured")
    policy = config.policy(version)
    normalized_version, normalized_checksum, normalized_payload = _normalized_release_request(
        version=policy.version,
        checksum=policy.checksum,
        payload=policy.payload,
    )
    if apply:
        created = release_policy(
            version=normalized_version,
            checksum=normalized_checksum,
            payload=normalized_payload,
        ).created
    else:
        existing = BotPolicyRelease.objects.filter(version=normalized_version).first()
        if existing is not None:
            _assert_matching_existing_release(
                existing,
                version=normalized_version,
                checksum=normalized_checksum,
                payload=normalized_payload,
            )
        created = existing is None
    return PolicyOperationSummary(
        scanned=1,
        locked=0,
        changed=int(created),
        skipped=int(not created),
        failed=0,
        reasons=() if created else ("already_released",),
        version=normalized_version,
        checksum=normalized_checksum,
    )


def get_policy_release(*, version: int, expected_checksum: str | None = None) -> BotPolicyRelease:
    try:
        release = BotPolicyRelease.objects.get(version=int(version))
    except BotPolicyRelease.DoesNotExist as exc:
        raise PolicyAssignmentError(f"policy version {version} is not released") from exc
    if expected_checksum is not None and release.checksum != str(expected_checksum).strip().lower():
        raise PolicyAssignmentError(f"policy version {version} checksum does not match")
    if policy_checksum(release.payload) != release.checksum:
        raise PolicyAssignmentError(f"policy version {version} payload is corrupt")
    return release


def get_assignable_policy_release(
    *,
    version: int,
    expected_checksum: str,
) -> BotPolicyRelease:
    if isinstance(version, bool) or int(version) != 2:
        raise PolicyAssignmentError("policy 2 is the only assignable virtual-player policy")
    release = get_policy_release(
        version=version,
        expected_checksum=expected_checksum,
    )
    if release.retired_at is not None:
        raise PolicyAssignmentError(f"policy version {version} is retired")
    return release


def lock_assignable_policy_release(*, version: int, expected_checksum: str) -> BotPolicyRelease:
    if isinstance(version, bool) or int(version) != 2:
        raise PolicyAssignmentError("policy 2 is the only assignable virtual-player policy")
    try:
        release = BotPolicyRelease.objects.select_for_update().get(version=int(version))
    except BotPolicyRelease.DoesNotExist as exc:
        raise PolicyAssignmentError(f"policy version {version} is not released") from exc
    if release.retired_at is not None:
        raise PolicyAssignmentError(f"policy version {version} is retired")
    if release.checksum != str(expected_checksum).strip().lower():
        raise PolicyAssignmentError(f"policy version {version} checksum does not match")
    if policy_checksum(release.payload) != release.checksum:
        raise PolicyAssignmentError(f"policy version {version} payload is corrupt")
    return release


def _routing_references_policy(version: int) -> bool:
    from gameplay.services.runtime_configs import parse_calibration_routes

    state = BotRuntimeRoutingState.objects.filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is None:
        return False
    return any(route.policy_version == version for route in parse_calibration_routes(state.calibration_routes))


def _rollout_references_policy(version: int) -> bool:
    return BotRuntimeRoutingState.objects.filter(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        policy_rollout_enabled=True,
        policy_rollout_target_version=int(version),
    ).exists()


@transaction.atomic
def extend_retirement_deadline(
    *,
    version: int,
    expected_checksum: str,
    reference_removed_at: datetime | None = None,
) -> BotPolicyRelease:
    release = lock_assignable_policy_release(version=version, expected_checksum=expected_checksum)
    event_time = reference_removed_at or _database_utc_now()
    if timezone.is_naive(event_time):
        raise PolicyRegistryError("reference_removed_at must be timezone-aware")
    proposed = event_time + POLICY_RETIREMENT_GUARD
    if proposed > release.retire_not_before:
        BotPolicyRelease.objects.filter(version=release.version).update(retire_not_before=proposed)
        release.retire_not_before = proposed
    return release


@transaction.atomic
def update_routing_policy_references(
    *,
    added_versions: set[int] | frozenset[int],
    removed_versions: set[int] | frozenset[int],
    apply: bool,
    reference_removed_at: datetime | None = None,
) -> None:
    normalized_added = {int(version) for version in added_versions}
    normalized_removed = {int(version) for version in removed_versions}
    versions = sorted(normalized_added | normalized_removed)
    if any(version < 1 for version in versions):
        raise PolicyRegistryError("routing policy versions must be positive integers")
    if normalized_added - {2}:
        raise PolicyRegistryError("routing may only reference policy 2")
    releases = {
        release.version: release
        for release in BotPolicyRelease.objects.select_for_update().filter(version__in=versions).order_by("version")
    }
    missing_added = sorted(normalized_added - set(releases))
    if missing_added:
        raise PolicyAssignmentError(f"routing references unreleased policy versions {missing_added}")
    for version in sorted(normalized_added):
        release = releases[version]
        if release.retired_at is not None:
            raise PolicyAssignmentError(f"policy version {version} is retired")
        if policy_checksum(release.payload) != release.checksum:
            raise PolicyAssignmentError(f"policy version {version} payload is corrupt")

    if not apply or not normalized_removed:
        return
    event_time = reference_removed_at or _database_utc_now()
    if timezone.is_naive(event_time):
        raise PolicyRegistryError("reference_removed_at must be timezone-aware")
    proposed = event_time + POLICY_RETIREMENT_GUARD
    changed_releases = []
    for version in sorted(normalized_removed):
        removed_release = releases.get(version)
        if (
            removed_release is not None
            and removed_release.retired_at is None
            and proposed > removed_release.retire_not_before
        ):
            removed_release.retire_not_before = proposed
            changed_releases.append(removed_release)
    if changed_releases:
        BotPolicyRelease.objects.bulk_update(
            changed_releases,
            ["retire_not_before"],
        )


@transaction.atomic
def _retire_policy_release(
    *,
    version: int,
    expected_checksum: str,
    retired_at: datetime | None = None,
    apply: bool,
) -> _PolicyRetirementResult:
    normalized_version = int(version)
    try:
        release = BotPolicyRelease.objects.select_for_update().get(version=normalized_version)
    except BotPolicyRelease.DoesNotExist as exc:
        raise PolicyRetirementBlocked(f"policy version {version} is not released") from exc
    if release.checksum != str(expected_checksum).strip().lower():
        raise PolicyRetirementBlocked(f"policy version {version} checksum does not match")
    if release.retired_at is not None:
        if retired_at is None or release.retired_at == retired_at:
            return _PolicyRetirementResult(release=release, changed=False)
        raise PolicyRetirementBlocked(f"policy version {version} is already retired at a different time")
    if policy_checksum(release.payload) != release.checksum:
        raise PolicyRetirementBlocked(f"policy version {version} payload is corrupt")
    if BotProfile.objects.filter(policy_version=normalized_version).exists():
        raise PolicyRetirementBlocked(f"policy version {version} is still referenced by profiles")
    if _routing_references_policy(normalized_version):
        raise PolicyRetirementBlocked(f"policy version {version} is still referenced by routing")
    if _rollout_references_policy(normalized_version):
        raise PolicyRetirementBlocked(f"policy version {version} is still referenced by rollout")

    now = retired_at or _database_utc_now()
    if timezone.is_naive(now):
        raise PolicyRegistryError("retired_at must be timezone-aware")
    if now < release.retire_not_before:
        raise PolicyRetirementBlocked(
            f"policy version {version} cannot retire before {release.retire_not_before.isoformat()}"
        )
    if apply:
        BotPolicyRelease.objects.filter(version=normalized_version, retired_at__isnull=True).update(retired_at=now)
        release.retired_at = now
    return _PolicyRetirementResult(release=release, changed=True)


def retire_policy_release(
    *,
    version: int,
    expected_checksum: str,
    retired_at: datetime | None = None,
) -> BotPolicyRelease:
    return _retire_policy_release(
        version=version,
        expected_checksum=expected_checksum,
        retired_at=retired_at,
        apply=True,
    ).release


def retire_policy_release_operation(
    *,
    version: int,
    expected_checksum: str,
    apply: bool = False,
) -> PolicyOperationSummary:
    normalized_checksum = str(expected_checksum).strip().lower()
    result = _retire_policy_release(
        version=version,
        expected_checksum=normalized_checksum,
        apply=apply,
    )
    return PolicyOperationSummary(
        scanned=1,
        locked=0,
        changed=int(result.changed),
        skipped=int(not result.changed),
        failed=0,
        reasons=() if result.changed else ("already_retired",),
        version=int(result.release.version),
        checksum=result.release.checksum,
    )


__all__ = [
    "POLICY_RETIREMENT_GUARD",
    "PolicyAssignmentError",
    "PolicyRegistryError",
    "PolicyReleaseConflict",
    "PolicyReleaseResult",
    "PolicyOperationSummary",
    "PolicyRetirementBlocked",
    "extend_retirement_deadline",
    "get_assignable_policy_release",
    "get_policy_release",
    "lock_assignable_policy_release",
    "release_configured_policy_operation",
    "release_policy",
    "retire_policy_release",
    "retire_policy_release_operation",
    "update_routing_policy_references",
]
