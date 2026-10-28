"""出征费用独立于货物预留，旧在途快照继续按原规则完成。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from fractions import Fraction
from math import ceil
from typing import Any

from django.utils.dateparse import parse_datetime

from core.exceptions import GameError

from .merit import split_integer
from .resource_rules import resource_rules


def run_operation_rules(run: Any) -> dict[str, Any]:
    return dict(run.result.get("operation_costs", {}).get("rules", {}))


def repair_cost(season: Any, amount: int) -> dict[str, int]:
    rules = resource_rules(season)
    if not rules["enabled"]:
        from .campaign_rules import campaign_rules

        return dict(campaign_rules(season)["expeditions"]["repair_cost"])
    return _wall_cost(rules["operations"]["repair_cost_per_100_actual_wall"], amount)


def _wall_cost(prices: dict[str, int], amount: int, hp_per_work: int = 1) -> dict[str, int]:
    return {key: ceil(Fraction(max(0, amount) * value, 100 * hp_per_work)) for key, value in prices.items()}


def frozen_repair_cost(run: Any, amount: int) -> dict[str, int]:
    return _wall_cost(run_operation_rules(run)["repair_cost_per_100_actual_wall"], amount)


def tower_repair_cost(season: Any, amount: int) -> dict[str, int]:
    from .fortifications import fortification_rules
    from .hitpoints import hitpoint_rules

    hp = hitpoint_rules(season)
    return _wall_cost(
        fortification_rules(season)["tower"]["repair_cost_per_100"], amount, hp["hp_per_work"] if hp["enabled"] else 1
    )


def fortification_repair_plan(
    snapshot: dict[str, Any], wall_missing: int, tower_missing: int, cargo: dict[str, int] | None = None
) -> tuple[int, int, dict[str, int]]:
    """一次工作量先修墙再修塔；到达时只使用已预留、可支付的物资。"""
    wall = min(snapshot["work_amount"], max(0, wall_missing))
    tower = min(snapshot["work_amount"] - wall, max(0, tower_missing))

    def cost(wall_amount: int, tower_amount: int) -> dict[str, int]:
        wall_cost = _wall_cost(snapshot["wall_prices"], wall_amount, snapshot.get("hp_per_work", 1))
        tower_cost = _wall_cost(snapshot["tower_prices"], tower_amount, snapshot.get("hp_per_work", 1))
        return {key: wall_cost.get(key, 0) + tower_cost.get(key, 0) for key in wall_cost.keys() | tower_cost.keys()}

    if cargo is not None:

        def affordable(wall_amount: int, tower_amount: int) -> bool:
            return all(cargo.get(key, 0) >= value for key, value in cost(wall_amount, tower_amount).items())

        def maximum(limit: int, predicate: Any) -> int:
            low, high = 0, limit
            while low < high:
                mid = (low + high + 1) // 2
                if predicate(mid):
                    low = mid
                else:
                    high = mid - 1
            return low

        if not affordable(wall, 0):
            wall, tower = maximum(wall, lambda amount: affordable(amount, 0)), 0
        elif not affordable(wall, tower):
            tower = maximum(tower, lambda amount: affordable(wall, amount))
    return wall, tower, cost(wall, tower)


def _spend_public(member: Any, cost: dict[str, int], now: datetime, *, reason: str = "operations") -> None:
    from .economy import produce_locked

    if not any(cost.values()):
        return
    state = produce_locked(member.season, now)[member.faction]
    from .occupation import occupation_rules

    if occupation_rules(member.season)["enabled"]:
        from .recovery import resource_reserves

        protected = resource_reserves(member.season, member.faction, now=now)
        if any(state.resources.get(key, 0) - amount < protected.get(key, 0) for key, amount in cost.items() if amount):
            raise GameError("公共资源须先保障已有驻军补给和下一轮城防维修，请先运输或讨伐补充")
    elif reason == "rations_reserve":
        from .resource_production import _protected_grain

        protected_grain = _protected_grain(member.season, member.faction, resource_rules(member.season), now=now)
        if state.resources.get("grain", 0) - cost.get("grain", 0) < protected_grain:
            raise GameError("公共粮草须先保障现有驻军补给，请先运输前线或讨伐粮草")
    if any(state.resources.get(key, 0) < value for key, value in cost.items()):
        raise GameError("阵营战备不足，请先补充粮草、木材或铁料")
    state.resources = {key: count - cost.get(key, 0) for key, count in state.resources.items()}
    from .resource_ledger import record_flow

    record_flow(state, reason, {key: -value for key, value in cost.items()}, now)
    state.save(update_fields=["resources", "economy_state"])


def _record(run: Any, kind: str, resources: dict[str, int], **details: Any) -> None:
    book = deepcopy(run.result.get("operation_costs", {}))
    book["expenses"] = [*book.get("expenses", []), {"kind": kind, "resources": resources, **details}]
    run.result = {**run.result, "operation_costs": book}
    run.save(update_fields=["result"])


def reserve_operation_costs_locked(
    member: Any,
    troops: dict[str, int],
    kind: str,
    travel_seconds: int,
    now: datetime,
    *,
    station_seconds: int | None = None,
    reserve_convoy_battle: bool = False,
) -> dict[str, Any]:
    config = resource_rules(member.season)
    if not config["enabled"]:
        return {}
    rules = deepcopy(config["operations"])
    march = 2 * ceil(
        Fraction(sum(troops.values()) * rules["march_grain_per_100_per_10_minutes"] * travel_seconds, 60000)
    )
    courier = rules["courier_grain_per_order"] if kind == "transport" else 0
    from .simplification import simplification_rules

    simple = simplification_rules(member.season)
    carried = (
        ceil(
            weighted_troops(troops, rules["cavalry_garrison_cost_multiplier"])
            * rules["garrison_normal_grain_per_100_per_hour"]
            * min(
                simple.get("garrison_carried_food_hours", 1),
                station_seconds / 3600 if station_seconds is not None else simple.get("garrison_carried_food_hours", 1),
            )
            / 100
        )
        if kind == "garrison" and simple["enabled"]
        else 0
    )
    convoy_battle = (
        ceil(
            weighted_troops(troops, rules["cavalry_battle_cost_multiplier"])
            * rules["battle_normal_grain_per_100_per_wave"]
            / 100
        )
        if reserve_convoy_battle
        else 0
    )
    _spend_public(member, {"grain": march + courier + carried + convoy_battle}, now, reason="rations_reserve")
    return {
        "operation_costs": {
            "version": 1,
            "rules": rules,
            "march_balance": march,
            "march_spent": 0,
            "courier_balance": courier,
            "courier_spent": 0,
            "carried_food_balance": carried,
            **({"convoy_battle_balance": convoy_battle} if reserve_convoy_battle else {}),
            "outward_settled": False,
            "return_settled": False,
            "expenses": [],
        }
    }


def reprice_march_locked(run: Any, travel_seconds: int, now: datetime) -> None:
    rules = run_operation_rules(run)
    if not rules:
        return
    book = deepcopy(run.result["operation_costs"])
    wanted = 2 * ceil(
        Fraction(sum(run.initial_troops.values()) * rules["march_grain_per_100_per_10_minutes"] * travel_seconds, 60000)
    )
    held = book["march_balance"] + book["march_spent"]
    extra = max(0, wanted - held)
    _spend_public(run.member, {"grain": extra}, now, reason="rations_reserve")
    book["march_balance"] += extra
    run.result = {**run.result, "operation_costs": book}
    run.save(update_fields=["result"])


def mark_outbound_arrival_locked(run: Any, now: datetime) -> None:
    rules = run_operation_rules(run)
    if not rules or run.result["operation_costs"].get("outward_settled"):
        return
    # 到达后反复车轮战仍为同一段行军；扫描延迟也不增加行军时间。
    seconds = min(run.travel_seconds, max(0, int((now - run.launched_at).total_seconds())))
    cost = ceil(
        Fraction(sum(run.initial_troops.values()) * rules["march_grain_per_100_per_10_minutes"] * seconds, 60000)
    )
    book = deepcopy(run.result["operation_costs"])
    cost = min(book["march_balance"], cost)
    book["march_balance"] -= cost
    book["march_spent"] += cost
    book["outward_settled"] = True
    run.result = {**run.result, "operation_costs": book}
    _record(run, "outward_march", {"grain": cost}, seconds=seconds)


def start_return_costs_locked(run: Any, now: datetime) -> None:
    rules = run_operation_rules(run)
    if not rules:
        return
    if run.status == "outbound":
        mark_outbound_arrival_locked(run, now)
    book = deepcopy(run.result["operation_costs"])
    if book.get("return_settled"):
        return
    cost = ceil(
        Fraction(sum(run.troops.values()) * rules["march_grain_per_100_per_10_minutes"] * run.travel_seconds, 60000)
    )
    # 往返粮已预留。即使旧异常数据不足也不阻止返家，记录差额供审计。
    charged = min(book["march_balance"], cost)
    book["march_balance"] -= charged
    book["march_spent"] += charged
    book["return_settled"] = True
    run.result = {**run.result, "operation_costs": book}
    _record(run, "return_march", {"grain": charged}, waived=max(0, cost - charged))


def mark_courier_delivered_locked(run: Any) -> None:
    if not run_operation_rules(run):
        return
    book = deepcopy(run.result["operation_costs"])
    fee = book.get("courier_balance", 0)
    if not fee:
        return
    book["courier_balance"] = 0
    book["courier_spent"] += fee
    run.result = {**run.result, "operation_costs": book}
    _record(run, "courier", {"grain": fee})


def finish_operation_costs_locked(run: Any, now: datetime) -> None:
    from .economy import grant_activity_resources_locked

    if not run_operation_rules(run):
        return
    book = deepcopy(run.result["operation_costs"])
    refund = (
        book.get("march_balance", 0)
        + book.get("courier_balance", 0)
        + book.get("carried_food_balance", 0)
        + book.get("convoy_battle_balance", 0)
    )
    book["march_balance"] = book["courier_balance"] = book["carried_food_balance"] = 0
    if "convoy_battle_balance" in book:
        book["convoy_battle_balance"] = 0
    run.result = {**run.result, "operation_costs": book}
    if refund:
        grant_activity_resources_locked(run.member, {"grain": refund}, now, refund=True)
        _record(run, "unused_rations_refund", {"grain": refund})


def weighted_troops(troops: dict[str, int], multiplier: int | float) -> Fraction:
    return Fraction(sum(count for key, count in troops.items() if key != "bloodthirsty_cavalry")) + Fraction(
        str(multiplier)
    ) * troops.get("bloodthirsty_cavalry", 0)


def battle_grain(run: Any, troops: dict[str, int]) -> Fraction:
    rules = run_operation_rules(run)
    if not rules:
        return Fraction(0)
    return (
        weighted_troops(troops, rules["cavalry_battle_cost_multiplier"])
        * rules["battle_normal_grain_per_100_per_wave"]
        / 100
    )


def charge_convoy_battle_locked(runs: list[Any], now: datetime) -> None:
    """单次遭遇战只扣各队出发时预留口粮，不依赖窗口末公共库存。"""
    for run in runs:
        if not run_operation_rules(run):
            continue
        cost = ceil(battle_grain(run, run.troops))
        book = deepcopy(run.result["operation_costs"])
        if cost > book.get("convoy_battle_balance", 0):
            raise GameError("车队预留战斗口粮不足，无法结算")
        book["convoy_battle_balance"] = book.get("convoy_battle_balance", 0) - cost
        run.result = {**run.result, "operation_costs": book}
        if cost:
            _record(run, "battle", {"grain": cost}, wave=run.battles + 1)
        else:
            run.save(update_fields=["result"])


def charge_attack_battle_locked(runs: list[Any], allocations: dict[int, dict[str, int]], now: datetime) -> None:
    amounts = {run.pk: battle_grain(run, allocations.get(run.pk, {})) for run in runs}
    total = ceil(sum(amounts.values()))
    if not total:
        return
    _spend_public(runs[0].member, {"grain": total}, now, reason="battle")
    shares = split_integer(total, amounts)
    for run in runs:
        if run_operation_rules(run):
            _record(run, "battle", {"grain": shares[run.pk]}, wave=run.battles + 1)


def charge_demolition_locked(
    runs: list[Any], allocations: dict[int, dict[str, int]], amount: int, now: datetime
) -> None:
    if amount <= 0:
        return
    weights = {run.pk: sum(allocations.get(run.pk, {}).values()) for run in runs}
    total_weight = sum(weights.values())
    if not total_weight:
        return
    due: dict[str, dict[int, Fraction]] = {}
    for run in runs:
        rules = run_operation_rules(run)
        if not rules:
            continue
        for resource, price in rules["siege_cost_per_100_actual_wall"].items():
            due.setdefault(resource, {})[run.pk] = Fraction(amount * weights[run.pk] * price, total_weight * 100)
    cost = {resource: ceil(sum(values.values())) for resource, values in due.items()}
    _spend_public(runs[0].member, cost, now, reason="demolition")
    by_run: dict[int, dict[str, int]] = {run.pk: {} for run in runs}
    for resource, values in due.items():
        for run_id, share in split_integer(cost[resource], values).items():
            by_run[run_id][resource] = share
    for run in runs:
        if run_operation_rules(run):
            _record(run, "demolition", by_run[run.pk], actual_wall=amount)


def settle_garrison_cost_locked(run: Any, site: Any, now: datetime, *, final: bool = False) -> bool:
    rules = run_operation_rules(run)
    if not rules:
        return True
    if run.result.get("supply_exhausted_at"):
        return False
    arrived = parse_datetime(run.result.get("stationed_at", "")) or run.launched_at
    previous = parse_datetime(run.result.get("supply_paid_at", "")) or arrived
    until = min(now, run.stationed_until or run.due_at)
    seconds = max(0, int((until - previous).total_seconds()))
    remainder = Fraction(str(run.result.get("supply_fraction", "0")))
    per_second = (
        weighted_troops(run.troops, rules["cavalry_garrison_cost_multiplier"])
        * rules["garrison_normal_grain_per_100_per_hour"]
        / 360000
    )
    numerator = remainder + per_second * seconds
    cost = ceil(numerator) if final else int(numerator)
    available = site.supplies.get("grain", 0)
    paid = min(available, cost)
    exhausted = numerator > available
    if exhausted:
        # 扫描延迟不能变成无粮驻守；贡献只计算粮草实际维持的时间。
        supported = max(0, int((available - remainder) / per_second)) if per_second else 0
        until = min(until, previous + timedelta(seconds=supported))
        run.stationed_until = until
        run.result = {**run.result, "supply_exhausted_at": until.isoformat()}
        run.save(update_fields=["stationed_until"])
    site.supplies = {**site.supplies, "grain": available - paid}
    site.save(update_fields=["supplies"])
    run.result = {
        **run.result,
        "supply_paid_at": until.isoformat(),
        "supply_fraction": "0" if final or exhausted else str(numerator - cost),
    }
    if paid or cost > paid:
        _record(run, "garrison", {"grain": paid}, seconds=seconds, unpaid=cost - paid)
    else:
        run.save(update_fields=["result"])
    return not exhausted
