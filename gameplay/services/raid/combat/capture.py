"""Raid guest capture logic (split from battle.py)."""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

from django.db.models import Q

from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.constants import get_raid_capture_guest_rate
from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX
from guests.models import Guest, GuestRarity, GuestStatus, GuestTemplate, WorldUniqueGuest
from guests.services.custom_guest import CUSTOM_GUEST_TEMPLATE_KEYS, is_custom_guest
from guests.services.world_unique import WORLD_UNIQUE_LUBU_TEMPLATE_KEY, release_world_unique_guest_after_raid

from ....models import JailPrisoner, Manor, OathBond, RaidRun
from ...jail_expiration import release_expired_prisoners_for_captor

logger = logging.getLogger(__name__)

ACTIVE_CAPTURE_CANDIDATE_WEIGHT = 2
VACATION_CAPTURE_CANDIDATE_WEIGHT = 1


def _resolve_capture_sides(run: RaidRun, is_attacker_victory: bool) -> tuple[Manor, Manor]:
    winner = run.attacker if is_attacker_victory else run.defender
    loser = run.defender if is_attacker_victory else run.attacker
    return winner, loser


def _can_attempt_capture(winner: Manor, *, losing_side: str, rng: random.Random) -> bool:
    capacity = int(getattr(winner, "jail_capacity", 0) or 0)
    if capacity <= 0:
        return False

    release_expired_prisoners_for_captor(winner)
    held_count = JailPrisoner.objects.filter(captor=winner, status=JailPrisoner.Status.HELD).count()
    if held_count >= capacity:
        return False

    capture_rate = get_raid_capture_guest_rate(losing_side=losing_side)
    if capture_rate <= 0:
        return False
    if rng.random() >= capture_rate:
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
    unique_guest_ids = set(
        Guest.objects.filter(
            id__in=losing_guest_ids,
        )
        .filter(
            Q(template__is_world_unique=True)
            | Q(template__key=WORLD_UNIQUE_LUBU_TEMPLATE_KEY)
            | Q(template__key__in=CUSTOM_GUEST_TEMPLATE_KEYS)
        )
        .values_list("id", flat=True)
    )
    oathed_ids = set(OathBond.objects.filter(guest_id__in=losing_guest_ids).values_list("guest_id", flat=True))
    return [
        guest_id for guest_id in losing_guest_ids if guest_id not in oathed_ids and guest_id not in unique_guest_ids
    ]


def _select_capture_target(
    candidates: List[int] | List[tuple[int, int]],
    loser: Manor,
    *,
    rng: random.Random,
) -> Optional[Guest]:
    weighted_candidates = [(candidate, 1) if isinstance(candidate, int) else candidate for candidate in candidates]
    total_weight = sum(weight for _guest_id, weight in weighted_candidates if weight > 0)
    if total_weight <= 0:
        return None

    draw = rng.randrange(total_weight)
    target_guest_id: int | None = None
    for guest_id, weight in weighted_candidates:
        if weight <= 0:
            continue
        if draw < weight:
            target_guest_id = guest_id
            break
        draw -= weight
    if target_guest_id is None:
        return None

    target = (
        Guest.objects.select_for_update()
        .select_related("template", "manor")
        .filter(pk=target_guest_id, manor=loser)
        .first()
    )
    if not target:
        return None
    if is_custom_guest(target):
        return None

    if OathBond.objects.filter(guest=target).exists():
        return None
    if getattr(target.template, "is_world_unique", False) or target.template.key == WORLD_UNIQUE_LUBU_TEMPLATE_KEY:
        return None

    return target


def _find_losing_world_unique_guest_id(report: Any, is_attacker_victory: bool) -> int | None:
    winning_side = "attacker" if is_attacker_victory else "defender"
    if str(getattr(report, "winner", "") or "").strip() != winning_side:
        return None

    losing_team = (report.defender_team or []) if is_attacker_victory else (report.attacker_team or [])
    candidate_ids: list[int] = []
    for entry in losing_team:
        if not isinstance(entry, dict):
            continue
        raw_guest_id = entry.get("guest_id")
        if raw_guest_id is None:
            continue
        try:
            guest_id = int(str(raw_guest_id).strip())
        except (TypeError, ValueError):
            continue
        if guest_id > 0:
            if entry.get("template_key") == WORLD_UNIQUE_LUBU_TEMPLATE_KEY:
                return guest_id
            candidate_ids.append(guest_id)

    if not candidate_ids:
        return None

    # Older battle snapshots may omit template_key. Resolve the live guest by
    # ID so a defeated Lubu still returns to the wild instead of entering jail.
    live_guest_ids = set(
        Guest.objects.filter(
            pk__in=candidate_ids,
            template__key=WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
        ).values_list("pk", flat=True)
    )
    for guest_id in candidate_ids:
        if guest_id in live_guest_ids:
            return guest_id
    return None


