from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Final

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from gameplay.models import BotArenaShortageBaseline, BotRuntimeRoutingState

from .config import V2_PRESTIGE_BAND_NAMES, MaintenanceMode
from .database_clock import database_utc_now as _database_utc_now
from .random_context import canonical_json_bytes

BASELINE_PAYLOAD_SCHEMA_VERSION: Final = 1
BASELINE_RATIO_QUANTUM: Final = Decimal("0.000000000001")
ARENA_SHORTAGE_BASELINE_MODES: Final = tuple(mode.value for mode in BotArenaShortageBaseline.Mode)
BASELINE_SOURCE_PRE_ACTIVATION: Final = BotArenaShortageBaseline.Source.PRE_ACTIVATION
BASELINE_SOURCE_RUNTIME_BOOTSTRAP: Final = BotArenaShortageBaseline.Source.RUNTIME_BOOTSTRAP
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_PRE_ACTIVATION_MAINTENANCE_MODES: Final = frozenset(
    {
        MaintenanceMode.LEGACY_BEFORE_GATE.value,
        MaintenanceMode.V2_CUTOVER.value,
    }
)
_RUNTIME_BOOTSTRAP_MAINTENANCE_MODES: Final = frozenset(
    {
        MaintenanceMode.V2_ACTIVE.value,
        MaintenanceMode.V2_PAUSED.value,
    }
)


class ArenaShortageBaselineError(ValueError):
    pass


class ArenaShortageBaselineConflict(ArenaShortageBaselineError):
    pass


class ArenaShortageBaselineActivationBlocked(ArenaShortageBaselineError):
    pass


class ArenaShortageBaselineCorrupt(ArenaShortageBaselineError):
    pass


@dataclass(frozen=True, slots=True)
class ArenaShortageBaselineRequest:
    mode: str
    prestige_band: str
    baseline_ratio: Decimal
    evidence_id: str
    evidence_checksum: str
    payload_digest: str


@dataclass(frozen=True, slots=True)
class ArenaShortageBaselineSnapshot:
    mode: str
    prestige_band: str
    baseline_ratio: Decimal
    frozen_at: datetime
    evidence_id: str
    evidence_checksum: str
    payload_digest: str
    source: str = BASELINE_SOURCE_PRE_ACTIVATION
    expires_at: datetime | None = None
    max_real_entry_count: int | None = None


@dataclass(frozen=True, slots=True)
class ArenaShortageBaselineFreezeResult:
    baseline: ArenaShortageBaselineSnapshot
    created: bool


@dataclass(frozen=True, slots=True)
class ArenaShortageBaselineOperationSummary:
    scanned: int
    locked: int
    changed: int
    skipped: int
    failed: int
    reasons: tuple[str, ...]
    mode: str
    prestige_band: str
    baseline_ratio: Decimal
    payload_digest: str


@dataclass(frozen=True, slots=True)
class ArenaShortageBaselineCleanupSummary:
    scanned: int
    expired: int
    deleted: int


def _normalize_mode(value: object) -> str:
    if not isinstance(value, str):
        raise ArenaShortageBaselineError("mode must be tournament or coop")
    try:
        return BotArenaShortageBaseline.Mode(value).value
    except ValueError as exc:
        raise ArenaShortageBaselineError("mode must be tournament or coop") from exc


def _normalize_prestige_band(value: object) -> str:
    if not isinstance(value, str) or value not in V2_PRESTIGE_BAND_NAMES:
        raise ArenaShortageBaselineError("prestige_band must be a configured V2 prestige band")
    return value


def _normalize_ratio(value: object) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (Decimal, int, str)):
        raise ArenaShortageBaselineError("baseline_ratio must be a canonical decimal string, Decimal, or integer")
    try:
        ratio = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ArenaShortageBaselineError("baseline_ratio must be a finite decimal") from exc
    if not ratio.is_finite() or ratio < 0 or ratio > 1:
        raise ArenaShortageBaselineError("baseline_ratio must be between 0 and 1")
    quantized = ratio.quantize(BASELINE_RATIO_QUANTUM)
    if quantized != ratio:
        raise ArenaShortageBaselineError("baseline_ratio may contain at most 12 decimal places")
    return quantized


def _normalize_max_real_entry_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ArenaShortageBaselineError("max_real_entry_count must be a positive integer")
    return value


