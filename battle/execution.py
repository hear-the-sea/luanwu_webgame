from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from django.db import transaction
from django.utils import timezone

from core.exceptions import BattlePreparationError
from core.utils import require_positive_int
from guests.guest_combat_stats import is_live_guest_model, resolve_guest_combat_stats
from guests.models import Guest, GuestStatus
from guests.services.health import recover_guest_hp
from guests.services.loyalty import grant_battle_victory_loyalty, start_injury_loyalty_decay
from guests.services.status import GUEST_STATUS_UPDATE_FIELDS, prepare_guest_status_transition

from .city_defense import build_city_defense_combatants, serialize_city_defenses_for_report
from .combatants_pkg import (
    Combatant,
    assign_agility_based_priorities,
    build_ai_guests,
    build_guest_combatants,
    build_named_ai_guests,
    build_troop_combatants,
    generate_ai_loadout,
    normalize_troop_loadout,
    serialize_guest_for_report,
)
from .combatants_pkg.troop_device_bonuses import TroopDeviceBonusSummary, build_troop_device_bonus_summary
from .constants import DEFAULT_BATTLE_TYPE, MAX_SQUAD, get_battle_config
from .defender_setup import build_defender_guest_and_loadout as _build_defender_guest_and_loadout_from_sources
from .models import BattleReport
from .random_context import (
    CURRENT_BATTLE_ENGINE_VERSION,
    CURRENT_RNG_VERSION,
    MAX_PERSISTED_SEED,
    RNG_STREAM_AI_GROWTH,
    RNG_STREAM_COMBAT,
    BattleRandomContext,
)
from .rewards import dispatch_battle_message, grant_battle_rewards
from .simulation_core import simulate_battle


@dataclass
class BattleOptions:
    battle_type: str = DEFAULT_BATTLE_TYPE
    seed: int | None = None
    rng_version: int = CURRENT_RNG_VERSION
    battle_engine_version: str = CURRENT_BATTLE_ENGINE_VERSION
    troop_loadout: Dict[str, int] | None = None
    fill_default_troops: bool = True
    defender_setup: Dict[str, Any] | None = None
    defender_guests: List[Guest] | None = None
    defender_limit: int = MAX_SQUAD
    drop_table: Dict[str, Any] | None = None
    opponent_name: str | None = None
    travel_seconds: int | None = None
    auto_reward: bool = True
    drop_handler: Callable[[Dict[str, int]], None] | None = None
    rng_source: random.Random | None = None
    send_message: bool = True
    limit: int = MAX_SQUAD
    apply_damage: bool = True
    # 快照战斗不得把胜负奖励写回真实门客。
    apply_victory_loyalty: bool = True
    # 快照战斗不得触发真实门客的 HP 自然恢复。
    recover_live_guest_hp: bool = True
    attacker_tech_levels: Dict[str, int] | None = None
    attacker_guest_bonuses: Dict[str, float] | None = None
    attacker_guest_skills: List[str] | None = None
    attacker_manor: Any | None = None
    defender_manor: Any | None = None
    validate_attacker_troop_capacity: bool = True


def _normalize_optional_mapping(raw: Any, *, contract_name: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid {contract_name}: {raw!r}")
    return raw


def _normalize_skill_keys(raw: Any, *, contract_name: str) -> List[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, set)):
        raise AssertionError(f"invalid {contract_name}: {raw!r}")
    keys: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise AssertionError(f"invalid {contract_name} entry: {item!r}")
        keys.append(item.strip())
    return keys or None


def _recover_guest_hp_batch(guests: List[Any], now) -> None:
    for guest in guests:
        if is_live_guest_model(guest) and guest.pk:
            recover_guest_hp(guest, now=now)


def _resolve_battle_rng(
    seed: int | None,
    rng_source: random.Random | None,
    *,
    rng_version: int,
) -> tuple[BattleRandomContext, random.Random]:
    # Compatibility contract: rng_source supplies a base seed only when seed is absent.
    # The battle itself always uses versioned substreams so the persisted seed can replay it.
    resolved_seed = seed
    if resolved_seed is None and rng_source is not None:
        resolved_seed = rng_source.randrange(1, MAX_PERSISTED_SEED + 1)
    context = BattleRandomContext.create(resolved_seed, rng_version=rng_version)
    return context, context.rng(RNG_STREAM_COMBAT)