def _release_losing_world_unique_guest(
    run: RaidRun,
    report: Any,
    is_attacker_victory: bool,
) -> Dict[str, Any] | None:
    winning_side = "attacker" if is_attacker_victory else "defender"
    if str(getattr(report, "winner", "") or "").strip() != winning_side:
        return None

    if is_attacker_victory:
        # A defeated defending manor loses Lubu even when he is absent from its lineup.
        guest_id = (
            WorldUniqueGuest.objects.filter(
                template__key=WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
                status=WorldUniqueGuest.Status.SERVING,
                owner_manor_id=run.defender.pk,
            )
            .values_list("owner_guest_id", flat=True)
            .first()
        )
    else:
        guest_id = _find_losing_world_unique_guest_id(report, is_attacker_victory)
    if guest_id is None:
        return None

    _winner, loser = _resolve_capture_sides(run, is_attacker_victory)
    return release_world_unique_guest_after_raid(
        loser,
        guest_id=guest_id,
        template_key=WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
        losing_side="defender" if is_attacker_victory else "attacker",
    )


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


def _resolve_capture_template(template: GuestTemplate) -> GuestTemplate:
    """Store the original template when a dynamically converted guest is captured."""
    if template.rarity != GuestRarity.BLUE:
        return template

    template_key = str(template.key or "").strip()
    if not template_key.startswith(RARITY_CONVERSION_TEMPLATE_KEY_PREFIX):
        return template

    conversion_suffix = template_key[len(RARITY_CONVERSION_TEMPLATE_KEY_PREFIX) :]
    target_rarity, separator, source_template_id_text = conversion_suffix.rpartition("_")
    if target_rarity != GuestRarity.BLUE or not separator:
        return template

    try:
        source_template_id = int(source_template_id_text)
    except (TypeError, ValueError):
        return template

    return GuestTemplate.objects.filter(pk=source_template_id, rarity=GuestRarity.GREEN).first() or template


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


def _try_capture_guest(
    run: RaidRun,
    report: Any,
    is_attacker_victory: bool,
    *,
    rng: random.Random,
) -> Optional[Dict[str, Any]]:
    """
    尝试俘获失败方门客（单场最多1名）。

    规则：
    - 进攻方或防守方失败时，单场俘获判定概率均为10%
    - 失败方：从本场出战门客中抽取；防守方战败时，逍遥山庄归闲门客也可被抽取
    - 出战门客权重为2，逍遥山庄归闲门客权重为1
    - 结义门客不可被俘获
    - 监牢满员时不进行俘获判定，不给任何补偿
    - 俘获成功：门客从失败方移除，装备自动消失，进入胜利方监牢
    """
    winner, loser = _resolve_capture_sides(run, is_attacker_victory)
    losing_side = "defender" if is_attacker_victory else "attacker"
    if not _can_attempt_capture(winner, losing_side=losing_side, rng=rng):
        return None

    losing_guest_ids = _collect_losing_guest_ids(report, is_attacker_victory)
    active_candidates = _filter_capture_candidates(losing_guest_ids)
    weighted_candidates = [(guest_id, ACTIVE_CAPTURE_CANDIDATE_WEIGHT) for guest_id in active_candidates]

    if is_attacker_victory:
        vacation_guest_ids = list(loser.guests.filter(status=GuestStatus.VACATION).values_list("id", flat=True))
        vacation_candidates = _filter_capture_candidates(vacation_guest_ids)
        weighted_candidates.extend((guest_id, VACATION_CAPTURE_CANDIDATE_WEIGHT) for guest_id in vacation_candidates)

    if not weighted_candidates:
        return None

    target = _select_capture_target(weighted_candidates, loser, rng=rng)
    if not target:
        return None

    captured_name = target.display_name
    captured_template = _resolve_capture_template(target.template)
    captured_rarity = captured_template.rarity
    captured_template_key = captured_template.key

    _delete_captured_guest_gear(run, target)

    JailPrisoner.objects.create(
        captor=winner,
        original_manor=loser,
        guest_template=captured_template,
        original_guest_name=captured_name,
        original_level=target.level,
        loyalty=target.loyalty,
        captured_loyalty=target.loyalty,
        status=JailPrisoner.Status.HELD,
        raid_run=run,
    )

    target.delete()

    return _capture_guest_payload(captured_name, captured_rarity, captured_template_key, is_attacker_victory)
