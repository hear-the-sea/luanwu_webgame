from __future__ import annotations

from datetime import timedelta
from typing import Any

from battle.random_context import current_replay_metadata
from core.exceptions import RaidStartError
from guests.services.status import GUEST_STATUS_UPDATE_FIELDS, prepare_guest_status_transition


def lock_manor_pair(attacker_id: int, defender_id: int, *, manor_model: Any) -> tuple[Any, Any]:
    """Lock attacker/defender rows in a stable order to avoid deadlocks."""
    ids = [attacker_id] if attacker_id == defender_id else sorted([attacker_id, defender_id])
    locked = {manor.pk: manor for manor in manor_model.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    attacker = locked.get(attacker_id)
    defender = locked.get(defender_id)
    if attacker is None or defender is None:
        raise RaidStartError("目标庄园不存在")
    return attacker, defender


def recheck_can_attack_target(attacker: Any, defender: Any, now: Any, *, can_attack_target: Any) -> tuple[bool, str]:
    return can_attack_target(attacker, defender, now=now, use_cached_recent_attacks=False)


def invalidate_recent_attacks_cache_on_commit(
    defender_id: int,
    *,
    on_commit: Any,
    invalidate_recent_attacks_cache: Any,
) -> None:
    on_commit(lambda: invalidate_recent_attacks_cache(defender_id))


def create_raid_run_record(
    attacker: Any,
    defender: Any,
    guests: list[Any],
    loadout: dict[str, int],
    travel_time: int,
    *,
    guest_model: Any,
    deployed_status: Any,
    build_guest_battle_snapshots: Any,
    raid_run_model: Any,
    now_func: Any,
) -> Any:
    now = now_func()
    for guest in guests:
        prepare_guest_status_transition(guest, deployed_status, now=now)
    guest_model.objects.bulk_update(guests, GUEST_STATUS_UPDATE_FIELDS)

    guest_snapshots = build_guest_battle_snapshots(guests, include_identity=True)
    replay_metadata = current_replay_metadata()
    run = raid_run_model.objects.create(
        attacker=attacker,
        defender=defender,
        guest_snapshots=guest_snapshots,
        troop_loadout=loadout,
        status=raid_run_model.Status.MARCHING,
        travel_time=travel_time,
        battle_at=now + timedelta(seconds=travel_time),
        return_at=now + timedelta(seconds=travel_time * 2),
        **replay_metadata,
    )
    run.guests.set(guests)
    return run


def get_active_raids(manor: Any, *, raid_run_model: Any) -> list[Any]:
    return list(
        raid_run_model.objects.filter(
            attacker=manor,
            status__in=[
                raid_run_model.Status.MARCHING,
                raid_run_model.Status.RETURNING,
                raid_run_model.Status.RETREATED,
            ],
        )
        .select_related("defender", "battle_report")
        .order_by("-started_at")
    )


def get_raid_history(manor: Any, *, raid_run_model: Any, q_object: Any, limit: int = 20) -> list[Any]:
    return list(
        raid_run_model.objects.filter(q_object(attacker=manor) | q_object(defender=manor))
        .select_related("attacker", "defender", "battle_report")
        .order_by("-started_at")[:limit]
    )
