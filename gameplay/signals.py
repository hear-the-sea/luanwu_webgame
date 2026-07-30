from __future__ import annotations

import logging

from celery import current_app
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.register_runtime import PUBLIC_REAL_USER_REGISTRATION_MARKER
from common.utils.celery import safe_apply_async

from .services.manor.core import bootstrap_manor
from .services.manor.prestige import prestige_change_committed
from .services.virtual_player_core.population_runtime import (
    merge_committed_prestige_transition_population_demands,
    merge_real_player_population_recompute_demand,
    try_merge_already_classified_mysql_prestige_transition_cells,
)

logger = logging.getLogger(__name__)


def _queue_virtual_player_population_reconcile(
    *,
    region: str,
    prestige_band: str,
) -> bool:
    task = current_app.signature("gameplay.reconcile_virtual_player_population_cell")
    return safe_apply_async(
        task,
        args=[region, prestige_band],
        logger=logger,
        log_message=("virtual-player population reconcile dispatch failed; " "relying on the periodic demand scan"),
        log_extra={
            "event": "virtual_player_population_reconcile_dispatch_deferred",
            "region": region,
            "prestige_band": prestige_band,
        },
    )


def _merge_and_queue_registration_population(*, region: str, prestige: int) -> None:
    demand = merge_real_player_population_recompute_demand(
        region=region,
        prestige=prestige,
    )
    if demand is None:
        return
    _queue_virtual_player_population_reconcile(
        region=str(demand.region),
        prestige_band=str(demand.prestige_band),
    )


def _merge_and_queue_prestige_transition_population(
    *,
    manor_id: int,
    region: str,
    before_prestige: int,
    after_prestige: int,
) -> None:
    merged_cells = try_merge_already_classified_mysql_prestige_transition_cells(
        manor_id=manor_id,
        region=region,
        before_prestige=before_prestige,
        after_prestige=after_prestige,
    )
    if merged_cells is not None:
        for merged_region, prestige_band in merged_cells:
            _queue_virtual_player_population_reconcile(
                region=merged_region,
                prestige_band=prestige_band,
            )
        return
    demands = merge_committed_prestige_transition_population_demands(
        manor_id=manor_id,
        region=region,
        before_prestige=before_prestige,
        after_prestige=after_prestige,
    )
    for demand in demands:
        _queue_virtual_player_population_reconcile(
            region=str(demand.region),
            prestige_band=str(demand.prestige_band),
        )


@receiver(
    prestige_change_committed,
    dispatch_uid="gameplay.virtual_player_population_prestige_transition",
)
def reconcile_population_after_prestige_change(
    sender,
    *,
    manor_id: int,
    region: str,
    before_prestige: int,
    after_prestige: int,
    **kwargs,
) -> None:
    _merge_and_queue_prestige_transition_population(
        manor_id=manor_id,
        region=region,
        before_prestige=before_prestige,
        after_prestige=after_prestige,
    )


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_manor_for_user(sender, instance, created, **kwargs):
    if not created:
        return
    # 从用户的注册数据中获取地区与庄园名（在 RegisterView 中设置）
    region = getattr(instance, "_signup_region", "overseas")
    manor_name = getattr(instance, "_signup_manor_name", "")
    manor = bootstrap_manor(instance, region=region, initial_name=manor_name)
    is_public_registration = bool(getattr(instance, PUBLIC_REAL_USER_REGISTRATION_MARKER, False))
    is_internal_virtual_player = bool(getattr(instance, "_virtual_player_internal", False))
    if not is_public_registration or is_internal_virtual_player or instance.is_staff or instance.is_superuser:
        return
    committed_region = str(manor.region)
    committed_prestige = int(manor.prestige)

    def _merge_and_queue_after_commit() -> None:
        _merge_and_queue_registration_population(
            region=committed_region,
            prestige=committed_prestige,
        )

    transaction.on_commit(_merge_and_queue_after_commit, robust=True)
