from __future__ import annotations

import logging
import random
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from hashlib import sha256
from threading import Event, Thread
from typing import Any, Iterator
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models import Case, Count, F, IntegerField, Q, When
from django.utils import timezone

from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock, renew_best_effort_lock
from gameplay.constants import PVPConstants
from gameplay.models import (
    BotBackfillDemand,
    BotPopulationControl,
    BotPopulationRecomputeDemand,
    BotProfile,
    Manor,
    RaidRun,
)
from gameplay.services.arena.virtual_protection import arena_protected_bot_manor_ids
from gameplay.services.arena.virtual_reserve_references import active_arena_population_activations
from gameplay.services.runtime_configs import lock_virtual_player_routing, read_virtual_player_routing
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES

from . import profile_store
from .bootstrap import _create_virtual_player_v1
from .config import V2_PRESTIGE_BAND_NAMES, BootstrapMode, load_virtual_player_config, load_virtual_player_v2_config
from .contracts import BotProjectionConfig, PopulationMutationStatus
from .legacy.projection import range_value as _range_value
from .legacy.projection import weighted_archetype as _weighted_archetype
from .maintenance import reactivate_locked_virtual_player_profile
from .population import PopulationCell, PopulationPlan, plan_population_cells
from .reference_snapshots import projection_for_band as _projection_for_band
from .selectors import active_real_player_count as _active_real_player_count
from .selectors import band_filter_kwargs as _band_filter_kwargs
from .selectors import configured_population_value as _configured_population_value
from .selectors import maintained_bot_count as _maintained_bot_count
from .selectors import maintained_bot_queryset as _maintained_bot_queryset
from .selectors import population_cell_membership_filter as _population_cell_membership_filter
from .selectors import population_config_int as _population_config_int
from .selectors import prestige_band_for_value as _prestige_band_for_value
from .selectors import prestige_bands as _prestige_bands
from .selectors import regions as _regions
from .selectors import target_band_filter as _target_band_filter
from .selectors import uses_regional_population_planning as _uses_regional_population_planning

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PopulationMutationResult:
    status: PopulationMutationStatus
    profile: BotProfile | None
    hard_cap: int
    maintained_count: int


@dataclass(frozen=True, slots=True)
class PopulationRecomputeClaim:
    demand_id: int
    region: str
    prestige_band: str
    claimed_revision: int
    claim_token: UUID
    claimed_at: datetime
    claim_expires_at: datetime


class PopulationCellReconcileStatus(str, Enum):
    ROUTING_INACTIVE = "routing_inactive"
    NO_DEMAND = "no_demand"
    DEFERRED = "deferred"
    CLAIM_LOST = "claim_lost"
    COMPLETED = "completed"
    CONTINUED = "continued"


@dataclass(frozen=True, slots=True)
class PopulationCellReconcileResult:
    status: PopulationCellReconcileStatus
    region: str
    prestige_band: str
    claimed_revision: int | None = None
    processed_count: int = 0
    created_count: int = 0
    reactivated_count: int = 0
    executable_deficit_remains: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "region": self.region,
            "prestige_band": self.prestige_band,
            "claimed_revision": self.claimed_revision,
            "processed_count": self.processed_count,
            "created_count": self.created_count,
            "reactivated_count": self.reactivated_count,
            "executable_deficit_remains": self.executable_deficit_remains,
        }


ROLL_LOCK_KEY = "virtual_players:roll_lock"
ROLL_LOCK_TIMEOUT_SECONDS = 300
POPULATION_RECOMPUTE_DEFAULT_BATCH_LIMIT = 8
POPULATION_RECOMPUTE_CLAIM_LEASE_SECONDS = 300
POPULATION_RECOMPUTE_FAILURE_BACKOFF_INITIAL_SECONDS = 60
POPULATION_RECOMPUTE_FAILURE_BACKOFF_MAX_SECONDS = 3600
V2_PERIODIC_CURRENT_BAND_SYNC_LIMIT = 100

_V2_BAND_ORDINAL = {band: index for index, band in enumerate(V2_PRESTIGE_BAND_NAMES)}


class VirtualPlayerPopulationLockLostError(RuntimeError):
    """Raised when a population roll no longer owns its distributed lock."""


class PopulationRecomputeDemandError(ValueError):
    """Raised when a durable population-demand request is invalid."""


def _database_utc_now() -> datetime:
    with connection.cursor() as cursor:
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        value = cursor.fetchone()[0]
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if timezone.is_naive(value):
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _demand_now(now: datetime | None) -> datetime:
    resolved = now or _database_utc_now()
    if timezone.is_naive(resolved):
        raise PopulationRecomputeDemandError("population demand time must be timezone-aware")
    return resolved.astimezone(UTC)


def _valid_population_demand_bands() -> tuple[str, ...]:
    config = load_virtual_player_v2_config()
    if config is None:
        raise PopulationRecomputeDemandError("bot_development_v2 is not configured")
    bands = tuple(band.name for band in config.bands)
    if bands != V2_PRESTIGE_BAND_NAMES:
        raise PopulationRecomputeDemandError("V2 prestige bands are not canonical")
    return bands


def _normalize_population_demand_cells(
    cells: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    valid_regions = frozenset(_regions())
    valid_bands = frozenset(_valid_population_demand_bands())
    normalized: set[tuple[str, str]] = set()
    for raw_region, raw_band in cells:
        region = str(raw_region)
        prestige_band = str(raw_band)
        if region not in valid_regions:
            raise PopulationRecomputeDemandError(f"unknown virtual-player population region: {region}")
        if prestige_band not in valid_bands:
            raise PopulationRecomputeDemandError(f"unknown V2 virtual-player prestige band: {prestige_band}")
        normalized.add((region, prestige_band))
    return tuple(sorted(normalized, key=lambda cell: (cell[0], _V2_BAND_ORDINAL[cell[1]])))


def _population_demand_claim_from_model(
    demand: BotPopulationRecomputeDemand,
) -> PopulationRecomputeClaim:
    if (
        demand.claimed_revision is None
        or demand.claim_token is None
        or demand.claimed_at is None
        or demand.claim_expires_at is None
    ):
        raise RuntimeError("population recompute demand has an incomplete claim")
    return PopulationRecomputeClaim(
        demand_id=int(demand.id),
        region=str(demand.region),
        prestige_band=str(demand.prestige_band),
        claimed_revision=int(demand.claimed_revision),
        claim_token=demand.claim_token,
        claimed_at=demand.claimed_at,
        claim_expires_at=demand.claim_expires_at,
    )


def _merge_normalized_population_recompute_demands_locked(
    normalized: tuple[tuple[str, str], ...],
    *,
    current_time: datetime,
) -> tuple[BotPopulationRecomputeDemand, ...]:
    if not normalized:
        return ()
    BotPopulationRecomputeDemand.objects.bulk_create(
        [
            BotPopulationRecomputeDemand(
                region=region,
                prestige_band=prestige_band,
                available_at=current_time,
            )
            for region, prestige_band in normalized
        ],
        ignore_conflicts=True,
    )
    cell_filter = Q()
    for region, prestige_band in normalized:
        cell_filter |= Q(region=region, prestige_band=prestige_band)
    band_order = Case(
        *(When(prestige_band=prestige_band, then=ordinal) for prestige_band, ordinal in _V2_BAND_ORDINAL.items()),
        default=len(_V2_BAND_ORDINAL),
        output_field=IntegerField(),
    )
    locked_demands = tuple(
        BotPopulationRecomputeDemand.objects.select_for_update()
        .filter(cell_filter)
        .order_by("region", band_order, "id")
    )
    if len(locked_demands) != len(normalized):
        raise RuntimeError("population recompute demand cells disappeared while locking")

    updated_at = timezone.now()
    for demand in locked_demands:
        demand.requested_revision = int(demand.requested_revision) + 1
        demand.updated_at = updated_at
    BotPopulationRecomputeDemand.objects.bulk_update(
        locked_demands,
        ["requested_revision", "updated_at"],
    )
    return locked_demands


@transaction.atomic
def merge_population_recompute_demands(
    cells: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    now: datetime | None = None,
) -> tuple[BotPopulationRecomputeDemand, ...]:
    """Coalesce one revision per distinct cell while preserving failure backoff."""
    normalized = _normalize_population_demand_cells(cells)
    if not normalized:
        return ()
    return _merge_normalized_population_recompute_demands_locked(
        normalized,
        current_time=_demand_now(now),
    )


def merge_population_recompute_demand(
    *,
    region: str,
    prestige_band: str,
    now: datetime | None = None,
) -> BotPopulationRecomputeDemand:
    return merge_population_recompute_demands(
        [(region, prestige_band)],
        now=now,
    )[0]


def merge_population_recompute_demand_for_prestige(
    *,
    region: str,
    prestige: int,
    now: datetime | None = None,
) -> BotPopulationRecomputeDemand | None:
    """Merge the canonical V2 population cell containing a persisted prestige value."""
    normalized_region = str(region)
    if normalized_region == "overseas":
        return None
    if isinstance(prestige, bool) or not isinstance(prestige, int) or prestige < 0:
        raise PopulationRecomputeDemandError("population reference prestige must be a non-negative integer")
    prestige_band = _prestige_band_for_value(
        prestige,
        _v2_population_runtime_config(),
    )
    if prestige_band is None:
        raise PopulationRecomputeDemandError("real-player prestige is outside the canonical V2 bands")
    return merge_population_recompute_demand(
        region=normalized_region,
        prestige_band=prestige_band,
        now=now,
    )


def merge_real_player_population_recompute_demand(
    *,
    region: str,
    prestige: int,
    now: datetime | None = None,
) -> BotPopulationRecomputeDemand | None:
    """Merge the V2 population cell affected by a committed real-player change."""
    return merge_population_recompute_demand_for_prestige(
        region=region,
        prestige=prestige,
        now=now,
    )


def try_merge_already_classified_mysql_prestige_transition_cells(
    *,
    manor_id: int,
    region: str,
    before_prestige: int,
    after_prestige: int,
) -> tuple[tuple[str, str], ...] | None:
    """Atomically merge an already-classified Bot transition in one MySQL statement."""
    if connection.vendor != "mysql":
        return None
    if isinstance(manor_id, bool) or not isinstance(manor_id, int) or manor_id < 1:
        raise PopulationRecomputeDemandError("manor_id must be a positive integer")
    normalized_region = _validate_prestige_transition_region(region)
    if normalized_region == "overseas":
        return None
    before_band = _v2_prestige_band_for_transition(
        before_prestige,
        field="before_prestige",
    )
    after_band = _v2_prestige_band_for_transition(
        after_prestige,
        field="after_prestige",
    )
    if before_band == after_band:
        return ()

    cells = _normalize_population_demand_cells(
        [
            (normalized_region, before_band),
            (normalized_region, after_band),
        ]
    )
    demand_table = connection.ops.quote_name(BotPopulationRecomputeDemand._meta.db_table)
    profile_table = connection.ops.quote_name(BotProfile._meta.db_table)
    manor_table = connection.ops.quote_name(Manor._meta.db_table)
    sql = f"""
        INSERT INTO {demand_table} (
            `region`, `prestige_band`, `requested_revision`,
            `completed_revision`, `available_at`,
            `consecutive_failure_count`, `last_error_digest`,
            `created_at`, `updated_at`
        )
        SELECT
            source.`region`, bands.`prestige_band`, 1,
            0, CURRENT_TIMESTAMP, 0, '',
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM (
            SELECT manor.`region` AS `region`
            FROM {profile_table} profile
            INNER JOIN {manor_table} manor ON manor.`id` = profile.`manor_id`
            WHERE profile.`manor_id` = %s
              AND profile.`current_prestige_band` = %s
              AND manor.`prestige` = %s
              AND manor.`region` = %s
            LIMIT 1
        ) source
        CROSS JOIN (
            SELECT %s AS `prestige_band`
            UNION ALL SELECT %s
        ) bands
        WHERE TRUE
        ON DUPLICATE KEY UPDATE
            `requested_revision` = `requested_revision` + 1,
            `updated_at` = CURRENT_TIMESTAMP
    """
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [
                manor_id,
                after_band,
                after_prestige,
                normalized_region,
                cells[0][1],
                cells[1][1],
            ],
        )
        if cursor.rowcount == 0:
            return None
    return cells


