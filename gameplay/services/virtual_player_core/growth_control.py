"""Daily aggregate-only growth control for the single V2 policy.

This module is intentionally separate from maintenance planning.  The task
that refreshes the rows may read active real-player Manors once per day; an
action only reads the durable aggregate row and never queries a real player's
live roster as a balancing dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from types import MappingProxyType
from typing import Any, Iterable, Mapping, TypedDict
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from gameplay.models import (
    Manor,
    VirtualPlayerGrowthControlPointer,
    VirtualPlayerGrowthControlRun,
    VirtualPlayerGrowthControlSnapshot,
)

from .config import VirtualPlayerConfigError, load_virtual_player_v2_config
from .projection import (
    CANONICAL_STRENGTH_COMPONENTS,
    ReferenceCandidate,
    ReferenceSelection,
    StrengthSummary,
    select_reference,
)
from .reference_snapshots import (
    ReferenceSnapshotError,
    load_manor_strength_summaries,
    policy_starter_snapshot,
    starter_snapshot_strength,
)

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CONTROL_POLICY_VERSION = 2
FIXED_DEFAULT_CONTROL_DIGEST = hashlib.sha256(b"virtual-player-policy-v2-fixed-growth-control").hexdigest()


class GrowthControlRefreshResult(TypedDict):
    control_date: str
    run_digest: str
    sample_count: int
    cell_count: int
    fallback_count: int


@dataclass(frozen=True, slots=True)
class GrowthControlPolicy:
    minimum_sample_count: int = 5
    smoothing_alpha: float = 0.35
    maximum_daily_delta_bps: int = 500
    active_sample_days: int = 30
    ttl_days: int = 2

    def __post_init__(self) -> None:
        for field_name in (
            "minimum_sample_count",
            "maximum_daily_delta_bps",
            "active_sample_days",
            "ttl_days",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")
        if self.minimum_sample_count <= 0 or self.active_sample_days <= 0:
            raise ValueError("sample and active windows must be positive")
        if isinstance(self.smoothing_alpha, bool) or not isinstance(self.smoothing_alpha, (int, float)):
            raise ValueError("smoothing_alpha must be a finite number")
        if not math.isfinite(float(self.smoothing_alpha)) or not 0 < float(self.smoothing_alpha) <= 1:
            raise ValueError("smoothing_alpha must be in (0, 1]")
        if self.maximum_daily_delta_bps < 0:
            raise ValueError("maximum_daily_delta_bps must be non-negative")
        if self.ttl_days <= 0:
            raise ValueError("ttl_days must be positive")


CONTROL_TTL = timedelta(days=GrowthControlPolicy().ttl_days)


def configured_growth_control_policy() -> GrowthControlPolicy:
    """Load bounded daily-control parameters from the typed V2 config."""

    config = load_virtual_player_v2_config()
    raw = {} if config is None else config.growth_control
    return GrowthControlPolicy(
        minimum_sample_count=int(raw.get("minimum_sample_count", 5)),
        smoothing_alpha=float(raw.get("smoothing_alpha", 0.35)),
        maximum_daily_delta_bps=int(raw.get("maximum_daily_delta_bps", 500)),
        active_sample_days=int(raw.get("active_sample_days", 30)),
        ttl_days=int(raw.get("ttl_days", 2)),
    )


@dataclass(frozen=True, slots=True)
class RealPlayerGrowthSample:
    region: str
    prestige_band: str
    strength: int
    components: Mapping[str, int] = MappingProxyType({})
    growth_24h_bps: int = 0
    growth_7d_bps: int = 0

    def __post_init__(self) -> None:
        if not str(self.region).strip() or not str(self.prestige_band).strip():
            raise ValueError("real-player control samples require region and prestige_band")
        if isinstance(self.strength, bool) or not isinstance(self.strength, int) or self.strength < 0:
            raise ValueError("strength must be a non-negative integer")
        for field_name in ("growth_24h_bps", "growth_7d_bps"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")
        object.__setattr__(self, "region", str(self.region).strip())
        object.__setattr__(self, "prestige_band", str(self.prestige_band).strip())
        object.__setattr__(
            self, "components", MappingProxyType({str(k): max(0, int(v)) for k, v in self.components.items()})
        )


@dataclass(frozen=True, slots=True)
class GrowthControlAggregate:
    region: str
    prestige_band: str
    sample_count: int
    strength_p50: int
    strength_p75: int
    growth_24h_bps: int
    growth_7d_bps: int
    component_statistics: Mapping[str, Mapping[str, int]]
    is_fallback: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "prestige_band": self.prestige_band,
            "sample_count": self.sample_count,
            "strength_p50": self.strength_p50,
            "strength_p75": self.strength_p75,
            "growth_24h_bps": self.growth_24h_bps,
            "growth_7d_bps": self.growth_7d_bps,
            "component_statistics": {key: dict(value) for key, value in sorted(self.component_statistics.items())},
            "is_fallback": self.is_fallback,
        }


def _nearest_rank(values: Iterable[int], quantile: float) -> int:
    ordered = sorted(max(0, int(value)) for value in values)
    if not ordered:
        return 0
    index = max(0, min(len(ordered) - 1, int(len(ordered) * quantile + 0.999999) - 1))
    return ordered[index]


def _smooth_growth(observed: int, previous: int | None, policy: GrowthControlPolicy) -> int:
    observed_value = int(observed)
    if previous is None:
        return max(-policy.maximum_daily_delta_bps, min(policy.maximum_daily_delta_bps, observed_value))
    blended = int(round(previous + (observed_value - previous) * policy.smoothing_alpha))
    lower = previous - policy.maximum_daily_delta_bps
    upper = previous + policy.maximum_daily_delta_bps
    return max(lower, min(upper, blended))


def aggregate_growth_control_samples(
    samples: Iterable[RealPlayerGrowthSample],
    *,
    policy: GrowthControlPolicy = GrowthControlPolicy(),
    previous: Mapping[tuple[str, str], GrowthControlAggregate] | None = None,
) -> dict[tuple[str, str], GrowthControlAggregate]:
    grouped: dict[tuple[str, str], list[RealPlayerGrowthSample]] = defaultdict(list)
    for sample in samples:
        grouped[(sample.region, sample.prestige_band)].append(sample)
    previous = previous or {}
    result: dict[tuple[str, str], GrowthControlAggregate] = {}
    for cell, rows in sorted(grouped.items()):
        region, band = cell
        old = previous.get(cell)
        count = len(rows)
        fallback = count < policy.minimum_sample_count
        if fallback and old is not None:
            result[cell] = GrowthControlAggregate(
                region=region,
                prestige_band=band,
                sample_count=count,
                strength_p50=old.strength_p50,
                strength_p75=old.strength_p75,
                growth_24h_bps=old.growth_24h_bps,
                growth_7d_bps=old.growth_7d_bps,
                component_statistics=old.component_statistics,
                is_fallback=True,
            )
            continue
        strengths = [sample.strength for sample in rows]
        component_names = sorted({name for sample in rows for name in sample.components})
        component_stats = {
            name: {
                "p50": _nearest_rank(
                    [sample.components.get(name, 0) for sample in rows],
                    0.50,
                ),
                "p75": _nearest_rank(
                    [sample.components.get(name, 0) for sample in rows],
                    0.75,
                ),
            }
            for name in component_names
        }
        observed_24h = int(median([sample.growth_24h_bps for sample in rows])) if rows else 0
        observed_7d = int(median([sample.growth_7d_bps for sample in rows])) if rows else 0
        result[cell] = GrowthControlAggregate(
            region=region,
            prestige_band=band,
            sample_count=count,
            strength_p50=_nearest_rank(strengths, 0.50),
            strength_p75=_nearest_rank(strengths, 0.75),
            growth_24h_bps=_smooth_growth(
                observed_24h,
                None if old is None else old.growth_24h_bps,
                policy,
            ),
            growth_7d_bps=_smooth_growth(
                observed_7d,
                None if old is None else old.growth_7d_bps,
                policy,
            ),
            component_statistics=component_stats,
            is_fallback=fallback,
        )
    return result


def _policy_checksum() -> str:
    config = load_virtual_player_v2_config()
    if config is None:
        raise VirtualPlayerConfigError("growth control requires the configured V2 policy")
    checksum = str(config.policy(CONTROL_POLICY_VERSION).checksum).strip()
    if len(checksum) != 64:
        raise VirtualPlayerConfigError("growth control requires a SHA-256 V2 policy checksum")
    try:
        bytes.fromhex(checksum)
    except ValueError as exc:
        raise VirtualPlayerConfigError("growth control requires a hexadecimal V2 policy checksum") from exc
    return checksum


def _control_date(now: datetime) -> date:
    return now.astimezone(SHANGHAI_TZ).date()


def _snapshot_digest(
    aggregate: GrowthControlAggregate,
    *,
    control_date: date,
    policy_checksum: str,
) -> str:
    payload = {
        "control_date": control_date.isoformat(),
        "policy_checksum": policy_checksum,
        **aggregate.to_payload(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_digest(
    *,
    control_date: date,
    policy_checksum: str,
    source_sample_count: int,
    aggregates: Mapping[tuple[str, str], GrowthControlAggregate],
) -> str:
    payload = {
        "control_date": control_date.isoformat(),
        "policy_version": CONTROL_POLICY_VERSION,
        "policy_checksum": policy_checksum,
        "source_sample_count": int(source_sample_count),
        "aggregates": [
            aggregate.to_payload() for _cell, aggregate in sorted(aggregates.items(), key=lambda item: item[0])
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _start_growth_control_run(
    *,
    control_date: date,
    policy_checksum: str,
    source_sample_count: int,
    cell_count: int,
    fallback_count: int,
    run_digest: str,
    now: datetime,
) -> VirtualPlayerGrowthControlRun:
    run_key = f"growth-control-{control_date.isoformat()}-{run_digest[:32]}"
    with transaction.atomic():
        run, _created = VirtualPlayerGrowthControlRun.objects.get_or_create(
            run_digest=run_digest,
            defaults={
                "run_key": run_key,
                "control_date": control_date,
                "policy_version": CONTROL_POLICY_VERSION,
                "policy_checksum": policy_checksum,
                "source_sample_count": int(source_sample_count),
                "cell_count": int(cell_count),
                "fallback_count": int(fallback_count),
                "status": VirtualPlayerGrowthControlRun.Status.RUNNING,
                "started_at": now,
            },
        )
        run = VirtualPlayerGrowthControlRun.objects.select_for_update().get(pk=run.pk)
        if run.status != VirtualPlayerGrowthControlRun.Status.COMPLETE:
            run.run_key = run_key
            run.control_date = control_date
            run.policy_version = CONTROL_POLICY_VERSION
            run.policy_checksum = policy_checksum
            run.source_sample_count = int(source_sample_count)
            run.cell_count = int(cell_count)
            run.fallback_count = int(fallback_count)
            run.status = VirtualPlayerGrowthControlRun.Status.RUNNING
            run.failure_digest = ""
            run.failure_reason = ""
            run.started_at = now
            run.completed_at = None
            run.failed_at = None
            run.save(
                update_fields=[
                    "run_key",
                    "control_date",
                    "policy_version",
                    "policy_checksum",
                    "source_sample_count",
                    "cell_count",
                    "fallback_count",
                    "status",
                    "failure_digest",
                    "failure_reason",
                    "started_at",
                    "completed_at",
                    "failed_at",
                    "updated_at",
                ]
            )
        return run


def _mark_growth_control_run_failed(*, run_digest: str, error: BaseException, now: datetime) -> None:
    reason = f"{type(error).__name__}: {str(error)[:480]}"
    failure_digest = hashlib.sha256(reason.encode("utf-8", "replace")).hexdigest()
    try:
        with transaction.atomic():
            run = VirtualPlayerGrowthControlRun.objects.select_for_update().filter(run_digest=run_digest).first()
            if run is None or run.status == VirtualPlayerGrowthControlRun.Status.COMPLETE:
                return
            run.status = VirtualPlayerGrowthControlRun.Status.FAILED
            run.failure_digest = failure_digest
            run.failure_reason = reason
            run.failed_at = now
            run.completed_at = None
            run.save(
                update_fields=["status", "failure_digest", "failure_reason", "failed_at", "completed_at", "updated_at"]
            )
    except Exception:
        logger.exception("Unable to persist failed virtual-player growth-control run")


def _advance_growth_control_pointer_locked(run: VirtualPlayerGrowthControlRun) -> None:
    pointer, _created = VirtualPlayerGrowthControlPointer.objects.select_for_update().get_or_create(
        key=VirtualPlayerGrowthControlPointer.GLOBAL_KEY,
    )
    current = None
    if pointer.current_run_id is not None:
        current = VirtualPlayerGrowthControlRun.objects.filter(pk=pointer.current_run_id).first()
    if current is not None and current.control_date > run.control_date:
        return
    if pointer.current_run_id == run.id:
        return
    pointer.current_run = run
    pointer.save(update_fields=["current_run", "updated_at"])


def _write_growth_control_snapshots_locked(
    *,
    run: VirtualPlayerGrowthControlRun,
    control_date: date,
    policy: GrowthControlPolicy,
    aggregates: Mapping[tuple[str, str], GrowthControlAggregate],
    now: datetime,
) -> None:
    effective_until = now + timedelta(days=policy.ttl_days)
    expected: list[VirtualPlayerGrowthControlSnapshot] = []
    for (region, band), aggregate in sorted(aggregates.items()):
        expected.append(
            VirtualPlayerGrowthControlSnapshot(
                run=run,
                control_date=control_date,
                region=region,
                prestige_band=band,
                policy_version=CONTROL_POLICY_VERSION,
                policy_checksum=run.policy_checksum,
                sample_count=aggregate.sample_count,
                strength_p50=aggregate.strength_p50,
                strength_p75=aggregate.strength_p75,
                growth_24h_bps=aggregate.growth_24h_bps,
                growth_7d_bps=aggregate.growth_7d_bps,
                component_statistics=aggregate.to_payload()["component_statistics"],
                effective_until=effective_until,
                is_fallback=aggregate.is_fallback,
                snapshot_digest=_snapshot_digest(
                    aggregate,
                    control_date=control_date,
                    policy_checksum=run.policy_checksum,
                ),
            )
        )
    expected_by_digest = {snapshot.snapshot_digest: snapshot for snapshot in expected}
    existing = {
        row.snapshot_digest: row
        for row in VirtualPlayerGrowthControlSnapshot.objects.select_for_update().filter(
            snapshot_digest__in=tuple(expected_by_digest)
        )
    }
    to_create: list[VirtualPlayerGrowthControlSnapshot] = []
    to_attach: list[VirtualPlayerGrowthControlSnapshot] = []
    for digest, snapshot in expected_by_digest.items():
        row = existing.get(digest)
        if row is None:
            to_create.append(snapshot)
            continue
        if row.run_id not in (None, run.id):
            raise RuntimeError("growth-control snapshot digest belongs to another run")
        if row.run_id is None:
            row.run = run
            to_attach.append(row)
    if to_create:
        VirtualPlayerGrowthControlSnapshot.objects.bulk_create(to_create)
    if to_attach:
        VirtualPlayerGrowthControlSnapshot.objects.bulk_update(to_attach, ["run"])
    persisted_count = VirtualPlayerGrowthControlSnapshot.objects.filter(run_id=run.id).count()
    if persisted_count != len(expected):
        raise RuntimeError("growth-control run did not persist every aggregate cell")


def collect_real_player_growth_samples(
    *,
    now: datetime | None = None,
    policy: GrowthControlPolicy = GrowthControlPolicy(),
) -> tuple[RealPlayerGrowthSample, ...]:
    """Read active real players once for the daily aggregate task.

    The returned objects contain no user/manor identifier.  Growth amplitudes
    are filled by ``refresh_growth_control_snapshots`` from prior aggregate
    rows; this collection boundary only reads the current anonymous cohort.
    """

    current_time = now or timezone.now()
    manors = tuple(
        Manor.objects.filter(
            bot_profile__isnull=True,
            user__is_staff=False,
            user__is_superuser=False,
            last_active_at__gte=current_time - timedelta(days=policy.active_sample_days),
        )
        .only("id", "region", "prestige")
        .order_by("id")
    )
    if not manors:
        return ()
    strengths = load_manor_strength_summaries(manor_ids=tuple(int(manor.id) for manor in manors))
    v2_config = load_virtual_player_v2_config()
    legacy_config: dict[str, Any] | None = None
    if v2_config is None:
        from .config import load_virtual_player_config

        legacy_config = load_virtual_player_config()
    samples: list[RealPlayerGrowthSample] = []
    for manor in manors:
        band: str | None
        if v2_config is not None:
            band = v2_config.band_for_prestige(int(manor.prestige or 0)).name
        else:
            from .selectors import prestige_band_for_value

            band = prestige_band_for_value(int(manor.prestige or 0), legacy_config or {})
        if band is None:
            continue
        strength = strengths.get(int(manor.id))
        if strength is None:
            continue
        samples.append(
            RealPlayerGrowthSample(
                region=str(manor.region),
                prestige_band=str(band),
                strength=max(0, int(round(strength.composite))),
                components={key: int(round(value)) for key, value in strength.components.items()},
            )
        )
    return tuple(samples)


def _previous_aggregates(
    *,
    region_band_pairs: Iterable[tuple[str, str]],
    now: datetime,
) -> dict[tuple[str, str], GrowthControlAggregate]:
    result: dict[tuple[str, str], GrowthControlAggregate] = {}
    for region, band in region_band_pairs:
        row = (
            VirtualPlayerGrowthControlSnapshot.objects.filter(
                region=region,
                prestige_band=band,
                control_date__lt=_control_date(now),
                effective_until__gt=now,
                run__status=VirtualPlayerGrowthControlRun.Status.COMPLETE,
            )
            .order_by("-control_date", "-id")
            .first()
        )
        if row is None:
            continue
        result[(region, band)] = GrowthControlAggregate(
            region=region,
            prestige_band=band,
            sample_count=int(row.sample_count),
            strength_p50=int(row.strength_p50),
            strength_p75=int(row.strength_p75),
            growth_24h_bps=int(row.growth_24h_bps),
            growth_7d_bps=int(row.growth_7d_bps),
            component_statistics=row.component_statistics or {},
            is_fallback=bool(row.is_fallback),
        )
    return result


def _historical_aggregate(
    *,
    region: str,
    prestige_band: str,
    before: date,
) -> GrowthControlAggregate | None:
    row = (
        VirtualPlayerGrowthControlSnapshot.objects.filter(
            region=str(region),
            prestige_band=str(prestige_band),
            control_date__lt=before,
            run__status=VirtualPlayerGrowthControlRun.Status.COMPLETE,
        )
        .order_by("-control_date", "-id")
        .first()
    )
    if row is None:
        return None
    return GrowthControlAggregate(
        region=str(row.region),
        prestige_band=str(row.prestige_band),
        sample_count=int(row.sample_count),
        strength_p50=int(row.strength_p50),
        strength_p75=int(row.strength_p75),
        growth_24h_bps=int(row.growth_24h_bps),
        growth_7d_bps=int(row.growth_7d_bps),
        component_statistics=row.component_statistics or {},
        is_fallback=bool(row.is_fallback),
    )


def _with_derived_growth_metrics(
    samples: Iterable[RealPlayerGrowthSample],
    *,
    previous: Mapping[tuple[str, str], GrowthControlAggregate],
    weekly_previous: Mapping[tuple[str, str], GrowthControlAggregate],
) -> tuple[RealPlayerGrowthSample, ...]:
    """Derive cohort-level growth without retaining real-player identities."""

    result: list[RealPlayerGrowthSample] = []
    for sample in samples:
        prior = previous.get((sample.region, sample.prestige_band))
        weekly = weekly_previous.get((sample.region, sample.prestige_band))
        growth_24h = 0
        growth_7d = 0
        if prior is not None and int(prior.strength_p50) > 0:
            growth_24h = round((int(sample.strength) - int(prior.strength_p50)) * 10_000 / int(prior.strength_p50))
        if weekly is not None and int(weekly.strength_p50) > 0:
            growth_7d = round((int(sample.strength) - int(weekly.strength_p50)) * 10_000 / int(weekly.strength_p50))
        result.append(
            RealPlayerGrowthSample(
                region=sample.region,
                prestige_band=sample.prestige_band,
                strength=sample.strength,
                components=sample.components,
                growth_24h_bps=int(growth_24h),
                growth_7d_bps=int(growth_7d),
            )
        )
    return tuple(result)


def refresh_growth_control_snapshots(
    *,
    now: datetime | None = None,
    samples: Iterable[RealPlayerGrowthSample] | None = None,
    policy: GrowthControlPolicy | None = None,
) -> GrowthControlRefreshResult:
    """Write one complete Shanghai-date aggregate run and then publish it."""

    current_time = now or timezone.now()
    policy = configured_growth_control_policy() if policy is None else policy
    control_date = _control_date(current_time)
    checksum = _policy_checksum()
    collected_samples = tuple(
        collect_real_player_growth_samples(now=current_time, policy=policy) if samples is None else samples
    )
    cells = {(sample.region, sample.prestige_band) for sample in collected_samples}
    previous = _previous_aggregates(region_band_pairs=cells, now=current_time)
    weekly_previous = {
        cell: aggregate
        for cell in cells
        if (
            aggregate := _historical_aggregate(
                region=cell[0],
                prestige_band=cell[1],
                before=control_date - timedelta(days=6),
            )
        )
        is not None
    }
    source_samples = _with_derived_growth_metrics(
        collected_samples,
        previous=previous,
        weekly_previous=weekly_previous,
    )
    aggregates = aggregate_growth_control_samples(source_samples, policy=policy, previous=previous)
    sample_count = len(source_samples)
    cell_count = len(aggregates)
    fallback_count = sum(int(aggregate.is_fallback) for aggregate in aggregates.values())
    run_digest = _run_digest(
        control_date=control_date,
        policy_checksum=checksum,
        source_sample_count=sample_count,
        aggregates=aggregates,
    )
    run = _start_growth_control_run(
        control_date=control_date,
        policy_checksum=checksum,
        source_sample_count=sample_count,
        cell_count=cell_count,
        fallback_count=fallback_count,
        run_digest=run_digest,
        now=current_time,
    )
    try:
        with transaction.atomic():
            run = VirtualPlayerGrowthControlRun.objects.select_for_update().get(pk=run.pk)
            if run.status != VirtualPlayerGrowthControlRun.Status.COMPLETE:
                _write_growth_control_snapshots_locked(
                    run=run,
                    control_date=control_date,
                    policy=policy,
                    aggregates=aggregates,
                    now=current_time,
                )
                run.status = VirtualPlayerGrowthControlRun.Status.COMPLETE
                run.source_sample_count = sample_count
                run.cell_count = cell_count
                run.fallback_count = fallback_count
                run.completed_at = current_time
                run.failed_at = None
                run.failure_digest = ""
                run.failure_reason = ""
                run.save(
                    update_fields=[
                        "status",
                        "source_sample_count",
                        "cell_count",
                        "fallback_count",
                        "completed_at",
                        "failed_at",
                        "failure_digest",
                        "failure_reason",
                        "updated_at",
                    ]
                )
            _advance_growth_control_pointer_locked(run)
    except Exception as exc:
        _mark_growth_control_run_failed(run_digest=run_digest, error=exc, now=current_time)
        raise
    return {
        "control_date": control_date.isoformat(),
        "run_digest": run_digest,
        "sample_count": sample_count,
        "cell_count": cell_count,
        "fallback_count": fallback_count,
    }


def effective_growth_control_snapshot(
    *,
    region: str,
    prestige_band: str,
    now: datetime | None = None,
) -> VirtualPlayerGrowthControlSnapshot | None:
    current_time = now or timezone.now()
    pointer = (
        VirtualPlayerGrowthControlPointer.objects.select_related("current_run")
        .filter(key=VirtualPlayerGrowthControlPointer.GLOBAL_KEY)
        .first()
    )
    if pointer is None or pointer.current_run is None:
        return None
    if pointer.current_run.status != VirtualPlayerGrowthControlRun.Status.COMPLETE:
        return None
    return (
        VirtualPlayerGrowthControlSnapshot.objects.filter(
            run_id=pointer.current_run_id,
            region=str(region),
            prestige_band=str(prestige_band),
            effective_until__gt=current_time,
            run__status=VirtualPlayerGrowthControlRun.Status.COMPLETE,
        )
        .order_by("-control_date", "-id")
        .first()
    )


def _usable_growth_control_snapshot(
    snapshot: VirtualPlayerGrowthControlSnapshot | None,
) -> VirtualPlayerGrowthControlSnapshot | None:
    if (
        snapshot is None
        or bool(snapshot.is_fallback)
        or int(snapshot.sample_count) <= 0
        or int(snapshot.strength_p75) <= 0
    ):
        return None
    return snapshot


def growth_control_digest_for_route(
    *,
    region: str,
    prestige_band: str,
    now: datetime | None = None,
) -> str:
    """Return the digest a new V2 operation must freeze for this route."""

    snapshot = _usable_growth_control_snapshot(
        effective_growth_control_snapshot(
            region=str(region),
            prestige_band=str(prestige_band),
            now=now,
        )
    )
    return FIXED_DEFAULT_CONTROL_DIGEST if snapshot is None else str(snapshot.snapshot_digest)


def growth_control_reference_selection(
    *,
    manor_strength: StrengthSummary,
    context,
    region: str,
    prestige_band: str,
    now: datetime | None = None,
    expected_digest: str | None = None,
) -> tuple[int, ReferenceSelection, str]:
    """Resolve the V2 cap from one durable aggregate row.

    The resolver deliberately has no calibration-route or live-real-player
    fallback.  A current row becomes a one-anchor local cohort so the
    existing sample-tier safety rules still apply; when no row is available,
    the current Manor summary is used only as a conservative fixed fallback.
    ``digest`` is returned separately so callers can freeze it in an
    operation/receipt payload without making the snapshot itself mutable.
    """

    if not isinstance(manor_strength, StrengthSummary):
        raise TypeError("manor_strength must be a StrengthSummary")
    current_time = now or timezone.now()
    normalized_expected_digest = str(expected_digest or "").strip()
    if normalized_expected_digest:
        if len(normalized_expected_digest) != 64:
            raise ReferenceSnapshotError("frozen growth-control digest must be a SHA-256 digest")
        try:
            bytes.fromhex(normalized_expected_digest)
        except ValueError as exc:
            raise ReferenceSnapshotError("frozen growth-control digest must be hexadecimal") from exc
        if normalized_expected_digest == FIXED_DEFAULT_CONTROL_DIGEST:
            snapshot = None
        else:
            snapshot = (
                VirtualPlayerGrowthControlSnapshot.objects.filter(
                    region=str(region),
                    prestige_band=str(prestige_band),
                    snapshot_digest=normalized_expected_digest,
                    run__status=VirtualPlayerGrowthControlRun.Status.COMPLETE,
                )
                .order_by("-control_date", "-id")
                .first()
            )
            if _usable_growth_control_snapshot(snapshot) is None:
                raise ReferenceSnapshotError("frozen growth-control snapshot is missing or no longer valid")
    else:
        snapshot = effective_growth_control_snapshot(
            region=str(region),
            prestige_band=str(prestige_band),
            now=current_time,
        )
    snapshot = _usable_growth_control_snapshot(snapshot)
    if snapshot is not None:
        component_values: dict[str, float] = {}
        component_statistics = snapshot.component_statistics or {}
        for key in CANONICAL_STRENGTH_COMPONENTS:
            raw_stats = component_statistics.get(key, {})
            fallback = float(manor_strength.components.get(key, 0))
            raw_value = raw_stats.get("p75", fallback) if isinstance(raw_stats, Mapping) else fallback
            try:
                component_values[key] = max(0.0, float(raw_value))
            except (TypeError, ValueError):
                component_values[key] = fallback
        candidate = ReferenceCandidate(
            business_key=(f"growth-control:{snapshot.control_date.isoformat()}:{snapshot.snapshot_digest[:16]}"),
            prestige_band=str(prestige_band),
            strength=StrengthSummary(
                composite=max(0.0, float(snapshot.strength_p75)),
                components=component_values,
            ),
            features={
                "prestige": float(component_values.get("prestige", 0)),
                "core_building_level": float(component_values.get("core_building_level", 0)),
                "guest_count": float(component_values.get("guest_count", 0)),
                "max_guest_level": float(component_values.get("max_guest_level", 0)),
                "arena_lineup_power": float(component_values.get("arena_lineup_power", 0)),
                "troop_total": float(component_values.get("troop_total", 0)),
            },
        )
        selection = select_reference(
            context=context,
            prestige_band=str(prestige_band),
            target_features=candidate.features,
            starter_strength=manor_strength,
            local_candidates=(candidate,),
            local_sample_count=max(1, int(snapshot.sample_count)),
            nearest_k=1,
        )
        return CONTROL_POLICY_VERSION, selection, str(snapshot.snapshot_digest)

    fallback_strength = manor_strength
    fallback_features = {
        "prestige": float(manor_strength.components.get("prestige", 0)),
        "core_building_level": float(manor_strength.components.get("core_building_level", 0)),
        "guest_count": float(manor_strength.components.get("guest_count", 0)),
        "max_guest_level": float(manor_strength.components.get("max_guest_level", 0)),
        "arena_lineup_power": float(manor_strength.components.get("arena_lineup_power", 0)),
        "troop_total": float(manor_strength.components.get("troop_total", 0)),
    }
    config = load_virtual_player_v2_config()
    if config is not None:
        try:
            _snapshot_version, fixed_snapshot = policy_starter_snapshot(
                config.policy(CONTROL_POLICY_VERSION).payload,
                prestige_band=str(prestige_band),
            )
            fallback_strength = starter_snapshot_strength(fixed_snapshot)
            fallback_features = {
                key: float(fixed_snapshot.get(key, 0))
                for key in (
                    "prestige",
                    "core_building_level",
                    "guest_count",
                    "max_guest_level",
                    "arena_lineup_power",
                    "troop_total",
                )
            }
        except (KeyError, TypeError, ValueError, ReferenceSnapshotError, VirtualPlayerConfigError):
            logger.exception("Unable to load policy-2 fixed growth-control fallback")
    selection = select_reference(
        context=context,
        prestige_band=str(prestige_band),
        target_features=fallback_features,
        starter_strength=fallback_strength,
        local_candidates=(),
        local_sample_count=0,
        nearest_k=1,
    )
    return CONTROL_POLICY_VERSION, selection, FIXED_DEFAULT_CONTROL_DIGEST


def run_growth_control_task(
    *,
    now: datetime | None = None,
    raise_on_database_error: bool = False,
) -> GrowthControlRefreshResult:
    """Task boundary: every config or write failure remains visible to Celery."""

    # Keep the keyword for callers that explicitly request strict database
    # propagation; all failures are strict now so an error can never look like
    # a successful zero-cell refresh.
    del raise_on_database_error
    return refresh_growth_control_snapshots(now=now)


__all__ = [
    "CONTROL_POLICY_VERSION",
    "CONTROL_TTL",
    "FIXED_DEFAULT_CONTROL_DIGEST",
    "GrowthControlAggregate",
    "GrowthControlPolicy",
    "RealPlayerGrowthSample",
    "SHANGHAI_TZ",
    "aggregate_growth_control_samples",
    "collect_real_player_growth_samples",
    "configured_growth_control_policy",
    "effective_growth_control_snapshot",
    "growth_control_digest_for_route",
    "growth_control_reference_selection",
    "refresh_growth_control_snapshots",
    "run_growth_control_task",
]
