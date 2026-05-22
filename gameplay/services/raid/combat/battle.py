"""Raid battle execution (split from legacy combat.py)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from importlib import import_module
from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import BattlePreparationError, MessageError
from core.utils import safe_positive_int
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.services.battle_snapshots import build_guest_battle_snapshots, build_guest_snapshot_proxies
from gameplay.services.technology import build_player_battle_technology_payload
from gameplay.services.virtual_player_loot_limits import clamp_bot_loot_resources
from guests.models import Guest, GuestStatus
from guests.query_utils import guest_template_rarity_rank_case

from ....models import Manor, PlayerTroop, RaidRun
from ...recruitment.troops import apply_defender_troop_losses
from . import runs as combat_runs

# Re-export capture helpers so existing imports from battle keep working.
from .battle_guest_damage import apply_guest_damage_from_report as _apply_guest_damage_from_report_impl
from .battle_guest_damage import extract_side_guest_state as _extract_side_guest_state_impl

# Re-export messaging helpers.
from .battle_post_actions import dispatch_complete_raid_task as _dispatch_complete_raid_task_impl
from .battle_post_actions import fail_raid_run_due_missing_manor as _fail_raid_run_due_missing_manor_impl
from .capture import (  # noqa: F401
    _can_attempt_capture,
    _capture_guest_payload,
    _collect_losing_guest_ids,
    _delete_captured_guest_gear,
    _filter_capture_candidates,
    _resolve_capture_sides,
    _select_capture_target,
    _try_capture_guest,
)
from .config import PVPConstants
from .loot import _apply_loot, _calculate_loot
from .messaging import _send_raid_battle_messages  # noqa: F401
from .travel import (
    _dismiss_marching_raids_if_protected,
    _get_defender_battle_block_reason,
    _retreat_raid_run_due_to_blocked_target,
)
from .troops import _normalize_mapping, _normalize_positive_int_mapping

logger = logging.getLogger(__name__)


RAID_BATTLE_INFRASTRUCTURE_EXCEPTIONS: InfrastructureExceptions = DATABASE_INFRASTRUCTURE_EXCEPTIONS
RAID_BATTLE_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)
RAID_CAPTURE_DEGRADED_EXCEPTIONS: InfrastructureExceptions = INFRASTRUCTURE_EXCEPTIONS


def _load_locked_raid_run(run_pk: int) -> Optional[RaidRun]:
    return (
        RaidRun.objects.select_for_update()
        .select_related("attacker", "defender")
        .prefetch_related("guests")
        .filter(pk=run_pk)
        .first()
    )


def _prepare_run_for_battle(run_pk: int, now: datetime) -> Optional[RaidRun]:
    locked_run = _load_locked_raid_run(run_pk)
    if not locked_run:
        return None

    if locked_run.status == RaidRun.Status.RETREATED:
        if locked_run.return_at and locked_run.return_at > now:
            return None
        combat_runs._finalize_raid_retreat(locked_run, now=now)
        return None

    if locked_run.status != RaidRun.Status.MARCHING:
        return None

    locked_run.status = RaidRun.Status.BATTLING
    locked_run.save(update_fields=["status"])
    return locked_run


def _lock_battle_manors(attacker_id: int, defender_id: int) -> tuple[Manor, Manor]:
    ids = [attacker_id] if attacker_id == defender_id else sorted([attacker_id, defender_id])
    locked = {m.pk: m for m in Manor.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    attacker = locked.get(attacker_id)
    defender = locked.get(defender_id)
    if attacker is None or defender is None:
        raise BattlePreparationError("目标庄园不存在")
    return attacker, defender


def _apply_raid_loot_if_needed(locked_run: RaidRun, is_attacker_victory: bool) -> None:
    if not is_attacker_victory:
        return

    locked_defender = Manor.objects.select_for_update().get(pk=locked_run.defender_id)
    loot_resources, loot_items = _calculate_loot(
        locked_defender,
        guests=list(locked_run.guests.all()),
        troop_loadout=locked_run.troop_loadout,
        battle_report=locked_run.battle_report,
    )
    loot_resources = clamp_bot_loot_resources(
        attacker=locked_run.attacker,
        defender=locked_defender,
        loot_resources=loot_resources,
    )
    applied_resources, applied_items = _apply_loot(
        locked_defender,
        loot_resources,
        loot_items,
        locked_manor=locked_defender,
    )
    locked_run.loot_resources = applied_resources
    locked_run.loot_items = applied_items


def _apply_defeat_protection(run: RaidRun, is_attacker_victory: bool, *, now: Optional[datetime] = None) -> None:
    if not is_attacker_victory:
        return
    now = now or timezone.now()
    duration_seconds = int(getattr(PVPConstants, "RAID_DEFEAT_PROTECTION_SECONDS", 1800) or 0)
    if duration_seconds <= 0:
        return

    defender = Manor.objects.select_for_update().get(pk=run.defender_id)
    new_until = now + timedelta(seconds=duration_seconds)
    current_until = defender.defeat_protection_until
    if current_until and current_until > new_until:
        new_until = current_until
    defender.defeat_protection_until = new_until
    defender.save(update_fields=["defeat_protection_until"])
    run.defender = defender


def _apply_capture_reward(locked_run: RaidRun, report: Any, is_attacker_victory: bool) -> None:
    try:
        capture_info = _try_capture_guest(locked_run, report, is_attacker_victory)
        if capture_info:
            battle_rewards = _normalize_mapping(locked_run.battle_rewards)
            locked_run.battle_rewards = {**battle_rewards, "capture": capture_info}
    except RAID_CAPTURE_DEGRADED_EXCEPTIONS as exc:
        logger.warning(
            "raid capture failed: run_id=%s attacker=%s defender=%s error=%s",
            locked_run.id,
            locked_run.attacker_id,
            locked_run.defender_id,
            exc,
            exc_info=True,
            extra={
                "degraded": True,
                "component": "raid_capture_reward",
                "run_id": locked_run.id,
            },
        )


def _apply_salvage_reward(locked_run: RaidRun, report: Any, is_attacker_victory: bool) -> None:
    from gameplay.services.battle_salvage import calculate_battle_salvage, grant_battle_salvage

    exp_fruit_count, equipment_recovery = calculate_battle_salvage(report)
    normalized_exp_fruit_count = safe_positive_int(exp_fruit_count, 0)
    normalized_equipment_recovery = _normalize_positive_int_mapping(equipment_recovery)
    if normalized_exp_fruit_count <= 0 and not normalized_equipment_recovery:
        return

    winner_manor = locked_run.attacker if is_attacker_victory else locked_run.defender
    grant_battle_salvage(winner_manor, normalized_exp_fruit_count, normalized_equipment_recovery)
    battle_rewards = _normalize_mapping(locked_run.battle_rewards)
    locked_run.battle_rewards = {
        **battle_rewards,
        "exp_fruit": normalized_exp_fruit_count,
        "equipment": normalized_equipment_recovery,
    }


def _fail_raid_run_due_missing_manor(locked_run: RaidRun, *, now: Optional[datetime] = None) -> None:
    _fail_raid_run_due_missing_manor_impl(
        locked_run,
        now=now,
        normalize_positive_int_mapping=_normalize_positive_int_mapping,
        add_troops_batch=combat_runs._add_troops_batch,
    )


def _dispatch_complete_raid_task(run: RaidRun, *, now: Optional[datetime] = None) -> None:
    try:
        complete_raid_task = import_module("gameplay.tasks").complete_raid_task
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning(
            "complete_raid_task import failed: run_id=%s error=%s",
            run.id,
            exc,
            exc_info=True,
            extra={"degraded": True, "component": "raid_task_import", "run_id": run.id},
        )
        complete_raid_task = None

    _dispatch_complete_raid_task_impl(
        run,
        now=now,
        logger=logger,
        safe_apply_async_fn=safe_apply_async,
        complete_raid_task=complete_raid_task,
        finalize_raid_fn=combat_runs.finalize_raid,
    )


def process_raid_battle(run: RaidRun, now: Optional[datetime] = None) -> None:
    """
    处理踢馆战斗。

    Args:
        run: 踢馆记录
        now: 当前时间（可选）
    """
    now = now or timezone.now()
    blocked_reason: str | None = None

    with transaction.atomic():
        locked_run = _prepare_run_for_battle(run.pk, now)
        if locked_run is None:
            return

        try:
            attacker_locked, defender_locked = _lock_battle_manors(locked_run.attacker_id, locked_run.defender_id)
        except BattlePreparationError:
            logger.warning(
                "raid battle aborted due to missing manor: run_id=%s attacker=%s defender=%s",
                locked_run.id,
                locked_run.attacker_id,
                locked_run.defender_id,
            )
            _fail_raid_run_due_missing_manor(locked_run, now=now)
            return
        locked_run.attacker = attacker_locked
        locked_run.defender = defender_locked
        blocked_reason = _get_defender_battle_block_reason(defender_locked, now=now)
        if blocked_reason:
            _retreat_raid_run_due_to_blocked_target(locked_run, now=now, reason=blocked_reason)
        else:
            report = _execute_raid_battle(locked_run)
            apply_defender_troop_losses(locked_run.defender, report)

            is_attacker_victory = report.winner == "attacker"
            locked_run.is_attacker_victory = is_attacker_victory
            locked_run.battle_report = report

            _apply_raid_loot_if_needed(locked_run, is_attacker_victory)
            _apply_prestige_changes(locked_run, is_attacker_victory)
            _apply_defeat_protection(locked_run, is_attacker_victory, now=now)
            _apply_capture_reward(locked_run, report, is_attacker_victory)
            _apply_salvage_reward(locked_run, report, is_attacker_victory)

            locked_run.status = RaidRun.Status.RETURNING
            locked_run.save()

    if blocked_reason:
        _dispatch_complete_raid_task(locked_run, now=now)
        return

    try:
        try:
            _send_raid_battle_messages(locked_run)
        except RAID_BATTLE_MESSAGE_EXCEPTIONS as exc:
            logger.warning(
                "raid battle messages failed: run_id=%s attacker=%s defender=%s error=%s",
                locked_run.id,
                locked_run.attacker_id,
                locked_run.defender_id,
                exc,
                exc_info=True,
                extra={
                    "degraded": True,
                    "component": "raid_battle_message",
                    "run_id": locked_run.id,
                },
            )
    finally:
        try:
            _dismiss_marching_raids_if_protected(locked_run.defender)
        except RAID_BATTLE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "dismiss marching raids failed: run_id=%s defender=%s error=%s",
                locked_run.id,
                locked_run.defender_id,
                exc,
                exc_info=True,
                extra={
                    "degraded": True,
                    "component": "raid_protection_cleanup",
                    "run_id": locked_run.id,
                },
            )
        finally:
            _dispatch_complete_raid_task(locked_run, now=now)


# ============ Battle execution and guest damage ============


def _extract_side_guest_state(report: Any, side: str) -> tuple[Dict[int, int], set[int]]:
    return _extract_side_guest_state_impl(report, side)


def _apply_guest_damage_from_report(
    report: Any,
    *,
    attacker_guest_ids: set[int],
    defender_guest_ids: set[int],
) -> None:
    _apply_guest_damage_from_report_impl(
        report,
        attacker_guest_ids=attacker_guest_ids,
        defender_guest_ids=defender_guest_ids,
        guest_model=Guest,
        guest_status=GuestStatus,
        now=timezone.now(),
    )


def _execute_raid_battle(run: RaidRun) -> Any:
    """执行踢馆战斗"""
    from battle.services import simulate_report

    attacker = run.attacker
    defender = run.defender
    attacker_guests = list(run.guests.select_for_update().select_related("template").prefetch_related("skills"))
    loadout = _normalize_positive_int_mapping(run.troop_loadout)
    attacker_guest_ids = {guest.id for guest in attacker_guests}

    attacker_snapshots = list(run.guest_snapshots or [])
    if not attacker_snapshots and attacker_guests:
        attacker_snapshots = build_guest_battle_snapshots(attacker_guests, include_identity=True)
    attacker_combat_guests: list[Any] = build_guest_snapshot_proxies(attacker_snapshots, include_guest_identity=True)
    if not attacker_combat_guests:
        attacker_combat_guests = attacker_guests  # type: ignore[assignment]

    defender_guests = list(
        defender.guests.select_for_update()
        .filter(status=GuestStatus.IDLE)
        .select_related("template")
        .prefetch_related("skills")
        .annotate(_template_rarity_rank=guest_template_rarity_rank_case("template__rarity"))
        .order_by("-_template_rarity_rank", "-level", "id")
    )

    defender_troops: Dict[str, int] = {}
    for troop in (
        PlayerTroop.objects.select_for_update().filter(manor=defender, count__gt=0).select_related("troop_template")
    ):
        defender_troops[troop.troop_template.key] = troop.count
    defender_guest_ids = {guest.id for guest in defender_guests}
    defender_setup = {
        "troop_loadout": defender_troops,
        "technology": build_player_battle_technology_payload(defender),
    }

    report = simulate_report(
        manor=attacker,
        battle_type="raid",
        troop_loadout=loadout,
        fill_default_troops=False,
        attacker_guests=attacker_combat_guests,
        defender_setup=defender_setup,
        defender_guests=defender_guests,  # type: ignore[arg-type]
        defender_manor=defender,
        defender_max_squad=len(defender_guests) if defender_guests else None,
        opponent_name=defender.display_name,
        travel_seconds=0,
        send_message=False,
        auto_reward=False,
        apply_damage=False,
        use_lock=False,
    )
    _apply_guest_damage_from_report(
        report,
        attacker_guest_ids=attacker_guest_ids,
        defender_guest_ids=defender_guest_ids,
    )
    from gameplay.services.city_defense import apply_city_defense_battle_damage

    apply_city_defense_battle_damage(defender, getattr(report, "defender_city_defenses", []) or [])

    return report


def _apply_prestige_changes(run: RaidRun, is_attacker_victory: bool) -> None:
    """应用声望变化"""
    from gameplay.models import Manor as ManorModel

    from ...manor.prestige import PRESTIGE_SILVER_THRESHOLD

    if is_attacker_victory:
        attacker_change = PVPConstants.RAID_ATTACKER_WIN_PRESTIGE
        defender_change = PVPConstants.RAID_DEFENDER_LOSE_PRESTIGE
    else:
        attacker_change = PVPConstants.RAID_ATTACKER_LOSE_PRESTIGE
        defender_change = PVPConstants.RAID_DEFENDER_WIN_PRESTIGE

    def _apply_pvp_delta(manor: Manor, delta: int) -> int:
        before_total = manor.prestige
        spending_prestige = manor.prestige_silver_spent // PRESTIGE_SILVER_THRESHOLD
        before_pvp = max(0, before_total - spending_prestige)
        after_pvp = max(0, before_pvp + delta)
        after_total = spending_prestige + after_pvp
        manor.prestige = after_total
        manor.save(update_fields=["prestige"])
        return after_total - before_total

    ids = [run.attacker_id] if run.attacker_id == run.defender_id else sorted([run.attacker_id, run.defender_id])
    manor_map = {m.pk: m for m in ManorModel.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    attacker = manor_map.get(run.attacker_id)
    defender = manor_map.get(run.defender_id)
    if attacker is None or defender is None:
        return

    run.attacker_prestige_change = _apply_pvp_delta(attacker, attacker_change)
    run.defender_prestige_change = _apply_pvp_delta(defender, defender_change)