def _v2_prestige_band_for_transition(value: int, *, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PopulationRecomputeDemandError(f"{field} must be a non-negative integer")
    config = load_virtual_player_v2_config()
    if config is None:
        raise PopulationRecomputeDemandError("bot_development_v2 is not configured")
    try:
        return config.band_for_prestige(value).name
    except ValueError as exc:
        raise PopulationRecomputeDemandError(f"{field} is outside the canonical V2 bands") from exc


def _validate_prestige_transition_region(region: str) -> str:
    normalized = str(region)
    if normalized != "overseas" and normalized not in frozenset(_regions()):
        raise PopulationRecomputeDemandError(f"unknown virtual-player population region: {normalized}")
    return normalized


@transaction.atomic
def merge_committed_prestige_transition_population_demands(
    *,
    manor_id: int,
    region: str,
    before_prestige: int,
    after_prestige: int,
    now: datetime | None = None,
) -> tuple[BotPopulationRecomputeDemand, ...]:
    """Sync a Bot profile when present, then atomically hand off the old/new cells."""
    if isinstance(manor_id, bool) or not isinstance(manor_id, int) or manor_id < 1:
        raise PopulationRecomputeDemandError("manor_id must be a positive integer")
    normalized_region = _validate_prestige_transition_region(region)
    before_band = _v2_prestige_band_for_transition(
        before_prestige,
        field="before_prestige",
    )
    after_band = _v2_prestige_band_for_transition(
        after_prestige,
        field="after_prestige",
    )
    if before_band == after_band:
        return ()

    normalized_manor_id = manor_id
    profile_state = (
        BotProfile.objects.filter(manor_id=normalized_manor_id)
        .values(
            "id",
            "current_prestige_band",
            "manor__prestige",
            "manor__region",
        )
        .first()
    )
    effective_region = normalized_region
    if profile_state is not None:
        profile_id = int(profile_state["id"])
        persisted_prestige = int(profile_state["manor__prestige"] or 0)
        current_band = str(profile_state["current_prestige_band"])
        if persisted_prestige == after_prestige and current_band == after_band:
            effective_region = _validate_prestige_transition_region(str(profile_state["manor__region"]))
        else:
            sync_result = profile_store.sync_current_prestige_band_from_manor(profile_id)
            if sync_result.reason == "missing_or_locked":
                raise RuntimeError(f"virtual-player profile {profile_id} disappeared during prestige handoff")
            effective_region = _validate_prestige_transition_region(sync_result.region)
    else:
        real_manor = (
            Manor.objects.filter(
                pk=normalized_manor_id,
                user__is_staff=False,
                user__is_superuser=False,
            )
            .values("region")
            .first()
        )
        if real_manor is None:
            return ()
        effective_region = _validate_prestige_transition_region(str(real_manor["region"]))

    if effective_region == "overseas":
        return ()
    normalized_cells = _normalize_population_demand_cells(
        [
            (effective_region, before_band),
            (effective_region, after_band),
        ]
    )
    return _merge_normalized_population_recompute_demands_locked(
        normalized_cells,
        current_time=_demand_now(now),
    )


def _claim_locked_population_recompute_demand(
    demand: BotPopulationRecomputeDemand,
    *,
    now: datetime,
) -> PopulationRecomputeClaim | None:
    pending = int(demand.requested_revision) > int(demand.completed_revision)
    unexpired_claim = demand.claim_expires_at is not None and demand.claim_expires_at > now
    if not pending or demand.available_at > now or unexpired_claim:
        return None
    token = uuid4()
    demand.claimed_revision = int(demand.requested_revision)
    demand.claim_token = token
    demand.claimed_at = now
    demand.claim_expires_at = now + timedelta(seconds=POPULATION_RECOMPUTE_CLAIM_LEASE_SECONDS)
    demand.save(
        update_fields=[
            "claimed_revision",
            "claim_token",
            "claimed_at",
            "claim_expires_at",
            "updated_at",
        ]
    )
    return _population_demand_claim_from_model(demand)


@transaction.atomic
def claim_population_recompute_demand(
    *,
    region: str,
    prestige_band: str,
    now: datetime | None = None,
) -> PopulationRecomputeClaim | None:
    normalized = _normalize_population_demand_cells([(region, prestige_band)])
    current_time = _demand_now(now)
    demand = (
        BotPopulationRecomputeDemand.objects.select_for_update()
        .filter(region=normalized[0][0], prestige_band=normalized[0][1])
        .first()
    )
    if demand is None:
        return None
    return _claim_locked_population_recompute_demand(demand, now=current_time)


@transaction.atomic
def claim_next_population_recompute_demand(
    *,
    now: datetime | None = None,
) -> PopulationRecomputeClaim | None:
    current_time = _demand_now(now)
    band_order = Case(
        *[When(prestige_band=band, then=ordinal) for band, ordinal in _V2_BAND_ORDINAL.items()],
        default=len(_V2_BAND_ORDINAL),
        output_field=IntegerField(),
    )
    demand = (
        BotPopulationRecomputeDemand.objects.select_for_update(skip_locked=True)
        .filter(
            requested_revision__gt=F("completed_revision"),
            available_at__lte=current_time,
        )
        .filter(Q(claimed_revision__isnull=True) | Q(claim_expires_at__lte=current_time))
        .order_by("available_at", "region", band_order, "id")
        .first()
    )
    if demand is None:
        return None
    _normalize_population_demand_cells([(demand.region, demand.prestige_band)])
    return _claim_locked_population_recompute_demand(demand, now=current_time)


def _claim_matches(
    demand: BotPopulationRecomputeDemand,
    claim: PopulationRecomputeClaim,
    *,
    now: datetime,
) -> bool:
    return bool(
        int(demand.id) == int(claim.demand_id)
        and demand.region == claim.region
        and demand.prestige_band == claim.prestige_band
        and demand.claim_token == claim.claim_token
        and demand.claimed_revision == claim.claimed_revision
        and demand.claim_expires_at is not None
        and demand.claim_expires_at > now
    )


def _clear_population_demand_claim(demand: BotPopulationRecomputeDemand) -> None:
    demand.claimed_revision = None
    demand.claim_token = None
    demand.claimed_at = None
    demand.claim_expires_at = None


@transaction.atomic
def finalize_population_recompute_demand(
    claim: PopulationRecomputeClaim,
    *,
    executable_deficit_remains: bool = False,
    now: datetime | None = None,
) -> bool:
    current_time = _demand_now(now)
    demand = BotPopulationRecomputeDemand.objects.select_for_update().filter(id=claim.demand_id).first()
    if demand is None or not _claim_matches(demand, claim, now=current_time):
        return False
    if executable_deficit_remains:
        demand.requested_revision = int(demand.requested_revision) + 1
    demand.completed_revision = int(claim.claimed_revision)
    demand.consecutive_failure_count = 0
    demand.last_error_digest = ""
    _clear_population_demand_claim(demand)
    if int(demand.requested_revision) > int(demand.completed_revision):
        demand.available_at = current_time
    demand.save(
        update_fields=[
            "requested_revision",
            "completed_revision",
            "claimed_revision",
            "claim_token",
            "claimed_at",
            "claim_expires_at",
            "available_at",
            "consecutive_failure_count",
            "last_error_digest",
            "updated_at",
        ]
    )
    return True


def _population_failure_digest(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        payload = f"{type(error).__module__}.{type(error).__qualname__}:{error}"
    else:
        payload = str(error)
    return sha256(payload.encode("utf-8", errors="replace")).hexdigest()


@transaction.atomic
def fail_population_recompute_demand(
    claim: PopulationRecomputeClaim,
    *,
    error: BaseException | str,
    now: datetime | None = None,
) -> bool:
    current_time = _demand_now(now)
    demand = BotPopulationRecomputeDemand.objects.select_for_update().filter(id=claim.demand_id).first()
    if demand is None or not _claim_matches(demand, claim, now=current_time):
        return False
    failure_count = int(demand.consecutive_failure_count) + 1
    exponent = min(failure_count - 1, 6)
    backoff_seconds = min(
        POPULATION_RECOMPUTE_FAILURE_BACKOFF_MAX_SECONDS,
        POPULATION_RECOMPUTE_FAILURE_BACKOFF_INITIAL_SECONDS * (2**exponent),
    )
    demand.consecutive_failure_count = failure_count
    demand.last_error_digest = _population_failure_digest(error)
    demand.available_at = current_time + timedelta(seconds=backoff_seconds)
    _clear_population_demand_claim(demand)
    demand.save(
        update_fields=[
            "claimed_revision",
            "claim_token",
            "claimed_at",
            "claim_expires_at",
            "available_at",
            "consecutive_failure_count",
            "last_error_digest",
            "updated_at",
        ]
    )
    return True


@contextmanager
def _population_ownership() -> Iterator[Callable[[], None] | None]:
    acquired, from_cache, lock_token = acquire_best_effort_lock(
        ROLL_LOCK_KEY,
        timeout_seconds=ROLL_LOCK_TIMEOUT_SECONDS,
        logger=logger,
        log_context="virtual player population roll",
        allow_local_fallback=False,
    )
    if not acquired:
        yield None
        return

    stop_heartbeat = Event()
    lost_ownership = Event()
    heartbeat_failed = Event()
    heartbeat_errors: list[Exception] = []

    def ownership_guard() -> None:
        if heartbeat_failed.is_set():
            raise heartbeat_errors[0]
        if lost_ownership.is_set():
            raise VirtualPlayerPopulationLockLostError("virtual player population roll lock ownership was lost")

    def heartbeat() -> None:
        interval_seconds = max(1, int(ROLL_LOCK_TIMEOUT_SECONDS)) / 3
        try:
            while not stop_heartbeat.wait(interval_seconds):
                renewed = renew_best_effort_lock(
                    ROLL_LOCK_KEY,
                    from_cache=from_cache,
                    lock_token=lock_token,
                    timeout_seconds=ROLL_LOCK_TIMEOUT_SECONDS,
                    logger=logger,
                    log_context="virtual player population roll",
                )
                if not renewed:
                    lost_ownership.set()
                    stop_heartbeat.set()
                    return
        except Exception as exc:
            heartbeat_errors.append(exc)
            heartbeat_failed.set()
            stop_heartbeat.set()
            logger.exception("Virtual player population roll heartbeat raised an unexpected error")

    heartbeat_thread = Thread(
        target=heartbeat,
        name="virtual-player-population-lock-heartbeat",
        daemon=True,
    )
    heartbeat_started = False
    completed_normally = False
    try:
        heartbeat_thread.start()
        heartbeat_started = True
        yield ownership_guard
        completed_normally = True
    finally:
        stop_heartbeat.set()
        if heartbeat_started and heartbeat_thread.is_alive():
            heartbeat_thread.join()
        try:
            if completed_normally:
                ownership_guard()
        finally:
            release_best_effort_lock(
                ROLL_LOCK_KEY,
                from_cache=from_cache,
                lock_token=lock_token,
                logger=logger,
                log_context="virtual player population roll",
            )


def _arena_protected_bot_manor_ids() -> set[int]:
    return arena_protected_bot_manor_ids()


def _build_population_plan(
    config: dict[str, Any],
    *,
    now,
    target_based_membership: bool | None = None,
    required_engine_version: int | None = None,
) -> PopulationPlan:
    population = config.get("population") or {}
    uses_regional_planning = _uses_regional_population_planning()
    target_based = uses_regional_planning if target_based_membership is None else bool(target_based_membership)
    active_days = max(1, int(population.get("active_window_days") or 7))
    active_after = now - timedelta(days=active_days)
    recent_after = now - timedelta(hours=24)
    exhausted_manor_ids = list(
        RaidRun.objects.filter(started_at__gte=recent_after, defender__bot_profile__isnull=False)
        .values("defender_id")
        .annotate(received=Count("id"))
        .filter(received__gte=PVPConstants.RAID_MAX_DAILY_ATTACKS_RECEIVED)
        .values_list("defender_id", flat=True)
    )
    maintained = _maintained_bot_queryset().select_related("manor")
    if required_engine_version is not None:
        maintained = maintained.filter(engine_version=int(required_engine_version))
    attackable = maintained.filter(
        Q(manor__newbie_protection_until__isnull=True) | Q(manor__newbie_protection_until__lte=now),
        Q(manor__defeat_protection_until__isnull=True) | Q(manor__defeat_protection_until__lte=now),
        Q(manor__peace_shield_until__isnull=True) | Q(manor__peace_shield_until__lte=now),
    ).exclude(manor_id__in=exhausted_manor_ids)
    demands = {
        (row["region"], row["prestige_band"]): int(row["needed"] or 0)
        for row in BotBackfillDemand.objects.values("region", "prestige_band", "needed")
    }
    arena_demands: dict[tuple[str, str], int] = {}
    if required_engine_version == 2:
        valid_regions = frozenset(_regions())
        for activation in active_arena_population_activations():
            band_name = _prestige_band_for_value(activation.prestige, config)
            if activation.region not in valid_regions or band_name is None:
                continue
            key = (activation.region, band_name)
            arena_demands[key] = arena_demands.get(key, 0) + int(activation.needed)

    cells: list[PopulationCell] = []
    for region in _regions():
        for band_name, (low, high) in _prestige_bands(config).items():
            band_filter = _band_filter_kwargs(low, high, prefix="manor__")
            real_filter = _band_filter_kwargs(low, high)
            cells.append(
                PopulationCell(
                    region=region,
                    prestige_band=band_name,
                    active_real=Manor.objects.filter(
                        bot_profile__isnull=True,
                        user__is_staff=False,
                        user__is_superuser=False,
                        region=region,
                        last_active_at__gte=active_after,
                        **real_filter,
                    ).count(),
                    maintained_supply=maintained.filter(manor__region=region)
                    .filter(
                        _population_cell_membership_filter(
                            band_name,
                            config=config,
                            target_based=target_based,
                        )
                    )
                    .count(),
                    attackable_supply=attackable.filter(manor__region=region, **band_filter).count(),
                    search_demand=max(
                        demands.get((region, band_name), 0),
                        arena_demands.get((region, band_name), 0),
                    ),
                )
            )

    if uses_regional_planning:
        entry_band = _prestige_band_for_value(0, config) or next(iter(_prestige_bands(config)), "newbie")
        hard_cap_override = int(population.get("hard_cap") or 0) if "hard_cap" in population else None
        return plan_population_cells(
            cells,
            region_floor=max(0, _population_config_int(population, "region_floor", 8)),
            region_multiplier=max(0, _population_config_int(population, "region_active_multiplier", 8)),
            global_floor=max(0, _population_config_int(population, "global_floor", 32)),
            global_multiplier=max(0, _population_config_int(population, "global_active_multiplier", 20)),
            entry_band=entry_band,
            hard_cap_override=hard_cap_override,
        )

    return plan_population_cells(
        cells,
        cell_floor=max(
            0,
            _configured_population_value(
                population,
                "cell_floor",
                legacy_field="min_attackable_per_band",
                default=4,
            ),
        ),
        cell_multiplier=max(
            0,
            _configured_population_value(
                population,
                "cell_active_multiplier",
                legacy_field="active_player_multiplier",
                default=2,
            ),
        ),
        exploration_supply=max(0, int(population.get("exploration_supply") or 0)),
        hard_cap=max(0, int(population.get("hard_cap") or 0)),
    )


def _population_runtime_config_for_bootstrap_mode(
    bootstrap_mode: BootstrapMode,
) -> dict[str, Any]:
    if bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE:
        return load_virtual_player_config()
    return _v2_population_runtime_config()


def _lock_population_mutation_bootstrap_mode(
    *,
    required_engine_version: int | None = None,
) -> BootstrapMode | None:
    """Serialize a population write with routing transitions and revalidate its engine."""
    bootstrap_mode = lock_virtual_player_routing().bootstrap_mode
    if bootstrap_mode is BootstrapMode.V2_PAUSED:
        return None
    routed_engine_version = 1 if bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE else 2
    if required_engine_version is not None and routed_engine_version != int(required_engine_version):
        return None
    return bootstrap_mode


def get_virtual_player_capacity(*, now=None) -> tuple[int, int]:
    current_time = now or timezone.now()
    bootstrap_mode = read_virtual_player_routing().bootstrap_mode
    population_plan = _build_population_plan(
        _population_runtime_config_for_bootstrap_mode(bootstrap_mode),
        now=current_time,
    )
    return population_plan.hard_cap, _maintained_bot_count()


def _select_virtual_player_creation_region(
    *,
    now,
    config: dict[str, Any],
    required_engine_version: int,
) -> str | None:
    population_plan = _build_population_plan(
        config,
        now=now,
        target_based_membership=required_engine_version == 2,
        required_engine_version=required_engine_version,
    )
    region_targets = population_plan.region_targets
    if not region_targets:
        return None
    maintained_by_region = {
        str(row["manor__region"]): int(row["count"] or 0)
        for row in _maintained_bot_queryset().values("manor__region").annotate(count=Count("id"))
    }
    return min(
        region_targets,
        key=lambda region: (
            -(int(region_targets[region]) - maintained_by_region.get(region, 0)),
            region,
        ),
    )


def _lock_population_capacity(*, now) -> tuple[int, int]:
    BotPopulationControl.objects.select_for_update().get_or_create(
        key=BotPopulationControl.GLOBAL_KEY,
    )
    return get_virtual_player_capacity(now=now)


def _population_has_room(hard_cap: int, maintained_count: int) -> bool:
    return hard_cap <= 0 or maintained_count < hard_cap


def _v2_population_runtime_config() -> dict[str, Any]:
    v2_config = load_virtual_player_v2_config()
    if v2_config is None:
        raise PopulationRecomputeDemandError("bot_development_v2 is not configured")
    config = dict(load_virtual_player_config())
    config["prestige_bands"] = {band.name: [band.lower_inclusive, band.upper_exclusive] for band in v2_config.bands}
    return config


def _v2_bootstrap_routing_is_active() -> bool:
    return read_virtual_player_routing().bootstrap_mode is BootstrapMode.V2_ACTIVE


def _v2_periodic_population_cells() -> tuple[tuple[str, str], ...]:
    return _normalize_population_demand_cells(
        tuple((region, prestige_band) for region in _regions() for prestige_band in V2_PRESTIGE_BAND_NAMES)
    )


def sync_mismatched_v2_current_prestige_bands(
    *,
    limit: int = V2_PERIODIC_CURRENT_BAND_SYNC_LIMIT,
) -> int:
    """Repair a bounded mismatch-only batch so fixed rows cannot starve later rows."""
    normalized_limit = max(0, min(1000, int(limit)))
    if normalized_limit == 0 or not _v2_bootstrap_routing_is_active():
        return 0
    profile_ids = profile_store.list_mismatched_v2_prestige_band_profile_ids(limit=normalized_limit)
    changed = 0
    for profile_id in profile_ids:
        if not _v2_bootstrap_routing_is_active():
            break
        result = profile_store.sync_current_prestige_band_from_manor(
            profile_id,
            skip_locked=True,
        )
        changed += int(result.changed)
    return changed


def merge_periodic_v2_population_recompute_demands(
    *,
    now: datetime | None = None,
) -> tuple[BotPopulationRecomputeDemand, ...]:
    """Persist the complete V2 population work set used for periodic recovery."""
    if not _v2_bootstrap_routing_is_active():
        return ()
    return merge_population_recompute_demands(
        _v2_periodic_population_cells(),
        now=now,
    )


@transaction.atomic
def _v2_population_cell_has_executable_deficit(
    *,
    region: str,
    prestige_band: str,
    config: dict[str, Any],
    now: datetime,
) -> bool:
    hard_cap, maintained_count = _lock_population_capacity(now=now)
    if not _population_has_room(hard_cap, maintained_count):
        return False
    cell = _build_population_plan(
        config,
        now=now,
        target_based_membership=True,
        required_engine_version=2,
    ).by_key.get((region, prestige_band))
    return cell is not None and cell.structural_deficit > 0


def _reconcile_claimed_virtual_player_population_cell(
    claim: PopulationRecomputeClaim,
    *,
    limit: int,
    now: datetime | None,
) -> PopulationCellReconcileResult:
    if not _v2_bootstrap_routing_is_active():
        fail_population_recompute_demand(
            claim,
            error="V2 Bootstrap routing is not active",
            now=now,
        )
        return PopulationCellReconcileResult(
            status=PopulationCellReconcileStatus.ROUTING_INACTIVE,
            region=claim.region,
            prestige_band=claim.prestige_band,
            claimed_revision=claim.claimed_revision,
        )

    config = _v2_population_runtime_config()
    bands = _prestige_bands(config)
    low, high = bands[claim.prestige_band]
    rng = random.Random(f"population-demand:{claim.claim_token}:{claim.claimed_revision}")
    evaluated_profile_ids: set[int] = set()
    processed_count = 0
    created_count = 0
    reactivated_count = 0

    try:
        with _population_ownership() as ownership_guard:
            if ownership_guard is None:
                fail_population_recompute_demand(
                    claim,
                    error="virtual-player population ownership is unavailable",
                    now=now,
                )
                return PopulationCellReconcileResult(
                    status=PopulationCellReconcileStatus.DEFERRED,
                    region=claim.region,
                    prestige_band=claim.prestige_band,
                    claimed_revision=claim.claimed_revision,
                )

            from .bootstrap import build_virtual_player_v2_bootstrap_plan, create_virtual_player_v2

            while processed_count < limit:
                ownership_guard()
                if not _v2_bootstrap_routing_is_active():
                    fail_population_recompute_demand(
                        claim,
                        error="V2 Bootstrap routing stopped during population reconciliation",
                        now=now,
                    )
                    return PopulationCellReconcileResult(
                        status=PopulationCellReconcileStatus.ROUTING_INACTIVE,
                        region=claim.region,
                        prestige_band=claim.prestige_band,
                        claimed_revision=claim.claimed_revision,
                        processed_count=processed_count,
                        created_count=created_count,
                        reactivated_count=reactivated_count,
                    )

                mutation_time = _demand_now(now)
                seed = rng.randint(1, 2_147_483_647)
                archetype = _weighted_archetype(random.Random(seed))
                bootstrap_plan = build_virtual_player_v2_bootstrap_plan(
                    region=claim.region,
                    prestige_band=claim.prestige_band,
                    archetype=archetype,
                    growth_seed=seed,
                    now=mutation_time,
                )
                mutation = _reactivate_or_create_virtual_player(
                    region=claim.region,
                    prestige_band=claim.prestige_band,
                    low=low,
                    high=high,
                    archetype=archetype,
                    growth_seed=seed,
                    now=mutation_time,
                    config=config,
                    projection_factory=lambda: bootstrap_plan.projection,
                    evaluated_profile_ids=evaluated_profile_ids,
                    ownership_guard=ownership_guard,
                    require_population_deficit=True,
                    required_engine_version=2,
                    creation_factory=lambda population_permit: create_virtual_player_v2(
                        plan=bootstrap_plan,
                        population_permit=population_permit,
                        now=mutation_time,
                    ),
                    target_based_membership=True,
                    require_current_band_match=True,
                )
                if mutation.status is PopulationMutationStatus.CAP_REACHED:
                    break
                if mutation.profile is None:
                    break
                processed_count += 1
                if mutation.status is PopulationMutationStatus.CREATED:
                    created_count += 1
                elif mutation.status is PopulationMutationStatus.REACTIVATED:
                    reactivated_count += 1

            ownership_guard()
            if not _v2_bootstrap_routing_is_active():
                fail_population_recompute_demand(
                    claim,
                    error="V2 Bootstrap routing stopped before population finalization",
                    now=now,
                )
                return PopulationCellReconcileResult(
                    status=PopulationCellReconcileStatus.ROUTING_INACTIVE,
                    region=claim.region,
                    prestige_band=claim.prestige_band,
                    claimed_revision=claim.claimed_revision,
                    processed_count=processed_count,
                    created_count=created_count,
                    reactivated_count=reactivated_count,
                )
            revalidation_time = _demand_now(now)
            executable_deficit_remains = _v2_population_cell_has_executable_deficit(
                region=claim.region,
                prestige_band=claim.prestige_band,
                config=config,
                now=revalidation_time,
            )
            finalized = finalize_population_recompute_demand(
                claim,
                executable_deficit_remains=executable_deficit_remains,
                now=now,
            )
    except Exception as exc:
        fail_population_recompute_demand(claim, error=exc, now=now)
        raise

    if not finalized:
        return PopulationCellReconcileResult(
            status=PopulationCellReconcileStatus.CLAIM_LOST,
            region=claim.region,
            prestige_band=claim.prestige_band,
            claimed_revision=claim.claimed_revision,
            processed_count=processed_count,
            created_count=created_count,
            reactivated_count=reactivated_count,
            executable_deficit_remains=executable_deficit_remains,
        )
    return PopulationCellReconcileResult(
        status=(
            PopulationCellReconcileStatus.CONTINUED
            if executable_deficit_remains
            else PopulationCellReconcileStatus.COMPLETED
        ),
        region=claim.region,
        prestige_band=claim.prestige_band,
        claimed_revision=claim.claimed_revision,
        processed_count=processed_count,
        created_count=created_count,
        reactivated_count=reactivated_count,
        executable_deficit_remains=executable_deficit_remains,
    )


def reconcile_virtual_player_population_cell(
    *,
    region: str,
    prestige_band: str,
    limit: int = POPULATION_RECOMPUTE_DEFAULT_BATCH_LIMIT,
    now: datetime | None = None,
) -> PopulationCellReconcileResult:
    normalized = _normalize_population_demand_cells([(region, prestige_band)])[0]
    fixed_time = _demand_now(now) if now is not None else None
    if not _v2_bootstrap_routing_is_active():
        return PopulationCellReconcileResult(
            status=PopulationCellReconcileStatus.ROUTING_INACTIVE,
            region=normalized[0],
            prestige_band=normalized[1],
        )
    claim = claim_population_recompute_demand(
        region=normalized[0],
        prestige_band=normalized[1],
        now=fixed_time,
    )
    if claim is None:
        return PopulationCellReconcileResult(
            status=PopulationCellReconcileStatus.NO_DEMAND,
            region=normalized[0],
            prestige_band=normalized[1],
        )
    normalized_limit = max(0, min(100, int(limit)))
    return _reconcile_claimed_virtual_player_population_cell(
        claim,
        limit=normalized_limit,
        now=fixed_time,
    )


def scan_virtual_player_population_demands(
    *,
    limit: int = 100,
    cell_limit: int = POPULATION_RECOMPUTE_DEFAULT_BATCH_LIMIT,
    now: datetime | None = None,
) -> tuple[PopulationCellReconcileResult, ...]:
    normalized_limit = max(0, min(1000, int(limit)))
    normalized_cell_limit = max(0, min(100, int(cell_limit)))
    fixed_time = _demand_now(now) if now is not None else None
    if normalized_limit == 0 or not _v2_bootstrap_routing_is_active():
        return ()

    results: list[PopulationCellReconcileResult] = []
    for _index in range(normalized_limit):
        claim = claim_next_population_recompute_demand(now=fixed_time)
        if claim is None:
            break
        result = _reconcile_claimed_virtual_player_population_cell(
            claim,
            limit=normalized_cell_limit,
            now=fixed_time,
        )
        results.append(result)
        if result.status in {
            PopulationCellReconcileStatus.ROUTING_INACTIVE,
            PopulationCellReconcileStatus.DEFERRED,
            PopulationCellReconcileStatus.CLAIM_LOST,
        }:
            break
    return tuple(results)


def rebalance_virtual_player_target_bands(
    population_plan: PopulationPlan,
    *,
    limit: int,
    required_engine_version: int = 1,
) -> int:
    remaining = max(0, int(limit))
    updated = 0
    protected_manor_ids = _arena_protected_bot_manor_ids()
    for region in sorted(population_plan.region_targets):
        desired = {cell.prestige_band: cell.target for cell in population_plan.cells if cell.region == region}
        current = {
            band: _maintained_bot_queryset()
            .filter(engine_version=int(required_engine_version))
            .filter(manor__region=region)
            .filter(_target_band_filter(band))
            .count()
            for band in desired
        }
        deficits = [band for band in desired if desired[band] > current.get(band, 0)]
        for target_band in sorted(
            deficits,
            key=lambda band: (-(desired[band] - current.get(band, 0)), band),
        ):
            needed = desired[target_band] - current.get(target_band, 0)
            donor_bands = [band for band in desired if current.get(band, 0) > desired[band]]
            for donor_band in sorted(donor_bands):
                if remaining <= 0 or needed <= 0:
                    return updated
                with transaction.atomic():
                    if (
                        _lock_population_mutation_bootstrap_mode(required_engine_version=required_engine_version)
                        is None
                    ):
                        return updated
                    profile_ids = list(
                        _maintained_bot_queryset()
                        .select_for_update(skip_locked=True)
                        .filter(
                            engine_version=int(required_engine_version),
                            manor__region=region,
                            arena_virtual_reserve__isnull=True,
                        )
                        .exclude(manor_id__in=protected_manor_ids)
                        .filter(_target_band_filter(donor_band))
                        .order_by("last_planned_at", "id")
                        .values_list("id", flat=True)[: min(remaining, needed)]
                    )
                    changed = profile_store.retarget_profiles(
                        profile_ids,
                        region=region,
                        donor_filter=_target_band_filter(donor_band),
                        protected_manor_ids=_arena_protected_bot_manor_ids(),
                        target_prestige_band=target_band,
                    )
                updated += changed
                remaining -= changed
                needed -= changed
                current[donor_band] -= changed
                current[target_band] = current.get(target_band, 0) + changed
    return updated


@transaction.atomic
def reactivate_retired_virtual_player_with_capacity(
    profile_id: int,
    *,
    now=None,
) -> PopulationMutationResult:
    current_time = now or timezone.now()
    bootstrap_mode = _lock_population_mutation_bootstrap_mode()
    if bootstrap_mode is None:
        hard_cap, maintained_count = get_virtual_player_capacity(now=current_time)
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    required_engine_version = 1 if bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE else 2
    hard_cap, maintained_count = _lock_population_capacity(now=current_time)
    if required_engine_version == 2:
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    profile = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(
            pk=profile_id,
            state=BotProfile.State.RETIRED,
            engine_version=required_engine_version,
        )
        .first()
    )
    if profile is None:
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    if not _population_has_room(hard_cap, maintained_count):
        return PopulationMutationResult(
            status=PopulationMutationStatus.CAP_REACHED,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    reactivated = reactivate_locked_virtual_player_profile(profile, now=current_time)
    return PopulationMutationResult(
        status=PopulationMutationStatus.REACTIVATED,
        profile=reactivated,
        hard_cap=hard_cap,
        maintained_count=maintained_count,
    )


@transaction.atomic
def reactivate_virtual_player_profile(profile_id: int, *, now=None) -> BotProfile | None:
    current_time = now or timezone.now()
    bootstrap_mode = _lock_population_mutation_bootstrap_mode()
    if bootstrap_mode is None:
        return None
    required_engine_version = 1 if bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE else 2
    if required_engine_version == 2:
        return None
    state = (
        BotProfile.objects.filter(
            pk=profile_id,
            engine_version=required_engine_version,
        )
        .values_list("state", flat=True)
        .first()
    )
    if state == BotProfile.State.RETIRED:
        return reactivate_retired_virtual_player_with_capacity(
            profile_id,
            now=current_time,
        ).profile

    profile = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(
            pk=profile_id,
            state=BotProfile.State.ABANDONED,
            engine_version=required_engine_version,
        )
        .first()
    )
    if profile is None:
        return None
    return reactivate_locked_virtual_player_profile(profile, now=current_time)


@transaction.atomic
def _try_reactivate_retired_player(
    *,
    region: str,
    prestige_band: str,
    low: int,
    high: int | None,
    now,
    config: dict[str, Any],
    evaluated_profile_ids: set[int],
    ownership_guard: Callable[[], None] | None = None,
    required_engine_version: int = 1,
) -> BotProfile | None:
    if ownership_guard is not None:
        ownership_guard()
    if _lock_population_mutation_bootstrap_mode(required_engine_version=required_engine_version) is None:
        return None
    hard_cap, maintained_count = _lock_population_capacity(now=now)
    if not _population_has_room(hard_cap, maintained_count):
        return None
    queryset = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(
            _population_cell_membership_filter(
                prestige_band,
                config=config,
                target_based=_uses_regional_population_planning(),
            )
        )
        .filter(
            state=BotProfile.State.RETIRED,
            engine_version=int(required_engine_version),
            manor__region=str(region),
        )
        .exclude(id__in=evaluated_profile_ids)
        .order_by("-maintenance_stopped_at", "-updated_at", "id")
    )
    profile = queryset.first()
    if profile is None:
        return None
    evaluated_profile_ids.add(int(profile.id))
    if ownership_guard is not None:
        ownership_guard()
    return reactivate_locked_virtual_player_profile(profile, now=now)


