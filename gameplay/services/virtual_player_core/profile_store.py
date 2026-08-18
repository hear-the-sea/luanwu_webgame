from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.utils import timezone

from gameplay.constants import VIRTUAL_PLAYER_REGION_KEYS
from gameplay.models import BotProfile, Manor
from gameplay.services.arena.virtual_protection import with_arena_reserve_guard
from gameplay.services.runtime_configs import runtime_routing_guard_expression
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES

from .config import VirtualPlayerV2Config, load_virtual_player_v2_config
from .contracts import (
    MaintenanceOutcome,
    MaintenanceResult,
    MaintenanceTriggerPolicy,
    StrengthBudgetEntry,
    parse_strength_budget_entries,
    serialize_strength_budget_entries,
)
from .economy import (
    ForcedSettlementBudget,
    ForcedSettlementDecision,
    parse_forced_settlement_budget,
    serialize_forced_settlement_budget,
)
from .projection import StrengthSummary
from .strategy import BotDevelopmentPlan, parse_development_plan

if TYPE_CHECKING:
    from gameplay.services.runtime_configs import RuntimeRoutingSnapshot

RUNTIME_ELIGIBLE_V1_STATES = (
    BotProfile.State.ACTIVE,
    BotProfile.State.SLOWING,
    BotProfile.State.ABANDONED,
    BotProfile.State.RETIRED,
)


class ProfileStoreError(ValueError):
    pass


class ProfileStateConflict(ProfileStoreError):
    pass


class ProfileLockUnavailable(ProfileStoreError):
    pass


@dataclass(frozen=True, slots=True)
class ProfileWriteResult:
    profile_id: int
    changed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ProfilePlanIdentity:
    profile_id: int
    growth_seed: int
    archetype: str
    engine_version: int
    rng_version: int
    plan_schema_version: int
    policy_version: int
    policy_checksum: str
    manor_region: str


@dataclass(frozen=True, slots=True)
class ProfilePrestigeBandCandidate:
    profile_id: int
    current_prestige_band: str
    manor_prestige: int


@dataclass(frozen=True, slots=True)
class ProfilePrestigeBandSyncResult:
    profile_id: int
    changed: bool
    reason: str
    previous_band: str = ""
    current_band: str = ""
    manor_prestige: int | None = None
    region: str = ""


@dataclass(frozen=True, slots=True)
class ExternalStrengthProfileSyncResult:
    profile_id: int
    manor_id: int
    changed: bool
    strength_increased: bool
    previous_band: str
    current_band: str
    region: str
    manor_prestige: int
    last_strength_increase_at: datetime | None
    post_strength_summary: StrengthSummary


@dataclass(frozen=True, slots=True)
class ForcedSettlementBudgetWriteResult:
    profile_id: int
    changed: bool
    budget: ForcedSettlementBudget | None


def _plan_identity_from_row(row: Mapping[str, Any]) -> ProfilePlanIdentity:
    return ProfilePlanIdentity(
        profile_id=int(row["id"]),
        growth_seed=int(row["growth_seed"]),
        archetype=str(row["archetype"]),
        engine_version=int(row["engine_version"]),
        rng_version=int(row["rng_version"]),
        plan_schema_version=int(row["plan_schema_version"]),
        policy_version=int(row["policy_version"]),
        policy_checksum=str(row["policy_checksum"]),
        manor_region=str(row["manor__region"]),
    )


def list_v1_enrollment_candidates(*, after_id: int = 0, limit: int = 100) -> tuple[ProfilePlanIdentity, ...]:
    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 1000:
        raise ProfileStoreError("enrollment batch limit must be between 1 and 1000")
    rows = (
        BotProfile.objects.filter(
            id__gt=max(0, int(after_id)),
            engine_version=1,
            state__in=RUNTIME_ELIGIBLE_V1_STATES,
        )
        .order_by("id")
        .values(
            "id",
            "growth_seed",
            "archetype",
            "engine_version",
            "rng_version",
            "plan_schema_version",
            "policy_version",
            "policy_checksum",
            "manor__region",
        )[:normalized_limit]
    )
    return tuple(_plan_identity_from_row(row) for row in rows)


def get_profile_plan_identity(profile_id: int) -> ProfilePlanIdentity | None:
    row = (
        BotProfile.objects.filter(pk=profile_id)
        .values(
            "id",
            "growth_seed",
            "archetype",
            "engine_version",
            "rng_version",
            "plan_schema_version",
            "policy_version",
            "policy_checksum",
            "manor__region",
        )
        .first()
    )
    return None if row is None else _plan_identity_from_row(row)