def _normalize_evidence_id(value: object) -> str:
    if not isinstance(value, str):
        raise ArenaShortageBaselineError("evidence_id must be a canonical identifier")
    normalized = value.strip()
    if _EVIDENCE_ID_PATTERN.fullmatch(normalized) is None:
        raise ArenaShortageBaselineError("evidence_id must be 1-128 canonical ASCII identifier characters")
    return normalized


def _normalize_checksum(value: object) -> str:
    if not isinstance(value, str):
        raise ArenaShortageBaselineError("evidence_checksum must be a SHA-256 checksum")
    normalized = value.strip().lower()
    if _SHA256_PATTERN.fullmatch(normalized) is None:
        raise ArenaShortageBaselineError("evidence_checksum must be a 64-character hexadecimal SHA-256 checksum")
    return normalized


def _canonical_payload(
    *,
    mode: str,
    prestige_band: str,
    baseline_ratio: Decimal,
    evidence_id: str,
    evidence_checksum: str,
) -> dict[str, int | str]:
    return {
        "schema_version": BASELINE_PAYLOAD_SCHEMA_VERSION,
        "mode": mode,
        "prestige_band": prestige_band,
        "baseline_ratio": format(baseline_ratio, ".12f"),
        "evidence_id": evidence_id,
        "evidence_checksum": evidence_checksum,
    }


def _payload_digest(payload: dict[str, int | str]) -> str:
    return sha256(canonical_json_bytes(payload)).hexdigest()


def normalize_arena_shortage_baseline_request(
    *,
    mode: object,
    prestige_band: object,
    baseline_ratio: object,
    evidence_id: object,
    evidence_checksum: object,
) -> ArenaShortageBaselineRequest:
    normalized_mode = _normalize_mode(mode)
    normalized_band = _normalize_prestige_band(prestige_band)
    normalized_ratio = _normalize_ratio(baseline_ratio)
    normalized_evidence_id = _normalize_evidence_id(evidence_id)
    normalized_evidence_checksum = _normalize_checksum(evidence_checksum)
    digest = _payload_digest(
        _canonical_payload(
            mode=normalized_mode,
            prestige_band=normalized_band,
            baseline_ratio=normalized_ratio,
            evidence_id=normalized_evidence_id,
            evidence_checksum=normalized_evidence_checksum,
        )
    )
    return ArenaShortageBaselineRequest(
        mode=normalized_mode,
        prestige_band=normalized_band,
        baseline_ratio=normalized_ratio,
        evidence_id=normalized_evidence_id,
        evidence_checksum=normalized_evidence_checksum,
        payload_digest=digest,
    )


def _request_from_stored(
    baseline: BotArenaShortageBaseline,
) -> ArenaShortageBaselineRequest:
    try:
        request = normalize_arena_shortage_baseline_request(
            mode=baseline.mode,
            prestige_band=baseline.prestige_band,
            baseline_ratio=baseline.baseline_ratio,
            evidence_id=baseline.evidence_id,
            evidence_checksum=baseline.evidence_checksum,
        )
    except ArenaShortageBaselineError as exc:
        raise ArenaShortageBaselineCorrupt(f"stored Arena shortage baseline {baseline.pk} is invalid") from exc
    if baseline.payload_digest != request.payload_digest:
        raise ArenaShortageBaselineCorrupt(f"stored Arena shortage baseline {baseline.pk} payload digest differs")
    if timezone.is_naive(baseline.frozen_at):
        raise ArenaShortageBaselineCorrupt(f"stored Arena shortage baseline {baseline.pk} frozen_at is naive")
    return request


