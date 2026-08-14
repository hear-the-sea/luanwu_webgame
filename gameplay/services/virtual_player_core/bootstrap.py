from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import get_ident
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from gameplay.models import BotProfile, Building, InventoryItem, Manor
from gameplay.services.manor.coordinates import is_occupied_manor_location_conflict
from gameplay.services.manor.core import generate_unique_coordinate
from gameplay.services.manor.naming import ManorNameConflictError
from guests.models import GearItem, Guest

from . import profile_store
from .bootstrap_assets import BootstrapAssetPlanningError, build_bootstrap_asset_targets
from .bootstrap_catalog import BootstrapCatalog, BootstrapCatalogError, load_bootstrap_catalog
from .bootstrap_materializer import BootstrapMaterializationError, materialize_bootstrap_assets
from .config import (
    DEFAULT_VIRTUAL_PLAYER_CONFIG,
    V2_PRESTIGE_BAND_NAMES,
    load_virtual_player_config,
    load_virtual_player_v2_config,
)
from .contracts import BotProjectionConfig
from .identity import build_manor_name_candidate, fallback_manor_name_candidate, select_manor_name_style
from .lifecycle import choose_lifecycle
from .maintenance_rules import (
    bootstrap_historical_age_days,
    guest_count_target_for_profile,
    parse_prestige_band_growth_policy,
)
from .policy_registry import get_policy_release
from .projection import (
    BootstrapBlueprint,
    ProjectionRuleError,
    ReferenceSelection,
    StrengthSummary,
    minimum_strength_cap,
    validate_strength_within_cap,
)
from .random_context import RandomContext
from .reference_snapshots import (
    ReferenceSnapshotError,
    build_strength_summary,
    load_manor_strength_summary,
    select_policy_reference,
    starter_snapshot_int,
)
from .runtime_helpers import range_value
from .strategy import BotDevelopmentPlan, development_plan_catalog_v1, generate_development_plan

logger = logging.getLogger(__name__)

VIRTUAL_PLAYER_COORDINATE_RETRY_LIMIT = 5
V2_BOOTSTRAP_MODE_POLICY_2_DEFAULT = "policy_2_default"


class V2BootstrapError(ValueError):
    pass


_V2_BOOTSTRAP_PERMIT_SEAL = object()


class _V2BootstrapPopulationPermit:
    __slots__ = (
        "_connection",
        "_consumed",
        "_prestige_band",
        "_region",
        "_thread_id",
    )

    def __init__(
        self,
        *,
        region: str,
        prestige_band: str,
        seal: object,
    ) -> None:
        if seal is not _V2_BOOTSTRAP_PERMIT_SEAL:
            raise V2BootstrapError("invalid V2 bootstrap population permit seal")
        connection = transaction.get_connection()
        if not connection.in_atomic_block:
            raise V2BootstrapError("V2 bootstrap population permits require transaction.atomic()")
        self._connection = connection
        self._consumed = False
        self._prestige_band = str(prestige_band)
        self._region = str(region)
        self._thread_id = get_ident()

    def consume(self, *, region: str, prestige_band: str) -> None:
        if self._consumed:
            raise V2BootstrapError("V2 bootstrap population permit was already consumed")
        self._consumed = True
        connection = transaction.get_connection()
        if connection is not self._connection or not connection.in_atomic_block or get_ident() != self._thread_id:
            raise V2BootstrapError("V2 bootstrap population permit left its owning transaction")
        if self._region != str(region) or self._prestige_band != str(prestige_band):
            raise V2BootstrapError("V2 bootstrap population permit does not match the planned cell")


def _issue_v2_bootstrap_population_permit(
    *,
    region: str,
    prestige_band: str,
) -> _V2BootstrapPopulationPermit:
    """Issue the one-shot capability consumed by the population-owned write path."""
    return _V2BootstrapPopulationPermit(
        region=region,
        prestige_band=prestige_band,
        seal=_V2_BOOTSTRAP_PERMIT_SEAL,
    )