def _extract_defender_tech_profile(defender_setup: Dict[str, Any] | None) -> tuple[dict, int, dict, List[str] | None]:
    defender_tech_levels: dict[str, int] = {}
    defender_guest_level = 50
    defender_guest_bonuses: dict[str, float] = {}
    defender_guest_skills: List[str] | None = None

    normalized_setup = _normalize_optional_mapping(defender_setup, contract_name="battle defender setup payload")
    if not normalized_setup:
        return defender_tech_levels, defender_guest_level, defender_guest_bonuses, defender_guest_skills

    tech_conf = _normalize_optional_mapping(
        normalized_setup.get("technology"),
        contract_name="battle defender technology payload",
    )
    if not tech_conf:
        return defender_tech_levels, defender_guest_level, defender_guest_bonuses, defender_guest_skills

    from core.game_data.technology import get_guest_stat_bonuses, resolve_enemy_tech_levels

    defender_tech_levels = resolve_enemy_tech_levels(tech_conf)
    if "guest_level" in tech_conf:
        defender_guest_level = require_positive_int(
            tech_conf.get("guest_level"),
            contract_name="battle defender guest_level",
        )
    defender_guest_bonuses = get_guest_stat_bonuses(tech_conf)
    if "guest_skills" in tech_conf:
        defender_guest_skills = _normalize_skill_keys(
            tech_conf.get("guest_skills"),
            contract_name="battle defender guest_skills",
        )

    return defender_tech_levels, defender_guest_level, defender_guest_bonuses, defender_guest_skills


def _build_defender_guest_and_loadout(
    defender_guests: List[Guest] | None,
    defender_setup: Dict[str, Any] | None,
    defender_limit: int,
    fill_default_troops: bool,
    rng: random.Random,
    now,
    defender_guest_level: int,
    defender_guest_bonuses: Dict[str, float],
    defender_guest_skills: List[str] | None,
    recover_live_guest_hp: bool = True,
) -> tuple[list[Combatant], Dict[str, int]]:
    return _build_defender_guest_and_loadout_from_sources(
        defender_guests=defender_guests,
        defender_setup=defender_setup,
        defender_limit=defender_limit,
        fill_default_troops=fill_default_troops,
        rng=rng,
        now=now,
        defender_guest_level=defender_guest_level,
        defender_guest_bonuses=defender_guest_bonuses,
        defender_guest_skills=defender_guest_skills,
        is_live_guest_model_fn=is_live_guest_model,
        recover_guest_hp_fn=recover_guest_hp,
        build_guest_combatants_fn=build_guest_combatants,
        build_named_ai_guests_fn=build_named_ai_guests,
        generate_ai_loadout_fn=generate_ai_loadout,
        normalize_troop_loadout_fn=normalize_troop_loadout,
        build_ai_guests_fn=build_ai_guests,
        recover_live_guest_hp=recover_live_guest_hp,
    )


def validate_troop_capacity(guests: List[Any], troop_loadout: Dict[str, int]) -> None:
    if not guests:
        return

    total_capacity = sum(resolve_guest_combat_stats(guest).troop_capacity for guest in guests)
    total_troops = sum(troop_loadout.values())
    if total_troops > total_capacity:
        guest_count = len(guests)
        raise BattlePreparationError(
            f"兵力超过带兵上限！当前出征{guest_count}名门客，"
            f"总带兵上限为{total_capacity}，实际兵力为{total_troops}。"
            f"请减少兵力或增派更多门客。"
        )


def _prepare_battle_environment(active_guests: List[Guest], options: BattleOptions) -> Dict[str, int]:
    now = timezone.now()
    if options.recover_live_guest_hp:
        _recover_guest_hp_batch(active_guests, now)

    normalized_loadout = normalize_troop_loadout(options.troop_loadout, default_if_empty=options.fill_default_troops)
    if options.validate_attacker_troop_capacity:
        validate_troop_capacity(active_guests, normalized_loadout)
    return normalized_loadout


def _build_attacker_units(
    guests: List[Guest],
    active_guests: List[Guest],
    normalized_loadout: Dict[str, int],
    options: BattleOptions,
    manor,
) -> tuple[List[Combatant], List[Combatant], TroopDeviceBonusSummary]:
    attacker_guests_comb = build_guest_combatants(
        guests,
        side="attacker",
        limit=options.limit,
        stat_bonuses=options.attacker_guest_bonuses,
        override_skill_keys=options.attacker_guest_skills,
    )

    attacker_manor = manor if options.attacker_manor is None else options.attacker_manor
    attacker_device_summary = build_troop_device_bonus_summary(active_guests)
    attacker_troops = build_troop_combatants(
        normalized_loadout,
        side="attacker",
        manor=attacker_manor,
        tech_levels=options.attacker_tech_levels,
        device_bonuses=attacker_device_summary.bonuses,
    )
    return attacker_guests_comb, attacker_troops, attacker_device_summary