def _snapshot_from_stored(
    baseline: BotArenaShortageBaseline,
) -> ArenaShortageBaselineSnapshot:
    request = _request_from_stored(baseline)
    if baseline.source not in {
        BASELINE_SOURCE_PRE_ACTIVATION,
        BASELINE_SOURCE_RUNTIME_BOOTSTRAP,
    }:
        raise ArenaShortageBaselineCorrupt(f"stored Arena shortage baseline {baseline.pk} source is invalid")
    if baseline.source == BASELINE_SOURCE_PRE_ACTIVATION:
        if baseline.expires_at is not None or baseline.max_real_entry_count is not None:
            raise ArenaShortageBaselineCorrupt(
                f"stored pre-activation baseline {baseline.pk} has runtime lifecycle metadata"
            )
    elif (
        baseline.expires_at is None
        or timezone.is_naive(baseline.expires_at)
        or baseline.expires_at <= baseline.frozen_at
        or baseline.max_real_entry_count is None
        or baseline.max_real_entry_count < 1
    ):
        raise ArenaShortageBaselineCorrupt(f"stored runtime baseline {baseline.pk} has invalid lifecycle metadata")
    return ArenaShortageBaselineSnapshot(
        mode=request.mode,
        prestige_band=request.prestige_band,
        baseline_ratio=request.baseline_ratio,
        frozen_at=baseline.frozen_at.astimezone(UTC),
        evidence_id=request.evidence_id,
        evidence_checksum=request.evidence_checksum,
        payload_digest=request.payload_digest,
        source=baseline.source,
        expires_at=None if baseline.expires_at is None else baseline.expires_at.astimezone(UTC),
        max_real_entry_count=baseline.max_real_entry_count,
    )


def _assert_matching_existing(
    existing: BotArenaShortageBaseline,
    request: ArenaShortageBaselineRequest,
) -> ArenaShortageBaselineSnapshot:
    snapshot = _snapshot_from_stored(existing)
    if (
        snapshot.mode != request.mode
        or snapshot.prestige_band != request.prestige_band
        or snapshot.baseline_ratio != request.baseline_ratio
        or snapshot.evidence_id != request.evidence_id
        or snapshot.evidence_checksum != request.evidence_checksum
        or snapshot.payload_digest != request.payload_digest
    ):
        raise ArenaShortageBaselineConflict(
            "Arena shortage baseline scope already has different frozen content: "
            f"{request.mode}:{request.prestige_band}"
        )
    return snapshot


def _lock_routing_state() -> BotRuntimeRoutingState | None:
    return BotRuntimeRoutingState.objects.select_for_update().filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()


def _assert_pre_activation(state: BotRuntimeRoutingState | None) -> None:
    if state is None:
        raise ArenaShortageBaselineActivationBlocked(
            "persisted virtual-player routing is required before freezing an " "Arena shortage baseline"
        )
    if state.maintenance_mode not in _PRE_ACTIVATION_MAINTENANCE_MODES:
        raise ArenaShortageBaselineActivationBlocked(
            "Arena shortage baseline may only be frozen before Maintenance V2 activation"
        )


def _assert_runtime_bootstrap(state: BotRuntimeRoutingState | None) -> None:
    if state is None:
        raise ArenaShortageBaselineActivationBlocked(
            "persisted virtual-player routing is required before runtime baseline bootstrap"
        )
    if state.maintenance_mode not in _RUNTIME_BOOTSTRAP_MAINTENANCE_MODES:
        raise ArenaShortageBaselineActivationBlocked(
            "runtime Arena shortage baseline bootstrap requires Maintenance V2 to be active or paused"
        )


