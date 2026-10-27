"""军需调剂只处理公共库存富余/短缺，不产生个人贡献。"""

from datetime import datetime
from math import ceil
from typing import Any

from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import SeasonAction

from .economy import produce_locked
from .resource_ledger import record_flow
from .resource_rules import RESOURCES, daily_resource_output, economic_population, resource_caps, resource_rules
from .simplification import simplification_rules
from .warfare import campaign_open


def market_return_percent(season: Any, faction: str) -> int:
    """优惠只提高占领方返还比例，保持损耗和原有公共限额。"""
    base = simplification_rules(season)["market"]["return_percent"]
    config = season.rules.get("campaign", {}).get("market_control") or {}
    owns_market = (
        bool(faction) and config.get("enabled") and season.sites.filter(key="river:market", owner=faction).exists()
    )
    return min(99, base + (config.get("owner_return_bonus_percent", 0) if owns_market else 0))


def market_offers(season: Any, state: Any, now: datetime) -> dict[str, Any]:
    rules = simplification_rules(season)
    if not rules["enabled"] or state is None:
        return {"offers": [], "remaining": 0}
    config = rules["market"]
    return_percent = market_return_percent(season, state.faction)
    weights = resource_rules(season)["transport"]["resource_value_weights"]
    population = economic_population(season, state.faction)
    output = daily_resource_output(season, state.faction, state.levels)
    reference = resource_rules(season)["storage"]["reference_pve_daily"]
    income = {key: output[key] + population * reference[key] for key in RESOURCES}
    caps = resource_caps(season, state.faction, state.levels)
    used = SeasonAction.objects.filter(
        member__season=season, member__faction=state.faction, kind="market_exchange", date=timezone.localdate(now)
    ).count()
    remaining = max(0, config["daily_faction_orders"] - used)
    offers: list[dict[str, Any]] = []
    if not remaining or not campaign_open(season, now) or not population:
        return {"offers": offers, "remaining": remaining}
    from .occupation import occupation_rules

    if occupation_rules(season)["enabled"]:
        from .recovery import resource_reserves

        protected = resource_reserves(season, state.faction, operating=True, now=now)
    else:
        protected = {}
    for source in RESOURCES:
        cost = ceil(config["order_value"] / weights[source])
        if state.resources.get(source, 0) - cost < max(
            income[source] * config["source_reserve_days"], protected.get(source, 0)
        ):
            continue
        for target in RESOURCES:
            if source == target:
                continue
            received = cost * weights[source] * return_percent // (100 * weights[target])
            if not received or state.resources.get(target, 0) + received > caps[target]:
                continue
            if state.resources.get(target, 0) >= income[target] * config["target_shortage_days"]:
                continue
            offers.append({"source": source, "target": target, "cost": cost, "received": received})
    return {"offers": offers, "remaining": remaining}


def exchange_resources_locked(member: Any, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not simplification_rules(member.season)["enabled"] or not campaign_open(member.season, now):
        raise GameError("当前不能进行赛季军需调剂")
    state = produce_locked(member.season, now)[member.faction]
    offers = market_offers(member.season, state, now)
    offer = next(
        (
            row
            for row in offers["offers"]
            if row["source"] == payload.get("source_resource") and row["target"] == payload.get("target_resource")
        ),
        None,
    )
    if offer is None:
        raise GameError("当前没有对应富余/短缺或今日阵营调剂额度已用完，请先查看后勤")
    if offer["source"] == "grain":
        from .resource_production import _protected_grain

        protected = _protected_grain(member.season, member.faction, resource_rules(member.season), now=now)
        if state.resources["grain"] - offer["cost"] < protected:
            raise GameError("粮草须先保障现有驻军，不能调出")
    delta = {offer["source"]: -offer["cost"], offer["target"]: offer["received"]}
    state.resources = {key: amount + delta.get(key, 0) for key, amount in state.resources.items()}
    record_flow(state, "market_exchange", delta, now)
    state.save(update_fields=["resources", "economy_state"])
    names = {"grain": "粮草", "wood": "木材", "iron": "铁料"}
    return {
        "message": f"军需调剂：公共{names[offer['source']]} -{offer['cost']}，{names[offer['target']]} +{offer['received']}；不发个人贡献",
        **offer,
    }
