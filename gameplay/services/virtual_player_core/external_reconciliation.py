from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from celery import current_app
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.constants import VIRTUAL_PLAYER_REGION_KEYS
from gameplay.models import BotExternalStrengthReconciliation, BotProfile

from . import health, population_runtime, profile_store
from .config import V2_PRESTIGE_BAND_NAMES, load_virtual_player_v2_config
from .database_clock import database_utc_now as _database_utc_now
from .projection import ProjectionRuleError, StrengthSummary

logger = logging.getLogger(__name__)


EXTERNAL_RECONCILIATION_CLAIM_LEASE_SECONDS = 300
EXTERNAL_RECONCILIATION_MAX_ATTEMPTS_PER_PHASE = 12
EXTERNAL_RECONCILIATION_RETRY_INITIAL_SECONDS = 60
EXTERNAL_RECONCILIATION_RETRY_MAX_SECONDS = 21_600
EXTERNAL_RECONCILIATION_STRENGTH_SCHEMA_VERSION = 1
EXTERNAL_RECONCILIATION_RESULT_SCHEMA_VERSION = 1
NO_WORK_STATUS = "no_work"
CLAIM_LOST_STATUS = "claim_lost"

_STRENGTH_COMPONENTS = frozenset(
    {
        "arena_lineup_power",
        "core_building_level",
        "guest_count",
        "max_guest_level",
        "prestige",
        "troop_total",
    }
)
_UNRESOLVED_STATUSES = frozenset(
    {
        BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
        BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE,
        BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
        BotExternalStrengthReconciliation.Status.CLAIMED_POPULATION,
        BotExternalStrengthReconciliation.Status.QUARANTINED,
    }
)


class ExternalReconciliationError(ValueError):
    pass


class ExternalReconciliationConflict(ExternalReconciliationError):
    pass


class ExternalReconciliationPermanentError(ExternalReconciliationError):
    def __init__(self, failure_code: str, message: str):
        super().__init__(message)
        self.failure_code = failure_code


class ExternalReconciliationRetryableError(ExternalReconciliationError):
    def __init__(self, failure_code: str, message: str):
        super().__init__(message)
        self.failure_code = failure_code


class _ExternalReconciliationClaimLost(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReconciliationOperationSummary:
    scanned: int
    locked: int
    changed: int
    skipped: int
    failed: int
    reasons: tuple[str, ...]
    reconciliation_id: int


@dataclass(frozen=True, slots=True)
class ExternalReconciliationAnchor:
    profile_id: int
    manor_id: int
    pre_strength_summary: StrengthSummary
    pre_prestige_band: str


@dataclass(frozen=True, slots=True)
class ExternalReconciliationIntentResult:
    reconciliation_id: int
    profile_id: int
    created: bool
    status: str


@dataclass(frozen=True, slots=True)
class ExternalReconciliationClaim:
    reconciliation_id: int
    profile_id: int
    phase: str
    claim_token: UUID
    claimed_at: datetime
    claim_expires_at: datetime
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ExternalReconciliationProcessResult:
    reconciliation_id: int
    profile_id: int | None
    status: str
    phase: str = ""
    attempt_count: int | None = None
    failure_code: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "reconciliation_id": self.reconciliation_id,
            "profile_id": self.profile_id,
            "status": self.status,
            "phase": self.phase,
            "attempt_count": self.attempt_count,
            "failure_code": self.failure_code,
        }


def _reconciliation_now(now: datetime | None) -> datetime:
    resolved = now or _database_utc_now()
    if not isinstance(resolved, datetime) or timezone.is_naive(resolved):
        raise ExternalReconciliationError("external reconciliation time must be timezone-aware")
    return resolved.astimezone(UTC)


def serialize_strength_summary(summary: StrengthSummary) -> dict[str, Any]:
    if not isinstance(summary, StrengthSummary):
        raise ExternalReconciliationError("strength summary must be a StrengthSummary")
    if frozenset(summary.components) != _STRENGTH_COMPONENTS:
        raise ExternalReconciliationError("strength summary must use the canonical component set")
    return {
        "schema_version": EXTERNAL_RECONCILIATION_STRENGTH_SCHEMA_VERSION,
        "composite": summary.composite,
        "components": dict(summary.components),
    }


def parse_strength_summary(value: object) -> StrengthSummary:
    expected_fields = {"schema_version", "composite", "components"}
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise ExternalReconciliationPermanentError(
            "invalid_strength_summary",
            "external strength summary has invalid fields",
        )
    if value["schema_version"] != EXTERNAL_RECONCILIATION_STRENGTH_SCHEMA_VERSION:
        raise ExternalReconciliationPermanentError(
            "invalid_strength_summary",
            "external strength summary schema version is unsupported",
        )
    components = value["components"]
    if not isinstance(components, Mapping) or frozenset(components) != _STRENGTH_COMPONENTS:
        raise ExternalReconciliationPermanentError(
            "invalid_strength_summary",
            "external strength summary component set is invalid",
        )
    try:
        return StrengthSummary(
            composite=value["composite"],  # type: ignore[arg-type]
            components=components,  # type: ignore[arg-type]
        )
    except (ProjectionRuleError, TypeError, ValueError) as exc:
        raise ExternalReconciliationPermanentError(
            "invalid_strength_summary",
            "external strength summary values are invalid",
        ) from exc


