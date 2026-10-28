"""城内实物驱动自动维修，未到物资不追溯修复，公共训练先留足战备。"""

from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gameplay.models import SeasonExpedition, SeasonFaction

from .fortifications import tower_state
from .hitpoints import hitpoint_rules
from .occupation import occupation_rules


def repair_snapshot(season: Any) -> dict[str, Any]:
    from .fortifications import fortification_rules
    from .resource_rules import resource_rules

    hp = hitpoint_rules(season)
    return {
        "work_amount": hp["repair_amount"],
        "hp_per_work": hp["hp_per_work"],
        "wall_prices": resource_rules(season)["operations"]["repair_cost_per_100_actual_wall"],
        "tower_prices": fortification_rules(season)["tower"]["repair_cost_per_100"],
    }


def next_repair_plan(site: Any, *, affordable: bool = False, cycles: int = 1) -> tuple[int, int, dict[str, int]]:
    from .operation_costs import fortification_repair_plan

    tower = tower_state(site)
    from .population import npc_profile

    maximum = site.npc_profile.get("durability") or npc_profile(site.season, site.key)["durability"]
    wall = max(0, maximum - site.durability)
    snapshot = repair_snapshot(site.season)
    snapshot["work_amount"] *= cycles
    return fortification_repair_plan(
        snapshot, wall, tower["max_hp"] - tower["hp"], site.supplies if affordable else None
    )


def settle_auto_repairs_locked(season: Any, now: datetime) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("自动维修须持有赛季锁并处于事务")
    config = occupation_rules(season)
    if not config["enabled"] or not config["automatic_repair"] or season.closed:
        return
    from .campaign_rules import campaign_rules
    from .operation_costs import fortification_repair_plan
    from .resource_ledger import record_flow

    hp = hitpoint_rules(season)
    cutoff = min(
        now, season.ends_at, season.starts_at + timedelta(days=campaign_rules(season)["expeditions"]["combat_days"])
    )
    interval = hp["repair_interval_seconds"]
    for site in season.sites.exclude(owner=""):
        if site.durability <= 0:
            continue  # 已被击破的旧归属不得在占领结算前复活。
        previous = parse_datetime(site.defenses.get("auto_repair_at", ""))
        if previous is None:
            site.defenses = {**site.defenses, "auto_repair_at": cutoff.isoformat()}
            site.save(update_fields=["defenses"])
            continue
        ticks = max(0, int((cutoff - previous).total_seconds()) // interval)
        if not ticks:
            continue
        snapshot = repair_snapshot(season)
        tower = tower_state(site)
        from .population import npc_profile

        maximum = site.npc_profile.get("durability") or npc_profile(season, site.key)["durability"]
        wall_missing = max(0, maximum - site.durability)
        tower_missing = max(0, tower["max_hp"] - tower["hp"])
        spent: dict[str, int] = {}
        flows: dict[str, dict[str, Any]] = {}
        restored_wall = restored_tower = 0
        last_at = previous
        for index in range(ticks):
            tick = previous + timedelta(seconds=interval * (index + 1))
            if not wall_missing and not tower_missing:
                break
            wall, repaired_tower, costs = fortification_repair_plan(
                snapshot, wall_missing, tower_missing, site.supplies
            )
            if not wall and not repaired_tower:
                break
            flow = flows.setdefault(timezone.localdate(tick).isoformat(), {"at": tick, "spent": {}})
            flow["at"] = tick
            for key, amount in costs.items():
                flow["spent"][key] = flow["spent"].get(key, 0) + amount
                site.supplies[key] = site.supplies.get(key, 0) - amount
                spent[key] = spent.get(key, 0) + amount
            wall_missing -= wall
            tower_missing -= repaired_tower
            restored_wall += wall
            restored_tower += repaired_tower
            last_at = tick
        site.durability += restored_wall
        site.defenses = {
            **site.defenses,
            "auto_repair_at": (previous + timedelta(seconds=ticks * interval)).isoformat(),
            "arrow_tower": {**site.defenses.get("arrow_tower", {}), **tower, "hp": tower["hp"] + restored_tower},
            "auto_repair_last": {
                "at": last_at.isoformat(),
                "wall": restored_wall,
                "tower": restored_tower,
                "spent": spent,
            },
        }
        site.save(update_fields=["durability", "supplies", "defenses"])
        if any(spent.values()):
            state = SeasonFaction.objects.get(season=season, faction=site.owner)
            for flow in flows.values():
                record_flow(state, "site_repair", {key: -amount for key, amount in flow["spent"].items()}, flow["at"])
            state.save(update_fields=["economy_state"])


def repair_material_reserve(season: Any, faction: str) -> dict[str, int]:
    from collections import defaultdict

    config = occupation_rules(season)
    reserve = {"wood": 0, "iron": 0}
    if not config["enabled"] or not config["automatic_repair"]:
        return reserve
    incoming: dict[str, dict[str, int]] = defaultdict(lambda: {"wood": 0, "iron": 0})
    for run in SeasonExpedition.objects.filter(
        member__season=season,
        member__faction=faction,
        kind__in=("transport", "repair"),
        status__in=("outbound", "transporting"),
    ).only("kind", "node_key", "result", "cargo"):
        target = run.node_key if run.kind == "repair" else run.result.get("activity", {}).get("target_site_key")
        if target:
            for key in reserve:
                incoming[target][key] += run.cargo.get(key, 0)
    for site in season.sites.filter(owner=faction):
        if site.durability <= 0:
            continue
        _, _, cost = next_repair_plan(site, cycles=config["repair_reserved_cycles"])
        for key in reserve:
            reserve[key] += max(
                0,
                cost.get(key, 0) - site.supplies.get(key, 0) - incoming[site.key][key],
            )
    return reserve


def resource_reserves(
    season: Any, faction: str, *, operating: bool = False, now: datetime | None = None
) -> dict[str, int]:
    from .resource_production import _protected_grain
    from .resource_rules import economic_population, resource_rules

    config = occupation_rules(season)
    result = {
        "grain": _protected_grain(season, faction, resource_rules(season), now=now),
        **repair_material_reserve(season, faction),
    }
    if operating and config["enabled"]:
        result["grain"] += int(economic_population(season, faction) * config["operating_grain_per_active"])
    return result


def recovery_status(site: Any, now: datetime) -> dict[str, Any]:
    config = occupation_rules(site.season)
    if not config["enabled"] or not config["automatic_repair"] or not site.owner or site.season.closed:
        return {}
    from .fortifications import needs_repair

    previous = parse_datetime(site.defenses.get("auto_repair_at", "")) or now
    _, _, costs = next_repair_plan(site)
    return {
        "enabled": True,
        "complete": not needs_repair(site),
        "next_at": previous + timedelta(seconds=hitpoint_rules(site.season)["repair_interval_seconds"]),
        "short": any(site.supplies.get(key, 0) < amount for key, amount in costs.items()),
        "paused": sum(next_repair_plan(site, affordable=True)[:2]) == 0 and needs_repair(site),
    }
