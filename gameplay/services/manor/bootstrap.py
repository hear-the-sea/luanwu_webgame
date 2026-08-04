from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import Building, BuildingType, ItemTemplate, Manor
from gameplay.services.manor import provisioning as _provisioning
from gameplay.services.manor.coordinates import is_occupied_manor_location_conflict
from gameplay.services.manor.naming import ManorNameConflictError, is_manor_name_available

logger = logging.getLogger(__name__)

INITIAL_PEACE_SHIELD_KEYS: tuple[str, ...] = (
    "peace_shield_small",
    "peace_shield_medium",
    "peace_shield_large",
)


class ManorNotFoundError(GameError):
    """Raised when a user tries to access gameplay without a provisioned manor."""

    error_code = "MANOR_NOT_FOUND"
    default_message = "庄园尚未初始化，请重新登录后再试"


def get_manor(user) -> Manor:
    try:
        return user.manor
    except Manor.DoesNotExist as exc:
        raise ManorNotFoundError() from exc


def bootstrap_manor(user, region: str = "overseas", initial_name: str | None = None) -> Manor:
    normalized_name = (initial_name or "").strip() or None
    manor, created = Manor.objects.get_or_create(user=user)
    location_assigned = manor.coordinate_x > 0 and manor.coordinate_y > 0
    if created or not location_assigned:
        try:
            assigned = _assign_manor_location_and_name(manor, region=region, normalized_name=normalized_name)
        except ManorNameConflictError:
            if created:
                Manor.objects.filter(pk=manor.pk, user=user, coordinate_x=0, coordinate_y=0).delete()
            raise
        if not assigned:
            if created:
                Manor.objects.filter(pk=manor.pk, user=user, coordinate_x=0, coordinate_y=0).delete()
            raise RuntimeError("Failed to allocate a unique manor coordinate after multiple attempts")
        manor.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y", "name"])
    _ensure_manor_provisioning(
        manor,
        created=created,
        provision_rewards=not bool(getattr(user, "_virtual_player_internal", False)),
    )
    # 历史庄园由迁移/显式修复命令统一补账；热路径只为新建庄园初始化一次，
    # 避免每次 ensure_manor 都额外查询粮食模板和库存行。
    if created and int(getattr(manor, "grain", 0) or 0) > 0:
        _ensure_warehouse_grain_ledger(manor)
    return manor


def ensure_manor(user, region: str = "overseas", initial_name: str | None = None) -> Manor:
    return bootstrap_manor(user, region=region, initial_name=initial_name)


def _ensure_manor_provisioning(
    manor: Manor,
    *,
    created: bool,
    provision_rewards: bool = True,
) -> None:
    _provisioning.ensure_manor_provisioning(
        manor,
        created=created,
        bootstrap_buildings_func=bootstrap_buildings,
        ensure_buildings_exist_func=ensure_buildings_exist,
        grant_initial_peace_shield_func=_grant_initial_peace_shield,
        deliver_active_global_mail_campaigns_func=_deliver_active_global_mail_campaigns,
        provision_rewards=provision_rewards,
    )


def _ensure_warehouse_grain_ledger(manor: Manor) -> None:
    """为新建且已有粮食的庄园初始化仓库粮食行。"""
    from ..inventory.core import set_warehouse_grain_quantity_locked

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        set_warehouse_grain_quantity_locked(locked_manor, int(locked_manor.grain or 0))
        manor.grain = locked_manor.grain


def _assign_manor_location_and_name(manor: Manor, *, region: str, normalized_name: str | None) -> bool:
    from gameplay.services.manor import core as manor_core

    return _provisioning.assign_manor_location_and_name(
        manor,
        region=region,
        normalized_name=normalized_name,
        generate_unique_coordinate_func=manor_core.generate_unique_coordinate,
        is_manor_name_available_func=is_manor_name_available,
        is_occupied_manor_location_conflict_func=is_occupied_manor_location_conflict,
        manor_model=Manor,
        conflict_error_cls=ManorNameConflictError,
    )


def _grant_initial_peace_shield(manor: Manor) -> None:
    _provisioning.grant_initial_peace_shield(
        manor,
        initial_peace_shield_keys=INITIAL_PEACE_SHIELD_KEYS,
        item_template_model=ItemTemplate,
        manor_model=Manor,
        timezone_module=timezone,
        logger=logger,
    )


def _deliver_active_global_mail_campaigns(manor: Manor) -> None:
    _provisioning.deliver_active_global_mail_campaigns(
        manor,
        is_missing_global_mail_schema_error_func=_is_missing_global_mail_schema_error,
        logger=logger,
    )


def _is_missing_global_mail_schema_error(exc: BaseException) -> bool:
    return _provisioning.is_missing_global_mail_schema_error(exc)


def generate_unique_coordinate(region: str) -> tuple[int, int]:
    return _provisioning.generate_unique_coordinate(region, manor_model=Manor, logger=logger)


def bootstrap_buildings(manor: Manor) -> None:
    _provisioning.bootstrap_buildings(manor, building_type_model=BuildingType, building_model=Building)


def ensure_buildings_exist(manor: Manor) -> None:
    _provisioning.ensure_buildings_exist(manor, building_type_model=BuildingType, building_model=Building)