@transaction.atomic
def _reactivate_or_create_virtual_player(
    *,
    region: str,
    prestige_band: str,
    low: int,
    high: int | None,
    archetype: str,
    growth_seed: int,
    now,
    config: dict[str, Any],
    projection_factory: Callable[[], BotProjectionConfig],
    evaluated_profile_ids: set[int],
    ownership_guard: Callable[[], None] | None = None,
    require_population_deficit: bool = False,
    include_target_pipeline: bool = False,
    required_engine_version: int = 1,
    creation_factory: Callable[[object], BotProfile] | None = None,
    target_based_membership: bool | None = None,
    require_current_band_match: bool = False,
) -> PopulationMutationResult:
    if required_engine_version == 2 and (
        ownership_guard is None or not require_population_deficit or creation_factory is None
    ):
        raise PopulationRecomputeDemandError(
            "V2 bootstrap requires population ownership, a cell deficit, and a materializer"
        )
    if ownership_guard is not None:
        ownership_guard()
    if _lock_population_mutation_bootstrap_mode(required_engine_version=required_engine_version) is None:
        hard_cap, maintained_count = _lock_population_capacity(now=now)
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    hard_cap, maintained_count = _lock_population_capacity(now=now)
    if not _population_has_room(hard_cap, maintained_count):
        return PopulationMutationResult(
            status=PopulationMutationStatus.CAP_REACHED,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    if require_population_deficit:
        current_cell = _build_population_plan(
            config,
            now=now,
            target_based_membership=target_based_membership,
            required_engine_version=required_engine_version,
        ).by_key.get((str(region), str(prestige_band)))
        current_deficit = 0 if current_cell is None else current_cell.structural_deficit
        if current_cell is not None and include_target_pipeline:
            current_band_filter = Q(**_band_filter_kwargs(low, high, prefix="manor__"))
            pipeline_supply = (
                _maintained_bot_queryset()
                .filter(engine_version=int(required_engine_version))
                .filter(manor__region=str(region))
                .filter(_target_band_filter(prestige_band) | current_band_filter)
                .count()
            )
            current_deficit = max(0, int(current_cell.target) - pipeline_supply)
        if current_deficit <= 0:
            return PopulationMutationResult(
                status=PopulationMutationStatus.UNAVAILABLE,
                profile=None,
                hard_cap=hard_cap,
                maintained_count=maintained_count,
            )

    retired_queryset = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(
            _population_cell_membership_filter(
                prestige_band,
                config=config,
                target_based=(
                    _uses_regional_population_planning()
                    if target_based_membership is None
                    else bool(target_based_membership)
                ),
            )
        )
        .filter(
            state=BotProfile.State.RETIRED,
            manor__region=str(region),
        )
        .exclude(id__in=evaluated_profile_ids)
        .order_by("-maintenance_stopped_at", "-updated_at", "id")
    )
    retired_queryset = retired_queryset.filter(engine_version=int(required_engine_version))
    if require_current_band_match:
        retired_queryset = retired_queryset.filter(
            current_prestige_band=str(prestige_band),
            **_band_filter_kwargs(low, high, prefix="manor__"),
        )
    retired = retired_queryset.first()
    if retired is not None:
        evaluated_profile_ids.add(int(retired.id))
        if ownership_guard is not None:
            ownership_guard()
        reactivated = reactivate_locked_virtual_player_profile(retired, now=now)
        if required_engine_version == 2 and ownership_guard is not None:
            ownership_guard()
        if (
            required_engine_version == 2
            and _lock_population_mutation_bootstrap_mode(required_engine_version=required_engine_version) is None
        ):
            raise PopulationRecomputeDemandError("V2 bootstrap routing stopped before reactivation committed")
        return PopulationMutationResult(
            status=PopulationMutationStatus.REACTIVATED,
            profile=reactivated,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )

    if ownership_guard is not None:
        ownership_guard()
    if creation_factory is not None:
        from .bootstrap import _issue_v2_bootstrap_population_permit

        population_permit = _issue_v2_bootstrap_population_permit(
            region=region,
            prestige_band=prestige_band,
        )
        profile = creation_factory(population_permit)
    else:
        profile = _create_virtual_player_v1(
            region=region,
            prestige_band=prestige_band,
            archetype=archetype,
            growth_seed=growth_seed,
            now=now,
            projection=projection_factory(),
            start_from_zero=True,
        )
    if required_engine_version == 2 and ownership_guard is not None:
        ownership_guard()
    if (
        required_engine_version == 2
        and _lock_population_mutation_bootstrap_mode(required_engine_version=required_engine_version) is None
    ):
        raise PopulationRecomputeDemandError("V2 bootstrap routing stopped before materialization committed")
    return PopulationMutationResult(
        status=PopulationMutationStatus.CREATED,
        profile=profile,
        hard_cap=hard_cap,
        maintained_count=maintained_count,
    )


