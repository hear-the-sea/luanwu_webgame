from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from core.exceptions import ScoutStartError

from ...models import PlayerTroop, ScoutCooldown, ScoutRecord
from ..scout_intel_rules import SCOUT_INTEL_SCHEMA_VERSION, normalize_scout_level


def format_scout_cooldown_message(remaining_seconds: int | None) -> str:
    remaining = max(0, int(remaining_seconds or 0))
    minutes = remaining // 60
    seconds = remaining % 60
    return f"侦察冷却中，剩余 {minutes}分{seconds}秒"


def start_scout_command(
    attacker: Any,
    defender: Any,
    *,
    can_attack_target_fn: Callable[..., tuple[bool, str]],
    check_scout_cooldown_fn: Callable[[Any, Any], tuple[bool, int | None]],
    get_scout_count_fn: Callable[[Any], int],
    lock_manor_pair_fn: Callable[[int, int], tuple[Any, Any]],
    calculate_success_rate_fn: Callable[[Any, Any], float],
    get_scout_level_fn: Callable[[Any], int],
    calculate_travel_time_fn: Callable[[Any, Any], int],
    schedule_completion_fn: Callable[[Any, int], None],
    now_fn: Callable[[], datetime] = timezone.now,
    scout_cooldown_model: Any = ScoutCooldown,
    player_troop_model: Any = PlayerTroop,
    scout_record_model: Any = ScoutRecord,
    scout_troop_key: str,
) -> Any:
    can_attack, reason = can_attack_target_fn(
        attacker,
        defender,
        use_cached_recent_attacks=False,
        check_defeat_protection=False,
    )
    if not can_attack:
        raise ScoutStartError(reason)

    in_cooldown, remaining = check_scout_cooldown_fn(attacker, defender)
    if in_cooldown:
        raise ScoutStartError(format_scout_cooldown_message(remaining))

    if get_scout_count_fn(attacker) < 1:
        raise ScoutStartError("探子不足，无法发起侦察")

    with transaction.atomic():
        attacker_locked, defender_locked = lock_manor_pair_fn(attacker.pk, defender.pk)
        current_time = now_fn()

        can_attack_locked, reason_locked = can_attack_target_fn(
            attacker_locked,
            defender_locked,
            now=current_time,
            use_cached_recent_attacks=False,
            check_defeat_protection=False,
        )
        if not can_attack_locked:
            raise ScoutStartError(reason_locked)

        cooldown_locked = (
            scout_cooldown_model.objects.select_for_update()
            .filter(attacker=attacker_locked, defender=defender_locked, cooldown_until__gt=current_time)
            .first()
        )
        if cooldown_locked:
            remaining_locked = int((cooldown_locked.cooldown_until - current_time).total_seconds())
            raise ScoutStartError(format_scout_cooldown_message(remaining_locked))

        success_rate = calculate_success_rate_fn(attacker_locked, defender_locked)
        travel_time = calculate_travel_time_fn(attacker_locked, defender_locked)

        troop = player_troop_model.objects.select_for_update().get(
            manor=attacker_locked,
            troop_template__key=scout_troop_key,
        )
        if troop.count < 1:
            raise ScoutStartError("探子不足，无法发起侦察")

        troop.count -= 1
        troop.save(update_fields=["count"])

        record = scout_record_model.objects.create(
            attacker=attacker_locked,
            defender=defender_locked,
            status=scout_record_model.Status.SCOUTING,
            scout_cost=1,
            success_rate=success_rate,
            travel_time=travel_time,
            complete_at=current_time + timedelta(seconds=travel_time),
            intel_data={
                "intel_schema_version": SCOUT_INTEL_SCHEMA_VERSION,
                "scout_level": normalize_scout_level(get_scout_level_fn(attacker_locked)),
            },
        )
        schedule_completion_fn(record, travel_time)

    return record
