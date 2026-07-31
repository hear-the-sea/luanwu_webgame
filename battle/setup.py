from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from core.exceptions import BattlePreparationError
from guests.models import Guest, GuestStatus

from .constants import MAX_SQUAD
from .execution import BattleOptions


def _coerce_positive_id(raw_id, *, contract_name: str) -> int:
    try:
        parsed_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"broken {contract_name} contract") from exc
    if parsed_id <= 0:
        raise AssertionError(f"broken {contract_name} contract")
    return parsed_id


def validate_attacker_guest_ownership(manor, guests: list[Guest]) -> None:
    manor_pk = getattr(manor, "pk", None)
    if not manor_pk:
        return
    parsed_manor_pk = _coerce_positive_id(manor_pk, contract_name="battle attacker manor id")

    unresolved_ids: list[int] = []
    for guest in guests:
        guest_pk = getattr(guest, "pk", None)
        if not guest_pk:
            continue
        parsed_guest_pk = _coerce_positive_id(guest_pk, contract_name="battle attacker guest id")
        is_snapshot_proxy = bool(getattr(guest, "is_battle_snapshot_proxy", False))

        guest_manor_id = getattr(guest, "manor_id", None)
        if guest_manor_id is None:
            guest_manor = getattr(guest, "manor", None)
            guest_manor_id = getattr(guest_manor, "pk", None)

        if guest_manor_id is None:
            if is_snapshot_proxy:
                continue
            unresolved_ids.append(parsed_guest_pk)
            continue

        parsed_guest_manor_id = _coerce_positive_id(guest_manor_id, contract_name="battle attacker guest manor id")
        if parsed_guest_manor_id != parsed_manor_pk:
            raise BattlePreparationError("攻击方门客必须属于当前庄园")

    if not unresolved_ids:
        return

    owned_ids = set(Guest.objects.filter(id__in=unresolved_ids, manor_id=parsed_manor_pk).values_list("id", flat=True))
    if len(owned_ids) != len(set(unresolved_ids)):
        raise BattlePreparationError("攻击方门客必须属于当前庄园")


def select_default_attacker_guests(manor, limit: int) -> list[Guest]:
    guest_qs = manor.guests.select_related("template").prefetch_related("skills")
    total_guests = guest_qs.count()
    guests = list(guest_qs.filter(status=GuestStatus.IDLE).order_by("-template__rarity", "-level")[:limit])
    if guests:
        return guests

    if total_guests > 0:
        injured_count = guest_qs.filter(status=GuestStatus.INJURED).count()
        if injured_count > 0:
            raise BattlePreparationError(f"有{injured_count}名门客处于重伤状态，请使用药品治疗后再出征")
        raise BattlePreparationError("仅空闲门客可出征，请先让门客空闲后再尝试战斗")
    raise BattlePreparationError("请先招募门客后再尝试战斗")


def resolve_attacker_guests_for_battle(
    manor,
    attacker_guests: list[Guest] | None,
    limit: int,
    *,
    select_default_attacker_guests_fn: Callable[[Any, int], list[Guest]] = select_default_attacker_guests,
    validate_attacker_guest_ownership_fn: Callable[[Any, list[Guest]], None] = validate_attacker_guest_ownership,
) -> tuple[list[Guest], list[Guest]]:
    if attacker_guests is None:
        guests = select_default_attacker_guests_fn(manor, limit)
    else:
        guests = attacker_guests
        if not guests:
            raise BattlePreparationError("请选择可出征的门客")
        if len(guests) > limit:
            raise BattlePreparationError(f"最多只能派出 {limit} 名门客出征")
        validate_attacker_guest_ownership_fn(manor, guests)
    return guests, guests[:limit]


def build_battle_options(
    *,
    battle_type: str,
    seed: int | None,
    rng_version: int,
    battle_engine_version: str,
    troop_loadout: dict[str, int] | None,
    fill_default_troops: bool,
    defender_setup: dict[str, Any] | None,
    defender_guests: list[Guest] | None,
    defender_limit: int,
    drop_table: dict[str, Any] | None,
    opponent_name: str | None,
    travel_seconds: int | None,
    auto_reward: bool,
    drop_handler: Callable[[dict[str, int]], None] | None,
    rng_source: random.Random | None,
    send_message: bool,
    limit: int = MAX_SQUAD,
    apply_damage: bool,
    attacker_tech_levels: dict[str, int] | None,
    attacker_guest_bonuses: dict[str, float] | None,
    attacker_guest_skills: list[str] | None,
    attacker_manor,
    defender_manor,
    validate_attacker_troop_capacity: bool,
) -> BattleOptions:
    return BattleOptions(
        battle_type=battle_type,
        seed=seed,
        rng_version=rng_version,
        battle_engine_version=battle_engine_version,
        troop_loadout=troop_loadout,
        fill_default_troops=fill_default_troops,
        defender_setup=defender_setup,
        defender_guests=defender_guests,
        defender_limit=defender_limit,
        drop_table=drop_table,
        opponent_name=opponent_name,
        travel_seconds=travel_seconds,
        auto_reward=auto_reward,
        drop_handler=drop_handler,
        rng_source=rng_source,
        send_message=send_message,
        limit=limit,
        apply_damage=apply_damage,
        attacker_tech_levels=attacker_tech_levels,
        attacker_guest_bonuses=attacker_guest_bonuses,
        attacker_guest_skills=attacker_guest_skills,
        attacker_manor=attacker_manor,
        defender_manor=defender_manor,
        validate_attacker_troop_capacity=validate_attacker_troop_capacity,
    )
