from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.exceptions import (
    BattlePreparationError,
    MissionDailyLimitError,
    MissionGuestSelectionError,
    MissionSquadSizeExceededError,
    MissionTroopLoadoutError,
    TroopLoadoutError,
)

from ...models import Manor, MissionRun, MissionTemplate
from ..action_points import consume_action_points_for_expedition
from ..battle_snapshots import build_guest_battle_snapshots
from .attempts import get_mission_daily_limit, mission_attempts_today
from .loadout import normalize_mission_loadout, travel_time_seconds


def _normalize_entry_cost(mission: MissionTemplate) -> dict[str, int]:
    raw_entry_cost = getattr(mission, "entry_cost", None)
    if raw_entry_cost is None:
        return {}
    if not isinstance(raw_entry_cost, dict):
        raise AssertionError(f"invalid mission entry_cost: {raw_entry_cost!r}")

    normalized: dict[str, int] = {}
    for raw_key, raw_amount in raw_entry_cost.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise AssertionError(f"invalid mission entry_cost key: {raw_key!r}")
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, int) or raw_amount <= 0:
            raise AssertionError(f"invalid mission entry_cost amount: {raw_amount!r}")
        normalized[raw_key.strip()] = raw_amount
    return normalized


def consume_entry_cost_if_needed(manor: Manor, mission: MissionTemplate) -> None:
    entry_cost = _normalize_entry_cost(mission)
    if not entry_cost:
        return

    from ..inventory.core import consume_inventory_item_for_manor_locked

    for item_key, amount in entry_cost.items():
        consume_inventory_item_for_manor_locked(manor, item_key, amount)


def _resolve_max_squad_size(manor: Manor) -> int:
    raw_max_squad_size = getattr(manor, "max_squad_size", None)
    if raw_max_squad_size is None:
        return 0
    if isinstance(raw_max_squad_size, bool):
        raise AssertionError(f"invalid mission max_squad_size: {raw_max_squad_size!r}")
    try:
        max_squad_size = int(raw_max_squad_size)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission max_squad_size: {raw_max_squad_size!r}") from exc
    if max_squad_size < 0:
        raise AssertionError(f"invalid mission max_squad_size: {raw_max_squad_size!r}")
    return max_squad_size


def _resolve_base_travel_time(mission: MissionTemplate) -> int:
    raw_base_travel_time = getattr(mission, "base_travel_time", None)
    if raw_base_travel_time is None or isinstance(raw_base_travel_time, bool):
        raise AssertionError(f"invalid mission base_travel_time: {raw_base_travel_time!r}")
    try:
        base_travel_time = int(raw_base_travel_time)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission base_travel_time: {raw_base_travel_time!r}") from exc
    if base_travel_time <= 0:
        raise AssertionError(f"invalid mission base_travel_time: {raw_base_travel_time!r}")
    return base_travel_time


def _validate_defense_launch_inputs(guest_ids: list[int], troop_loadout: dict[str, int]) -> None:
    if guest_ids:
        raise AssertionError(f"defense mission guest_ids must be empty: {guest_ids!r}")
    if troop_loadout:
        raise AssertionError(f"defense mission troop_loadout must be empty: {troop_loadout!r}")


def validate_mission_attempts(manor: Manor, mission: MissionTemplate) -> None:
    attempts = mission_attempts_today(manor, mission)
    daily_limit = get_mission_daily_limit(manor, mission)
    if attempts >= daily_limit:
        raise MissionDailyLimitError()