def list_prestige_band_reclassification_candidates(
    *,
    after_id: int = 0,
    limit: int = 100,
) -> tuple[ProfilePrestigeBandCandidate, ...]:
    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 1000:
        raise ProfileStoreError("prestige-band reclassification batch limit must be between 1 and 1000")
    rows = (
        BotProfile.objects.filter(id__gt=max(0, int(after_id)))
        .order_by("id")
        .values("id", "current_prestige_band", "manor__prestige")[:normalized_limit]
    )
    return tuple(
        ProfilePrestigeBandCandidate(
            profile_id=int(row["id"]),
            current_prestige_band=str(row["current_prestige_band"]),
            manor_prestige=int(row["manor__prestige"] or 0),
        )
        for row in rows
    )


def list_mismatched_v2_prestige_band_profile_ids(
    *,
    limit: int = 100,
    config: VirtualPlayerV2Config | None = None,
) -> tuple[int, ...]:
    """Return only V2 profiles whose persisted current band disagrees with Manor."""
    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 1000:
        raise ProfileStoreError("prestige-band mismatch batch limit must be between 1 and 1000")
    resolved = config or load_virtual_player_v2_config()
    if resolved is None:
        raise ProfileStoreError("bot_development_v2 is not configured")

    correctly_classified = Q()
    for band in resolved.bands:
        membership = Q(
            current_prestige_band=band.name,
            manor__prestige__gte=band.lower_inclusive,
        )
        if band.upper_exclusive is not None:
            membership &= Q(manor__prestige__lt=band.upper_exclusive)
        correctly_classified |= membership

    return tuple(
        int(profile_id)
        for profile_id in BotProfile.objects.filter(engine_version=2)
        .exclude(correctly_classified)
        .order_by("id")
        .values_list("id", flat=True)[:normalized_limit]
    )


def list_v2_policy_candidates(
    *,
    policy_version: int,
    after_id: int = 0,
    limit: int = 100,
) -> tuple[ProfilePlanIdentity, ...]:
    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 1000:
        raise ProfileStoreError("policy upgrade batch limit must be between 1 and 1000")
    rows = (
        BotProfile.objects.filter(
            id__gt=max(0, int(after_id)),
            engine_version=2,
            policy_version=int(policy_version),
        )
        .order_by("id")
        .values(
            "id",
            "growth_seed",
            "archetype",
            "engine_version",
            "rng_version",
            "plan_schema_version",
            "policy_version",
            "policy_checksum",
            "manor__region",
        )[:normalized_limit]
    )
    return tuple(_plan_identity_from_row(row) for row in rows)


def any_v2_profiles_exist() -> bool:
    return BotProfile.objects.filter(engine_version=2).exists()


def any_non_policy2_profiles_exist() -> bool:
    """Return whether any maintained profile still belongs to the retired runtime."""

    return (
        BotProfile.objects.filter(state__in=VIRTUAL_PROFILE_MAINTAINED_STATES)
        .exclude(engine_version=2, policy_version=2)
        .exists()
    )


def runtime_eligible_v1_profile_count() -> int:
    return BotProfile.objects.filter(
        engine_version=1,
        state__in=RUNTIME_ELIGIBLE_V1_STATES,
    ).count()


def _is_nowait_lock_unavailable(exc: DatabaseError) -> bool:
    cause = exc.__cause__
    sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    if sqlstate == "55P03":
        return True
    error_code = exc.args[0] if exc.args else None
    if error_code == 3572:
        return True
    cause_args = getattr(cause, "args", ())
    return bool(cause_args and cause_args[0] == 3572)


def lock_maintained_profile(
    profile_id: int,
    *,
    skip_locked: bool = False,
    nowait: bool = False,
    expected_v2_routing: RuntimeRoutingSnapshot | None = None,
    include_arena_reserve_guard: bool = False,
) -> BotProfile | None:
    if skip_locked and nowait:
        raise ValueError("skip_locked and nowait are mutually exclusive")
    queryset = BotProfile.objects.select_for_update(
        skip_locked=skip_locked,
        nowait=nowait,
    )
    if expected_v2_routing is not None:
        queryset = queryset.annotate(maintenance_routing_matches=runtime_routing_guard_expression(expected_v2_routing))
    if include_arena_reserve_guard:
        queryset = with_arena_reserve_guard(queryset)
    try:
        return queryset.filter(
            pk=profile_id,
            state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
        ).first()
    except DatabaseError as exc:
        if nowait and _is_nowait_lock_unavailable(exc):
            raise ProfileLockUnavailable from exc
        raise


def create_active_profile(
    *,
    manor: Manor,
    archetype: str,
    prestige_band: str,
    current_prestige_band: str,
    growth_seed: int,
    growth_stage: int,
    guest_count_target: int,
    next_growth_at: datetime,
    abandon_at: datetime,
    retire_at: datetime,
    loot_budget_daily: int,
    maintenance_started_at: datetime,
    last_planned_at: datetime,
) -> BotProfile:
    return BotProfile.objects.create(
        manor=manor,
        archetype=archetype,
        state=BotProfile.State.ACTIVE,
        prestige_band=prestige_band,
        target_prestige_band=prestige_band,
        current_prestige_band=current_prestige_band,
        growth_seed=growth_seed,
        growth_stage=growth_stage,
        guest_count_target=guest_count_target,
        next_growth_at=next_growth_at,
        abandon_at=abandon_at,
        retire_at=retire_at,
        loot_budget_daily=loot_budget_daily,
        maintenance_started_at=maintenance_started_at,
        last_planned_at=last_planned_at,
    )