def virtual_player_prestige_bands(
    config: dict[str, Any] | None = None,
) -> dict[str, tuple[int, int | None]]:
    if config is not None:
        return _prestige_bands(config)
    bootstrap_mode = read_virtual_player_routing().bootstrap_mode
    return _prestige_bands(_population_runtime_config_for_bootstrap_mode(bootstrap_mode))


@transaction.atomic
def _create_virtual_player_for_band(
    *,
    region: str,
    prestige_band: str,
    archetype: str,
    growth_seed: int,
    now,
    rng: random.Random,
) -> BotProfile | None:
    bootstrap_mode = _lock_population_mutation_bootstrap_mode()
    if bootstrap_mode is None:
        return None
    config = _population_runtime_config_for_bootstrap_mode(bootstrap_mode)
    bands = _prestige_bands(config)
    if prestige_band not in bands:
        raise ValueError(f"unknown prestige band: {prestige_band}")
    low, high = bands[prestige_band]
    if bootstrap_mode is BootstrapMode.V2_ACTIVE:
        return None
    return _create_virtual_player_v1(
        region=region,
        prestige_band=prestige_band,
        archetype=archetype,
        growth_seed=growth_seed,
        now=now,
        projection=_projection_for_band(
            prestige_band,
            low,
            high,
            rng,
            region=region,
            config=config,
            sample_seed=growth_seed,
            archetype=archetype,
        ),
    )


