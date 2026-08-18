"""
技术系统服务模块。

保留历史导入入口，同时把纯规则计算与升级运行态拆到子模块，
降低单文件复杂度并保持现有 monkeypatch/导入兼容性。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from common.utils.celery import safe_apply_async
from core.exceptions import (
    InsufficientResourceError,
    TechnologyConcurrentUpgradeLimitError,
    TechnologyMaxLevelError,
    TechnologyNotFoundError,
    TechnologyUpgradeInProgressError,
)
from core.utils.time_scale import scale_duration
from core.utils.yaml_loader import load_yaml_data

from ..constants import MAX_CONCURRENT_TECH_UPGRADES
from . import technology_catalog as _technology_catalog
from . import technology_helpers as _technology_helpers
from . import technology_refresh_state as _technology_refresh_state
from . import technology_rules as _technology_rules
from . import technology_runtime as _technology_runtime
from .utils import notifications as _notifications
from .utils.cache import invalidate_home_stats_cache

if TYPE_CHECKING:
    from ..models import PlayerTechnology

logger = logging.getLogger(__name__)

TechnologyUpgradeQuote = _technology_runtime.TechnologyUpgradeQuote
TechnologyUpgradeQuoteStaleError = _technology_runtime.TechnologyUpgradeQuoteStaleError


def _should_skip_tech_refresh_by_local_fallback(manor_id: int, min_interval: int) -> bool:
    return _technology_refresh_state.should_skip_tech_refresh_by_local_fallback(
        manor_id=manor_id,
        min_interval=min_interval,
        monotonic_func=time.monotonic,
    )


def load_technology_templates() -> Dict[str, Any]:
    return _technology_catalog.load_technology_templates(
        load_yaml_data_func=load_yaml_data,
    )


def clear_technology_cache() -> None:
    _technology_catalog.clear_technology_cache()
    _technology_refresh_state.clear_local_tech_refresh_fallback()


def get_technology_template(tech_key: str) -> Optional[Dict[str, Any]]:
    return _technology_catalog.build_technology_index(
        load_technology_templates_func=load_technology_templates,
    ).get(tech_key)


def get_technologies_by_category(category: str) -> List[Dict[str, Any]]:
    data = load_technology_templates()
    return [
        tech
        for tech in (data.get("technologies", []) or [])
        if isinstance(tech, dict) and tech.get("category") == category
    ]


def get_categories() -> List[Dict[str, Any]]:
    data = load_technology_templates()
    categories = data.get("categories", [])
    if isinstance(categories, list):
        return categories
    return []


def get_troop_classes() -> Dict[str, Any]:
    data = load_technology_templates()
    troop_classes = data.get("troop_classes", {})
    if isinstance(troop_classes, dict):
        return troop_classes
    return {}


def calculate_upgrade_cost(tech_key: str, current_level: int) -> int:
    return _technology_rules.calculate_upgrade_cost(
        get_technology_template(tech_key),
        current_level,
        coerce_int_func=_technology_rules.coerce_int,
        coerce_float_func=_technology_rules.coerce_float,
    )


def get_troop_class_for_key(troop_key: str) -> Optional[str]:
    return _technology_catalog.build_troop_to_class_index(
        load_technology_templates_func=load_technology_templates,
    ).get(troop_key)


def get_player_technology_level(manor: Any, tech_key: str) -> int:
    from ..models import PlayerTechnology

    try:
        tech = manor.technologies.get(tech_key=tech_key)
        return tech.level
    except PlayerTechnology.DoesNotExist:
        return 0


def get_player_technologies(manor: Any) -> Dict[str, int]:
    return {tech.tech_key: tech.level for tech in manor.technologies.all()}


def build_player_battle_technology_payload(manor: Any) -> Dict[str, Dict[str, int]]:
    return {"levels": get_player_technologies(manor)}


def get_technology_display_data(manor: Any, category: str) -> List[Dict[str, Any]]:
    technologies = get_technologies_by_category(category)
    player_techs = {pt.tech_key: pt for pt in manor.technologies.all()}
    return [
        _technology_helpers.build_technology_display_entry(
            tech=tech,
            player_tech=player_techs.get(tech["key"]),
            calculate_upgrade_cost=calculate_upgrade_cost,
            scale_duration=scale_duration,
        )
        for tech in technologies
    ]


def get_martial_technologies_grouped(manor: Any) -> List[Dict[str, Any]]:
    return _technology_helpers.group_martial_technology_entries(
        get_technology_display_data(manor, "martial"),
        get_troop_classes(),
    )


def schedule_technology_completion(tech: Any, eta_seconds: int) -> None:
    _technology_helpers.schedule_technology_completion_task(
        tech,
        eta_seconds,
        logger=logger,
        transaction_module=transaction,
        safe_apply_async_func=safe_apply_async,
    )


def upgrade_technology(manor: Any, tech_key: str) -> Dict[str, Any]:
    return _technology_runtime.upgrade_technology(
        manor,
        tech_key,
        get_technology_template_func=get_technology_template,
        calculate_upgrade_cost_func=calculate_upgrade_cost,
        max_concurrent_tech_upgrades=MAX_CONCURRENT_TECH_UPGRADES,
        schedule_technology_completion_func=schedule_technology_completion,
        build_technology_upgrade_response_func=_technology_helpers.build_technology_upgrade_response,
        transaction_module=transaction,
        technology_not_found_error_cls=TechnologyNotFoundError,
        technology_upgrade_in_progress_error_cls=TechnologyUpgradeInProgressError,
        technology_max_level_error_cls=TechnologyMaxLevelError,
        technology_concurrent_upgrade_limit_error_cls=TechnologyConcurrentUpgradeLimitError,
        insufficient_resource_error_cls=InsufficientResourceError,
    )


def quote_technology_upgrade(
    manor: Any,
    tech_key: str,
    *,
    technologies: Sequence[Any] | None = None,
) -> TechnologyUpgradeQuote:
    return _technology_runtime.quote_technology_upgrade(
        manor,
        tech_key,
        get_technology_template_func=get_technology_template,
        calculate_upgrade_cost_func=calculate_upgrade_cost,
        max_concurrent_tech_upgrades=MAX_CONCURRENT_TECH_UPGRADES,
        technology_not_found_error_cls=TechnologyNotFoundError,
        technology_upgrade_in_progress_error_cls=TechnologyUpgradeInProgressError,
        technology_max_level_error_cls=TechnologyMaxLevelError,
        technology_concurrent_upgrade_limit_error_cls=TechnologyConcurrentUpgradeLimitError,
        technologies=technologies,
    )


def apply_technology_upgrade_locked(
    manor: Any,
    quote: TechnologyUpgradeQuote,
    *,
    sync_production: bool = True,
    technologies: Sequence[Any] | None = None,
    technologies_locked: bool = False,
) -> PlayerTechnology:
    return _technology_runtime.apply_technology_upgrade_locked(
        manor,
        quote,
        get_technology_template_func=get_technology_template,
        calculate_upgrade_cost_func=calculate_upgrade_cost,
        max_concurrent_tech_upgrades=MAX_CONCURRENT_TECH_UPGRADES,
        transaction_module=transaction,
        invalidate_home_stats_cache_func=invalidate_home_stats_cache,
        technology_not_found_error_cls=TechnologyNotFoundError,
        technology_upgrade_in_progress_error_cls=TechnologyUpgradeInProgressError,
        technology_max_level_error_cls=TechnologyMaxLevelError,
        technology_concurrent_upgrade_limit_error_cls=TechnologyConcurrentUpgradeLimitError,
        insufficient_resource_error_cls=InsufficientResourceError,
        sync_production=sync_production,
        technologies=technologies,
        technologies_locked=technologies_locked,
    )


def start_technology_upgrade_locked(
    manor: Any,
    quote: TechnologyUpgradeQuote,
    *,
    sync_production: bool = True,
    technologies: Sequence[Any] | None = None,
    technologies_locked: bool = False,
    now: Any | None = None,
) -> PlayerTechnology:
    """Start a timer-backed technology upgrade without changing its level."""

    return _technology_runtime.start_technology_upgrade_locked(
        manor,
        quote,
        get_technology_template_func=get_technology_template,
        calculate_upgrade_cost_func=calculate_upgrade_cost,
        max_concurrent_tech_upgrades=MAX_CONCURRENT_TECH_UPGRADES,
        transaction_module=transaction,
        invalidate_home_stats_cache_func=invalidate_home_stats_cache,
        technology_not_found_error_cls=TechnologyNotFoundError,
        technology_upgrade_in_progress_error_cls=TechnologyUpgradeInProgressError,
        technology_max_level_error_cls=TechnologyMaxLevelError,
        technology_concurrent_upgrade_limit_error_cls=TechnologyConcurrentUpgradeLimitError,
        insufficient_resource_error_cls=InsufficientResourceError,
        schedule_technology_completion_func=schedule_technology_completion,
        sync_production=sync_production,
        technologies=technologies,
        technologies_locked=technologies_locked,
        now=now,
    )


def finalize_technology_upgrade(tech: Any, send_notification: bool = False, now: Any | None = None) -> bool:
    return _technology_runtime.finalize_technology_upgrade(
        tech,
        get_technology_template_func=get_technology_template,
        resolve_technology_name_func=_technology_helpers.resolve_technology_name,
        send_technology_completion_notification_func=_technology_helpers.send_technology_completion_notification,
        notify_user_func=_notifications.notify_user,
        invalidate_home_stats_cache_func=invalidate_home_stats_cache,
        logger=logger,
        send_notification=send_notification,
        now=now,
    )


def refresh_technology_upgrades(manor: Any) -> int:
    return _technology_runtime.refresh_technology_upgrades(
        manor,
        settings_obj=settings,
        cache_backend=cache,
        logger=logger,
        should_skip_tech_refresh_by_local_fallback_func=_should_skip_tech_refresh_by_local_fallback,
        finalize_technology_upgrade_func=finalize_technology_upgrade,
    )


def get_tech_bonus_from_levels(levels: Dict[str, int], effect_type: str, troop_class: Optional[str] = None) -> float:
    data = load_technology_templates()
    return _technology_rules.get_tech_bonus_from_templates(
        data.get("technologies", []) or [],
        levels,
        effect_type,
        troop_class=troop_class,
        coerce_int_func=_technology_rules.coerce_int,
        coerce_float_func=_technology_rules.coerce_float,
    )


def get_tech_bonus(manor: Any, effect_type: str, troop_class: Optional[str] = None) -> float:
    return get_tech_bonus_from_levels(get_player_technologies(manor), effect_type, troop_class)


def build_uniform_tech_levels(level: int) -> Dict[str, int]:
    data = load_technology_templates()
    return _technology_rules.build_uniform_tech_levels(
        data.get("technologies", []) or [],
        level,
        coerce_int_func=_technology_rules.coerce_int,
    )


def resolve_enemy_tech_levels(config: Dict[str, Any]) -> Dict[str, int]:
    return _technology_rules.resolve_enemy_tech_levels(
        config,
        build_uniform_tech_levels_func=build_uniform_tech_levels,
        coerce_int_func=_technology_rules.coerce_int,
    )


def get_guest_stat_bonuses(config: Dict[str, Any]) -> Dict[str, float]:
    return _technology_rules.get_guest_stat_bonuses(
        config,
        coerce_float_func=_technology_rules.coerce_float,
    )


def get_resource_production_bonus_from_levels(
    levels: Dict[str, int],
    resource_type: str,
    building_key: Optional[str] = None,
) -> float:
    data = load_technology_templates()
    return _technology_rules.get_resource_production_bonus_from_templates(
        data.get("technologies", []) or [],
        levels,
        resource_type,
        building_key=building_key,
        coerce_int_func=_technology_rules.coerce_int,
        coerce_float_func=_technology_rules.coerce_float,
    )


def get_resource_production_bonus(manor: Any, resource_type: str, building_key: Optional[str] = None) -> float:
    return get_resource_production_bonus_from_levels(
        get_player_technologies(manor),
        resource_type,
        building_key=building_key,
    )


def get_troop_stat_bonuses(
    manor: Any,
    troop_key: str,
    tech_levels: Optional[Dict[str, int]] = None,
) -> Dict[str, float]:
    troop_class = get_troop_class_for_key(troop_key)
    if not troop_class:
        return {}

    levels = tech_levels if tech_levels is not None else get_player_technologies(manor)
    bonuses = {}
    stat_types = [
        ("troop_attack", "attack"),
        ("troop_defense", "defense"),
        ("troop_agility", "agility"),
        ("troop_hp", "hp"),
    ]

    for effect_type, stat_key in stat_types:
        bonus = get_tech_bonus_from_levels(levels, effect_type, troop_class)
        if bonus > 0:
            bonuses[stat_key] = bonus

    return bonuses


def get_march_speed_bonus(manor: Any) -> float:
    return get_tech_bonus(manor, "march_speed")


def get_building_cost_reduction(manor: Any) -> float:
    return get_tech_bonus(manor, "building_cost_reduction")