def _normalized_plan_payload(
    development_profile: BotDevelopmentPlan | Mapping[str, Any],
    *,
    expected_schema_version: int,
) -> dict[str, Any]:
    plan = (
        development_profile
        if isinstance(development_profile, BotDevelopmentPlan)
        else parse_development_plan(development_profile)
    )
    if plan.schema_version != expected_schema_version:
        raise ProfileStoreError(
            f"development profile schema {plan.schema_version} does not match {expected_schema_version}"
        )
    return plan.to_payload()


@transaction.atomic
def enroll_profile_v2(
    profile_id: int,
    *,
    rng_version: int,
    plan_schema_version: int,
    policy_version: int,
    policy_checksum: str,
    development_profile: BotDevelopmentPlan | Mapping[str, Any],
    enrolled_at: datetime,
    expected_identity: ProfilePlanIdentity,
    skip_locked: bool = False,
) -> ProfileWriteResult:
    from .policy_registry import lock_assignable_policy_release

    normalized_rng_version = int(rng_version)
    normalized_plan_schema_version = int(plan_schema_version)
    if isinstance(rng_version, bool) or normalized_rng_version < 1:
        raise ProfileStoreError("rng_version must be a positive integer")
    if isinstance(plan_schema_version, bool) or normalized_plan_schema_version < 1:
        raise ProfileStoreError("plan_schema_version must be a positive integer")
    if timezone.is_naive(enrolled_at):
        raise ProfileStoreError("enrolled_at must be timezone-aware")
    normalized_plan = _normalized_plan_payload(
        development_profile,
        expected_schema_version=normalized_plan_schema_version,
    )
    release = lock_assignable_policy_release(
        version=policy_version,
        expected_checksum=policy_checksum,
    )
    profile = (
        BotProfile.objects.select_for_update(skip_locked=skip_locked)
        .select_related("manor")
        .filter(pk=profile_id)
        .first()
    )
    if profile is None:
        return ProfileWriteResult(profile_id=int(profile_id), changed=False, reason="missing_or_locked")
    if profile.state == BotProfile.State.STALE:
        return ProfileWriteResult(profile_id=profile.id, changed=False, reason="stale_ineligible")
    current_identity = ProfilePlanIdentity(
        profile_id=profile.id,
        growth_seed=profile.growth_seed,
        archetype=profile.archetype,
        engine_version=profile.engine_version,
        rng_version=profile.rng_version,
        plan_schema_version=profile.plan_schema_version,
        policy_version=profile.policy_version,
        policy_checksum=profile.policy_checksum,
        manor_region=str(profile.manor.region),
    )
    if current_identity != expected_identity:
        raise ProfileStateConflict(f"profile {profile.id} enrollment identity changed while plan was prepared")
    if profile.engine_version == 2:
        expected_values = (
            profile.rng_version == normalized_rng_version,
            profile.plan_schema_version == normalized_plan_schema_version,
            profile.policy_version == release.version,
            profile.policy_checksum == release.checksum,
            profile.development_profile == normalized_plan,
        )
        if all(expected_values):
            return ProfileWriteResult(profile_id=profile.id, changed=False, reason="already_enrolled")
        raise ProfileStateConflict(f"V2 profile {profile.id} has a different persistent assignment")
    if expected_identity.engine_version != 1:
        raise ProfileStateConflict(f"profile {profile.id} cannot enroll from engine {expected_identity.engine_version}")
    if current_identity.manor_region not in VIRTUAL_PLAYER_REGION_KEYS:
        raise ProfileStateConflict(
            f"profile {profile.id} manor region is not eligible for V2 enrollment: " f"{current_identity.manor_region}"
        )

    profile.engine_version = 2
    profile.rng_version = normalized_rng_version
    profile.plan_schema_version = normalized_plan_schema_version
    profile.policy_version = release.version
    profile.policy_checksum = release.checksum
    profile.development_profile = normalized_plan
    profile.maintenance_sequence = 0
    profile.strength_budget_entries = []
    profile.forced_settlement_daily_budget = {}
    profile.last_strength_increase_at = enrolled_at
    profile.v2_enrolled_at = enrolled_at
    profile.save(
        update_fields=[
            "engine_version",
            "rng_version",
            "plan_schema_version",
            "policy_version",
            "policy_checksum",
            "development_profile",
            "maintenance_sequence",
            "strength_budget_entries",
            "forced_settlement_daily_budget",
            "last_strength_increase_at",
            "v2_enrolled_at",
            "updated_at",
        ]
    )
    return ProfileWriteResult(profile_id=profile.id, changed=True, reason="enrolled")