def _build_defender_units(
    options: BattleOptions,
    rng: random.Random,
    now,
) -> tuple[List[Combatant], List[Combatant], List[Combatant], Dict[str, int], TroopDeviceBonusSummary]:
    defender_tech_levels, defender_guest_level, defender_guest_bonuses, defender_guest_skills = (
        _extract_defender_tech_profile(options.defender_setup)
    )

    defender_guests_comb, defender_loadout = _build_defender_guest_and_loadout(
        options.defender_guests,
        options.defender_setup,
        options.defender_limit,
        options.fill_default_troops,
        rng,
        now,
        defender_guest_level,
        defender_guest_bonuses,
        defender_guest_skills,
        options.recover_live_guest_hp,
    )
    active_defender_guests = (options.defender_guests or [])[: options.defender_limit]
    defender_device_summary = build_troop_device_bonus_summary(active_defender_guests)
    defender_troops = build_troop_combatants(
        defender_loadout,
        side="defender",
        tech_levels=defender_tech_levels or None,
        device_bonuses=defender_device_summary.bonuses,
    )
    defender_city_defenses = build_city_defense_combatants(options.defender_manor, side="defender")
    return defender_guests_comb, defender_troops, defender_city_defenses, defender_loadout, defender_device_summary


def _execute_simulation(
    attacker_units: List[Combatant],
    defender_units: List[Combatant],
    options: BattleOptions,
    config: Dict,
    rng: random.Random,
    final_seed: int,
) -> tuple[Any, str]:
    assign_agility_based_priorities(attacker_units, defender_units)
    opponent_label = options.opponent_name or config.get("name", "乱军试炼")
    simulation = simulate_battle(
        attacker_units=attacker_units,
        defender_units=defender_units,
        rng=rng,
        seed=final_seed,
        travel_seconds=options.travel_seconds,
        config=config,
        drop_table=options.drop_table,
    )
    return simulation, opponent_label


def apply_guest_hp_updates(
    guests: List[Any],
    combatants: List[Combatant],
    apply_damage: bool,
) -> Dict[int, int]:
    now = timezone.now()
    guest_map = {c.guest_id: c for c in combatants if c.guest_id}
    hp_updates: Dict[int, int] = {}
    dirty_guests: List[Guest] = []
    for guest in guests:
        comb = guest_map.get(guest.pk)
        if not comb:
            continue
        defeated = comb.hp <= 0
        remaining_hp = 1 if defeated else max(1, min(guest.max_hp, comb.hp))
        hp_updates[guest.pk] = remaining_hp
        if apply_damage and is_live_guest_model(guest) and guest.pk:
            guest.current_hp = remaining_hp
            guest.last_hp_recovery_at = now
            if defeated:
                prepare_guest_status_transition(guest, GuestStatus.INJURED, now=now)
                start_injury_loyalty_decay(guest, now=now)
            dirty_guests.append(guest)
    if apply_damage and dirty_guests:
        Guest.objects.bulk_update(
            dirty_guests,
            list(
                dict.fromkeys(
                    [
                        "current_hp",
                        "last_hp_recovery_at",
                        "injury_loyalty_processed_at",
                        *GUEST_STATUS_UPDATE_FIELDS,
                    ]
                )
            ),
        )
    return hp_updates


def _guests_in_combatants(guests: List[Any], combatants: List[Combatant]) -> list[Any]:
    combatant_guest_ids = {combatant.guest_id for combatant in combatants if combatant.guest_id}
    if not combatant_guest_ids:
        return []
    return [
        guest for guest in guests if (getattr(guest, "pk", None) or getattr(guest, "id", None)) in combatant_guest_ids
    ]


