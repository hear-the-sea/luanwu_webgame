"""
庄园和建筑管理服务
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import BuildingConcurrentUpgradeLimitError, BuildingMaxLevelError, BuildingUpgradingError
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS
from core.utils.time_scale import scale_duration
from gameplay.services.action_points import ACTION_POINT_BUILDING_UPGRADE_COST, consume_action_points
from gameplay.services.manor.bootstrap import ManorNotFoundError as _ManorNotFoundError
from gameplay.services.manor.bootstrap import (
    _deliver_active_global_mail_campaigns as __deliver_active_global_mail_campaigns,
)
from gameplay.services.manor.bootstrap import bootstrap_buildings as _bootstrap_buildings
from gameplay.services.manor.bootstrap import bootstrap_manor as _bootstrap_manor
from gameplay.services.manor.bootstrap import ensure_buildings_exist as _ensure_buildings_exist
from gameplay.services.manor.bootstrap import ensure_manor as _ensure_manor
from gameplay.services.manor.bootstrap import generate_unique_coordinate as _generate_unique_coordinate
from gameplay.services.manor.bootstrap import get_manor as _get_manor
from gameplay.services.manor.naming import BANNED_WORDS as _BANNED_WORDS
from gameplay.services.manor.naming import MANOR_MESSAGE_BEST_EFFORT_EXCEPTIONS
from gameplay.services.manor.naming import MANOR_NAME_MAX_LENGTH as _MANOR_NAME_MAX_LENGTH
from gameplay.services.manor.naming import MANOR_NAME_MIN_LENGTH as _MANOR_NAME_MIN_LENGTH
from gameplay.services.manor.naming import ManorNameConflictError as _ManorNameConflictError
from gameplay.services.manor.naming import ManorRenameItemError as _ManorRenameItemError
from gameplay.services.manor.naming import ManorRenameValidationError as _ManorRenameValidationError
from gameplay.services.manor.naming import get_rename_card_count as _get_rename_card_count
from gameplay.services.manor.naming import is_manor_name_available as _is_manor_name_available
from gameplay.services.manor.naming import rename_manor as _rename_manor
from gameplay.services.manor.naming import validate_manor_name as _validate_manor_name

from ...constants import BUILDING_MAX_LEVELS, MAX_CONCURRENT_BUILDING_UPGRADES, BuildingKeys
from ...models import ArenaTournament, Building, Manor, Message, MissionRun, RaidRun, ResourceEvent, ScoutRecord
from ..utils.cache import invalidate_home_stats_cache
from ..utils.notifications import notify_user
from . import refresh as _refresh

if TYPE_CHECKING:
    from ..resources import ResourceProductionBasis

CAPACITY_BASE = 20000
CAPACITY_GROWTH_SILVER = 1.299657
CAPACITY_GROWTH_GRAIN = 1.3905


class BuildingUpgradeQuoteStaleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BuildingUpgradeQuote:
    manor_id: int
    building_id: int
    building_type_id: int
    building_key: str
    building_name: str
    current_level: int
    target_level: int
    max_level: int | None
    base_cost: tuple[tuple[str, int], ...]
    resource_cost: tuple[tuple[str, int], ...]
    cost_reduction: float
    base_duration: int
    duration_seconds: int
    upgrading_count: int

    def to_payload(self) -> dict[str, object]:
        return {
            "base_cost": dict(self.base_cost),
            "base_duration": self.base_duration,
            "building_id": self.building_id,
            "building_key": self.building_key,
            "building_name": self.building_name,
            "building_type_id": self.building_type_id,
            "cost_reduction": self.cost_reduction,
            "current_level": self.current_level,
            "duration_seconds": self.duration_seconds,
            "manor_id": self.manor_id,
            "max_level": self.max_level,
            "resource_cost": dict(self.resource_cost),
            "target_level": self.target_level,
            "upgrading_count": self.upgrading_count,
        }


@dataclass(frozen=True, slots=True)
class BuildingUpgradeResult:
    manor_id: int
    building_id: int
    building_key: str
    previous_level: int
    level: int
    resource_cost: tuple[tuple[str, int], ...]
    prestige_gained: int
    silver_capacity: int
    grain_capacity: int

    def to_payload(self) -> dict[str, object]:
        return {
            "building_id": self.building_id,
            "building_key": self.building_key,
            "grain_capacity": self.grain_capacity,
            "level": self.level,
            "manor_id": self.manor_id,
            "prestige_gained": self.prestige_gained,
            "previous_level": self.previous_level,
            "resource_cost": dict(self.resource_cost),
            "silver_capacity": self.silver_capacity,
        }


def calculate_building_capacity(level: int, is_silver_vault: bool = False) -> int:
    growth = CAPACITY_GROWTH_SILVER if is_silver_vault else CAPACITY_GROWTH_GRAIN
    return int(CAPACITY_BASE * (growth ** (level - 1)))


logger = logging.getLogger(__name__)

ManorNotFoundError = _ManorNotFoundError
bootstrap_buildings = _bootstrap_buildings
bootstrap_manor = _bootstrap_manor
ensure_buildings_exist = _ensure_buildings_exist
ensure_manor = _ensure_manor
generate_unique_coordinate = _generate_unique_coordinate
get_manor = _get_manor

BANNED_WORDS = _BANNED_WORDS
MANOR_NAME_MAX_LENGTH = _MANOR_NAME_MAX_LENGTH
MANOR_NAME_MIN_LENGTH = _MANOR_NAME_MIN_LENGTH
ManorNameConflictError = _ManorNameConflictError
ManorRenameItemError = _ManorRenameItemError
ManorRenameValidationError = _ManorRenameValidationError
get_rename_card_count = _get_rename_card_count
is_manor_name_available = _is_manor_name_available
rename_manor = _rename_manor
validate_manor_name = _validate_manor_name
_deliver_active_global_mail_campaigns = __deliver_active_global_mail_campaigns

_LOCAL_REFRESH_FALLBACK: dict[int, float] = {}
_LOCAL_REFRESH_FALLBACK_LOCK = Lock()
_LOCAL_REFRESH_FALLBACK_MAX_SIZE = 10000
_LOCAL_REFRESH_FALLBACK_CLEANUP_BATCH = 2000
_LOCAL_REFRESH_FALLBACK_EVICT_COUNT = 1000


def _cleanup_local_fallback_cache(now_monotonic: float, stale_threshold: float) -> None:
    _refresh.cleanup_local_fallback_cache(
        _LOCAL_REFRESH_FALLBACK,
        max_size=_LOCAL_REFRESH_FALLBACK_MAX_SIZE,
        cleanup_batch=_LOCAL_REFRESH_FALLBACK_CLEANUP_BATCH,
        evict_count=_LOCAL_REFRESH_FALLBACK_EVICT_COUNT,
        now_monotonic=now_monotonic,
        stale_threshold=stale_threshold,
    )


def _should_skip_refresh_by_local_fallback(manor_id: int, min_interval: int) -> bool:
    return _refresh.should_skip_refresh_by_local_fallback(
        _LOCAL_REFRESH_FALLBACK,
        state_lock=_LOCAL_REFRESH_FALLBACK_LOCK,
        max_size=_LOCAL_REFRESH_FALLBACK_MAX_SIZE,
        cleanup_batch=_LOCAL_REFRESH_FALLBACK_CLEANUP_BATCH,
        evict_count=_LOCAL_REFRESH_FALLBACK_EVICT_COUNT,
        manor_id=manor_id,
        min_interval=min_interval,
        monotonic_func=time.monotonic,
    )


def _has_due_manor_refresh_work(manor_id: int, now: datetime | None = None) -> bool:
    return _refresh.has_due_manor_refresh_work(
        mission_run_model=MissionRun,
        scout_record_model=ScoutRecord,
        raid_run_model=RaidRun,
        arena_tournament_model=ArenaTournament,
        manor_id=manor_id,
        now=now or timezone.now(),
        logger=logger,
    )


def _noop_manor_step(_manor: Manor) -> None:
    return None


def _run_manor_refresh(
    manor: Manor,
    *,
    prefer_async: bool,
    include_activity_refresh: bool,
    sync_resource_projection_func: Callable[[Manor], None],
) -> None:
    from ..arena.core import refresh_arena_activity
    from ..missions import refresh_mission_runs
    from ..raid import refresh_raid_runs, refresh_scout_records

    _refresh.refresh_manor_state(
        manor,
        prefer_async=prefer_async,
        include_activity_refresh=include_activity_refresh,
        settings_obj=settings,
        cache_backend=cache,
        logger=logger,
        timezone_module=timezone,
        finalize_upgrades_func=finalize_upgrades,
        has_due_manor_refresh_work_func=_has_due_manor_refresh_work,
        should_skip_refresh_by_local_fallback_func=_should_skip_refresh_by_local_fallback,
        sync_resource_production_func=sync_resource_projection_func,
        refresh_mission_runs_func=refresh_mission_runs,
        refresh_scout_records_func=refresh_scout_records,
        refresh_raid_runs_func=refresh_raid_runs,
        refresh_arena_activity_func=refresh_arena_activity,
    )


def refresh_manor_state(
    manor: Manor,
    *,
    prefer_async: bool = False,
    include_activity_refresh: bool = False,
) -> None:
    from ..resources import sync_resource_production

    def sync_resource_projection(current_manor: Manor) -> None:
        sync_resource_production(current_manor)

    _run_manor_refresh(
        manor,
        prefer_async=prefer_async,
        include_activity_refresh=include_activity_refresh,
        sync_resource_projection_func=sync_resource_projection,
    )


def project_manor_activity_for_read(
    manor: Manor,
    *,
    prefer_async: bool = False,
) -> ResourceProductionBasis | None:
    """
    Apply the read-side manor projection without mutating activity state.

    页面 GET 只能做读侧资源投影；mission / scout / raid / arena 的状态收口
    必须继续走显式 refresh / finalize / task 入口，不能再挂回页面读取链路。
    """
    del prefer_async
    from ..resources import project_resource_production_for_read

    return project_resource_production_for_read(manor)


def _require_building_upgrade_atomic() -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("apply_building_upgrade_locked must be called inside transaction.atomic()")


def _validate_building_upgrade_ownership(manor: Manor, building: Building) -> None:
    manor_id = getattr(manor, "pk", None)
    building_id = getattr(building, "pk", None)
    if (
        isinstance(manor_id, bool)
        or not isinstance(manor_id, int)
        or manor_id < 1
        or isinstance(building_id, bool)
        or not isinstance(building_id, int)
        or building_id < 1
    ):
        raise ValueError("building upgrade requires persisted Manor and Building rows")
    if int(building.manor_id) != manor_id:
        raise ValueError("building does not belong to the supplied Manor")


def quote_building_upgrade(
    manor: Manor,
    building: Building,
    *,
    buildings: Sequence[Building] | None = None,
    technology_levels: Mapping[str, int] | None = None,
) -> BuildingUpgradeQuote:
    """Validate and freeze the current one-level building upgrade inputs."""

    from ..technology import get_building_cost_reduction, get_tech_bonus_from_levels

    _validate_building_upgrade_ownership(manor, building)
    if building.is_upgrading:
        raise BuildingUpgradingError()

    building_key = str(building.building_type.key)
    building_name = str(building.building_type.name)
    current_level = int(building.level)
    max_level = BUILDING_MAX_LEVELS.get(building_key)
    if max_level is not None and current_level >= max_level:
        raise BuildingMaxLevelError(building_name, max_level)

    if buildings is None:
        upgrading_count = Building.objects.filter(
            manor_id=manor.pk,
            is_upgrading=True,
        ).count()
    else:
        building_snapshot = tuple(buildings)
        if any(int(candidate.manor_id) != int(manor.pk) for candidate in building_snapshot):
            raise ValueError("building snapshot contains a row from another Manor")
        if not any(int(candidate.pk) == int(building.pk) for candidate in building_snapshot):
            raise BuildingUpgradeQuoteStaleError("building snapshot does not contain the quoted building")
        upgrading_count = sum(bool(candidate.is_upgrading) for candidate in building_snapshot)
    if upgrading_count >= MAX_CONCURRENT_BUILDING_UPGRADES:
        raise BuildingConcurrentUpgradeLimitError(MAX_CONCURRENT_BUILDING_UPGRADES)

    base_cost = {str(resource): int(amount) for resource, amount in building.next_level_cost().items()}
    cost_reduction = float(
        get_building_cost_reduction(manor)
        if technology_levels is None
        else get_tech_bonus_from_levels(
            dict(technology_levels),
            "building_cost_reduction",
        )
    )
    reduction_multiplier = max(0.0, 1.0 - cost_reduction)
    resource_cost = {
        resource: max(1, math.ceil(amount * reduction_multiplier)) for resource, amount in base_cost.items()
    }
    base_duration = int(building.next_level_duration())
    duration_seconds = max(
        1,
        int(base_duration * (1.0 - float(manor.citang_building_time_reduction))),
    )
    duration_seconds = scale_duration(duration_seconds, minimum=1)
    return BuildingUpgradeQuote(
        manor_id=int(manor.pk),
        building_id=int(building.pk),
        building_type_id=int(building.building_type_id),
        building_key=building_key,
        building_name=building_name,
        current_level=current_level,
        target_level=current_level + 1,
        max_level=max_level,
        base_cost=tuple(sorted(base_cost.items())),
        resource_cost=tuple(sorted(resource_cost.items())),
        cost_reduction=cost_reduction,
        base_duration=base_duration,
        duration_seconds=duration_seconds,
        upgrading_count=upgrading_count,
    )


def _assert_current_building_upgrade_quote_locked(
    manor: Manor,
    building: Building,
    expected_quote: BuildingUpgradeQuote,
    *,
    buildings: Sequence[Building] | None = None,
    technology_levels: Mapping[str, int] | None = None,
) -> BuildingUpgradeQuote:
    _require_building_upgrade_atomic()
    if not isinstance(expected_quote, BuildingUpgradeQuote):
        raise TypeError("expected_quote must be BuildingUpgradeQuote")
    _validate_building_upgrade_ownership(manor, building)
    if (
        expected_quote.manor_id != manor.pk
        or expected_quote.building_id != building.pk
        or expected_quote.building_type_id != building.building_type_id
    ):
        raise BuildingUpgradeQuoteStaleError("building upgrade quote does not match the locked rows")
    current_quote = quote_building_upgrade(
        manor,
        building,
        buildings=buildings,
        technology_levels=technology_levels,
    )
    if current_quote != expected_quote:
        raise BuildingUpgradeQuoteStaleError("building upgrade quote is stale; retry with current state")
    return current_quote


def _consume_building_upgrade_quote_locked(
    manor: Manor,
    quote: BuildingUpgradeQuote,
    *,
    sync_production: bool,
) -> int:
    _require_building_upgrade_atomic()
    from ..resources import spend_resources_locked
    from .prestige import add_prestige_silver_locked

    spend_resources_locked(
        manor,
        dict(quote.resource_cost),
        quote.building_name,
        ResourceEvent.Reason.UPGRADE_COST,
        sync_production=sync_production,
    )
    return add_prestige_silver_locked(
        manor,
        dict(quote.resource_cost).get("silver", 0),
    )


def _schedule_building_cache_invalidation(manor: Manor) -> None:
    manor.invalidate_building_cache()
    manor_id = int(manor.pk)

    def _invalidate_after_commit() -> None:
        invalidate_home_stats_cache(manor_id)

    transaction.on_commit(_invalidate_after_commit, robust=True)


def _apply_building_upgrade_result_locked(
    manor: Manor,
    building: Building,
    *,
    completed_at: datetime,
) -> tuple[int, int]:
    _require_building_upgrade_atomic()
    _validate_building_upgrade_ownership(manor, building)
    previous_level = int(building.level)

    building_update_fields = ["level", "is_upgrading", "upgrade_complete_at"]
    building_key = str(building.building_type.key)
    from ..city_defense_rules import is_city_defense_key, project_city_defense_upgrade

    if is_city_defense_key(building_key):
        upgraded_state = project_city_defense_upgrade(
            building_key,
            previous_level,
            building.current_hp,
            building.hp_updated_at,
            completed_at=completed_at,
        )
        building.current_hp = upgraded_state.current_hp
        building.hp_updated_at = completed_at
        building_update_fields.extend(["current_hp", "hp_updated_at"])

    building.level = previous_level + 1
    building.is_upgrading = False
    building.upgrade_complete_at = None
    building.save(update_fields=building_update_fields)

    capacity_update_fields: list[str] = []
    if building_key == BuildingKeys.SILVER_VAULT:
        manor.silver_capacity = calculate_building_capacity(
            int(building.level),
            is_silver_vault=True,
        )
        capacity_update_fields.append("silver_capacity")
    elif building_key == BuildingKeys.GRANARY:
        manor.grain_capacity = calculate_building_capacity(
            int(building.level),
            is_silver_vault=False,
        )
        capacity_update_fields.append("grain_capacity")
    if capacity_update_fields:
        manor.save(update_fields=capacity_update_fields)

    building.manor = manor
    _schedule_building_cache_invalidation(manor)
    return previous_level, int(building.level)


def apply_building_upgrade_locked(
    manor: Manor,
    building: Building,
    expected_quote: BuildingUpgradeQuote,
    *,
    sync_production: bool = True,
    buildings: Sequence[Building] | None = None,
    technology_levels: Mapping[str, int] | None = None,
) -> BuildingUpgradeResult:
    """Synchronously complete one level with caller-held Manor -> Building locks."""

    quote = _assert_current_building_upgrade_quote_locked(
        manor,
        building,
        expected_quote,
        buildings=buildings,
        technology_levels=technology_levels,
    )
    prestige_gained = _consume_building_upgrade_quote_locked(
        manor,
        quote,
        sync_production=sync_production,
    )
    previous_level, level = _apply_building_upgrade_result_locked(
        manor,
        building,
        completed_at=timezone.now(),
    )
    return BuildingUpgradeResult(
        manor_id=int(manor.pk),
        building_id=int(building.pk),
        building_key=quote.building_key,
        previous_level=previous_level,
        level=level,
        resource_cost=quote.resource_cost,
        prestige_gained=prestige_gained,
        silver_capacity=int(manor.silver_capacity),
        grain_capacity=int(manor.grain_capacity),
    )


def apply_building_upgrade_free_locked(
    manor: Manor,
    building: Building,
    expected_quote: BuildingUpgradeQuote,
    *,
    buildings: Sequence[Building] | None = None,
    technology_levels: Mapping[str, int] | None = None,
) -> BuildingUpgradeResult:
    """Synchronously complete one explicitly subsidized upgrade.

    This primitive deliberately has no normal-economy side effects.  Callers
    must establish the business scope before entering it; currently the only
    supported caller is the Arena replenishment path for Juxianzhuang.
    """

    quote = _assert_current_building_upgrade_quote_locked(
        manor,
        building,
        expected_quote,
        buildings=buildings,
        technology_levels=technology_levels,
    )
    previous_level, level = _apply_building_upgrade_result_locked(
        manor,
        building,
        completed_at=timezone.now(),
    )
    return BuildingUpgradeResult(
        manor_id=int(manor.pk),
        building_id=int(building.pk),
        building_key=quote.building_key,
        previous_level=previous_level,
        level=level,
        resource_cost=(),
        prestige_gained=0,
        silver_capacity=int(manor.silver_capacity),
        grain_capacity=int(manor.grain_capacity),
    )


def start_building_upgrade_locked(
    manor: Manor,
    building: Building,
    expected_quote: BuildingUpgradeQuote,
    *,
    sync_production: bool = True,
    buildings: Sequence[Building] | None = None,
    technology_levels: Mapping[str, int] | None = None,
    now: datetime | None = None,
) -> Building:
    """Charge and start one building timer without completing its level.

    This is the write-side primitive for automated maintenance.  The normal
    user-facing ``start_upgrade`` flow has action-point semantics, while V2
    maintenance already owns its action budget and therefore uses this
    primitive directly.  The completion task remains the only owner of the
    level increment.
    """

    quote = _assert_current_building_upgrade_quote_locked(
        manor,
        building,
        expected_quote,
        buildings=buildings,
        technology_levels=technology_levels,
    )
    _consume_building_upgrade_quote_locked(
        manor,
        quote,
        sync_production=sync_production,
    )
    current_time = now or timezone.now()
    building.upgrade_complete_at = current_time + timedelta(seconds=quote.duration_seconds)
    building.is_upgrading = True
    building.save(update_fields=["upgrade_complete_at", "is_upgrading"])
    schedule_building_completion(building, quote.duration_seconds)
    building.manor = manor
    return building


def finalize_building_upgrade(
    building: Building,
    now: datetime | None = None,
    send_notification: bool = True,
) -> bool:
    current_time = now or timezone.now()
    building_id = getattr(building, "pk", None)
    if isinstance(building_id, bool) or not isinstance(building_id, int):
        return False
    manor_id = Building.objects.filter(pk=building_id).values_list("manor_id", flat=True).first()
    if manor_id is None:
        return False

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor_id)
        locked_building = (
            Building.objects.select_for_update()
            .select_related("building_type")
            .filter(pk=building_id, manor_id=locked_manor.pk)
            .first()
        )
        if (
            locked_building is None
            or not locked_building.is_upgrading
            or locked_building.upgrade_complete_at is None
            or locked_building.upgrade_complete_at > current_time
        ):
            return False
        _apply_building_upgrade_result_locked(
            locked_manor,
            locked_building,
            completed_at=current_time,
        )
        building = locked_building

    if send_notification:
        from ..utils.messages import create_message

        try:
            create_message(
                manor=building.manor,
                kind=Message.Kind.SYSTEM,
                title=f"{building.building_type.name} 升级完成",
                body=f"等级 {building.level - 1} → {building.level}",
            )
        except MANOR_MESSAGE_BEST_EFFORT_EXCEPTIONS as exc:
            logger.warning(
                "building upgrade message creation failed: building_id=%s manor_id=%s error=%s",
                building.id,
                building.manor_id,
                exc,
                exc_info=True,
            )
            return True

        try:
            notify_user(
                building.manor.user_id,
                {
                    "kind": "system",
                    "title": f"{building.building_type.name} 升级完成",
                    "building_key": building.building_type.key,
                    "level": building.level,
                },
                log_context="building upgrade notification",
            )
        except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "building upgrade notification failed: building_id=%s manor_id=%s error=%s",
                building.id,
                building.manor_id,
                exc,
                exc_info=True,
            )
    return True


def finalize_upgrades(manor: Manor, now: datetime | None = None) -> None:
    now = now or timezone.now()
    ready = list(
        manor.buildings.select_related("building_type").filter(is_upgrading=True, upgrade_complete_at__lte=now)
    )
    if not ready:
        return
    for building in ready:
        finalize_building_upgrade(building, now=now, send_notification=True)


def schedule_building_completion(building: Building, eta_seconds: int) -> None:
    countdown = max(0, int(eta_seconds))
    try:
        from gameplay.tasks import complete_building_upgrade
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning(
            "Unable to import complete_building_upgrade task; skip scheduling",
            exc_info=True,
        )
        return

    def _dispatch_completion() -> None:
        dispatched = safe_apply_async(
            complete_building_upgrade,
            args=[building.id],
            countdown=countdown,
            logger=logger,
            log_message="complete_building_upgrade dispatch failed",
        )
        if not dispatched:
            logger.error(
                "complete_building_upgrade dispatch returned False; building may remain upgrading",
                extra={
                    "task_name": "complete_building_upgrade",
                    "building_id": getattr(building, "id", None),
                    "manor_id": getattr(building, "manor_id", None),
                },
            )

    transaction.on_commit(_dispatch_completion)


def start_upgrade(building: Building) -> None:
    manor = building.manor
    finalize_upgrades(manor)
    manor.invalidate_building_cache()
    building.refresh_from_db(fields=["level", "is_upgrading", "upgrade_complete_at"])

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        locked_building = (
            Building.objects.select_for_update()
            .select_related("building_type")
            .get(pk=building.pk, manor_id=locked_manor.pk)
        )
        quote = quote_building_upgrade(
            locked_manor,
            locked_building,
        )
        consume_action_points(
            locked_manor,
            ACTION_POINT_BUILDING_UPGRADE_COST,
            insufficient_message="行动力不足，无法升级建筑",
        )
        _consume_building_upgrade_quote_locked(
            locked_manor,
            quote,
            sync_production=True,
        )

        locked_building.upgrade_complete_at = timezone.now() + timedelta(seconds=quote.duration_seconds)
        locked_building.is_upgrading = True
        locked_building.save(update_fields=["upgrade_complete_at", "is_upgrading"])
        schedule_building_completion(locked_building, quote.duration_seconds)

    building.level = locked_building.level
    building.is_upgrading = locked_building.is_upgrading
    building.upgrade_complete_at = locked_building.upgrade_complete_at