def create_virtual_players_for_band(
    *,
    region: str,
    prestige_band: str,
    count: int,
    archetype: str | None = None,
    now=None,
) -> list[BotProfile]:
    count = int(count)
    if count <= 0:
        raise ValueError("count must be positive")

    now = now or timezone.now()
    rng = random.Random(int(now.timestamp()))
    profiles: list[BotProfile] = []
    for _idx in range(count):
        seed = rng.randint(1, 2_147_483_647)
        selected_archetype = archetype or _weighted_archetype(rng)
        profile = _create_virtual_player_for_band(
            region=region,
            prestige_band=prestige_band,
            archetype=selected_archetype,
            growth_seed=seed,
            now=now,
            rng=rng,
        )
        if profile is None:
            break
        profiles.append(profile)
    return profiles


@transaction.atomic
def create_virtual_player_with_capacity(
    *,
    region: str | None,
    prestige_band: str,
    archetype: str | None = None,
    growth_seed: int | None = None,
    now=None,
    projection: BotProjectionConfig | None = None,
    start_from_zero: bool = False,
) -> PopulationMutationResult:
    current_time = now or timezone.now()
    bootstrap_mode = _lock_population_mutation_bootstrap_mode()
    if bootstrap_mode is None:
        hard_cap, maintained_count = get_virtual_player_capacity(now=current_time)
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    required_engine_version = 1 if bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE else 2
    config = _population_runtime_config_for_bootstrap_mode(bootstrap_mode)
    bands = _prestige_bands(config)
    if prestige_band not in bands:
        raise ValueError(f"unknown prestige band: {prestige_band}")
    hard_cap, maintained_count = _lock_population_capacity(now=current_time)
    if not _population_has_room(hard_cap, maintained_count):
        return PopulationMutationResult(
            status=PopulationMutationStatus.CAP_REACHED,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    if bootstrap_mode is BootstrapMode.V2_ACTIVE:
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )

    selected_region = region or _select_virtual_player_creation_region(
        now=current_time,
        config=config,
        required_engine_version=required_engine_version,
    )
    if selected_region is None:
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )

    seed = int(growth_seed or random.randint(1, 2_147_483_647))
    selected_archetype = archetype or _weighted_archetype(random.Random(seed))
    profile = _create_virtual_player_v1(
        region=selected_region,
        prestige_band=prestige_band,
        archetype=selected_archetype,
        growth_seed=seed,
        now=current_time,
        projection=projection,
        start_from_zero=start_from_zero,
    )
    return PopulationMutationResult(
        status=PopulationMutationStatus.CREATED,
        profile=profile,
        hard_cap=hard_cap,
        maintained_count=maintained_count,
    )