@transaction.atomic
def repair_profile_rng(
    profile_id: int,
    *,
    expected_rng_version: int,
    target_rng_version: int,
    supported_rng_versions: frozenset[int] = frozenset({1}),
) -> ProfileWriteResult:
    target = int(target_rng_version)
    if target not in supported_rng_versions:
        raise ProfileStoreError(f"rng_version {target} is not supported")
    profile = BotProfile.objects.select_for_update().filter(pk=profile_id).first()
    if profile is None:
        return ProfileWriteResult(profile_id=int(profile_id), changed=False, reason="missing")
    if profile.engine_version != 2:
        raise ProfileStateConflict(f"profile {profile.id} is not V2")
    if profile.rng_version != int(expected_rng_version):
        raise ProfileStateConflict(
            f"profile {profile.id} RNG changed: expected {expected_rng_version}, found {profile.rng_version}"
        )
    if profile.rng_version == target:
        return ProfileWriteResult(profile_id=profile.id, changed=False, reason="already_repaired")
    profile.rng_version = target
    profile.save(update_fields=["rng_version", "updated_at"])
    return ProfileWriteResult(profile_id=profile.id, changed=True, reason="rng_repaired")


@transaction.atomic
def repair_profile_plan(
    profile_id: int,
    *,
    expected_plan_schema_version: int,
    expected_identity: ProfilePlanIdentity,
    development_profile: BotDevelopmentPlan | Mapping[str, Any],
    apply: bool = True,
) -> ProfileWriteResult:
    normalized_plan = _normalized_plan_payload(
        development_profile,
        expected_schema_version=int(expected_plan_schema_version),
    )
    profile = BotProfile.objects.select_for_update().filter(pk=profile_id).first()
    if profile is None:
        return ProfileWriteResult(profile_id=int(profile_id), changed=False, reason="missing")
    current_identity = ProfilePlanIdentity(
        profile_id=profile.id,
        growth_seed=profile.growth_seed,
        archetype=profile.archetype,
        engine_version=profile.engine_version,
        rng_version=profile.rng_version,
        plan_schema_version=profile.plan_schema_version,
        policy_version=profile.policy_version,
        policy_checksum=profile.policy_checksum,
        manor_region=str(profile.manor.region),
    )
    if current_identity != expected_identity:
        raise ProfileStateConflict(f"profile {profile.id} plan identity changed while repair was prepared")
    if profile.engine_version != 2:
        raise ProfileStateConflict(f"profile {profile.id} is not V2")
    if profile.plan_schema_version != int(expected_plan_schema_version):
        raise ProfileStateConflict(
            f"profile {profile.id} plan schema changed: expected "
            f"{expected_plan_schema_version}, found {profile.plan_schema_version}"
        )
    if profile.development_profile == normalized_plan:
        return ProfileWriteResult(profile_id=profile.id, changed=False, reason="plan_already_valid")
    if not apply:
        return ProfileWriteResult(profile_id=profile.id, changed=True, reason="plan_repair_required")
    profile.development_profile = normalized_plan
    profile.save(update_fields=["development_profile", "updated_at"])
    return ProfileWriteResult(profile_id=profile.id, changed=True, reason="plan_repaired")


@transaction.atomic
def upgrade_profile_policy(
    profile_id: int,
    *,
    expected_policy_version: int,
    expected_policy_checksum: str,
    target_policy_version: int,
    target_policy_checksum: str,
) -> ProfileWriteResult:
    from .policy_registry import lock_assignable_policy_release

    versions = sorted({int(expected_policy_version), int(target_policy_version)})
    releases = {
        version: lock_assignable_policy_release(
            version=version,
            expected_checksum=(
                expected_policy_checksum if version == int(expected_policy_version) else target_policy_checksum
            ),
        )
        for version in versions
    }
    profile = BotProfile.objects.select_for_update().filter(pk=profile_id).first()
    if profile is None:
        return ProfileWriteResult(profile_id=int(profile_id), changed=False, reason="missing")
    if profile.engine_version != 2:
        raise ProfileStateConflict(f"profile {profile.id} is not V2")
    if (
        profile.policy_version != int(expected_policy_version)
        or profile.policy_checksum != str(expected_policy_checksum).strip().lower()
    ):
        raise ProfileStateConflict(f"profile {profile.id} policy assignment changed")
    target = releases[int(target_policy_version)]
    if profile.policy_version == target.version and profile.policy_checksum == target.checksum:
        return ProfileWriteResult(profile_id=profile.id, changed=False, reason="policy_already_assigned")
    profile.policy_version = target.version
    profile.policy_checksum = target.checksum
    profile.save(update_fields=["policy_version", "policy_checksum", "updated_at"])
    if not BotProfile.objects.filter(policy_version=int(expected_policy_version)).exists():
        from .policy_registry import extend_retirement_deadline

        extend_retirement_deadline(
            version=int(expected_policy_version),
            expected_checksum=str(expected_policy_checksum),
        )
    return ProfileWriteResult(profile_id=profile.id, changed=True, reason="policy_upgraded")


