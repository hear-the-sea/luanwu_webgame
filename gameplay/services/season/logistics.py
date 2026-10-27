"""据点补给需求：实际驻军、城防缺口、已预留维修与在途货物。"""

from datetime import datetime, timedelta
from fractions import Fraction
from math import ceil
from typing import Any

from gameplay.models import SeasonExpedition

from .campaign_rules import campaign_rules
from .resource_rules import RESOURCES, resource_rules


def garrison_supply_seconds(season: Any, run: Any, now: datetime, horizon_seconds: int) -> int:
    """驻防只预留仍可能发生的消耗，过期驻军和行军时间不生成缺口。"""
    exp = campaign_rules(season)["expeditions"]
    cutoff = min(season.ends_at, season.starts_at + timedelta(days=exp["combat_days"]))
    if run.status == "outbound":
        arrival = max(now, run.launched_at + timedelta(seconds=run.travel_seconds))
        duration = run.result.get("station_seconds", exp["station_seconds"])
        until = min(cutoff, arrival + timedelta(seconds=duration))
        return min(horizon_seconds, max(0, int((until - arrival).total_seconds())))
    if run.status == "stationed":
        until = min(cutoff, run.stationed_until or run.due_at)
        return min(horizon_seconds, max(0, int((until - now).total_seconds())))
    return 0


def garrison_grain_requirement(season: Any, run: Any, now: datetime, horizon_seconds: int) -> Fraction:
    """共用驻军真实时域和冻结单价，保持城内需求与公库保供口径一致。"""
    seconds = garrison_supply_seconds(season, run, now, horizon_seconds)
    if not seconds:
        return Fraction(0)
    operations = run.result.get("operation_costs", {}).get("rules")
    if operations:
        equivalent = Fraction(sum(count for key, count in run.troops.items() if key != "bloodthirsty_cavalry"))
        equivalent += Fraction(str(operations["cavalry_garrison_cost_multiplier"])) * run.troops.get(
            "bloodthirsty_cavalry", 0
        )
        return equivalent * operations["garrison_normal_grain_per_100_per_hour"] * seconds / 360000
    legacy = campaign_rules(season).get("activities", {}).get("transport", {})
    return Fraction(
        max(1, ceil(sum(run.troops.values()) / 100)) * legacy.get("garrison_grain_per_100", 2) * seconds,
        legacy.get("supply_tick_seconds", 300),
    )


def site_requirements(season: Any, site: Any, now: datetime) -> dict[str, int]:
    rules = resource_rules(season)
    if not site.owner:
        return dict.fromkeys(RESOURCES, 0)
    garrisons = SeasonExpedition.objects.filter(
        member__season=season,
        member__faction=site.owner,
        node_key=site.key,
        kind="garrison",
        status__in=("outbound", "stationed"),
    )
    horizon = int(rules["transport"]["target_food_hours"] * 3600)
    food = sum((garrison_grain_requirement(season, run, now, horizon) for run in garrisons), Fraction(0))
    wall = site.npc_profile.get("durability", campaign_rules(season)["sites"][site.key]["durability"])
    missing = max(0, wall - site.durability)
    repair = rules["operations"]["repair_cost_per_100_actual_wall"]
    from .fortifications import fortification_rules, tower_state
    from .hitpoints import hitpoint_rules
    from .operation_costs import tower_repair_cost

    hp = hitpoint_rules(season)
    divisor = 100 * (hp["hp_per_work"] if hp["enabled"] else 1)

    tower = tower_state(site)
    tower_cost = (
        tower_repair_cost(season, max(0, tower["max_hp"] - tower["hp"]))
        if fortification_rules(season)["enabled"]
        else {}
    )
    return {
        "grain": ceil(food),
        "wood": ceil(missing * repair["wood"] / divisor) + tower_cost.get("wood", 0),
        "iron": ceil(missing * repair["iron"] / divisor) + tower_cost.get("iron", 0),
    }


def site_demand(season: Any, site: Any, now: datetime, exclude_run_id: int | None = None) -> dict[str, int]:
    required = site_requirements(season, site, now)
    reserved = dict.fromkeys(RESOURCES, 0)
    # 随在途驻军携带的首批口粮已从公共库预留，不能再装第二份。
    for garrison in SeasonExpedition.objects.filter(
        member__season=season, member__faction=site.owner, node_key=site.key, kind="garrison", status="outbound"
    ):
        reserved["grain"] += garrison.result.get("operation_costs", {}).get("carried_food_balance", 0)
    # 本地已经扣出用于维修的材料仍属于已承诺供应，不能再次生成同一缺口订单。
    repairs = SeasonExpedition.objects.filter(
        member__season=season, member__faction=site.owner, node_key=site.key, kind="repair", status="outbound"
    )
    for run in repairs:
        for key, amount in run.cargo.items():
            reserved[key] = reserved.get(key, 0) + amount
    convoys = SeasonExpedition.objects.filter(
        member__season=season,
        member__faction=site.owner,
        kind="transport",
        status__in=("outbound", "transporting"),
        result__activity__target_site_key=site.key,
    )
    if exclude_run_id is not None:
        convoys = convoys.exclude(pk=exclude_run_id)
    for run in convoys:
        for key, amount in run.cargo.items():
            reserved[key] = reserved.get(key, 0) + amount
    return {key: max(0, required[key] - site.supplies.get(key, 0) - reserved.get(key, 0)) for key in RESOURCES}


def convoy_capacity(season: Any, faction: str) -> int:
    config = resource_rules(season)
    bonus = (
        config["site_effects"]["river:docks"]["cargo_capacity_bonus_percent"]
        if season.sites.filter(key="river:docks", owner=faction).exists()
        else 0
    )
    return int(config["transport"]["cargo_capacity"] * (100 + bonus) / 100)


def allocate_cargo(needs: dict[str, int], capacity: int) -> dict[str, int]:
    total = sum(needs.values())
    if total <= capacity:
        return dict(needs)
    result = {key: amount * capacity // total for key, amount in needs.items()}
    remaining = capacity - sum(result.values())
    for key in sorted(needs, key=lambda key: (-(needs[key] * capacity % total), key))[:remaining]:
        result[key] += 1
    return result


def settle_surplus_locked(season: Any, now: datetime) -> None:
    """需求减少时将多余当地库存退回当前所有方，保留旧队伍驻防用粮。"""
    from .economy import produce_locked
    from .resource_ledger import record_flow

    if not resource_rules(season)["enabled"]:
        return
    states = None
    for site in season.sites.exclude(owner=""):
        required = site_requirements(season, site, now)
        surplus = {key: max(0, amount - required.get(key, 0)) for key, amount in site.supplies.items()}
        if not any(surplus.values()):
            continue
        if states is None:
            states = produce_locked(season, now)
        state = states[site.owner]
        state.resources = {key: amount + surplus.get(key, 0) for key, amount in state.resources.items()}
        record_flow(state, "local_surplus_return", surplus, now)
        state.save(update_fields=["resources", "economy_state"])
        site.supplies = {key: amount - surplus.get(key, 0) for key, amount in site.supplies.items()}
        site.save(update_fields=["supplies"])