def _finalize_battle_results(
    manor,
    simulation: Any,
    guests: List[Guest],
    attacker_guests_comb: List[Combatant],
    defender_guests_comb: List[Combatant],
    defender_city_defenses: List[Combatant],
    normalized_loadout: Dict[str, int],
    defender_loadout: Dict[str, int],
    options: BattleOptions,
    opponent_label: str,
    random_context: BattleRandomContext | None = None,
    attacker_equipment_bonuses: list[dict[str, Any]] | None = None,
    defender_equipment_bonuses: list[dict[str, Any]] | None = None,
) -> BattleReport:
    resolved_random_context = random_context or BattleRandomContext.create(
        simulation.seed,
        rng_version=options.rng_version,
    )
    with transaction.atomic():
        grant_battle_rewards(
            manor,
            simulation.drops,
            opponent_label,
            auto_reward=options.auto_reward,
            drop_handler=options.drop_handler,
        )

        if options.apply_victory_loyalty:
            if simulation.winner == "attacker":
                grant_battle_victory_loyalty(_guests_in_combatants(guests, attacker_guests_comb))
            elif simulation.winner == "defender" and options.defender_guests is not None:
                grant_battle_victory_loyalty(_guests_in_combatants(options.defender_guests, defender_guests_comb))

        hp_updates = apply_guest_hp_updates(guests, attacker_guests_comb, apply_damage=options.apply_damage)
        simulation.losses["attacker"]["hp_updates"] = hp_updates

        if options.defender_guests is not None:
            defender_hp_updates = apply_guest_hp_updates(
                options.defender_guests,
                defender_guests_comb,
                apply_damage=options.apply_damage,
            )
            simulation.losses["defender"]["hp_updates"] = defender_hp_updates

        defender_city_defense_rows = serialize_city_defenses_for_report(defender_city_defenses)
        if options.apply_damage and options.defender_manor is not None:
            from gameplay.services.city_defense import apply_city_defense_battle_damage

            apply_city_defense_battle_damage(options.defender_manor, defender_city_defense_rows)

        report = BattleReport.objects.create(
            manor=manor,
            opponent_name=opponent_label,
            battle_type=options.battle_type,
            attacker_team=[serialize_guest_for_report(c) for c in attacker_guests_comb],
            attacker_troops=normalized_loadout,
            attacker_city_defenses=[],
            attacker_equipment_bonuses=list(attacker_equipment_bonuses or []),
            defender_team=[serialize_guest_for_report(c) for c in defender_guests_comb],
            defender_troops=defender_loadout,
            defender_city_defenses=defender_city_defense_rows,
            defender_equipment_bonuses=list(defender_equipment_bonuses or []),
            rounds=simulation.rounds,
            losses=simulation.losses,
            drops=simulation.drops,
            winner=simulation.winner,
            starts_at=simulation.starts_at,
            completed_at=simulation.completed_at,
            seed=simulation.seed,
            rng_version=resolved_random_context.rng_version,
            battle_engine_version=options.battle_engine_version,
        )

        if options.send_message:
            transaction.on_commit(lambda: dispatch_battle_message(manor, opponent_label, report))
        return report


def execute_battle(
    manor,
    guests: List[Guest],
    active_guests: List[Guest],
    options: BattleOptions,
) -> BattleReport:
    config = get_battle_config(options.battle_type)
    normalized_loadout = _prepare_battle_environment(active_guests, options)
    random_context, rng = _resolve_battle_rng(
        options.seed,
        options.rng_source,
        rng_version=options.rng_version,
    )
    attacker_guests_comb, attacker_troops, attacker_device_summary = _build_attacker_units(
        guests,
        active_guests,
        normalized_loadout,
        options,
        manor,
    )
    now = timezone.now()
    (
        defender_guests_comb,
        defender_troops,
        defender_city_defenses,
        defender_loadout,
        defender_device_summary,
    ) = _build_defender_units(options, random_context.rng(RNG_STREAM_AI_GROWTH), now)
    attacker_units = attacker_guests_comb + attacker_troops
    defender_units = defender_guests_comb + defender_troops + defender_city_defenses
    simulation, opponent_label = _execute_simulation(
        attacker_units,
        defender_units,
        options,
        config,
        rng,
        random_context.base_seed,
    )
    return _finalize_battle_results(
        manor,
        simulation,
        guests,
        attacker_guests_comb,
        defender_guests_comb,
        defender_city_defenses,
        normalized_loadout,
        defender_loadout,
        options,
        opponent_label,
        random_context,
        attacker_equipment_bonuses=attacker_device_summary.devices,
        defender_equipment_bonuses=defender_device_summary.devices,
    )


__all__ = [
    "BattleOptions",
    "_build_defender_guest_and_loadout",
    "_extract_defender_tech_profile",
    "apply_guest_hp_updates",
    "execute_battle",
    "validate_troop_capacity",
]