def _resolve_freeze_request(
    request: ArenaShortageBaselineRequest,
    *,
    apply: bool,
    runtime_bootstrap: bool = False,
    max_real_entry_count: object | None = None,
) -> ArenaShortageBaselineFreezeResult:
    state = _lock_routing_state()
    reference_at = _database_utc_now()
    normalized_max_real_entry_count = (
        None if max_real_entry_count is None else _normalize_max_real_entry_count(max_real_entry_count)
    )
    existing = (
        BotArenaShortageBaseline.objects.select_for_update()
        .filter(mode=request.mode, prestige_band=request.prestige_band)
        .first()
    )
    if existing is not None:
        existing_expires_at = existing.expires_at
        if (
            runtime_bootstrap
            and existing.source == BASELINE_SOURCE_RUNTIME_BOOTSTRAP
            and existing_expires_at is not None
        ):
            if timezone.is_naive(existing_expires_at):
                existing_expires_at = existing_expires_at.replace(tzinfo=UTC)
            if existing_expires_at <= reference_at:
                if not apply:
                    raise ArenaShortageBaselineActivationBlocked(
                        "runtime Arena shortage baseline bootstrap cannot be dry-run"
                    )
                _assert_runtime_bootstrap(state)
                existing.delete()
            else:
                return ArenaShortageBaselineFreezeResult(
                    baseline=_assert_matching_existing(existing, request),
                    created=False,
                )
        else:
            return ArenaShortageBaselineFreezeResult(
                baseline=_assert_matching_existing(existing, request),
                created=False,
            )
    if runtime_bootstrap:
        if not apply:
            raise ArenaShortageBaselineActivationBlocked("runtime Arena shortage baseline bootstrap cannot be dry-run")
        _assert_runtime_bootstrap(state)
    else:
        _assert_pre_activation(state)
    if not apply:
        return ArenaShortageBaselineFreezeResult(
            baseline=ArenaShortageBaselineSnapshot(
                mode=request.mode,
                prestige_band=request.prestige_band,
                baseline_ratio=request.baseline_ratio,
                frozen_at=reference_at,
                evidence_id=request.evidence_id,
                evidence_checksum=request.evidence_checksum,
                payload_digest=request.payload_digest,
                source=(BASELINE_SOURCE_RUNTIME_BOOTSTRAP if runtime_bootstrap else BASELINE_SOURCE_PRE_ACTIVATION),
                expires_at=(
                    reference_at + timedelta(hours=int(settings.ARENA_SHORTAGE_BASELINE_BOOTSTRAP_TTL_HOURS))
                    if runtime_bootstrap
                    else None
                ),
                max_real_entry_count=normalized_max_real_entry_count,
            ),
            created=True,
        )

    try:
        with transaction.atomic():
            frozen_at = reference_at
            baseline = BotArenaShortageBaseline.objects.create(
                mode=request.mode,
                prestige_band=request.prestige_band,
                baseline_ratio=request.baseline_ratio,
                source=(BASELINE_SOURCE_RUNTIME_BOOTSTRAP if runtime_bootstrap else BASELINE_SOURCE_PRE_ACTIVATION),
                expires_at=(
                    frozen_at + timedelta(hours=int(settings.ARENA_SHORTAGE_BASELINE_BOOTSTRAP_TTL_HOURS))
                    if runtime_bootstrap
                    else None
                ),
                max_real_entry_count=normalized_max_real_entry_count,
                frozen_at=frozen_at,
                evidence_id=request.evidence_id,
                evidence_checksum=request.evidence_checksum,
                payload_digest=request.payload_digest,
            )
    except IntegrityError as exc:
        existing = (
            BotArenaShortageBaseline.objects.select_for_update()
            .filter(mode=request.mode, prestige_band=request.prestige_band)
            .first()
        )
        if existing is None:
            raise ArenaShortageBaselineConflict("Arena shortage baseline could not be frozen atomically") from exc
        return ArenaShortageBaselineFreezeResult(
            baseline=_assert_matching_existing(existing, request),
            created=False,
        )
    return ArenaShortageBaselineFreezeResult(
        baseline=_snapshot_from_stored(baseline),
        created=True,
    )


@transaction.atomic
def freeze_arena_shortage_baseline(
    *,
    mode: object,
    prestige_band: object,
    baseline_ratio: object,
    evidence_id: object,
    evidence_checksum: object,
) -> ArenaShortageBaselineFreezeResult:
    request = normalize_arena_shortage_baseline_request(
        mode=mode,
        prestige_band=prestige_band,
        baseline_ratio=baseline_ratio,
        evidence_id=evidence_id,
        evidence_checksum=evidence_checksum,
    )
    return _resolve_freeze_request(request, apply=True)


@transaction.atomic
def freeze_arena_shortage_baseline_operation(
    *,
    mode: object,
    prestige_band: object,
    baseline_ratio: object,
    evidence_id: object,
    evidence_checksum: object,
    apply: bool = False,
) -> ArenaShortageBaselineOperationSummary:
    request = normalize_arena_shortage_baseline_request(
        mode=mode,
        prestige_band=prestige_band,
        baseline_ratio=baseline_ratio,
        evidence_id=evidence_id,
        evidence_checksum=evidence_checksum,
    )
    result = _resolve_freeze_request(request, apply=bool(apply))
    return ArenaShortageBaselineOperationSummary(
        scanned=1,
        locked=1,
        changed=int(result.created),
        skipped=int(not result.created),
        failed=0,
        reasons=() if result.created else ("already_frozen",),
        mode=result.baseline.mode,
        prestige_band=result.baseline.prestige_band,
        baseline_ratio=result.baseline.baseline_ratio,
        payload_digest=result.baseline.payload_digest,
    )