def touch_profile_updated_at(profile: BotProfile, *, now: datetime) -> None:
    BotProfile.objects.filter(pk=profile.pk).update(updated_at=now)


@transaction.atomic
def reset_virtual_player_simulation_clock(
    profile_id: int,
    *,
    now: datetime,
    next_recruitment_at: datetime | None,
) -> ProfileWriteResult:
    """Reset only scheduling timestamps for an isolated V2 simulation run.

    The simulation must use the production profile write owner even though it
    runs against a disposable database.  Assets, resources, lifecycle dates,
    and policy assignments are intentionally left untouched.
    """

    if timezone.is_naive(now):
        raise ProfileStoreError("simulation clock reset requires a timezone-aware timestamp")
    if next_recruitment_at is not None and timezone.is_naive(next_recruitment_at):
        raise ProfileStoreError("next_recruitment_at must be timezone-aware")
    profile = BotProfile.objects.select_for_update().filter(pk=profile_id, engine_version=2, policy_version=2).first()
    if profile is None:
        return ProfileWriteResult(profile_id=int(profile_id), changed=False, reason="missing_or_not_v2")
    profile.next_growth_at = now
    profile.next_recruitment_at = next_recruitment_at
    profile.last_planned_at = None
    profile.maintenance_started_at = now
    profile.maintenance_stopped_at = None
    profile.save(
        update_fields=[
            "next_growth_at",
            "next_recruitment_at",
            "last_planned_at",
            "maintenance_started_at",
            "maintenance_stopped_at",
            "updated_at",
        ]
    )
    return ProfileWriteResult(profile_id=profile.id, changed=True, reason="simulation_clock_reset")


def reactivate_profile(
    profile: BotProfile,
    *,
    now: datetime,
    next_growth_at: datetime,
    abandon_at: datetime,
    retire_at: datetime,
) -> None:
    profile.state = BotProfile.State.ACTIVE
    profile.next_growth_at = next_growth_at
    profile.abandon_at = abandon_at
    profile.retire_at = retire_at
    profile.maintenance_started_at = now
    profile.maintenance_stopped_at = None
    profile.last_planned_at = now
    profile.save(
        update_fields=[
            "state",
            "next_growth_at",
            "abandon_at",
            "retire_at",
            "maintenance_started_at",
            "maintenance_stopped_at",
            "last_planned_at",
            "updated_at",
        ]
    )


def set_current_prestige_band(profile: BotProfile, *, prestige_band: str) -> None:
    profile.current_prestige_band = prestige_band
    profile.save(update_fields=["current_prestige_band", "updated_at"])


@transaction.atomic
def sync_current_prestige_band_from_manor(
    profile_id: int,
    *,
    config: VirtualPlayerV2Config | None = None,
    skip_locked: bool = False,
) -> ProfilePrestigeBandSyncResult:
    """Lock Profile then Manor and classify only the current band from persisted prestige."""
    resolved = config or load_virtual_player_v2_config()
    if resolved is None:
        raise ProfileStoreError("bot_development_v2 is not configured")
    profile = BotProfile.objects.select_for_update(skip_locked=skip_locked).filter(pk=int(profile_id)).first()
    if profile is None:
        return ProfilePrestigeBandSyncResult(
            profile_id=int(profile_id),
            changed=False,
            reason="missing_or_locked",
        )
    manor = Manor.objects.select_for_update().only("id", "prestige", "region").get(pk=profile.manor_id)
    prestige = int(manor.prestige or 0)
    target_band = resolved.band_for_prestige(prestige).name
    previous_band = str(profile.current_prestige_band)
    if previous_band == target_band:
        return ProfilePrestigeBandSyncResult(
            profile_id=profile.id,
            changed=False,
            reason="already_classified",
            previous_band=previous_band,
            current_band=target_band,
            manor_prestige=prestige,
            region=str(manor.region),
        )
    set_current_prestige_band(profile, prestige_band=target_band)
    return ProfilePrestigeBandSyncResult(
        profile_id=profile.id,
        changed=True,
        reason="current_band_reclassified",
        previous_band=previous_band,
        current_band=target_band,
        manor_prestige=prestige,
        region=str(manor.region),
    )


