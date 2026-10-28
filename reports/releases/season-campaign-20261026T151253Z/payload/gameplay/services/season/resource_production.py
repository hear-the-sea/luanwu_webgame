"""三资源后勤与定额补兵；所有写入由赛季事务串行。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from math import ceil
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gameplay.models import SeasonExpedition

from .resource_rules import (
    RESOURCES,
    daily_resource_output,
    economic_population,
    level_multiplier,
    resource_caps,
    resource_rules,
)

ACTIVE = ("gathering", "outbound", "stationed", "transporting", "returning")


def _flow(row: Any, reason: str, amounts: dict[str, int], now: datetime) -> None:
    from .resource_ledger import record_flow

    record_flow(row, reason, amounts, now)


def _deployed(season: Any, faction: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for troop_map in SeasonExpedition.objects.filter(
        member__season=season, member__faction=faction, status__in=ACTIVE
    ).values_list("troops", flat=True):
        for key, count in troop_map.items():
            counts[key] += count
    return dict(counts)


def _protected_grain(season: Any, faction: str, config: dict[str, Any], now: datetime | None = None) -> int:
    """已扣库的行军预付不再重复预留，只保护未覆盖的前线口粮。"""
    from .logistics import garrison_grain_requirement

    now = now or timezone.now()
    by_site: dict[str, Fraction] = defaultdict(Fraction)
    carried_by_site: dict[str, int] = defaultdict(int)
    horizon = int(config["transport"]["target_food_hours"] * 3600)
    for run in SeasonExpedition.objects.filter(
        member__season=season, member__faction=faction, kind="garrison", status__in=("outbound", "stationed")
    ).only("node_key", "troops", "result", "status", "stationed_until", "due_at", "launched_at", "travel_seconds"):
        by_site[run.node_key] += garrison_grain_requirement(season, run, now, horizon)
        carried_by_site[run.node_key] += run.result.get("operation_costs", {}).get("carried_food_balance", 0)
    if not by_site:
        return 0
    local = {site.key: site.supplies.get("grain", 0) for site in season.sites.filter(key__in=by_site, owner=faction)}
    for run in SeasonExpedition.objects.filter(
        member__season=season, member__faction=faction, kind="transport", status__in=("outbound", "transporting")
    ).only("cargo", "result"):
        key = run.result.get("activity", {}).get("target_site_key")
        if key in by_site:
            local[key] = local.get(key, 0) + run.cargo.get("grain", 0)
    for key, count in carried_by_site.items():
        local[key] = local.get(key, 0) + count
    return sum(max(0, ceil(required) - local.get(key, 0)) for key, required in by_site.items())


def open_new_faction_locked(row: Any, now: datetime) -> bool:
    """仅新规则创建的空库开仓；给旧季启用时绝不补发或覆盖库存。"""
    state = row.economy_state
    if state.get("opened", not state.get("new_economy", False)):
        return False
    season = row.season
    count = economic_population(season, row.faction)
    if count <= 0:
        return False
    config = resource_rules(season)
    grant = {
        key: int(config["initial_grant"]["fixed"][key] + config["initial_grant"]["per_active"][key] * count)
        for key in RESOURCES
    }
    ordinary = sorted(key for key in row.troops if key != "bloodthirsty_cavalry")
    total = int(config["training"]["normal_initial_per_active"] * count)
    for index, key in enumerate(ordinary):
        row.troops[key] = row.troops.get(key, 0) + total // len(ordinary) + (index < total % len(ordinary))
    row.resources = {key: row.resources.get(key, 0) + grant[key] for key in RESOURCES}
    row.economy_state = {**state, "opened": True, "opened_at": now.isoformat(), "initial_population": count}
    _flow(row, "initial", grant, now)
    row.produced_at = max(row.produced_at, now)
    row.save(update_fields=["resources", "troops", "economy_state", "produced_at"])
    return True


def produce_resources_locked(row: Any, rules: dict[str, Any], cutoff: datetime, special_rates: dict[str, Any]) -> None:
    season = row.season
    config = resource_rules(season)
    seconds = int(config["settlement_seconds"])
    if cutoff < row.produced_at:
        return
    if open_new_faction_locked(row, cutoff):
        return
    ticks = max(0, int((cutoff - row.produced_at).total_seconds()) // seconds)
    if not ticks:
        return
    count = economic_population(season, row.faction)
    if not count:
        row.produced_at += timedelta(seconds=ticks * seconds)
        row.save(update_fields=["produced_at"])
        return
    output = daily_resource_output(season, row.faction, row.levels)
    caps = resource_caps(season, row.faction, row.levels)
    away = _deployed(season, row.faction)
    from .recovery import resource_reserves

    protected = resource_reserves(season, row.faction, operating=True, now=cutoff)
    state = dict(row.economy_state)
    carries = {key: Decimal(str(state.get("production_remainders", {}).get(key, 0))) for key in RESOURCES}
    normal_credit = Decimal(str(state.get("normal_training_credit", 0)))
    cavalry_credit = Decimal(str(state.get("cavalry_training_credit", 0)))
    training = config["training"]
    base_training = training
    transition = state.get("training_transition", {})
    effective_at = parse_datetime(transition["effective_at"]) if transition else None
    ordinary = sorted(key for key in row.troops if key != "bloodthirsty_cavalry")
    resources, troops = dict(row.resources), dict(row.troops)
    flows: dict[str, dict[str, Any]] = {}
    overflow = dict.fromkeys(RESOURCES, 0)
    flow_at = row.produced_at
    day_flow: dict[str, Any] = {}
    trained: dict[str, int] = defaultdict(int)
    rotation = int(state.get("training_rotation", 0))

    def train_batch(key: str, costs: dict[str, int]) -> bool:
        if any(resources.get(resource, 0) - protected.get(resource, 0) < amount for resource, amount in costs.items()):
            return False
        for resource, amount in costs.items():
            resources[resource] -= amount
            day_flow["training"][resource] -= amount
        troops[key] = troops.get(key, 0) + 10
        trained[key] += 10
        return True

    for index in range(ticks):
        flow_at = row.produced_at + timedelta(seconds=(index + 1) * seconds)
        training = transition["previous"] if effective_at and flow_at <= effective_at else base_training
        multiplier = level_multiplier(training["training_multipliers"], row.levels.get("training", 1))
        normal_daily = Decimal(str(count * training["normal_daily_capacity_per_active"] * multiplier))
        cavalry_daily = Decimal(str(count * training["cavalry_daily_capacity_per_active"]))
        targets = {
            "normal": int(count * training["normal_target_per_active"]),
            "cavalry": int(count * training["cavalry_target_per_active"]),
        }
        day = timezone.localdate(flow_at).isoformat()
        day_flow = flows.setdefault(
            day, {"at": flow_at, "production": dict.fromkeys(RESOURCES, 0), "training": dict.fromkeys(RESOURCES, 0)}
        )
        day_flow["at"] = flow_at
        for resource in RESOURCES:
            value = carries[resource] + Decimal(str(output[resource])) * seconds / 86400
            gross = int(value + Decimal("0.000000000000000001"))
            carries[resource] = max(Decimal(0), value - gross)
            accepted = min(max(0, caps[resource] - resources.get(resource, 0)), gross)
            resources[resource] = resources.get(resource, 0) + accepted
            day_flow["production"][resource] += accepted
            overflow[resource] += gross - accepted
        normal_total = sum(troops.get(key, 0) + away.get(key, 0) for key in ordinary)
        if normal_total >= targets["normal"]:
            normal_credit = Decimal(0)
        else:
            normal_credit = min(
                max(Decimal(10) + normal_daily * seconds / 86400, normal_daily / 24),
                normal_credit + normal_daily * seconds / 86400,
            )
            while normal_credit + Decimal("0.000000001") >= 10 and normal_total + 10 <= targets["normal"] and ordinary:
                key = min(
                    ordinary,
                    key=lambda item: (
                        troops.get(item, 0) + away.get(item, 0),
                        (ordinary.index(item) - rotation) % len(ordinary),
                    ),
                )
                if not train_batch(key, training["normal_cost_per_10"]):
                    break
                rotation = (ordinary.index(key) + 1) % len(ordinary)
                normal_credit = max(Decimal(0), normal_credit - 10)
                normal_total += 10
        cavalry_total = troops.get("bloodthirsty_cavalry", 0) + away.get("bloodthirsty_cavalry", 0)
        tick_start = row.produced_at + timedelta(seconds=index * seconds)
        special = special_rates.get("bloodthirsty_cavalry")
        if special is None or tick_start < special[1] or cavalry_total >= targets["cavalry"]:
            cavalry_credit = Decimal(0)
        else:
            cavalry_credit = min(
                max(Decimal(10) + cavalry_daily * seconds / 86400, cavalry_daily / 24),
                cavalry_credit + cavalry_daily * seconds / 86400,
            )
            while cavalry_credit + Decimal("0.000000001") >= 10 and cavalry_total + 10 <= targets["cavalry"]:
                if not train_batch("bloodthirsty_cavalry", training["cavalry_cost_per_10"]):
                    break
                cavalry_credit = max(Decimal(0), cavalry_credit - 10)
                cavalry_total += 10
    if effective_at and flow_at >= effective_at:
        state.pop("training_transition", None)
    row.resources, row.troops = resources, troops
    row.economy_state = {
        **state,
        "opened": True,
        "production_remainders": {key: str(value) for key, value in carries.items()},
        "normal_training_credit": str(normal_credit),
        "cavalry_training_credit": str(cavalry_credit),
        "training_rotation": rotation,
        "production_overflow_total": {
            key: state.get("production_overflow_total", {}).get(key, 0) + overflow[key] for key in RESOURCES
        },
        "trained_total": {
            key: state.get("trained_total", {}).get(key, 0) + trained.get(key, 0)
            for key in set(state.get("trained_total", {})) | set(trained)
        },
    }
    for day_flow in flows.values():
        _flow(row, "production", day_flow["production"], day_flow["at"])
        _flow(row, "training", day_flow["training"], day_flow["at"])
    row.produced_at += timedelta(seconds=ticks * seconds)
    row.save(update_fields=["resources", "troops", "economy_state", "produced_at"])
