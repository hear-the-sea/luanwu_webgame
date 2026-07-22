"""Raid guest capture logic (split from battle.py)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.constants import get_raid_capture_guest_rate
from guests.models import Guest

from ....models import JailPrisoner, Manor, OathBond, RaidRun
from .config import random

logger = logging.getLogger(__name__)


def _resolve_capture_sides(run: RaidRun, is_attacker_victory: bool) -> tuple[Manor, Manor]:
    winner = run.attacker if is_attacker_victory else run.defender
    loser = run.defender if is_attacker_victory else run.attacker
    return winner, loser


def _can_attempt_capture(winner: Manor) -> bool:
    capacity = int(getattr(winner, "jail_capacity", 0) or 0)
    if capacity <= 0:
        return False

    held_count = JailPrisoner.objects.filter(captor=winner, status=JailPrisoner.Status.HELD).count()
    if held_count >= capacity:
        return False

    capture_rate = get_raid_capture_guest_rate()
    if capture_rate <= 0:
        return False
    if random.random() >= capture_rate:
        return False

    return True


def _collect_losing_guest_ids(report: Any, is_attacker_victory: bool) -> List[int]:
    losing_team = (report.defender_team or []) if is_attacker_victory else (report.attacker_team or [])
    losing_guest_ids: List[int] = []

    for entry in losing_team:
        guest_id = entry.get("guest_id") if isinstance(entry, dict) else None
        if not guest_id:
            continue
        try:
            losing_guest_ids.append(int(guest_id))
        except (TypeError, ValueError):
            continue

    return losing_guest_ids


def _filter_capture_candidates(losing_guest_ids: List[int]) -> List[int]:
    oathed_ids = set(OathBond.objects.filter(guest_id__in=losing_guest_ids).values_list("guest_id", flat=True))
    return [guest_id for guest_id in losing_guest_ids if guest_id not in oathed_ids]


def _select_capture_target(candidates: List[int], loser: Manor) -> Optional[Guest]:
    target_guest_id = random.choice(candidates)
    target = (
        Guest.objects.select_for_update()
        .select_related("template", "manor")
        .filter(pk=target_guest_id, manor=loser)
        .first()
    )
    if not target:
        return None

    if OathBond.objects.filter(guest=target).exists():
        return None

    return target


def _delete_captured_guest_gear(run: RaidRun, target: Guest) -> None:
    from guests.models import GearItem

    try:
        GearItem.objects.filter(guest=target).delete()
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "failed to delete captured guest gear: run_id=%s guest_id=%s error=%s",
            run.id,
            target.pk,
            exc,
            exc_info=True,
        )


def _capture_guest_payload(
    captured_name: str, captured_rarity: str, captured_template_key: str, is_attacker_victory: bool
) -> Dict[str, Any]:
    return {
        "guest_name": captured_name,
        "rarity": captured_rarity,
        "template_key": captured_template_key,
        "from": "defender" if is_attacker_victory else "attacker",
        "into": "jail",
    }


def _try_capture_guest(run: RaidRun, report: Any, is_attacker_victory: bool) -> Optional[Dict[str, Any]]:
    """
    尝试俘获失败方出战门客（单场最多1名）。

    规则：
    - 概率固定（不受监牢/结义林影响）
    - 失败方：仅从本场出战门客中抽取
    - 结义门客不可被俘获
    - 监牢满员时不进行俘获判定，不给任何补偿
    - 俘获成功：门客从失败方列表移除，装备自动消失，进入胜利方监牢
    """
    winner, loser = _resolve_capture_sides(run, is_attacker_victory)
    if not _can_attempt_capture(winner):
        return None

    losing_guest_ids = _collect_losing_guest_ids(report, is_attacker_victory)
    if not losing_guest_ids:
        return None

    candidates = _filter_capture_candidates(losing_guest_ids)
    if not candidates:
        return None

    target = _select_capture_target(candidates, loser)
    if not target:
        return None

    captured_name = target.display_name
    captured_rarity = getattr(getattr(target, "template", None), "rarity", "") or ""
    captured_template_key = getattr(getattr(target, "template", None), "key", "") or ""

    _delete_captured_guest_gear(run, target)

    JailPrisoner.objects.create(
        captor=winner,
        original_manor=loser,
        guest_template=target.template,
        original_guest_name=captured_name,
        original_level=target.level,
        loyalty=target.loyalty,
        captured_loyalty=target.loyalty,
        status=JailPrisoner.Status.HELD,
        raid_run=run,
    )

    target.delete()

    return _capture_guest_payload(captured_name, captured_rarity, captured_template_key, is_attacker_victory)