def _retire_excess_virtual_players(
    *,
    target: int,
    now,
    ownership_guard: Callable[[], None] | None = None,
    required_engine_version: int = 1,
) -> int:
    target = max(0, int(target or 0))
    excess = _maintained_bot_queryset().filter(engine_version=int(required_engine_version)).count() - target
    if excess <= 0:
        return 0
    with transaction.atomic():
        if _lock_population_mutation_bootstrap_mode(required_engine_version=required_engine_version) is None:
            return 0
        protected_manor_ids = _arena_protected_bot_manor_ids()
        stale_ids = list(
            _maintained_bot_queryset()
            .select_for_update(skip_locked=True)
            .filter(
                engine_version=int(required_engine_version),
                state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
                arena_virtual_reserve__isnull=True,
            )
            .exclude(manor_id__in=protected_manor_ids)
            .order_by("last_planned_at", "created_at", "id")
            .values_list("id", flat=True)[:excess]
        )
        if not stale_ids:
            return 0
        if ownership_guard is not None:
            ownership_guard()
        retired_count = profile_store.retire_profiles(
            stale_ids,
            now=now,
            protected_manor_ids=_arena_protected_bot_manor_ids(),
        )
    if retired_count > 0:
        logger.info(
            "Virtual player overpopulation retired: target=%s excess=%s retired_count=%s",
            target,
            excess,
            retired_count,
            extra={
                "event": "virtual_player_overpopulation_retired",
                "target": target,
                "excess": excess,
                "retired_count": retired_count,
            },
        )
    return retired_count


