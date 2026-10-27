"""城门与箭塔使用战斗生命，跨场保留，维修按工作量折算。"""

from copy import deepcopy
from dataclasses import replace
from typing import Any

from django.utils.dateparse import parse_datetime

DEFAULT_HITPOINTS = {
    "enabled": True,
    "gate_hp": 1500000,
    "tower_hp": 100000,
    "hp_per_work": 1000,
    "repair_amount": 100000,
    "repair_interval_seconds": 1800,
    "gate_normal_rounds": 8,
}


def hitpoint_rules(season: Any) -> dict[str, Any]:
    from .fortifications import fortification_rules

    fort = fortification_rules(season)
    result = {**DEFAULT_HITPOINTS, **fort.get("hitpoints", {})}
    result["enabled"] = bool(fort["enabled"] and fort.get("hitpoints", {}).get("enabled", False))
    return deepcopy(result)


def repair_ready_at(site: Any) -> Any:
    value = site.defenses.get("repair_available_at")
    return parse_datetime(value) if value else None


def prepare_gate(prepared: dict[str, Any], site: Any) -> dict[str, Any]:
    from battle.combatants_pkg.core import Combatant

    if not hitpoint_rules(site.season)["enabled"] or not site.durability:
        return {}
    limit = hitpoint_rules(site.season)["gate_normal_rounds"]
    prepared["prepared"] = replace(
        prepared["prepared"], config={**prepared["prepared"].config, "normal_round_limit": limit}
    )
    maximum = site.npc_profile["durability"]
    prepared["prepared"].defender_city_defenses.append(
        Combatant(
            name="城门",
            attack=0,
            defense=0,
            hp=site.durability,
            max_hp=maximum,
            side="defender",
            rarity="city_defense",
            luck=0,
            agility=0,
            priority=0,
            kind="city_defense",
            troop_strength=1,
            initial_troop_strength=1,
            initial_hp=site.durability,
            unit_hp=maximum,
            template_key="wall",
            battle_modifiers={"skip_turn": True, "wall_intercept_chance": 0.8},
        )
    )
    return {"initial_hp": site.durability, "max_hp": maximum, "owner": site.owner, "normal_round_limit": limit}


def settle_gate_locked(site: Any, report: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    from django.db import transaction

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("城门结算需要赛季锁和事务")
    if not snapshot:
        return {}
    previous = site.defenses.get("gate_last_report_id", 0)
    if report.pk <= previous:
        return site.defenses.get("gate_last_battle", {})
    if site.owner != snapshot["owner"]:
        raise ValueError("城门归属已经变化")
    row = next(row for row in report.defender_city_defenses if row["key"] == "wall")
    remaining = min(snapshot["initial_hp"], max(0, int(row["hp"])))
    result = {**snapshot, "remaining_hp": remaining, "combat_damage": snapshot["initial_hp"] - remaining}
    site.durability = remaining
    site.defenses = {**site.defenses, "gate_last_report_id": report.pk, "gate_last_battle": result}
    site.save(update_fields=["durability", "defenses"])
    return result