@dataclass(frozen=True, slots=True)
class V2BootstrapPlan:
    region: str
    prestige_band: str
    archetype: str
    growth_seed: int
    planned_at: datetime
    engine_version: int
    rng_version: int
    plan_schema_version: int
    policy_version: int
    policy_checksum: str
    band_lower_inclusive: int
    band_upper_exclusive: int | None
    bootstrap_mode: str
    projection: BotProjectionConfig
    development_plan: BotDevelopmentPlan
    blueprint: BootstrapBlueprint

    def __post_init__(self) -> None:
        if not isinstance(self.region, str) or not self.region.strip():
            raise V2BootstrapError("region must be a non-empty string")
        if self.prestige_band not in V2_PRESTIGE_BAND_NAMES:
            raise V2BootstrapError(f"unknown V2 prestige band: {self.prestige_band!r}")
        if self.bootstrap_mode != V2_BOOTSTRAP_MODE_POLICY_2_DEFAULT:
            raise V2BootstrapError("V2 bootstrap uses the policy-2 fixed default envelope")
        if timezone.is_naive(self.planned_at):
            raise V2BootstrapError("planned_at must be timezone-aware")
        if self.engine_version != 2:
            raise V2BootstrapError("V2 bootstrap plan requires engine_version=2")
        for field in (
            "growth_seed",
            "rng_version",
            "plan_schema_version",
            "policy_version",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise V2BootstrapError(f"{field} must be a positive integer")
        if not self.policy_checksum:
            raise V2BootstrapError("policy_checksum must not be empty")
        if self.development_plan.schema_version != self.plan_schema_version:
            raise V2BootstrapError("development plan schema does not match bootstrap plan")
        if self.blueprint.prestige_band != self.prestige_band:
            raise V2BootstrapError("blueprint prestige band does not match bootstrap plan")
        projected_prestige = int(self.projection.prestige)
        if projected_prestige < self.band_lower_inclusive or (
            self.band_upper_exclusive is not None and projected_prestige >= self.band_upper_exclusive
        ):
            raise V2BootstrapError("V2 projection prestige is outside the target band")


def _create_bot_user(*, region: str, growth_seed: int) -> Any:
    User = get_user_model()
    for attempt in range(20):
        suffix = f"{growth_seed}_{timezone.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
        username = f"bot_{region}_{suffix}"[:150]
        user = User(username=username, is_active=False)
        user.set_unusable_password()
        setattr(user, "_signup_region", region)
        setattr(user, "_virtual_player_internal", True)
        setattr(
            user,
            "_signup_manor_name",
            _generate_bot_manor_name(growth_seed=growth_seed, salt=attempt),
        )
        try:
            with transaction.atomic():
                user.save()
        except (IntegrityError, ManorNameConflictError):
            continue
        return user
    raise RuntimeError("Failed to allocate a unique bot manor name after multiple attempts")


def _generate_bot_manor_name(*, growth_seed: int, salt: int = 0) -> str:
    """Generate player-like manor names without visible system markers."""
    for attempt in range(400):
        rng = random.Random(f"{growth_seed}:{salt}:{attempt}")
        style = select_manor_name_style(rng.random())
        candidate = build_manor_name_candidate(rng, style=style, variant=attempt)
        if not Manor.objects.filter(name=candidate).exists():
            return candidate

    fallback_rng = random.Random(uuid.uuid4().hex)
    for _ in range(400):
        candidate = fallback_manor_name_candidate(fallback_rng)
        if not Manor.objects.filter(name=candidate).exists():
            return candidate
    raise RuntimeError("Failed to generate a unique bot manor name")


def _set_unique_location(manor: Manor, *, region: str) -> None:
    x, y = generate_unique_coordinate(region)
    manor.region = region
    manor.coordinate_x = x
    manor.coordinate_y = y


def _save_virtual_player_manor_with_coordinate_retry(
    manor: Manor,
    *,
    region: str,
    update_fields: list[str],
) -> None:
    for attempt in range(VIRTUAL_PLAYER_COORDINATE_RETRY_LIMIT):
        try:
            with transaction.atomic():
                manor.save(update_fields=update_fields)
            return
        except IntegrityError as exc:
            if not is_occupied_manor_location_conflict(exc):
                raise
            if attempt + 1 >= VIRTUAL_PLAYER_COORDINATE_RETRY_LIMIT:
                raise
            _set_unique_location(manor, region=region)


def _backfill_historical_timestamps(
    *,
    user: Any,
    manor: Manor,
    profile: BotProfile,
    rng: random.Random,
    now,
    historical_created_at=None,
) -> None:
    historical_created_at = historical_created_at or (
        manor.last_active_at - timedelta(days=rng.randint(1, 30), hours=rng.randint(0, 23))
    )

    user.__class__.objects.filter(pk=user.pk).update(date_joined=historical_created_at, last_login=manor.last_active_at)
    Manor.objects.filter(pk=manor.pk).update(created_at=historical_created_at)
    Building.objects.filter(manor=manor).update(created_at=historical_created_at, hp_updated_at=manor.last_active_at)
    Guest.objects.filter(manor=manor).update(created_at=historical_created_at, last_hp_recovery_at=manor.last_active_at)
    GearItem.objects.filter(manor=manor).update(acquired_at=historical_created_at)
    InventoryItem.objects.filter(manor=manor).update(created_at=historical_created_at, updated_at=historical_created_at)
    profile_store.touch_profile_updated_at(profile, now=now)

    user.date_joined = historical_created_at
    user.last_login = manor.last_active_at
    manor.created_at = historical_created_at


def lifecycle_dates(now, rng: random.Random, config: dict[str, Any]) -> tuple[Any, Any, Any]:
    lifecycle_personas = config.get("lifecycle_personas") or DEFAULT_VIRTUAL_PLAYER_CONFIG["lifecycle_personas"]
    dates = choose_lifecycle(rng, now, lifecycle_personas)
    lifecycle = config.get("lifecycle") or {}
    next_growth_hours = range_value(rng, lifecycle.get("next_growth_hours"), default=(2, 18))
    next_growth_at = now + timedelta(hours=next_growth_hours, minutes=rng.randint(0, 59))
    return next_growth_at, dates.abandon_at, dates.retire_at


def growth_stage_cap_for_band(prestige_band: str, config: dict[str, Any]) -> int:
    growth = config.get("growth") or {}
    stage_caps = growth.get("stage_caps") or {}
    default_caps = DEFAULT_VIRTUAL_PLAYER_CONFIG["growth"]["stage_caps"]
    raw_cap = stage_caps.get(prestige_band, default_caps.get(prestige_band, max(default_caps.values())))
    return max(1, int(raw_cap or 1))


def _starter_snapshot_int(
    snapshot: Mapping[str, Any],
    field: str,
    *,
    minimum: int = 0,
) -> int:
    try:
        return starter_snapshot_int(snapshot, field, minimum=minimum)
    except ReferenceSnapshotError as exc:
        raise V2BootstrapError(str(exc)) from exc


def _starter_snapshot_projection(
    *,
    snapshot: Mapping[str, Any],
    strength_cap: StrengthSummary,
    band_lower_inclusive: int,
    band_upper_exclusive: int | None,
) -> tuple[BotProjectionConfig, StrengthSummary]:
    required_components = {
        "arena_lineup_power",
        "core_building_level",
        "guest_count",
        "max_guest_level",
        "prestige",
        "troop_total",
    }
    if set(strength_cap.components) != required_components:
        raise V2BootstrapError("reference cap has unsupported strength components")

    def capped(field: str, *, minimum: int = 0) -> int:
        raw = _starter_snapshot_int(snapshot, field, minimum=minimum)
        return min(raw, max(0, int(strength_cap.components[field])))

    prestige = capped("prestige")
    if prestige < band_lower_inclusive:
        raise V2BootstrapError("reference cap cannot produce prestige in the target band")
    if band_upper_exclusive is not None:
        prestige = min(prestige, band_upper_exclusive - 1)
    building_level = capped("core_building_level", minimum=1)
    if building_level < 1:
        raise V2BootstrapError("reference cap cannot produce a core building")
    guest_count = capped("guest_count")
    guest_level = capped("max_guest_level", minimum=1) if guest_count > 0 else 1
    if guest_count > 0 and guest_level < 1:
        raise V2BootstrapError("reference cap cannot produce a valid guest roster")

    arena_lineup_power = min(
        _starter_snapshot_int(snapshot, "arena_lineup_power"),
        max(0, int(strength_cap.components["arena_lineup_power"])),
    )
    troop_total = min(
        _starter_snapshot_int(snapshot, "troop_total"),
        max(0, int(strength_cap.components["troop_total"])),
    )
    composite = arena_lineup_power + 2 * troop_total
    if composite > strength_cap.composite and composite > 0:
        ratio = strength_cap.composite / composite
        arena_lineup_power = int(arena_lineup_power * ratio)
        troop_total = int(troop_total * ratio)

    projection = BotProjectionConfig(
        prestige=prestige,
        building_level=building_level,
        guest_count=guest_count,
        guest_level=guest_level,
        troop_count=troop_total,
    )
    target_strength = build_strength_summary(
        prestige=projection.prestige,
        core_building_level=projection.building_level,
        guest_count=projection.guest_count,
        max_guest_level=(projection.guest_level if projection.guest_count else 0),
        arena_lineup_power=arena_lineup_power,
        troop_total=projection.troop_count,
    )
    validate_strength_within_cap(target_strength, strength_cap)
    return projection, target_strength


def _select_bootstrap_reference(
    *,
    policy_payload: Mapping[str, Any],
    context: RandomContext,
    region: str,
    prestige_band: str,
    band_lower_inclusive: int,
    band_upper_exclusive: int | None,
    now: datetime,
) -> tuple[Mapping[str, Any], StrengthSummary, ReferenceSelection]:
    try:
        return select_policy_reference(
            policy_payload=policy_payload,
            context=context,
            region=region,
            prestige_band=prestige_band,
            band_lower_inclusive=band_lower_inclusive,
            band_upper_exclusive=band_upper_exclusive,
            now=now,
            use_real_player_data=False,
        )
    except ReferenceSnapshotError as exc:
        raise V2BootstrapError(str(exc)) from exc


def build_virtual_player_v2_bootstrap_plan(
    region: str,
    prestige_band: str,
    archetype: str,
    growth_seed: int,
    now: datetime,
) -> V2BootstrapPlan:
    """Build a side-effect-free V2 cold-start plan before population locks are held."""
    if timezone.is_naive(now):
        raise V2BootstrapError("now must be timezone-aware")
    config = load_virtual_player_v2_config()
    if config is None:
        raise V2BootstrapError("bot_development_v2 is not configured")
    if config.engine_version != 2:
        raise V2BootstrapError("configured bootstrap engine is not V2")
    try:
        band = next(item for item in config.bands if item.name == prestige_band)
    except StopIteration as exc:
        raise V2BootstrapError(f"unknown V2 prestige band: {prestige_band!r}") from exc

    configured_policy = config.policy()
    release = get_policy_release(
        version=configured_policy.version,
        expected_checksum=configured_policy.checksum,
    )
    context = RandomContext(
        rng_version=config.rng_version,
        growth_seed=int(growth_seed),
        engine_version=config.engine_version,
        plan_schema_version=config.plan_schema_version,
        policy_version=release.version,
        maintenance_sequence=0,
    )
    development_plan = generate_development_plan(
        context=context,
        archetype=archetype,
        catalog=development_plan_catalog_v1(),
    )
    growth_policy = parse_prestige_band_growth_policy(release.payload.get("prestige_band_growth"))
    historical_age_days = bootstrap_historical_age_days(
        policy=growth_policy,
        prestige_band=prestige_band,
        context=context,
    )
    snapshot, _starter_strength, reference_selection = _select_bootstrap_reference(
        policy_payload=release.payload,
        context=context,
        region=region,
        prestige_band=prestige_band,
        band_lower_inclusive=band.lower_inclusive,
        band_upper_exclusive=band.upper_exclusive,
        now=now,
    )
    projection, target_strength = _starter_snapshot_projection(
        snapshot=snapshot,
        strength_cap=reference_selection.strength_cap,
        band_lower_inclusive=band.lower_inclusive,
        band_upper_exclusive=band.upper_exclusive,
    )
    legacy_config = load_virtual_player_config()
    try:
        catalog = load_bootstrap_catalog(legacy_config)
        assets = build_bootstrap_asset_targets(
            context=context,
            development_plan=development_plan,
            catalog=catalog,
            config=legacy_config,
            projection=projection,
            target_strength=target_strength,
            archetype=str(archetype),
            historical_age_days=historical_age_days,
        )
    except (BootstrapCatalogError, BootstrapAssetPlanningError) as exc:
        raise V2BootstrapError(f"V2 bootstrap asset planning failed: {exc}") from exc
    blueprint = BootstrapBlueprint(
        business_key=f"bootstrap:{region}:{prestige_band}:{archetype}:{int(growth_seed)}",
        prestige_band=prestige_band,
        historical_age_days=historical_age_days,
        target_strength=target_strength,
        reference_selection=reference_selection,
        assets=assets,
    )
    return V2BootstrapPlan(
        region=region,
        prestige_band=prestige_band,
        archetype=archetype,
        growth_seed=int(growth_seed),
        planned_at=now,
        engine_version=config.engine_version,
        rng_version=config.rng_version,
        plan_schema_version=config.plan_schema_version,
        policy_version=release.version,
        policy_checksum=release.checksum,
        band_lower_inclusive=band.lower_inclusive,
        band_upper_exclusive=band.upper_exclusive,
        bootstrap_mode=V2_BOOTSTRAP_MODE_POLICY_2_DEFAULT,
        projection=projection,
        development_plan=development_plan,
        blueprint=blueprint,
    )


def _materialize_virtual_player_v2(
    *,
    plan: V2BootstrapPlan,
    now: datetime,
    config: dict[str, Any],
    catalog: BootstrapCatalog,
) -> BotProfile:
    context = RandomContext(
        rng_version=plan.rng_version,
        growth_seed=plan.growth_seed,
        engine_version=plan.engine_version,
        plan_schema_version=plan.plan_schema_version,
        policy_version=plan.policy_version,
        maintenance_sequence=0,
    )
    user = _create_bot_user(region=plan.region, growth_seed=plan.growth_seed)
    manor = user.manor
    _set_unique_location(manor, region=plan.region)
    manor.prestige = int(plan.blueprint.target_strength.components["prestige"])
    manor.newbie_protection_until = None
    manor.defeat_protection_until = None
    manor.peace_shield_until = None
    recent_days = max(0, int(plan.blueprint.historical_age_days) - 1)
    last_active_rng = context.random(
        domain="lifecycle",
        discriminator="bootstrap-last-active",
    )
    manor.last_active_at = now - timedelta(
        days=recent_days,
        hours=last_active_rng.randint(0, 23),
    )
    _save_virtual_player_manor_with_coordinate_retry(
        manor,
        region=plan.region,
        update_fields=[
            "region",
            "coordinate_x",
            "coordinate_y",
            "prestige",
            "newbie_protection_until",
            "defeat_protection_until",
            "peace_shield_until",
            "last_active_at",
        ],
    )

    account_created_at = now - timedelta(days=int(plan.blueprint.historical_age_days))
    growth_stage = max(1, max(plan.blueprint.assets.building_levels.values()))
    materialize_bootstrap_assets(
        manor=manor,
        assets=plan.blueprint.assets,
        catalog=catalog,
        context=context,
        config=config,
        account_created_at=account_created_at,
        now=now,
        growth_stage=growth_stage,
    )

    lifecycle_rng = random.Random(f"lifecycle:{plan.growth_seed}")
    next_growth_at, abandon_at, retire_at = lifecycle_dates(
        now,
        lifecycle_rng,
        config,
    )
    profile = profile_store.create_active_profile(
        manor=manor,
        archetype=plan.archetype,
        prestige_band=plan.prestige_band,
        current_prestige_band=plan.prestige_band,
        growth_seed=plan.growth_seed,
        growth_stage=growth_stage,
        guest_count_target=guest_count_target_for_profile(
            starter_guest_count=int(plan.projection.guest_count),
            growth_stage=growth_stage,
            roster_focus=float(plan.development_plan.roster_focus),
        ),
        next_growth_at=next_growth_at,
        abandon_at=abandon_at,
        retire_at=retire_at,
        loot_budget_daily=int((config.get("projection") or {}).get("loot_budget_daily", 2_000_000) or 0),
        maintenance_started_at=now,
        last_planned_at=now,
    )
    profile_store.set_inventory_template_keys(
        profile,
        template_keys=[target.template_key for target in plan.blueprint.assets.inventory],
    )
    user.__class__.objects.filter(pk=user.pk).update(
        date_joined=account_created_at,
        last_login=manor.last_active_at,
    )
    Manor.objects.filter(pk=manor.pk).update(created_at=account_created_at)
    profile_store.touch_profile_updated_at(profile, now=now)
    user.date_joined = account_created_at
    user.last_login = manor.last_active_at
    manor.created_at = account_created_at
    return profile


@transaction.atomic
def create_virtual_player(
    *,
    region: str,
    prestige_band: str,
    archetype: str = BotProfile.Archetype.BALANCED,
    growth_seed: int | None = None,
    now=None,
    projection: BotProjectionConfig | None = None,
    start_from_zero: bool = False,
) -> BotProfile:
    raise V2BootstrapError("legacy virtual-player bootstrap is retired; use the policy-2 population materializer")


def _revalidated_bootstrap_strength_cap(
    *,
    plan: V2BootstrapPlan,
    now: datetime,
) -> StrengthSummary:
    config = load_virtual_player_v2_config()
    if config is None or config.engine_version != plan.engine_version:
        raise V2BootstrapError("V2 bootstrap configuration is unavailable")
    try:
        band = next(item for item in config.bands if item.name == plan.prestige_band)
    except StopIteration as exc:
        raise V2BootstrapError(f"unknown V2 prestige band: {plan.prestige_band!r}") from exc
    if band.lower_inclusive != plan.band_lower_inclusive or band.upper_exclusive != plan.band_upper_exclusive:
        raise V2BootstrapError("V2 prestige band changed after bootstrap planning")
    release = get_policy_release(
        version=plan.policy_version,
        expected_checksum=plan.policy_checksum,
    )
    context = RandomContext(
        rng_version=plan.rng_version,
        growth_seed=plan.growth_seed,
        engine_version=plan.engine_version,
        plan_schema_version=plan.plan_schema_version,
        policy_version=plan.policy_version,
        maintenance_sequence=0,
    )
    _snapshot, _starter_strength, current_selection = _select_bootstrap_reference(
        policy_payload=release.payload,
        context=context,
        region=plan.region,
        prestige_band=plan.prestige_band,
        band_lower_inclusive=plan.band_lower_inclusive,
        band_upper_exclusive=plan.band_upper_exclusive,
        now=now,
    )
    effective_cap = minimum_strength_cap(
        plan.blueprint.reference_selection.cap,
        current_selection.cap,
    )
    try:
        validate_strength_within_cap(plan.blueprint.target_strength, effective_cap)
    except ProjectionRuleError as exc:
        raise V2BootstrapError("reference strength cap tightened after bootstrap planning") from exc
    return effective_cap


@transaction.atomic
def create_virtual_player_v2(
    *,
    plan: V2BootstrapPlan,
    population_permit: object | None = None,
    now=None,
) -> BotProfile:
    """Atomically materialize a previously built V2 bootstrap plan."""
    if not isinstance(plan, V2BootstrapPlan):
        raise V2BootstrapError("plan must be a V2BootstrapPlan")
    enrolled_at = now or timezone.now()
    if timezone.is_naive(enrolled_at):
        raise V2BootstrapError("now must be timezone-aware")
    if not isinstance(population_permit, _V2BootstrapPopulationPermit):
        raise V2BootstrapError("valid population materialization permit is required")
    population_permit.consume(
        region=plan.region,
        prestige_band=plan.prestige_band,
    )

    effective_cap = _revalidated_bootstrap_strength_cap(
        plan=plan,
        now=enrolled_at,
    )
    config = load_virtual_player_config()
    try:
        catalog = load_bootstrap_catalog(config, lock=True)
    except BootstrapCatalogError as exc:
        raise V2BootstrapError(f"V2 bootstrap catalog revalidation failed: {exc}") from exc
    if catalog.digest != plan.blueprint.assets.catalog_digest:
        raise V2BootstrapError("V2 bootstrap catalog changed after planning")
    try:
        profile = _materialize_virtual_player_v2(
            plan=plan,
            now=enrolled_at,
            config=config,
            catalog=catalog,
        )
    except BootstrapMaterializationError as exc:
        raise V2BootstrapError(f"V2 bootstrap materialization failed: {exc}") from exc
    actual_strength = load_manor_strength_summary(manor_id=profile.manor_id)
    committed_cap = minimum_strength_cap(
        effective_cap,
        plan.blueprint.target_strength,
    )
    try:
        validate_strength_within_cap(actual_strength, committed_cap)
    except ProjectionRuleError as exc:
        raise V2BootstrapError("materialized V2 profile exceeds the bootstrap strength cap") from exc
    expected_identity = profile_store.get_profile_plan_identity(profile.id)
    if expected_identity is None:
        raise V2BootstrapError("new V2 profile identity is missing")
    result = profile_store.enroll_profile_v2(
        profile.id,
        rng_version=plan.rng_version,
        plan_schema_version=plan.plan_schema_version,
        policy_version=plan.policy_version,
        policy_checksum=plan.policy_checksum,
        development_profile=plan.development_plan,
        enrolled_at=enrolled_at,
        expected_identity=expected_identity,
    )
    if not result.changed:
        raise V2BootstrapError(f"new V2 profile was not enrolled: {result.reason}")
    profile.refresh_from_db()

    def _log_committed_creation() -> None:
        logger.info(
            "Virtual player V2 created: region=%s prestige_band=%s "
            "archetype=%s manor_id=%s sample_tier=%s strength=%s",
            plan.region,
            plan.prestige_band,
            plan.archetype,
            profile.manor_id,
            plan.blueprint.reference_selection.tier.value,
            actual_strength.composite,
            extra={
                "event": "virtual_player_v2_created",
                "region": plan.region,
                "prestige_band": plan.prestige_band,
                "archetype": plan.archetype,
                "manor_id": profile.manor_id,
                "reference_sample_tier": (plan.blueprint.reference_selection.tier.value),
                "reference_sample_count": (plan.blueprint.reference_selection.local_sample_count),
                "reference_anchor_fingerprint": (
                    plan.blueprint.reference_selection.anchor.business_key
                    if plan.blueprint.reference_selection.anchor is not None
                    else ""
                ),
                "strength_composite": actual_strength.composite,
                "strength_components": dict(actual_strength.components),
            },
        )

    transaction.on_commit(_log_committed_creation)
    return profile


__all__ = [
    "V2BootstrapError",
    "V2BootstrapPlan",
    "build_virtual_player_v2_bootstrap_plan",
    "create_virtual_player",
]