def _retire_unsupported_v2_profiles(
    *,
    now: datetime,
    limit: int,
) -> int:
    """Retire maintained V2 profiles outside population-managed regions."""

    normalized_limit = max(0, min(100, int(limit)))
    if normalized_limit == 0:
        return 0
    supported_regions = tuple(_regions())
    with _population_ownership() as ownership_guard:
        if ownership_guard is None:
            return 0
        with transaction.atomic():
            ownership_guard()
            if _lock_population_mutation_bootstrap_mode(required_engine_version=2) is None:
                return 0
            _lock_population_capacity(now=now)
            protected_manor_ids = _arena_protected_bot_manor_ids()
            profile_ids = list(
                _maintained_bot_queryset()
                .select_for_update(skip_locked=True)
                .filter(
                    engine_version=2,
                    arena_virtual_reserve__isnull=True,
                )
                .exclude(manor__region__in=supported_regions)
                .exclude(manor_id__in=protected_manor_ids)
                .order_by("last_planned_at", "created_at", "id")
                .values_list("id", flat=True)[:normalized_limit]
            )
            if not profile_ids:
                return 0
            ownership_guard()
            retired_count = profile_store.retire_profiles(
                profile_ids,
                now=now,
                protected_manor_ids=_arena_protected_bot_manor_ids(),
            )
        if retired_count > 0:
            logger.warning(
                "Retired V2 virtual players outside supported regions: count=%s",
                retired_count,
                extra={
                    "event": "virtual_player_unsupported_region_retired",
                    "retired_count": retired_count,
                    "supported_regions": supported_regions,
                },
            )
        return retired_count


def _retire_excess_population_cells(
    population_plan: PopulationPlan,
    *,
    config: dict[str, Any],
    now,
    ownership_guard: Callable[[], None] | None = None,
    required_engine_version: int = 1,
) -> int:
    retired_count = 0
    total_excess = 0
    bands = _prestige_bands(config)
    target_based = _uses_regional_population_planning()
    for cell in population_plan.cells:
        excess = int(cell.excess)
        if excess <= 0 or cell.prestige_band not in bands:
            continue
        membership_filter = _population_cell_membership_filter(
            cell.prestige_band,
            config=config,
            target_based=target_based,
        )
        with transaction.atomic():
            if _lock_population_mutation_bootstrap_mode(required_engine_version=required_engine_version) is None:
                return retired_count
            protected_manor_ids = _arena_protected_bot_manor_ids()
            stale_ids = list(
                _maintained_bot_queryset()
                .select_for_update(skip_locked=True)
                .filter(membership_filter)
                .filter(
                    engine_version=int(required_engine_version),
                    manor__region=cell.region,
                    state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
                    arena_virtual_reserve__isnull=True,
                )
                .exclude(manor_id__in=protected_manor_ids)
                .order_by("last_planned_at", "created_at", "id")
                .values_list("id", flat=True)[:excess]
            )
            if not stale_ids:
                continue
            if ownership_guard is not None:
                ownership_guard()
            updated = profile_store.retire_profiles(
                stale_ids,
                now=now,
                region=cell.region,
                membership_filter=membership_filter,
                protected_manor_ids=_arena_protected_bot_manor_ids(),
            )
        retired_count += updated
        total_excess += excess

    if retired_count > 0:
        logger.info(
            "Virtual player overpopulation retired by cell: target=%s excess=%s retired_count=%s",
            population_plan.target_total,
            total_excess,
            retired_count,
            extra={
                "event": "virtual_player_overpopulation_retired",
                "target": population_plan.target_total,
                "excess": total_excess,
                "retired_count": retired_count,
            },
        )
    return retired_count


def plan_virtual_player_population(*, now=None) -> dict[str, Any]:
    bootstrap_mode = read_virtual_player_routing().bootstrap_mode
    config = _population_runtime_config_for_bootstrap_mode(bootstrap_mode)
    required_engine_version = 1 if bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE else 2
    now = now or timezone.now()
    active_real_players = _active_real_player_count(now)
    population_plan = _build_population_plan(
        config,
        now=now,
        target_based_membership=required_engine_version == 2,
        required_engine_version=required_engine_version,
    )
    planned_bots = sum(cell.maintained_supply for cell in population_plan.cells)
    maintained_bots = _maintained_bot_count()
    unplanned_bots = max(0, maintained_bots - planned_bots)
    attackable_bots = sum(cell.attackable_supply for cell in population_plan.cells)
    population = config.get("population") or {}
    cell_floor = max(
        0,
        _configured_population_value(
            population,
            "cell_floor",
            legacy_field="min_attackable_per_band",
            default=4,
        ),
    )
    cell_multiplier = max(
        0,
        _configured_population_value(
            population,
            "cell_active_multiplier",
            legacy_field="active_player_multiplier",
            default=2,
        ),
    )
    payload = {
        "enabled": bool(config.get("enabled", True)),
        "regions": _regions(),
        "prestige_bands": list(_prestige_bands(config).keys()),
        "active_real_players": active_real_players,
        "target_bot_total": population_plan.target_total,
        "active_bots": maintained_bots,
        "maintained_bots": maintained_bots,
        "planned_bots": planned_bots,
        "unplanned_bots": unplanned_bots,
        "attackable_bots": attackable_bots,
        "hard_cap": population_plan.hard_cap,
        "region_targets": population_plan.region_targets,
        "config_summary": {
            "active_window_days": max(1, int(population.get("active_window_days") or 7)),
            "cell_floor": cell_floor,
            "cell_active_multiplier": cell_multiplier,
            "region_floor": max(0, _population_config_int(population, "region_floor", 8)),
            "region_active_multiplier": max(
                0,
                _population_config_int(population, "region_active_multiplier", 8),
            ),
            "global_floor": max(0, _population_config_int(population, "global_floor", 32)),
            "global_active_multiplier": max(
                0,
                _population_config_int(population, "global_active_multiplier", 20),
            ),
            "exploration_supply": max(0, int(population.get("exploration_supply") or 0)),
        },
        "cells": [
            {
                "region": cell.region,
                "prestige_band": cell.prestige_band,
                "active_real": cell.active_real,
                "maintained_supply": cell.maintained_supply,
                "attackable_supply": cell.attackable_supply,
                "search_demand": cell.search_demand,
                "target": cell.target,
                "deficit": cell.deficit,
                "structural_deficit": cell.structural_deficit,
                "attackable_target": cell.attackable_target,
                "attackable_deficit": cell.attackable_deficit,
                "excess": cell.excess,
            }
            for cell in population_plan.cells
        ],
        "planned_at": now.isoformat(),
    }
    logger.info(
        "Virtual player population planned: active_real=%s maintained=%s planned=%s attackable=%s target=%s",
        active_real_players,
        maintained_bots,
        planned_bots,
        attackable_bots,
        population_plan.target_total,
        extra={
            "event": "virtual_player_population_planned",
            "active_real_players": active_real_players,
            "maintained_bots": maintained_bots,
            "maintained_count": maintained_bots,
            "planned_bots": planned_bots,
            "unplanned_bots": unplanned_bots,
            "attackable_bots": attackable_bots,
            "target_bot_total": population_plan.target_total,
            "hard_cap": population_plan.hard_cap,
            "region_targets": population_plan.region_targets,
            "reactivated_count": 0,
            "created_count": 0,
            "retired_count": 0,
            "cells": payload["cells"],
        },
    )
    return payload