@transaction.atomic
def reconcile_external_strength_change(
    profile_id: int,
    *,
    pre_strength_summary: StrengthSummary,
    origin_committed_at: datetime,
    config: VirtualPlayerV2Config | None = None,
) -> ExternalStrengthProfileSyncResult:
    """Lock Profile then Manor and record a committed external strength result."""
    if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1:
        raise ProfileStoreError("profile_id must be a positive integer")
    if not isinstance(pre_strength_summary, StrengthSummary):
        raise ProfileStoreError("pre_strength_summary must be a StrengthSummary")
    if timezone.is_naive(origin_committed_at):
        raise ProfileStoreError("origin_committed_at must be timezone-aware")
    committed_at = origin_committed_at.astimezone(UTC)
    resolved = config or load_virtual_player_v2_config()
    if resolved is None:
        raise ProfileStoreError("bot_development_v2 is not configured")

    profile = BotProfile.objects.select_for_update().filter(pk=profile_id).first()
    if profile is None:
        raise ProfileStateConflict(f"profile {profile_id} does not exist")
    manor = Manor.objects.select_for_update().only("id", "prestige", "region").get(pk=profile.manor_id)

    from .reference_snapshots import load_manor_strength_summary

    post_strength_summary = load_manor_strength_summary(manor_id=manor.id)
    if pre_strength_summary.components.keys() != post_strength_summary.components.keys():
        raise ProfileStoreError("external strength summary component keys changed during reconciliation")
    strength_increased = bool(
        post_strength_summary.composite > pre_strength_summary.composite
        or any(
            post_strength_summary.components[key] > pre_strength_summary.components[key]
            for key in pre_strength_summary.components
        )
    )
    try:
        current_band = resolved.band_for_prestige(int(manor.prestige or 0)).name
    except ValueError as exc:
        raise ProfileStoreError("persisted Manor prestige is outside the canonical V2 bands") from exc

    previous_band = str(profile.current_prestige_band)
    update_fields: list[str] = []
    if previous_band != current_band:
        profile.current_prestige_band = current_band
        update_fields.append("current_prestige_band")
    if strength_increased and (
        profile.last_strength_increase_at is None or profile.last_strength_increase_at < committed_at
    ):
        profile.last_strength_increase_at = committed_at
        update_fields.append("last_strength_increase_at")
    if update_fields:
        profile.save(update_fields=[*update_fields, "updated_at"])

    return ExternalStrengthProfileSyncResult(
        profile_id=int(profile.id),
        manor_id=int(manor.id),
        changed=bool(update_fields),
        strength_increased=strength_increased,
        previous_band=previous_band,
        current_band=current_band,
        region=str(manor.region),
        manor_prestige=int(manor.prestige or 0),
        last_strength_increase_at=profile.last_strength_increase_at,
        post_strength_summary=post_strength_summary,
    )


def record_maintenance_growth(
    profile: BotProfile,
    *,
    growth_stage: int,
    next_growth_at: datetime,
    last_planned_at: datetime,
) -> None:
    profile.growth_stage = growth_stage
    profile.next_growth_at = next_growth_at
    profile.last_planned_at = last_planned_at
    profile.save(
        update_fields=[
            "growth_stage",
            "next_growth_at",
            "last_planned_at",
            "updated_at",
        ]
    )