@transaction.atomic
def freeze_arena_shortage_baseline_runtime(
    *,
    mode: object,
    prestige_band: object,
    baseline_ratio: object,
    evidence_id: object,
    evidence_checksum: object,
    max_real_entry_count: object,
) -> ArenaShortageBaselineFreezeResult:
    """Freeze a validated runtime bootstrap baseline, replacing only expired runtime rows."""
    request = normalize_arena_shortage_baseline_request(
        mode=mode,
        prestige_band=prestige_band,
        baseline_ratio=baseline_ratio,
        evidence_id=evidence_id,
        evidence_checksum=evidence_checksum,
    )
    return _resolve_freeze_request(
        request,
        apply=True,
        runtime_bootstrap=True,
        max_real_entry_count=max_real_entry_count,
    )


def lock_frozen_arena_shortage_baselines(
    *,
    now: datetime | None = None,
) -> tuple[ArenaShortageBaselineSnapshot, ...]:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("lock_frozen_arena_shortage_baselines must be called inside transaction.atomic()")
    if now is None:
        reference_at = _database_utc_now()
    elif timezone.is_naive(now):
        raise ArenaShortageBaselineError("baseline reference time must be timezone-aware")
    else:
        reference_at = now.astimezone(UTC)
    _lock_routing_state()
    rows = tuple(
        BotArenaShortageBaseline.objects.select_for_update()
        .filter(
            Q(source=BASELINE_SOURCE_PRE_ACTIVATION)
            | Q(source=BASELINE_SOURCE_RUNTIME_BOOTSTRAP, expires_at__gt=reference_at)
        )
        .order_by("mode", "prestige_band", "id")
    )
    return tuple(_snapshot_from_stored(row) for row in rows)


@transaction.atomic
def cleanup_expired_arena_shortage_baselines(
    *,
    before: datetime | None = None,
    limit: int = 1000,
    apply: bool = False,
) -> ArenaShortageBaselineCleanupSummary:
    """Delete expired runtime baselines so a fresh bootstrap can be attempted."""
    _lock_routing_state()
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ArenaShortageBaselineError("limit must be an integer between 1 and 10000")
    if before is None:
        reference_at = _database_utc_now()
    elif timezone.is_naive(before):
        raise ArenaShortageBaselineError("baseline expiry reference time must be timezone-aware")
    else:
        reference_at = before.astimezone(UTC)
    expired = BotArenaShortageBaseline.objects.filter(
        source=BASELINE_SOURCE_RUNTIME_BOOTSTRAP,
        expires_at__isnull=False,
        expires_at__lte=reference_at,
    ).order_by("expires_at", "id")
    expired_count = expired.count()
    if not apply:
        return ArenaShortageBaselineCleanupSummary(
            scanned=expired_count,
            expired=expired_count,
            deleted=0,
        )
    expired_ids = list(expired[:limit].values_list("pk", flat=True))
    deleted_count = BotArenaShortageBaseline.objects.filter(pk__in=expired_ids).delete()[0]
    return ArenaShortageBaselineCleanupSummary(
        scanned=expired_count,
        expired=expired_count,
        deleted=deleted_count,
    )


__all__ = [
    "ARENA_SHORTAGE_BASELINE_MODES",
    "BASELINE_PAYLOAD_SCHEMA_VERSION",
    "BASELINE_RATIO_QUANTUM",
    "BASELINE_SOURCE_PRE_ACTIVATION",
    "BASELINE_SOURCE_RUNTIME_BOOTSTRAP",
    "ArenaShortageBaselineActivationBlocked",
    "ArenaShortageBaselineConflict",
    "ArenaShortageBaselineCorrupt",
    "ArenaShortageBaselineCleanupSummary",
    "ArenaShortageBaselineError",
    "ArenaShortageBaselineFreezeResult",
    "ArenaShortageBaselineOperationSummary",
    "ArenaShortageBaselineRequest",
    "ArenaShortageBaselineSnapshot",
    "cleanup_expired_arena_shortage_baselines",
    "freeze_arena_shortage_baseline",
    "freeze_arena_shortage_baseline_operation",
    "freeze_arena_shortage_baseline_runtime",
    "lock_frozen_arena_shortage_baselines",
    "normalize_arena_shortage_baseline_request",
]