def _normalize_positive_id(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExternalReconciliationError(f"{field} must be a positive integer")
    return value


def _normalize_domain_text(value: object, *, field: str, maximum_length: int) -> str:
    if not isinstance(value, str):
        raise ExternalReconciliationError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ExternalReconciliationError(f"{field} must not be blank")
    if len(normalized) > maximum_length:
        raise ExternalReconciliationError(f"{field} must contain at most {maximum_length} characters")
    return normalized


def _canonical_prestige_band_for_summary(summary: StrengthSummary) -> str:
    raw_prestige = summary.components["prestige"]
    prestige = int(raw_prestige)
    if prestige < 0 or raw_prestige != prestige:
        raise ExternalReconciliationError("strength summary prestige must be a non-negative integer")
    config = load_virtual_player_v2_config()
    if config is None:
        raise ExternalReconciliationError("bot_development_v2 is not configured")
    try:
        return config.band_for_prestige(prestige).name
    except ValueError as exc:
        raise ExternalReconciliationError("strength summary prestige is outside the canonical V2 bands") from exc


def capture_external_reconciliation_anchors(
    manor_ids: Iterable[int],
) -> dict[int, ExternalReconciliationAnchor]:
    normalized_ids = sorted({_normalize_positive_id(manor_id, field="manor_id") for manor_id in manor_ids})
    if not normalized_ids:
        return {}
    from .reference_snapshots import load_manor_strength_summary

    anchors: dict[int, ExternalReconciliationAnchor] = {}
    profiles = BotProfile.objects.filter(manor_id__in=normalized_ids).select_related("manor").order_by("manor_id")
    for profile in profiles:
        summary = load_manor_strength_summary(manor_id=int(profile.manor_id))
        anchors[int(profile.manor_id)] = ExternalReconciliationAnchor(
            profile_id=int(profile.id),
            manor_id=int(profile.manor_id),
            pre_strength_summary=summary,
            pre_prestige_band=_canonical_prestige_band_for_summary(summary),
        )
    return anchors


def _queue_external_reconciliation(reconciliation_id: int) -> bool:
    task = current_app.signature("gameplay.reconcile_external_strength_reconciliation")
    return safe_apply_async(
        task,
        args=[int(reconciliation_id)],
        logger=logger,
        log_message=("external virtual-player reconciliation dispatch failed; " "relying on the periodic scan"),
        log_extra={
            "event": "virtual_player_external_reconciliation_dispatch_deferred",
            "reconciliation_id": int(reconciliation_id),
        },
    )


def _queue_population_reconciliation(*, region: str, prestige_band: str) -> bool:
    task = current_app.signature("gameplay.reconcile_virtual_player_population_cell")
    return safe_apply_async(
        task,
        args=[region, prestige_band],
        logger=logger,
        log_message=("external reconciliation population dispatch failed; " "relying on the periodic demand scan"),
        log_extra={
            "event": "virtual_player_external_population_dispatch_deferred",
            "region": region,
            "prestige_band": prestige_band,
        },
    )


def _intent_payload_matches(
    reconciliation: BotExternalStrengthReconciliation,
    *,
    origin_committed_at: datetime,
    pre_strength_summary: Mapping[str, Any],
    pre_prestige_band: str,
) -> bool:
    return bool(
        reconciliation.origin_committed_at == origin_committed_at
        and reconciliation.pre_strength_summary == pre_strength_summary
        and reconciliation.pre_prestige_band == pre_prestige_band
    )


def create_external_reconciliation_intent(
    *,
    anchor: ExternalReconciliationAnchor,
    domain_event_kind: str,
    domain_event_id: str,
    origin_committed_at: datetime,
) -> ExternalReconciliationIntentResult:
    """Persist an idempotent intent inside the already-open domain transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise ExternalReconciliationError("external reconciliation intent must be created inside transaction.atomic()")
    if not isinstance(anchor, ExternalReconciliationAnchor):
        raise ExternalReconciliationError("anchor must be an ExternalReconciliationAnchor")
    profile_id = _normalize_positive_id(anchor.profile_id, field="anchor.profile_id")
    manor_id = _normalize_positive_id(anchor.manor_id, field="anchor.manor_id")
    event_kind = _normalize_domain_text(
        domain_event_kind,
        field="domain_event_kind",
        maximum_length=64,
    )
    event_id = _normalize_domain_text(
        domain_event_id,
        field="domain_event_id",
        maximum_length=128,
    )
    committed_at = _reconciliation_now(origin_committed_at)
    pre_strength_payload = serialize_strength_summary(anchor.pre_strength_summary)
    expected_band = _canonical_prestige_band_for_summary(anchor.pre_strength_summary)
    if anchor.pre_prestige_band != expected_band:
        raise ExternalReconciliationError("anchor.pre_prestige_band does not match the strength summary")
    if not BotProfile.objects.filter(pk=profile_id, manor_id=manor_id).exists():
        raise ExternalReconciliationError("anchor profile and manor identity no longer match")

    lookup = {
        "profile_id": profile_id,
        "domain_event_kind": event_kind,
        "domain_event_id": event_id,
    }
    reconciliation = BotExternalStrengthReconciliation.objects.filter(**lookup).first()
    created = False
    if reconciliation is None:
        try:
            with transaction.atomic():
                reconciliation = BotExternalStrengthReconciliation.objects.create(
                    **lookup,
                    origin_committed_at=committed_at,
                    pre_strength_summary=pre_strength_payload,
                    pre_prestige_band=expected_band,
                    available_at=_database_utc_now(),
                )
                created = True
        except IntegrityError:
            reconciliation = BotExternalStrengthReconciliation.objects.get(**lookup)
    if not _intent_payload_matches(
        reconciliation,
        origin_committed_at=committed_at,
        pre_strength_summary=pre_strength_payload,
        pre_prestige_band=expected_band,
    ):
        raise ExternalReconciliationConflict(
            "external reconciliation event key already has a different immutable payload"
        )

    reconciliation_id = int(reconciliation.id)
    if reconciliation.status in _UNRESOLVED_STATUSES and reconciliation.status != (
        BotExternalStrengthReconciliation.Status.QUARANTINED
    ):
        transaction.on_commit(
            lambda: _queue_external_reconciliation(reconciliation_id),
            robust=True,
        )
    return ExternalReconciliationIntentResult(
        reconciliation_id=reconciliation_id,
        profile_id=profile_id,
        created=created,
        status=str(reconciliation.status),
    )


def _failure_digest(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        payload = f"{type(error).__module__}.{type(error).__qualname__}:{error}"
    else:
        payload = str(error)
    return sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _phase_contract(status: str) -> tuple[str, str, str, str] | None:
    if status in {
        BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
        BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE,
    }:
        return (
            BotExternalStrengthReconciliation.Phase.PROFILE,
            BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
            BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE,
            "profile_attempt_count",
        )
    if status in {
        BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
        BotExternalStrengthReconciliation.Status.CLAIMED_POPULATION,
    }:
        return (
            BotExternalStrengthReconciliation.Phase.POPULATION,
            BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
            BotExternalStrengthReconciliation.Status.CLAIMED_POPULATION,
            "population_attempt_count",
        )
    return None


def _clear_claim(reconciliation: BotExternalStrengthReconciliation) -> None:
    reconciliation.claim_token = None
    reconciliation.claimed_at = None
    reconciliation.claim_expires_at = None


def _retry_backoff_seconds(attempt_count: int) -> int:
    exponent = min(max(0, int(attempt_count) - 1), 30)
    return min(
        EXTERNAL_RECONCILIATION_RETRY_MAX_SECONDS,
        EXTERNAL_RECONCILIATION_RETRY_INITIAL_SECONDS * (2**exponent),
    )


def _requeue_expired_claim_locked(
    reconciliation: BotExternalStrengthReconciliation,
    *,
    phase: str,
    pending_status: str,
    attempt_field: str,
    now: datetime,
    reset_attempts: bool,
) -> health.VirtualPlayerHealthSnapshot:
    failure_code = f"{phase}_claim_lease_expired"
    health_snapshot = health.retryable_failure(
        failure_code=failure_code,
        error="external reconciliation claim lease expired before completion",
        now=now,
    )
    attempt_count = 0 if reset_attempts else int(getattr(reconciliation, attempt_field))
    setattr(reconciliation, attempt_field, attempt_count)
    reconciliation.status = pending_status
    backoff_seconds = EXTERNAL_RECONCILIATION_RETRY_MAX_SECONDS if reset_attempts else 0
    retry_at = now + timedelta(seconds=backoff_seconds)
    if health_snapshot.next_probe_at is not None:
        retry_at = max(retry_at, health_snapshot.next_probe_at)
    reconciliation.available_at = retry_at
    _clear_claim(reconciliation)
    reconciliation.failure_code = failure_code
    reconciliation.last_error_digest = _failure_digest("external reconciliation claim lease expired before completion")
    reconciliation.save(
        update_fields=[
            attempt_field,
            "status",
            "available_at",
            "claim_token",
            "claimed_at",
            "claim_expires_at",
            "failure_code",
            "last_error_digest",
            "updated_at",
        ]
    )
    return health_snapshot


def _quarantine_locked(
    reconciliation: BotExternalStrengthReconciliation,
    *,
    phase: str,
    failure_code: str,
    error: BaseException | str,
    now: datetime,
) -> None:
    code = _normalize_domain_text(
        failure_code,
        field="failure_code",
        maximum_length=64,
    )
    reconciliation.status = BotExternalStrengthReconciliation.Status.QUARANTINED
    reconciliation.available_at = now
    _clear_claim(reconciliation)
    reconciliation.quarantined_at = now
    reconciliation.quarantined_phase = phase
    reconciliation.failure_code = code
    reconciliation.last_error_digest = _failure_digest(error)
    reconciliation.save(
        update_fields=[
            "status",
            "available_at",
            "claim_token",
            "claimed_at",
            "claim_expires_at",
            "quarantined_at",
            "quarantined_phase",
            "failure_code",
            "last_error_digest",
            "updated_at",
        ]
    )


def _has_unresolved_earlier_intent(
    reconciliation: BotExternalStrengthReconciliation,
) -> bool:
    earlier = Q(origin_committed_at__lt=reconciliation.origin_committed_at) | Q(
        origin_committed_at=reconciliation.origin_committed_at,
        id__lt=reconciliation.id,
    )
    return (
        BotExternalStrengthReconciliation.objects.filter(profile_id=reconciliation.profile_id)
        .exclude(status=BotExternalStrengthReconciliation.Status.APPLIED)
        .filter(earlier)
        .exists()
    )


def _claim_progress_is_valid(
    reconciliation: BotExternalStrengthReconciliation,
    *,
    phase: str,
) -> bool:
    if phase == BotExternalStrengthReconciliation.Phase.PROFILE:
        return bool(
            reconciliation.profile_completed_at is None
            and reconciliation.population_handoff_completed_at is None
            and reconciliation.applied_at is None
            and reconciliation.population_attempt_count == 0
        )
    return bool(
        reconciliation.profile_completed_at is not None
        and reconciliation.population_handoff_completed_at is None
        and reconciliation.applied_at is None
    )


def _claim_from_model(
    reconciliation: BotExternalStrengthReconciliation,
    *,
    phase: str,
    attempt_field: str,
) -> ExternalReconciliationClaim:
    if (
        reconciliation.claim_token is None
        or reconciliation.claimed_at is None
        or reconciliation.claim_expires_at is None
    ):
        raise RuntimeError("external reconciliation has an incomplete claim")
    return ExternalReconciliationClaim(
        reconciliation_id=int(reconciliation.id),
        profile_id=int(reconciliation.profile_id),
        phase=phase,
        claim_token=reconciliation.claim_token,
        claimed_at=reconciliation.claimed_at,
        claim_expires_at=reconciliation.claim_expires_at,
        attempt_count=int(getattr(reconciliation, attempt_field)),
    )


def _claim_locked_reconciliation(
    reconciliation: BotExternalStrengthReconciliation,
    *,
    now: datetime,
) -> ExternalReconciliationClaim | None:
    contract = _phase_contract(str(reconciliation.status))
    if contract is None:
        return None
    phase, pending_status, claimed_status, attempt_field = contract
    if _has_unresolved_earlier_intent(reconciliation):
        return None
    if not _claim_progress_is_valid(reconciliation, phase=phase):
        _quarantine_locked(
            reconciliation,
            phase=phase,
            failure_code=f"{phase}_progress_invalid",
            error="external reconciliation phase progress is inconsistent",
            now=now,
        )
        return None

    health_probe_at = health.reconciliation_deferred_until(now=now)

    claim_values = (
        reconciliation.claim_token,
        reconciliation.claimed_at,
        reconciliation.claim_expires_at,
    )
    claim_expired = False
    if reconciliation.status == pending_status:
        if reconciliation.available_at > now:
            return None
        if any(value is not None for value in claim_values):
            _quarantine_locked(
                reconciliation,
                phase=phase,
                failure_code=f"{phase}_claim_invalid",
                error="pending external reconciliation has claim fields",
                now=now,
            )
            return None
    else:
        if any(value is None for value in claim_values):
            _quarantine_locked(
                reconciliation,
                phase=phase,
                failure_code=f"{phase}_claim_invalid",
                error="claimed external reconciliation has incomplete claim fields",
                now=now,
            )
            return None
        claim_expires_at = reconciliation.claim_expires_at
        assert claim_expires_at is not None
        if claim_expires_at > now:
            return None
        claim_expired = True

    if health_probe_at is not None:
        if reconciliation.status == claimed_status:
            reconciliation.status = pending_status
            _clear_claim(reconciliation)
        reconciliation.available_at = max(reconciliation.available_at, health_probe_at)
        reconciliation.save(
            update_fields=[
                "status",
                "available_at",
                "claim_token",
                "claimed_at",
                "claim_expires_at",
                "updated_at",
            ]
        )
        return None

    attempt_count = int(getattr(reconciliation, attempt_field))
    if claim_expired:
        health_snapshot = _requeue_expired_claim_locked(
            reconciliation,
            phase=phase,
            pending_status=pending_status,
            attempt_field=attempt_field,
            now=now,
            reset_attempts=attempt_count >= EXTERNAL_RECONCILIATION_MAX_ATTEMPTS_PER_PHASE,
        )
        if health_snapshot.next_probe_at is not None and health_snapshot.next_probe_at > now:
            return None
        if attempt_count >= EXTERNAL_RECONCILIATION_MAX_ATTEMPTS_PER_PHASE:
            return None
        return _claim_locked_reconciliation(reconciliation, now=now)

    if attempt_count >= EXTERNAL_RECONCILIATION_MAX_ATTEMPTS_PER_PHASE:
        if reconciliation.failure_code:
            setattr(reconciliation, attempt_field, 0)
            reconciliation.status = pending_status
            reconciliation.available_at = max(
                reconciliation.available_at,
                now + timedelta(seconds=EXTERNAL_RECONCILIATION_RETRY_MAX_SECONDS),
            )
            _clear_claim(reconciliation)
            reconciliation.save(
                update_fields=[
                    attempt_field,
                    "status",
                    "available_at",
                    "claim_token",
                    "claimed_at",
                    "claim_expires_at",
                    "updated_at",
                ]
            )
            return None
        _requeue_expired_claim_locked(
            reconciliation,
            phase=phase,
            pending_status=pending_status,
            attempt_field=attempt_field,
            now=now,
            reset_attempts=True,
        )
        return None

    setattr(reconciliation, attempt_field, attempt_count + 1)
    reconciliation.status = claimed_status
    reconciliation.claim_token = uuid4()
    reconciliation.claimed_at = now
    reconciliation.claim_expires_at = now + timedelta(seconds=EXTERNAL_RECONCILIATION_CLAIM_LEASE_SECONDS)
    reconciliation.save(
        update_fields=[
            "status",
            attempt_field,
            "claim_token",
            "claimed_at",
            "claim_expires_at",
            "updated_at",
        ]
    )
    return _claim_from_model(
        reconciliation,
        phase=phase,
        attempt_field=attempt_field,
    )


@transaction.atomic
def claim_external_reconciliation(
    reconciliation_id: int,
    *,
    now: datetime | None = None,
) -> ExternalReconciliationClaim | None:
    normalized_id = _normalize_positive_id(
        reconciliation_id,
        field="reconciliation_id",
    )
    current_time = _reconciliation_now(now)
    reconciliation = BotExternalStrengthReconciliation.objects.select_for_update().filter(pk=normalized_id).first()
    if reconciliation is None:
        return None
    return _claim_locked_reconciliation(reconciliation, now=current_time)


@transaction.atomic
def claim_next_external_reconciliation(
    *,
    now: datetime | None = None,
) -> ExternalReconciliationClaim | None:
    current_time = _reconciliation_now(now)
    earlier = (
        BotExternalStrengthReconciliation.objects.filter(profile_id=OuterRef("profile_id"))
        .exclude(status=BotExternalStrengthReconciliation.Status.APPLIED)
        .filter(
            Q(origin_committed_at__lt=OuterRef("origin_committed_at"))
            | Q(
                origin_committed_at=OuterRef("origin_committed_at"),
                id__lt=OuterRef("id"),
            )
        )
    )
    due = (
        Q(
            status=BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
            available_at__lte=current_time,
        )
        | Q(
            status=BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
            available_at__lte=current_time,
        )
        | Q(
            status=BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE,
            claim_expires_at__lte=current_time,
        )
        | Q(
            status=BotExternalStrengthReconciliation.Status.CLAIMED_POPULATION,
            claim_expires_at__lte=current_time,
        )
    )
    reconciliation = (
        BotExternalStrengthReconciliation.objects.select_for_update(skip_locked=True)
        .filter(due)
        .annotate(has_unresolved_earlier=Exists(earlier))
        .filter(has_unresolved_earlier=False)
        .order_by("available_at", "profile_id", "origin_committed_at", "id")
        .first()
    )
    if reconciliation is None:
        return None
    return _claim_locked_reconciliation(reconciliation, now=current_time)


def _claim_matches(
    reconciliation: BotExternalStrengthReconciliation,
    claim: ExternalReconciliationClaim,
    *,
    now: datetime,
) -> bool:
    contract = _phase_contract(str(reconciliation.status))
    return bool(
        contract is not None
        and contract[0] == claim.phase
        and reconciliation.status == contract[2]
        and int(reconciliation.id) == claim.reconciliation_id
        and int(reconciliation.profile_id) == claim.profile_id
        and reconciliation.claim_token == claim.claim_token
        and reconciliation.claim_expires_at is not None
        and reconciliation.claim_expires_at > now
    )


def _process_result(
    claim: ExternalReconciliationClaim,
    *,
    status: str,
    failure_code: str = "",
) -> ExternalReconciliationProcessResult:
    return ExternalReconciliationProcessResult(
        reconciliation_id=claim.reconciliation_id,
        profile_id=claim.profile_id,
        status=status,
        phase=claim.phase,
        attempt_count=claim.attempt_count,
        failure_code=failure_code,
    )


def _validate_pre_prestige_band(
    reconciliation: BotExternalStrengthReconciliation,
    summary: StrengthSummary,
) -> None:
    try:
        expected_band = _canonical_prestige_band_for_summary(summary)
    except ExternalReconciliationError as exc:
        raise ExternalReconciliationPermanentError(
            "invalid_pre_prestige_band",
            str(exc),
        ) from exc
    if reconciliation.pre_prestige_band != expected_band:
        raise ExternalReconciliationPermanentError(
            "invalid_pre_prestige_band",
            "pre_prestige_band does not match the committed strength summary",
        )


def _strength_delta_payload(
    before: StrengthSummary,
    after: StrengthSummary,
) -> dict[str, Any]:
    return {
        "composite": after.composite - before.composite,
        "components": {key: after.components[key] - before.components[key] for key in before.components},
    }


def _result_summary_payload(
    reconciliation: BotExternalStrengthReconciliation,
    *,
    pre_strength_summary: StrengthSummary,
    sync_result: profile_store.ExternalStrengthProfileSyncResult,
    population_handoff_required: bool,
) -> dict[str, Any]:
    last_increase = sync_result.last_strength_increase_at
    return {
        "schema_version": EXTERNAL_RECONCILIATION_RESULT_SCHEMA_VERSION,
        "pre_prestige_band": reconciliation.pre_prestige_band,
        "current_prestige_band": sync_result.current_band,
        "region": sync_result.region,
        "post_strength_summary": serialize_strength_summary(sync_result.post_strength_summary),
        "strength_delta": _strength_delta_payload(
            pre_strength_summary,
            sync_result.post_strength_summary,
        ),
        "strength_increased": sync_result.strength_increased,
        "last_strength_increase_at": (
            None if last_increase is None else last_increase.astimezone(UTC).isoformat().replace("+00:00", "Z")
        ),
        "population_handoff_required": population_handoff_required,
    }


def _apply_claimed_profile_phase(
    claim: ExternalReconciliationClaim,
    *,
    now: datetime | None = None,
) -> ExternalReconciliationProcessResult:
    try:
        with transaction.atomic():
            reconciliation = (
                BotExternalStrengthReconciliation.objects.select_for_update().filter(pk=claim.reconciliation_id).first()
            )
            current_time = _reconciliation_now(now)
            if reconciliation is None or not _claim_matches(
                reconciliation,
                claim,
                now=current_time,
            ):
                return _process_result(claim, status=CLAIM_LOST_STATUS)

            pre_strength_summary = parse_strength_summary(reconciliation.pre_strength_summary)
            _validate_pre_prestige_band(reconciliation, pre_strength_summary)
            try:
                sync_result = profile_store.reconcile_external_strength_change(
                    claim.profile_id,
                    pre_strength_summary=pre_strength_summary,
                    origin_committed_at=reconciliation.origin_committed_at,
                )
            except profile_store.ProfileStateConflict as exc:
                raise ExternalReconciliationPermanentError(
                    "profile_missing",
                    str(exc),
                ) from exc
            except profile_store.ProfileLockUnavailable as exc:
                raise ExternalReconciliationRetryableError(
                    "profile_lock_unavailable",
                    str(exc),
                ) from exc
            except profile_store.ProfileStoreError as exc:
                raise ExternalReconciliationPermanentError(
                    "profile_contract_error",
                    str(exc),
                ) from exc

            if sync_result.current_band not in V2_PRESTIGE_BAND_NAMES:
                raise ExternalReconciliationPermanentError(
                    "profile_band_invalid",
                    "profile reconciliation produced a non-canonical prestige band",
                )
            population_handoff_required = bool(reconciliation.pre_prestige_band != sync_result.current_band)
            completion_time = _reconciliation_now(now)
            if not _claim_matches(
                reconciliation,
                claim,
                now=completion_time,
            ):
                raise _ExternalReconciliationClaimLost

            reconciliation.result_summary = _result_summary_payload(
                reconciliation,
                pre_strength_summary=pre_strength_summary,
                sync_result=sync_result,
                population_handoff_required=population_handoff_required,
            )
            reconciliation.profile_completed_at = completion_time
            reconciliation.available_at = completion_time
            _clear_claim(reconciliation)
            if population_handoff_required:
                reconciliation.status = BotExternalStrengthReconciliation.Status.PENDING_POPULATION
            else:
                reconciliation.status = BotExternalStrengthReconciliation.Status.APPLIED
                reconciliation.applied_at = completion_time
            reconciliation.save(
                update_fields=[
                    "status",
                    "available_at",
                    "claim_token",
                    "claimed_at",
                    "claim_expires_at",
                    "profile_completed_at",
                    "applied_at",
                    "result_summary",
                    "updated_at",
                ]
            )
            return _process_result(claim, status=str(reconciliation.status))
    except _ExternalReconciliationClaimLost:
        return _process_result(claim, status=CLAIM_LOST_STATUS)


def _validate_profile_result_summary(
    reconciliation: BotExternalStrengthReconciliation,
) -> tuple[str, str, str]:
    expected_fields = {
        "schema_version",
        "pre_prestige_band",
        "current_prestige_band",
        "region",
        "post_strength_summary",
        "strength_delta",
        "strength_increased",
        "last_strength_increase_at",
        "population_handoff_required",
    }
    summary = reconciliation.result_summary
    if not isinstance(summary, Mapping) or set(summary) != expected_fields:
        raise ExternalReconciliationPermanentError(
            "population_result_invalid",
            "profile result summary has invalid fields",
        )
    if summary["schema_version"] != EXTERNAL_RECONCILIATION_RESULT_SCHEMA_VERSION:
        raise ExternalReconciliationPermanentError(
            "population_result_invalid",
            "profile result summary schema version is unsupported",
        )
    pre_band = summary["pre_prestige_band"]
    current_band = summary["current_prestige_band"]
    region = summary["region"]
    if (
        not isinstance(pre_band, str)
        or pre_band != reconciliation.pre_prestige_band
        or pre_band not in V2_PRESTIGE_BAND_NAMES
        or not isinstance(current_band, str)
        or current_band not in V2_PRESTIGE_BAND_NAMES
        or current_band == pre_band
        or not isinstance(region, str)
        or not region
        or region not in VIRTUAL_PLAYER_REGION_KEYS
        or summary["population_handoff_required"] is not True
        or not isinstance(summary["strength_increased"], bool)
    ):
        raise ExternalReconciliationPermanentError(
            "population_result_invalid",
            "profile result summary has invalid handoff values",
        )
    parse_strength_summary(summary["post_strength_summary"])
    delta = summary["strength_delta"]
    if (
        not isinstance(delta, Mapping)
        or set(delta) != {"composite", "components"}
        or not isinstance(delta["components"], Mapping)
        or frozenset(delta["components"]) != _STRENGTH_COMPONENTS
    ):
        raise ExternalReconciliationPermanentError(
            "population_result_invalid",
            "profile strength delta is invalid",
        )
    return region, pre_band, current_band


def _apply_claimed_population_phase(
    claim: ExternalReconciliationClaim,
    *,
    now: datetime | None = None,
) -> ExternalReconciliationProcessResult:
    try:
        with transaction.atomic():
            reconciliation = (
                BotExternalStrengthReconciliation.objects.select_for_update().filter(pk=claim.reconciliation_id).first()
            )
            current_time = _reconciliation_now(now)
            if reconciliation is None or not _claim_matches(
                reconciliation,
                claim,
                now=current_time,
            ):
                return _process_result(claim, status=CLAIM_LOST_STATUS)
            if reconciliation.profile_completed_at is None:
                raise ExternalReconciliationPermanentError(
                    "population_progress_invalid",
                    "population handoff is missing profile completion",
                )
            region, pre_band, current_band = _validate_profile_result_summary(reconciliation)
            try:
                demands = population_runtime.merge_population_recompute_demands(
                    [(region, pre_band), (region, current_band)],
                    now=current_time,
                )
            except population_runtime.PopulationRecomputeDemandError as exc:
                raise ExternalReconciliationPermanentError(
                    "population_contract_error",
                    str(exc),
                ) from exc
            if {(str(demand.region), str(demand.prestige_band)) for demand in demands} != {
                (region, pre_band),
                (region, current_band),
            }:
                raise ExternalReconciliationPermanentError(
                    "population_handoff_incomplete",
                    "population handoff did not merge every required cell",
                )

            completion_time = _reconciliation_now(now)
            if not _claim_matches(
                reconciliation,
                claim,
                now=completion_time,
            ):
                raise _ExternalReconciliationClaimLost
            completion_time = max(
                completion_time,
                reconciliation.profile_completed_at,
            )
            reconciliation.status = BotExternalStrengthReconciliation.Status.APPLIED
            reconciliation.population_handoff_completed_at = completion_time
            reconciliation.applied_at = completion_time
            reconciliation.available_at = completion_time
            _clear_claim(reconciliation)
            reconciliation.save(
                update_fields=[
                    "status",
                    "available_at",
                    "claim_token",
                    "claimed_at",
                    "claim_expires_at",
                    "population_handoff_completed_at",
                    "applied_at",
                    "updated_at",
                ]
            )
            cells = tuple((str(demand.region), str(demand.prestige_band)) for demand in demands)

            def _wake_population_cells() -> None:
                for cell_region, cell_band in cells:
                    _queue_population_reconciliation(
                        region=cell_region,
                        prestige_band=cell_band,
                    )

            transaction.on_commit(_wake_population_cells, robust=True)
            return _process_result(
                claim,
                status=BotExternalStrengthReconciliation.Status.APPLIED,
            )
    except _ExternalReconciliationClaimLost:
        return _process_result(claim, status=CLAIM_LOST_STATUS)


@transaction.atomic
def _finalize_claim_failure(
    claim: ExternalReconciliationClaim,
    *,
    error: ExternalReconciliationPermanentError | ExternalReconciliationRetryableError,
    permanent: bool,
    now: datetime | None = None,
) -> ExternalReconciliationProcessResult:
    current_time = _reconciliation_now(now)
    reconciliation = (
        BotExternalStrengthReconciliation.objects.select_for_update().filter(pk=claim.reconciliation_id).first()
    )
    if reconciliation is None or not _claim_matches(
        reconciliation,
        claim,
        now=current_time,
    ):
        return _process_result(claim, status=CLAIM_LOST_STATUS)
    health_snapshot = None
    if not permanent:
        health_snapshot = health.retryable_failure(
            failure_code=error.failure_code,
            error=error,
            now=current_time,
        )
    exhausted = claim.attempt_count >= EXTERNAL_RECONCILIATION_MAX_ATTEMPTS_PER_PHASE
    if permanent:
        _quarantine_locked(
            reconciliation,
            phase=claim.phase,
            failure_code=error.failure_code,
            error=error,
            now=current_time,
        )
        return _process_result(
            claim,
            status=BotExternalStrengthReconciliation.Status.QUARANTINED,
            failure_code=error.failure_code,
        )

    pending_status = (
        BotExternalStrengthReconciliation.Status.PENDING_PROFILE
        if claim.phase == BotExternalStrengthReconciliation.Phase.PROFILE
        else BotExternalStrengthReconciliation.Status.PENDING_POPULATION
    )
    attempt_field = (
        "profile_attempt_count"
        if claim.phase == BotExternalStrengthReconciliation.Phase.PROFILE
        else "population_attempt_count"
    )
    if exhausted:
        setattr(reconciliation, attempt_field, 0)
        reconciliation.status = pending_status
        retry_at = current_time + timedelta(seconds=EXTERNAL_RECONCILIATION_RETRY_MAX_SECONDS)
        if health_snapshot is not None and health_snapshot.next_probe_at is not None:
            retry_at = max(retry_at, health_snapshot.next_probe_at)
        reconciliation.available_at = retry_at
        _clear_claim(reconciliation)
        reconciliation.failure_code = error.failure_code
        reconciliation.last_error_digest = _failure_digest(error)
        reconciliation.save(
            update_fields=[
                attempt_field,
                "status",
                "available_at",
                "claim_token",
                "claimed_at",
                "claim_expires_at",
                "failure_code",
                "last_error_digest",
                "updated_at",
            ]
        )
        return _process_result(
            claim,
            status=pending_status,
            failure_code=error.failure_code,
        )

    backoff_seconds = _retry_backoff_seconds(claim.attempt_count)
    reconciliation.status = pending_status
    reconciliation.available_at = current_time + timedelta(seconds=backoff_seconds)
    _clear_claim(reconciliation)
    reconciliation.failure_code = error.failure_code
    reconciliation.last_error_digest = _failure_digest(error)
    reconciliation.save(
        update_fields=[
            "status",
            "available_at",
            "claim_token",
            "claimed_at",
            "claim_expires_at",
            "failure_code",
            "last_error_digest",
            "updated_at",
        ]
    )
    return _process_result(
        claim,
        status=pending_status,
        failure_code=error.failure_code,
    )


def reconcile_claimed_external_reconciliation(
    claim: ExternalReconciliationClaim,
    *,
    now: datetime | None = None,
) -> ExternalReconciliationProcessResult:
    if not isinstance(claim, ExternalReconciliationClaim):
        raise ExternalReconciliationError("claim must be an ExternalReconciliationClaim")
    try:
        if claim.phase == BotExternalStrengthReconciliation.Phase.PROFILE:
            result = _apply_claimed_profile_phase(claim, now=now)
        elif claim.phase == BotExternalStrengthReconciliation.Phase.POPULATION:
            result = _apply_claimed_population_phase(claim, now=now)
        else:
            raise ExternalReconciliationPermanentError(
                "claim_phase_invalid",
                "external reconciliation claim phase is invalid",
            )
        if result.status in {
            BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
            BotExternalStrengthReconciliation.Status.APPLIED,
        }:
            health.reconciliation_success(now=_reconciliation_now(now))
        return result
    except ExternalReconciliationPermanentError as exc:
        return _finalize_claim_failure(
            claim,
            error=exc,
            permanent=True,
            now=now,
        )
    except ExternalReconciliationRetryableError as exc:
        return _finalize_claim_failure(
            claim,
            error=exc,
            permanent=False,
            now=now,
        )
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        retryable = ExternalReconciliationRetryableError(
            "infrastructure_unavailable",
            f"external reconciliation infrastructure failure: {exc}",
        )
        return _finalize_claim_failure(
            claim,
            error=retryable,
            permanent=False,
            now=now,
        )


def reconcile_external_reconciliation(
    reconciliation_id: int,
    *,
    now: datetime | None = None,
) -> ExternalReconciliationProcessResult:
    normalized_id = _normalize_positive_id(
        reconciliation_id,
        field="reconciliation_id",
    )
    fixed_time = _reconciliation_now(now) if now is not None else None
    claim = claim_external_reconciliation(normalized_id, now=fixed_time)
    if claim is None:
        return ExternalReconciliationProcessResult(
            reconciliation_id=normalized_id,
            profile_id=None,
            status=NO_WORK_STATUS,
        )
    return reconcile_claimed_external_reconciliation(claim, now=fixed_time)


def scan_external_reconciliations(
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> tuple[ExternalReconciliationProcessResult, ...]:
    if isinstance(limit, bool):
        raise ExternalReconciliationError("scan limit must be an integer")
    normalized_limit = max(0, min(1000, int(limit)))
    fixed_time = _reconciliation_now(now) if now is not None else None
    results: list[ExternalReconciliationProcessResult] = []
    for _index in range(normalized_limit):
        claim = claim_next_external_reconciliation(now=fixed_time)
        if claim is None:
            break
        result = reconcile_claimed_external_reconciliation(claim, now=fixed_time)
        results.append(result)
        if result.status == CLAIM_LOST_STATUS:
            break
    return tuple(results)


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ExternalReconciliationError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ExternalReconciliationError(f"{field} must not be blank")
    return normalized


@transaction.atomic
def requeue_quarantined_reconciliation_operation(
    *,
    reconciliation_id: int,
    expected_failure_code: str,
    expected_attempt_count: int,
    recovery_basis: str,
    apply: bool = False,
) -> ReconciliationOperationSummary:
    normalized_id = int(reconciliation_id)
    normalized_attempt_count = int(expected_attempt_count)
    if isinstance(reconciliation_id, bool) or normalized_id < 1:
        raise ExternalReconciliationError("reconciliation_id must be a positive integer")
    if isinstance(expected_attempt_count, bool) or normalized_attempt_count < 1 or normalized_attempt_count > 12:
        raise ExternalReconciliationError("expected_attempt_count must be between 1 and 12")
    normalized_failure_code = _required_text(
        expected_failure_code,
        field="expected_failure_code",
    )
    normalized_recovery_basis = _required_text(recovery_basis, field="recovery_basis")

    reconciliation = BotExternalStrengthReconciliation.objects.select_for_update().filter(pk=normalized_id).first()
    if reconciliation is None:
        raise ExternalReconciliationConflict(f"reconciliation {normalized_id} does not exist")
    if reconciliation.status != BotExternalStrengthReconciliation.Status.QUARANTINED:
        raise ExternalReconciliationConflict(f"reconciliation {normalized_id} is not quarantined")
    if reconciliation.failure_code != normalized_failure_code:
        raise ExternalReconciliationConflict(f"reconciliation {normalized_id} failure code changed")
    if any(
        value is not None
        for value in (
            reconciliation.claim_token,
            reconciliation.claimed_at,
            reconciliation.claim_expires_at,
        )
    ):
        raise ExternalReconciliationConflict(f"reconciliation {normalized_id} has an active or corrupt claim")

    phase = reconciliation.quarantined_phase
    if phase == BotExternalStrengthReconciliation.Phase.PROFILE:
        if (
            reconciliation.profile_completed_at is not None
            or reconciliation.population_handoff_completed_at is not None
            or reconciliation.applied_at is not None
            or reconciliation.population_attempt_count != 0
        ):
            raise ExternalReconciliationConflict(
                f"reconciliation {normalized_id} has inconsistent profile-phase progress"
            )
        attempt_count = reconciliation.profile_attempt_count
        pending_status = BotExternalStrengthReconciliation.Status.PENDING_PROFILE
        attempt_field = "profile_attempt_count"
    elif phase == BotExternalStrengthReconciliation.Phase.POPULATION:
        if (
            reconciliation.profile_completed_at is None
            or reconciliation.population_handoff_completed_at is not None
            or reconciliation.applied_at is not None
        ):
            raise ExternalReconciliationConflict(
                f"reconciliation {normalized_id} has inconsistent population-phase progress"
            )
        attempt_count = reconciliation.population_attempt_count
        pending_status = BotExternalStrengthReconciliation.Status.PENDING_POPULATION
        attempt_field = "population_attempt_count"
    else:
        raise ExternalReconciliationConflict(f"reconciliation {normalized_id} has an invalid quarantined phase")
    if attempt_count != normalized_attempt_count:
        raise ExternalReconciliationConflict(
            f"reconciliation {normalized_id} attempt count changed: "
            f"expected {normalized_attempt_count}, found {attempt_count}"
        )

    if apply:
        available_at = _database_utc_now()
        quarantined_at = reconciliation.quarantined_at
        last_error_digest = reconciliation.last_error_digest
        reconciliation.status = pending_status
        setattr(reconciliation, attempt_field, 0)
        reconciliation.available_at = available_at
        reconciliation.claim_token = None
        reconciliation.claimed_at = None
        reconciliation.claim_expires_at = None
        reconciliation.quarantined_at = None
        reconciliation.quarantined_phase = ""
        reconciliation.failure_code = ""
        reconciliation.last_error_digest = ""
        reconciliation.save(
            update_fields=[
                "status",
                attempt_field,
                "available_at",
                "claim_token",
                "claimed_at",
                "claim_expires_at",
                "quarantined_at",
                "quarantined_phase",
                "failure_code",
                "last_error_digest",
                "updated_at",
            ]
        )
        profile_id = reconciliation.profile_id

        def _log_committed_requeue() -> None:
            logger.info(
                "Requeued quarantined virtual-player reconciliation",
                extra={
                    "event": "virtual_player_external_reconciliation_requeued",
                    "reconciliation_id": normalized_id,
                    "profile_id": profile_id,
                    "reconciliation_phase": phase,
                    "expected_failure_code": normalized_failure_code,
                    "expected_attempt_count": normalized_attempt_count,
                    "quarantined_at": quarantined_at,
                    "last_error_digest": last_error_digest,
                    "recovery_basis": normalized_recovery_basis,
                },
            )

        transaction.on_commit(_log_committed_requeue)

    return ReconciliationOperationSummary(
        scanned=1,
        locked=0,
        changed=1,
        skipped=0,
        failed=0,
        reasons=(),
        reconciliation_id=normalized_id,
    )


__all__ = [
    "CLAIM_LOST_STATUS",
    "EXTERNAL_RECONCILIATION_CLAIM_LEASE_SECONDS",
    "EXTERNAL_RECONCILIATION_MAX_ATTEMPTS_PER_PHASE",
    "EXTERNAL_RECONCILIATION_RETRY_INITIAL_SECONDS",
    "EXTERNAL_RECONCILIATION_RETRY_MAX_SECONDS",
    "ExternalReconciliationAnchor",
    "ExternalReconciliationClaim",
    "ExternalReconciliationConflict",
    "ExternalReconciliationError",
    "ExternalReconciliationIntentResult",
    "ExternalReconciliationPermanentError",
    "ExternalReconciliationProcessResult",
    "ExternalReconciliationRetryableError",
    "NO_WORK_STATUS",
    "ReconciliationOperationSummary",
    "capture_external_reconciliation_anchors",
    "claim_external_reconciliation",
    "claim_next_external_reconciliation",
    "create_external_reconciliation_intent",
    "parse_strength_summary",
    "reconcile_claimed_external_reconciliation",
    "reconcile_external_reconciliation",
    "requeue_quarantined_reconciliation_operation",
    "scan_external_reconciliations",
    "serialize_strength_summary",
]