def record_forced_settlement_budget(
    profile: BotProfile,
    *,
    decision: ForcedSettlementDecision,
) -> ForcedSettlementBudgetWriteResult:
    """在 Profile 行锁内持久化已由 economy 决定的强制结算预算。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("record_forced_settlement_budget must be called inside transaction.atomic()")
    if profile.engine_version != 2:
        raise ProfileStateConflict(f"profile {profile.id} is not V2")
    if not isinstance(decision, ForcedSettlementDecision):
        raise ProfileStoreError("decision must be a ForcedSettlementDecision")

    current = parse_forced_settlement_budget(profile.forced_settlement_daily_budget)
    if current != decision.budget_before:
        raise ProfileStateConflict(f"profile {profile.id} forced-settlement budget changed while planned")

    after = decision.budget_after
    if decision.combined_units == 0:
        if after != current:
            raise ProfileStoreError("zero forced settlement must preserve the existing budget")
    elif after is None:
        raise ProfileStoreError("positive forced settlement requires a resulting budget")
    else:
        if current is not None and current.utc_date == after.utc_date:
            silver_before = current.silver_units
            grain_before = current.grain_units
        else:
            silver_before = 0
            grain_before = 0
        if (
            after.silver_units - silver_before != decision.silver_units
            or after.grain_units - grain_before != decision.grain_units
        ):
            raise ProfileStoreError("forced-settlement budget delta must match the applied resource delta")

    payload = serialize_forced_settlement_budget(after)
    if profile.forced_settlement_daily_budget == payload:
        return ForcedSettlementBudgetWriteResult(
            profile_id=profile.id,
            changed=False,
            budget=after,
        )
    profile.forced_settlement_daily_budget = payload
    profile.save(update_fields=["forced_settlement_daily_budget", "updated_at"])
    return ForcedSettlementBudgetWriteResult(
        profile_id=profile.id,
        changed=True,
        budget=after,
    )


def commit_maintenance_cycle(
    profile: BotProfile,
    *,
    trigger_policy: MaintenanceTriggerPolicy,
    expected_sequence: int,
    now: datetime,
    outcome: MaintenanceOutcome,
    expected_strength_budget_entries: tuple[StrengthBudgetEntry, ...],
    strength_budget_entries_after: tuple[StrengthBudgetEntry, ...],
    expected_last_strength_increase_at: datetime | None,
    last_strength_increase_at_after: datetime | None,
    next_growth_at_after: datetime | None,
    action_kind: str = "",
    reason: str = "",
    shadow_cost: Mapping[str, int] | None = None,
    target_id: int | None = None,
    scheduled_cycle_slot_due: bool = False,
) -> MaintenanceResult:
    """提交一个已锁 V2 Profile 的完整维护周期元数据。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("commit_maintenance_cycle must be called inside transaction.atomic()")
    if not isinstance(trigger_policy, MaintenanceTriggerPolicy):
        raise ProfileStoreError("trigger_policy must be a MaintenanceTriggerPolicy")
    if profile.engine_version != 2:
        raise ProfileStateConflict(f"profile {profile.id} is not V2")
    if isinstance(expected_sequence, bool) or not isinstance(expected_sequence, int) or expected_sequence < 0:
        raise ProfileStoreError("expected_sequence must be a non-negative integer")
    if profile.maintenance_sequence != expected_sequence:
        raise ProfileStateConflict(
            f"profile {profile.id} maintenance sequence changed: expected "
            f"{expected_sequence}, found {profile.maintenance_sequence}"
        )
    if type(scheduled_cycle_slot_due) is not bool:
        raise ProfileStoreError("scheduled_cycle_slot_due must be a boolean")
    if not scheduled_cycle_slot_due and not trigger_policy.is_due(next_growth_at=profile.next_growth_at, now=now):
        raise ProfileStateConflict(f"profile {profile.id} is not due for maintenance")

    normalized_outcome = MaintenanceOutcome(outcome)
    if not trigger_policy.advances_sequence(normalized_outcome):
        raise ProfileStoreError("only APPLIED or NO_ACTION outcomes may commit a maintenance cycle")
    current_budget_entries = parse_strength_budget_entries(
        profile.strength_budget_entries,
        now=now,
    )
    if current_budget_entries != tuple(expected_strength_budget_entries):
        raise ProfileStateConflict(f"profile {profile.id} strength budget changed while planned")
    normalized_budget_after = parse_strength_budget_entries(
        serialize_strength_budget_entries(tuple(strength_budget_entries_after)),
        now=now,
    )
    if profile.last_strength_increase_at != expected_last_strength_increase_at:
        raise ProfileStateConflict(f"profile {profile.id} strength timestamp changed while planned")

    sequence_after = expected_sequence + 1
    result = MaintenanceResult(
        outcome=normalized_outcome,
        trigger=trigger_policy.trigger,
        profile_id=profile.id,
        sequence_before=expected_sequence,
        sequence_after=sequence_after,
        schedule_disposition=trigger_policy.schedule_disposition,
        next_growth_at_before=profile.next_growth_at,
        next_growth_at_after=next_growth_at_after,
        action_kind=action_kind,
        reason=reason,
        shadow_cost=(shadow_cost or {}),
        target_id=target_id,
        scheduled_cycle_slot_due=scheduled_cycle_slot_due,
    )
    committed_next_growth_at = result.next_growth_at_after
    if committed_next_growth_at is None:
        raise ProfileStoreError("committed maintenance requires a non-null next_growth_at")
    profile.maintenance_sequence = sequence_after
    profile.strength_budget_entries = serialize_strength_budget_entries(normalized_budget_after)
    profile.last_strength_increase_at = last_strength_increase_at_after
    profile.next_growth_at = committed_next_growth_at
    profile.last_planned_at = now
    profile.save(
        update_fields=[
            "maintenance_sequence",
            "strength_budget_entries",
            "last_strength_increase_at",
            "next_growth_at",
            "last_planned_at",
            "updated_at",
        ]
    )
    return result


def set_next_growth_at(profile: BotProfile, *, next_growth_at: datetime) -> None:
    profile.next_growth_at = next_growth_at
    profile.save(update_fields=["next_growth_at", "updated_at"])


def transition_profile(
    profile: BotProfile,
    *,
    state: str,
    next_growth_at: datetime,
    last_planned_at: datetime | None = None,
) -> None:
    profile.state = state
    profile.next_growth_at = next_growth_at
    update_fields = ["state", "next_growth_at"]
    if last_planned_at is not None:
        profile.last_planned_at = last_planned_at
        update_fields.append("last_planned_at")
    profile.save(update_fields=[*update_fields, "updated_at"])