def prepare_offense_launch_inputs(
    manor: Manor,
    mission: MissionTemplate,
    guest_ids: list[int],
    troop_loadout: dict[str, int],
) -> tuple[list[Any], dict[str, int], int]:
    from battle.services import validate_troop_capacity
    from guests.models import GuestStatus

    from ..recruitment.troops import _deduct_troops_batch

    guests = list(
        manor.guests.select_for_update().filter(id__in=guest_ids).select_related("template").prefetch_related("skills")
    )
    if len(guests) != len(set(guest_ids)):
        raise MissionGuestSelectionError("部分门客不可用或已离开庄园")
    if any(guest.status != GuestStatus.IDLE for guest in guests):
        raise MissionGuestSelectionError("部分门客不可用或已离开庄园")
    if not guests:
        raise MissionGuestSelectionError("请选择至少一名门客")

    max_squad_size = _resolve_max_squad_size(manor)
    if max_squad_size and len(guests) > max_squad_size:
        raise MissionSquadSizeExceededError(max_squad_size)

    if mission.guest_only:
        loadout: dict[str, int] = {}
    else:
        try:
            loadout = normalize_mission_loadout(troop_loadout)
            validate_troop_capacity(guests, loadout)
        except (BattlePreparationError, TroopLoadoutError) as exc:
            raise MissionTroopLoadoutError(str(exc)) from exc

    if loadout:
        try:
            _deduct_troops_batch(manor, loadout)
        except TroopLoadoutError as exc:
            raise MissionTroopLoadoutError(str(exc)) from exc

    travel_seconds = travel_time_seconds(_resolve_base_travel_time(mission), guests, loadout)
    return guests, loadout, travel_seconds


def prepare_launch_inputs(
    manor: Manor,
    mission: MissionTemplate,
    guest_ids: list[int],
    troop_loadout: dict[str, int],
    *,
    scale_duration,
) -> tuple[list[Any], dict[str, int], int]:
    if mission.is_defense:
        _validate_defense_launch_inputs(guest_ids, troop_loadout)
        return [], {}, scale_duration(_resolve_base_travel_time(mission), minimum=1)

    return prepare_offense_launch_inputs(manor, mission, guest_ids, troop_loadout)


def mark_guests_deployed_if_needed(mission: MissionTemplate, guests: list[Any]) -> None:
    if mission.is_defense or not guests:
        return

    from guests.models import Guest, GuestStatus

    for guest in guests:
        guest.status = GuestStatus.DEPLOYED
    Guest.objects.bulk_update(guests, ["status"])


def create_mission_run_record(
    manor: Manor,
    mission: MissionTemplate,
    guests: list[Any],
    guest_snapshots: list[dict[str, Any]],
    loadout: dict[str, int],
    travel_seconds: int,
) -> MissionRun:
    return_seconds = travel_seconds if mission.is_defense else travel_seconds * 2
    run = MissionRun.objects.create(
        manor=manor,
        mission=mission,
        guest_snapshots=guest_snapshots,
        troop_loadout=loadout,
        travel_time=travel_seconds,
        return_at=timezone.now() + timedelta(seconds=return_seconds),
    )
    if not mission.is_defense:
        run.guests.set(guests)
    return run


def launch_mission(
    manor: Manor,
    mission: MissionTemplate,
    guest_ids: list[int],
    troop_loadout: dict[str, int],
    *,
    seed: Any = None,
    scale_duration,
    prevalidate_launch_report,
    import_launch_post_action_tasks,
    try_prepare_launch_report,
    dispatch_complete_mission_task,
) -> MissionRun:
    validate_mission_attempts(manor, mission)

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        validate_mission_attempts(locked_manor, mission)
        consume_action_points_for_expedition(locked_manor)
        consume_entry_cost_if_needed(locked_manor, mission)

        guests, loadout, travel_seconds = prepare_launch_inputs(
            locked_manor,
            mission,
            guest_ids,
            troop_loadout,
            scale_duration=scale_duration,
        )
        prevalidate_launch_report(mission, loadout)
        mark_guests_deployed_if_needed(mission, guests)
        guest_snapshots = build_guest_battle_snapshots(guests, include_identity=True)
        run = create_mission_run_record(locked_manor, mission, guests, guest_snapshots, loadout, travel_seconds)

    generate_report_task, complete_mission_task = import_launch_post_action_tasks()
    try_prepare_launch_report(
        manor,
        mission,
        run,
        guests,
        loadout,
        travel_seconds,
        seed,
        generate_report_task,
    )
    dispatch_complete_mission_task(run, complete_mission_task)
    return run