def _create_backfill_demanded_players(
    *,
    demands: list[dict[str, Any]],
    bands: dict[str, tuple[int, int | None]],
    hard_cap: int,
    limit: int,
    now,
    rng: random.Random,
    config: dict[str, Any] | None = None,
    evaluated_profile_ids: set[int] | None = None,
    ownership_guard: Callable[[], None] | None = None,
) -> int:
    config = config or load_virtual_player_config()
    evaluated_profile_ids = evaluated_profile_ids if evaluated_profile_ids is not None else set()
    created = 0
    reactivated_count = 0
    normalized_demands: list[dict[str, Any]] = []
    invalid_demand_ids: list[int] = []
    for demand in demands:
        demand_id = int(demand.get("id") or 0)
        band_name = str(demand.get("prestige_band") or "")
        region = str(demand.get("region") or "")
        needed = max(0, int(demand.get("needed") or 0))
        if band_name not in bands or region not in _regions() or needed <= 0:
            if demand_id > 0:
                invalid_demand_ids.append(demand_id)
            continue
        normalized_demands.append(
            {
                "id": demand_id,
                "region": region,
                "prestige_band": band_name,
                "needed": needed,
            }
        )

    if invalid_demand_ids:
        if ownership_guard is not None:
            ownership_guard()
        BotBackfillDemand.objects.filter(id__in=invalid_demand_ids).delete()

    for demand in normalized_demands:
        if created >= limit:
            break
        demand_id = int(demand["id"])
        band_name = str(demand.get("prestige_band") or "")
        region = str(demand.get("region") or "")
        low, high = bands[band_name]
        needed = max(0, int(demand.get("needed") or 0))
        created_before_demand = created
        reactivated_before_demand = reactivated_count
        cap_reached = False
        while created < limit and created - created_before_demand < needed:
            seed = rng.randint(1, 2_147_483_647)
            selected_archetype = _weighted_archetype(rng)
            if ownership_guard is not None:
                ownership_guard()
            with transaction.atomic():
                locked_demand = BotBackfillDemand.objects.select_for_update().filter(id=demand_id).first()
                if locked_demand is None or int(locked_demand.needed or 0) <= 0:
                    break
                current_active = _maintained_bot_count()
                if hard_cap > 0 and current_active >= hard_cap:
                    cap_reached = True
                    break
                if ownership_guard is not None:
                    ownership_guard()
                mutation = _reactivate_or_create_virtual_player(
                    region=region,
                    prestige_band=band_name,
                    low=low,
                    high=high,
                    archetype=selected_archetype,
                    growth_seed=seed,
                    now=now,
                    config=config,
                    evaluated_profile_ids=evaluated_profile_ids,
                    ownership_guard=ownership_guard,
                    require_population_deficit=True,
                    include_target_pipeline=True,
                    projection_factory=lambda: _projection_for_band(
                        band_name,
                        low,
                        high,
                        rng,
                        region=region,
                        config=config,
                        sample_seed=seed,
                        archetype=selected_archetype,
                    ),
                )
                if mutation.status is PopulationMutationStatus.CAP_REACHED:
                    cap_reached = True
                    break
                if mutation.profile is None:
                    break
                if mutation.status is PopulationMutationStatus.REACTIVATED:
                    reactivated_count += 1
            created += 1
        if needed > 0:
            created_for_demand = created - created_before_demand
            reactivated_for_demand = reactivated_count - reactivated_before_demand
            newly_created_for_demand = created_for_demand - reactivated_for_demand
            logger.info(
                "Virtual player backfill demand provisioned: region=%s prestige_band=%s processed=%s needed=%s",
                region,
                band_name,
                created_for_demand,
                needed,
                extra={
                    "event": "virtual_player_backfill_demand_provisioned",
                    "region": region,
                    "prestige_band": band_name,
                    "processed_count": created_for_demand,
                    "created_count": newly_created_for_demand,
                    "reactivated_count": reactivated_for_demand,
                    "needed": needed,
                },
            )
        if cap_reached or created >= limit:
            return created
    return created


def roll_virtual_player_population(*, limit: int | None = None, now=None) -> int:
    bootstrap_mode = read_virtual_player_routing().bootstrap_mode
    if bootstrap_mode is BootstrapMode.V2_PAUSED:
        return 0
    if bootstrap_mode is BootstrapMode.V2_ACTIVE:
        return _roll_virtual_player_population_v2(limit=limit, now=now)
    with _population_ownership() as ownership_guard:
        if ownership_guard is None:
            return 0
        return _roll_virtual_player_population_unlocked(
            limit=limit,
            now=now,
            ownership_guard=ownership_guard,
        )


def _roll_virtual_player_population_v2(
    *,
    limit: int | None = None,
    now=None,
) -> int:
    config = _v2_population_runtime_config()
    if not bool(config.get("enabled", True)):
        return 0
    current_time = _demand_now(now)
    population = config.get("population") or {}
    rng = random.Random(int(current_time.timestamp()))
    if limit is None:
        limit = _range_value(
            rng,
            population.get("rolling_batch_size"),
            default=(3, 12),
        )
    normalized_limit = max(0, int(limit))

    sync_mismatched_v2_current_prestige_bands()
    cells = _v2_periodic_population_cells()
    merge_periodic_v2_population_recompute_demands(now=current_time)
    if normalized_limit == 0:
        return 0

    _retire_unsupported_v2_profiles(
        now=current_time,
        limit=normalized_limit,
    )

    processed = 0
    terminal_statuses = {
        PopulationCellReconcileStatus.ROUTING_INACTIVE,
        PopulationCellReconcileStatus.DEFERRED,
        PopulationCellReconcileStatus.CLAIM_LOST,
    }
    for region, prestige_band in cells:
        remaining = normalized_limit - processed
        if remaining <= 0:
            break
        result = reconcile_virtual_player_population_cell(
            region=region,
            prestige_band=prestige_band,
            limit=remaining,
            now=current_time,
        )
        processed += int(result.processed_count)
        if result.status in terminal_statuses:
            break
    return processed


def _roll_virtual_player_population_unlocked(
    *,
    limit: int | None = None,
    now=None,
    ownership_guard: Callable[[], None] | None = None,
) -> int:
    config = load_virtual_player_config()
    if not bool(config.get("enabled", True)):
        return 0

    now = now or timezone.now()
    population = config.get("population") or {}
    bands = _prestige_bands(config)
    rng = random.Random(int(now.timestamp()))
    if limit is None:
        limit = _range_value(rng, population.get("rolling_batch_size"), default=(3, 12))
    limit = max(0, int(limit))
    population_plan = _build_population_plan(
        config,
        now=now,
        required_engine_version=1,
    )
    if _uses_regional_population_planning() and limit > 0:
        rebalance_virtual_player_target_bands(
            population_plan,
            limit=limit,
            required_engine_version=1,
        )
        population_plan = _build_population_plan(
            config,
            now=now,
            required_engine_version=1,
        )
    hard_cap = population_plan.hard_cap
    retired_for_capacity = _retire_excess_population_cells(
        population_plan,
        config=config,
        now=now,
        ownership_guard=ownership_guard,
        required_engine_version=1,
    )
    active_bot_count = _maintained_bot_count()
    if hard_cap > 0 and active_bot_count >= hard_cap:
        return 0

    if limit <= 0:
        return 0

    if not bands:
        return retired_for_capacity

    evaluated_profile_ids: set[int] = set()
    created = _create_backfill_demanded_players(
        demands=[
            dict(row)
            for row in BotBackfillDemand.objects.order_by("region", "prestige_band", "id").values(
                "id",
                "region",
                "prestige_band",
                "needed",
            )[:limit]
        ],
        bands=bands,
        hard_cap=hard_cap,
        limit=limit,
        now=now,
        rng=rng,
        config=config,
        evaluated_profile_ids=evaluated_profile_ids,
        ownership_guard=ownership_guard,
    )

    refreshed_plan = _build_population_plan(
        config,
        now=now,
        required_engine_version=1,
    )
    deficit_cells: list[dict[str, Any]] = [
        {
            "region": cell.region,
            "band_name": cell.prestige_band,
            "low": bands[cell.prestige_band][0],
            "high": bands[cell.prestige_band][1],
            "deficit": cell.deficit,
            "search_demand": cell.search_demand,
        }
        for cell in refreshed_plan.cells
        if cell.prestige_band in bands and cell.deficit > 0
    ]
    while created < limit and deficit_cells:
        progressed = False
        for cell in deficit_cells:
            if created >= limit:
                break
            if int(cell["deficit"]) <= 0:
                continue
            current_active = _maintained_bot_count()
            if hard_cap > 0 and current_active >= hard_cap:
                return created
            seed = rng.randint(1, 2_147_483_647)
            selected_archetype = _weighted_archetype(rng)
            if ownership_guard is not None:
                ownership_guard()
            mutation = _reactivate_or_create_virtual_player(
                region=str(cell["region"]),
                prestige_band=str(cell["band_name"]),
                low=int(cell["low"]),
                high=cell["high"],
                archetype=selected_archetype,
                growth_seed=seed,
                now=now,
                config=config,
                evaluated_profile_ids=evaluated_profile_ids,
                ownership_guard=ownership_guard,
                require_population_deficit=True,
                include_target_pipeline=int(cell["search_demand"]) > 0,
                projection_factory=lambda: _projection_for_band(
                    str(cell["band_name"]),
                    int(cell["low"]),
                    cell["high"],
                    rng,
                    region=str(cell["region"]),
                    config=config,
                    sample_seed=seed,
                    archetype=selected_archetype,
                ),
            )
            if mutation.status is PopulationMutationStatus.CAP_REACHED:
                return created
            if mutation.profile is None:
                continue
            cell["deficit"] = int(cell["deficit"]) - 1
            created += 1
            progressed = True
        if not progressed:
            break
        deficit_cells = [cell for cell in deficit_cells if int(cell["deficit"]) > 0]
    return created


__all__ = [
    "POPULATION_RECOMPUTE_DEFAULT_BATCH_LIMIT",
    "POPULATION_RECOMPUTE_CLAIM_LEASE_SECONDS",
    "POPULATION_RECOMPUTE_FAILURE_BACKOFF_INITIAL_SECONDS",
    "POPULATION_RECOMPUTE_FAILURE_BACKOFF_MAX_SECONDS",
    "PopulationMutationResult",
    "PopulationCellReconcileResult",
    "PopulationCellReconcileStatus",
    "PopulationRecomputeClaim",
    "PopulationRecomputeDemandError",
    "claim_next_population_recompute_demand",
    "claim_population_recompute_demand",
    "create_virtual_player_with_capacity",
    "create_virtual_players_for_band",
    "fail_population_recompute_demand",
    "finalize_population_recompute_demand",
    "get_virtual_player_capacity",
    "merge_real_player_population_recompute_demand",
    "merge_committed_prestige_transition_population_demands",
    "merge_population_recompute_demand",
    "merge_population_recompute_demand_for_prestige",
    "merge_population_recompute_demands",
    "plan_virtual_player_population",
    "reactivate_retired_virtual_player_with_capacity",
    "reactivate_virtual_player_profile",
    "reconcile_virtual_player_population_cell",
    "roll_virtual_player_population",
    "scan_virtual_player_population_demands",
    "virtual_player_prestige_bands",
]