def retarget_profiles(
    profile_ids: list[int],
    *,
    region: str,
    donor_filter: Q,
    protected_manor_ids: set[int],
    target_prestige_band: str,
) -> int:
    return (
        BotProfile.objects.filter(
            id__in=profile_ids,
            state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
            manor__region=region,
            arena_virtual_reserve__isnull=True,
        )
        .exclude(manor_id__in=protected_manor_ids)
        .filter(donor_filter)
        .update(
            target_prestige_band=target_prestige_band,
            prestige_band=target_prestige_band,
        )
    )


def retire_profiles(
    profile_ids: list[int],
    *,
    now: datetime,
    protected_manor_ids: set[int],
    region: str | None = None,
    membership_filter: Q | None = None,
) -> int:
    queryset = BotProfile.objects.filter(
        id__in=profile_ids,
        state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
        arena_virtual_reserve__isnull=True,
    )
    if region is not None:
        queryset = queryset.filter(manor__region=region)
    if membership_filter is not None:
        queryset = queryset.filter(membership_filter)
    return queryset.exclude(manor_id__in=protected_manor_ids).update(
        state=BotProfile.State.RETIRED,
        next_growth_at=now,
        maintenance_stopped_at=now,
    )


def mark_profiles_stale(profile_ids: list[int] | tuple[int, ...], *, now: datetime) -> int:
    normalized_ids = list(dict.fromkeys(int(profile_id) for profile_id in profile_ids))
    if not normalized_ids:
        return 0
    return (
        BotProfile.objects.filter(id__in=normalized_ids)
        .exclude(state=BotProfile.State.STALE)
        .update(
            state=BotProfile.State.STALE,
            next_growth_at=now,
            maintenance_stopped_at=now,
        )
    )


def defer_profile_retirement(profile: BotProfile, *, now: datetime, retry_after: timedelta) -> None:
    next_growth_at = now + retry_after
    if profile.next_growth_at == next_growth_at:
        return
    profile.next_growth_at = next_growth_at
    profile.save(update_fields=["next_growth_at", "updated_at"])


def mark_profile_retired(profile: BotProfile, *, now: datetime) -> None:
    profile.state = BotProfile.State.RETIRED
    profile.next_growth_at = now
    profile.maintenance_stopped_at = now
    profile.save(
        update_fields=[
            "state",
            "next_growth_at",
            "maintenance_stopped_at",
            "updated_at",
        ]
    )


def record_arena_participation(profile_ids: list[int] | tuple[int, ...], *, participated_at: datetime) -> int:
    normalized_ids = list(dict.fromkeys(int(profile_id) for profile_id in profile_ids))
    if not normalized_ids:
        return 0
    return BotProfile.objects.filter(id__in=normalized_ids).update(
        last_arena_participated_at=participated_at,
        arena_participation_count=F("arena_participation_count") + 1,
    )


def set_inventory_template_keys(profile: BotProfile, *, template_keys: list[str]) -> None:
    normalized_keys = [str(key) for key in template_keys]
    if profile.inventory_template_keys == normalized_keys:
        return
    profile.inventory_template_keys = normalized_keys
    profile.save(update_fields=["inventory_template_keys", "updated_at"])


__all__ = [
    "ExternalStrengthProfileSyncResult",
    "ForcedSettlementBudgetWriteResult",
    "ProfilePlanIdentity",
    "ProfilePrestigeBandCandidate",
    "ProfilePrestigeBandSyncResult",
    "ProfileLockUnavailable",
    "ProfileStateConflict",
    "ProfileStoreError",
    "ProfileWriteResult",
    "RUNTIME_ELIGIBLE_V1_STATES",
    "any_v2_profiles_exist",
    "create_active_profile",
    "commit_maintenance_cycle",
    "defer_profile_retirement",
    "lock_maintained_profile",
    "mark_profiles_stale",
    "mark_profile_retired",
    "reactivate_profile",
    "reconcile_external_strength_change",
    "enroll_profile_v2",
    "get_profile_plan_identity",
    "list_v1_enrollment_candidates",
    "list_v2_policy_candidates",
    "list_prestige_band_reclassification_candidates",
    "list_mismatched_v2_prestige_band_profile_ids",
    "repair_profile_plan",
    "repair_profile_rng",
    "record_arena_participation",
    "record_forced_settlement_budget",
    "record_maintenance_growth",
    "retarget_profiles",
    "retire_profiles",
    "runtime_eligible_v1_profile_count",
    "set_current_prestige_band",
    "set_inventory_template_keys",
    "set_next_growth_at",
    "touch_profile_updated_at",
    "transition_profile",
    "sync_current_prestige_band_from_manor",
    "upgrade_profile_policy",
]
