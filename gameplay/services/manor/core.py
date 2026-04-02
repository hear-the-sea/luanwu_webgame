"""
庄园和建筑管理服务
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from threading import Lock

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import BuildingConcurrentUpgradeLimitError, BuildingMaxLevelError, BuildingUpgradingError
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS
from core.utils.time_scale import scale_duration
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

CAPACITY_BASE = 20000
CAPACITY_GROWTH_SILVER = 1.299657
CAPACITY_GROWTH_GRAIN = 1.3905


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

    _run_manor_refresh(
        manor,
        prefer_async=prefer_async,
        include_activity_refresh=include_activity_refresh,
        sync_resource_projection_func=sync_resource_production,
    )


def project_manor_activity_for_read(
    manor: Manor,
    *,
    prefer_async: bool = False,
) -> None:
    """
    Apply the read-side manor projection and compensate due activity state.

    This keeps page reads lightweight for resources while still finalizing
    overdue mission/scout/raid/arena activity so guest availability and status
    displays do not lag behind completed activity.
    """
    from ..resources import project_resource_production_for_read

    project_resource_production_for_read(manor)
    _run_manor_refresh(
        manor,
        prefer_async=prefer_async,
        include_activity_refresh=True,
        sync_resource_projection_func=_noop_manor_step,
    )


def finalize_building_upgrade(building: Building, now: datetime | None = None, send_notification: bool = True) -> bool:
    now = now or timezone.now()
    if not building.pk:
        return False

    updated = Building.objects.filter(
        pk=building.pk,
        is_upgrading=True,
        upgrade_complete_at__isnull=False,
        upgrade_complete_at__lte=now,
    ).update(
        level=F("level") + 1,
        is_upgrading=False,
        upgrade_complete_at=None,
    )
    if updated != 1:
        return False

    building = Building.objects.select_related("manor", "building_type").get(pk=building.pk)

    building_key = building.building_type.key
    if building_key == BuildingKeys.SILVER_VAULT:
        new_capacity = calculate_building_capacity(building.level, is_silver_vault=True)
        Manor.objects.filter(pk=building.manor_id).update(silver_capacity=new_capacity)
    elif building_key == BuildingKeys.GRANARY:
        new_capacity = calculate_building_capacity(building.level, is_silver_vault=False)
        Manor.objects.filter(pk=building.manor_id).update(grain_capacity=new_capacity)

    building.manor.invalidate_building_cache()
    invalidate_home_stats_cache(building.manor_id)
    if send_notification:
        from ..utils.messages import create_message

        try:
            create_message(
                manor=building.manor,
                kind=Message.Kind.SYSTEM,
                title=f"{building.building_type.name} 升级完成",
                body=f"等级 Lv{building.level - 1} → Lv{building.level}",
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
        logger.warning("Unable to import complete_building_upgrade task; skip scheduling", exc_info=True)
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
    from ..technology import get_building_cost_reduction

    manor = building.manor
    finalize_upgrades(manor)
    building.refresh_from_db(fields=["level", "is_upgrading", "upgrade_complete_at"])

    if building.is_upgrading:
        raise BuildingUpgradingError()

    building_key = building.building_type.key
    max_level = BUILDING_MAX_LEVELS.get(building_key)
    if max_level is not None and building.level >= max_level:
        raise BuildingMaxLevelError(building.building_type.name, max_level)

    upgrading_count = Building.objects.filter(manor=manor, is_upgrading=True).count()
    if upgrading_count >= MAX_CONCURRENT_BUILDING_UPGRADES:
        raise BuildingConcurrentUpgradeLimitError(MAX_CONCURRENT_BUILDING_UPGRADES)

    base_cost = building.next_level_cost()
    cost_reduction = get_building_cost_reduction(manor)
    reduction_multiplier = max(0, 1 - cost_reduction)
    cost = {resource: max(1, math.ceil(amount * reduction_multiplier)) for resource, amount in base_cost.items()}

    base_duration = building.next_level_duration()
    time_reduction = manor.citang_building_time_reduction
    duration_seconds = max(1, int(base_duration * (1 - time_reduction)))
    duration_seconds = scale_duration(duration_seconds, minimum=1)

    with transaction.atomic():
        manor = Manor.objects.select_for_update().get(pk=manor.pk)
        building = Building.objects.select_for_update().get(pk=building.pk)
        if building.is_upgrading:
            raise BuildingUpgradingError()

        upgrading_count = Building.objects.filter(manor=manor, is_upgrading=True).count()
        if upgrading_count >= MAX_CONCURRENT_BUILDING_UPGRADES:
            raise BuildingConcurrentUpgradeLimitError(MAX_CONCURRENT_BUILDING_UPGRADES)

        from ..resources import spend_resources_locked

        spend_resources_locked(manor, cost, building.building_type.name, ResourceEvent.Reason.UPGRADE_COST)

        silver_spent = cost.get("silver", 0)
        if silver_spent > 0:
            from .prestige import add_prestige_silver_locked

            add_prestige_silver_locked(manor, silver_spent)

        building.upgrade_complete_at = timezone.now() + timedelta(seconds=duration_seconds)
        building.is_upgrading = True
        building.save(update_fields=["upgrade_complete_at", "is_upgrading"])
        schedule_building_completion(building, duration_seconds)
