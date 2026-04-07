from __future__ import annotations

from typing import Any, Callable

from core.exceptions import MessageError, RaidStartError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)

RAID_START_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def start_raid(
    attacker: Any,
    defender: Any,
    guest_ids: list[int],
    troop_loadout: dict[str, int],
    *,
    validate_and_normalize_inputs: Callable[[Any, Any, list[int], dict[str, int]], tuple[list[int], dict[str, int]]],
    transaction_atomic: Callable[[], Any],
    lock_manor_pair: Callable[[int, int], tuple[Any, Any]],
    now_func: Callable[[], Any],
    recheck_can_attack_target: Callable[..., tuple[bool, str]],
    get_active_raid_count: Callable[[Any], int],
    raid_max_concurrent: int,
    load_and_validate_attacker_guests: Callable[[Any, list[int]], list[Any]],
    normalize_and_validate_raid_loadout: Callable[[list[Any], dict[str, int]], dict[str, int]],
    deduct_troops: Callable[[Any, dict[str, int]], None],
    calculate_raid_travel_time: Callable[[Any, Any, list[Any], dict[str, int]], int],
    create_raid_run_record: Callable[[Any, Any, list[Any], dict[str, int], int], Any],
    invalidate_recent_attacks_cache_on_commit: Callable[[int], None],
    send_raid_incoming_message: Callable[[Any], None],
    dispatch_raid_battle_task: Callable[[Any, int], None],
    logger: Any,
) -> Any:
    guest_ids, troop_loadout = validate_and_normalize_inputs(attacker, defender, guest_ids, troop_loadout)

    with transaction_atomic():
        attacker_locked, defender_locked = lock_manor_pair(attacker.pk, defender.pk)
        now = now_func()

        can_attack, reason = recheck_can_attack_target(attacker_locked, defender_locked, now=now)
        if not can_attack:
            raise RaidStartError(reason)

        active_count = get_active_raid_count(attacker_locked)
        if active_count >= raid_max_concurrent:
            raise RaidStartError(f"同时最多进行 {raid_max_concurrent} 次出征")

        guests = load_and_validate_attacker_guests(attacker_locked, guest_ids)
        loadout = normalize_and_validate_raid_loadout(guests, troop_loadout)
        deduct_troops(attacker_locked, loadout)
        travel_time = calculate_raid_travel_time(attacker_locked, defender_locked, guests, loadout)
        run = create_raid_run_record(attacker_locked, defender_locked, guests, loadout, travel_time)
        if attacker_locked.defeat_protection_until and attacker_locked.defeat_protection_until > now:
            attacker_locked.defeat_protection_until = None
            attacker_locked.save(update_fields=["defeat_protection_until"])
        invalidate_recent_attacks_cache_on_commit(defender_locked.pk)

    try:
        send_raid_incoming_message(run)
    except RAID_START_MESSAGE_EXCEPTIONS as exc:
        logger.warning(
            "raid incoming message failed: run_id=%s attacker=%s defender=%s error=%s",
            getattr(run, "id", None),
            getattr(run, "attacker_id", getattr(getattr(run, "attacker", None), "id", None)),
            getattr(run, "defender_id", getattr(getattr(run, "defender", None), "id", None)),
            exc,
            exc_info=True,
            extra={
                "degraded": True,
                "component": "raid_start_message",
                "run_id": getattr(run, "id", None),
            },
        )
    try:
        dispatch_raid_battle_task(run, travel_time)
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.error(
            "raid battle dispatch failed after start: run_id=%s attacker=%s defender=%s error=%s",
            getattr(run, "id", None),
            getattr(run, "attacker_id", getattr(getattr(run, "attacker", None), "id", None)),
            getattr(run, "defender_id", getattr(getattr(run, "defender", None), "id", None)),
            exc,
            exc_info=True,
            extra={
                "degraded": True,
                "component": "raid_start_dispatch",
                "run_id": getattr(run, "id", None),
            },
        )

    return run
