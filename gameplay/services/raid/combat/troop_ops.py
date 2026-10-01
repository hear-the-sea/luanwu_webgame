"""Troop management helpers for raid combat (deduct / add / return)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict

from django.db import IntegrityError
from django.db.models import F
from django.utils import timezone

from core.exceptions import RaidStartError, TroopLoadoutError

from ....models import Manor, PlayerTroop, RaidRun
from ...manor.troop_capacity import ensure_manor_troop_capacity_locked
from ...manor.troop_dispatch import deduct_dispatch_troops_locked

# Normalization / calculation helpers from the troops sub-module.
from .troops import (
    _calculate_surviving_raid_troops,
    _collect_troop_upserts,
    _extract_raid_troops_lost,
    _normalize_positive_int_mapping,
    _normalize_troops_for_addition,
)

logger = logging.getLogger(__name__)


def _deduct_troops(manor: Manor, loadout: Dict[str, int]) -> None:
    """出征时先扣庄园护院，不足部分自动扣除钱庄库存。"""
    loadout = _normalize_positive_int_mapping(loadout)
    try:
        deduct_dispatch_troops_locked(manor, loadout)
    except TroopLoadoutError as exc:
        if "troop_key" in exc.context:
            raise RaidStartError("没有该类型的护院") from exc
        raise RaidStartError(str(exc)) from exc


def _bulk_create_troops_with_fallback(to_create: list[PlayerTroop], now: datetime) -> None:
    if not to_create:
        return
    for pt in to_create:
        updated = PlayerTroop.objects.filter(manor=pt.manor, troop_template=pt.troop_template).update(
            count=F("count") + pt.count,
            updated_at=now,
        )
        if updated:
            continue
        try:
            PlayerTroop.objects.create(
                manor=pt.manor,
                troop_template=pt.troop_template,
                count=pt.count,
            )
        except IntegrityError:
            PlayerTroop.objects.filter(manor=pt.manor, troop_template=pt.troop_template).update(
                count=F("count") + pt.count,
                updated_at=now,
            )


def _add_troops(manor: Manor, troop_key: str, count: int) -> None:
    """给庄园添加护院（单个兵种）"""
    if count <= 0:
        return
    _add_troops_batch(manor, {troop_key: count})


def _add_troops_batch(
    manor: Manor,
    troops_to_add: Dict[str, int],
    *,
    enforce_capacity: bool = True,
) -> None:
    """批量给庄园添加护院"""
    from battle.models import TroopTemplate

    if not troops_to_add:
        return

    troops_to_add = _normalize_troops_for_addition(troops_to_add)
    if not troops_to_add:
        return

    if enforce_capacity:
        ensure_manor_troop_capacity_locked(manor, sum(troops_to_add.values()))

    from core.utils.template_loader import load_templates_by_key

    templates = load_templates_by_key(TroopTemplate, keys=troops_to_add.keys())

    if not templates:
        return

    existing = {
        pt.troop_template.key: pt
        for pt in PlayerTroop.objects.select_for_update()
        .filter(manor=manor, troop_template__key__in=troops_to_add.keys())
        .select_related("troop_template")
    }

    now = timezone.now()
    to_update, to_create = _collect_troop_upserts(manor, troops_to_add, templates, existing, now)

    if to_update:
        PlayerTroop.objects.bulk_update(to_update, ["count", "updated_at"])
    _bulk_create_troops_with_fallback(to_create, now)


def _return_surviving_troops(run: RaidRun) -> None:
    """批量归还存活的护院"""
    loadout = _normalize_positive_int_mapping(getattr(run, "troop_loadout", {}))
    if not loadout:
        return

    if not run.battle_report:
        _return_troops_to_storage(run.attacker, loadout)
        return

    troops_lost = _extract_raid_troops_lost(loadout, run.battle_report)
    surviving_troops = _calculate_surviving_raid_troops(loadout, troops_lost)

    if surviving_troops:
        _return_troops_to_storage(run.attacker, surviving_troops)


def _return_troops_to_storage(manor: Manor, troop_loadout: dict[str, int]) -> None:
    from ...manor.troop_bank import return_troops_to_manor_or_bank_locked

    return_troops_to_manor_or_bank_locked(
        manor,
        troop_loadout,
        add_to_manor=lambda current_manor, troops: _add_troops_batch(
            current_manor,
            troops,
            enforce_capacity=False,
        ),
    )
