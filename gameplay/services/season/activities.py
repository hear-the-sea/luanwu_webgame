"""探索、真实补给和劫运任务；奖励贡献由统一账本结算。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import SeasonExpedition, SeasonIntel, SeasonSite

from .campaign_rules import campaign_rules
from .development_rules import feature_rules
from .economy import grant_activity_resources_locked, produce_locked
from .resource_rules import resource_rules


def _intel_query(member: Any, now: datetime) -> Any:
    query = SeasonIntel.objects.filter(season=member.season, expires_at__gt=now)
    return query.filter(faction=member.faction) if member.faction else query.filter(member=member, faction="")


def _used_orders(member: Any, mode: str, now: datetime, *, npc_only: bool = False) -> int:
    query = member.expeditions.filter(kind=mode, launched_at__date=timezone.localdate(now))
    if npc_only:
        query = query.filter(result__activity__npc_order=True)
    return sum(not run.result.get("quota_refunded") for run in query.only("result"))


def hostile_transports(member: Any, node_key: str, now: datetime) -> Any:
    route_nodes = ("river:docks", "river:warehouse") if node_key in {"river:docks", "river:warehouse"} else (node_key,)
    return (
        SeasonExpedition.objects.filter(
            member__season=member.season,
            node_key__in=route_nodes,
            kind="transport",
            status="transporting",
            intercepted=False,
            due_at__gt=now,
        )
        .exclude(member__faction__in=("", member.faction))
        .order_by("due_at", "pk")
    )


def prepare_activity_locked(
    member: Any, node_key: str, mode: str, payload: dict[str, Any], now: datetime
) -> dict[str, Any]:
    config = feature_rules(member.season, "activities")
    activity: dict[str, Any] = {"type": mode}
    cargo: dict[str, int] = {}
    if mode == "explore":
        if node_key not in config["explore"]["nodes"]:
            raise GameError("该建筑没有探索区域")
        scout_site = str(
            payload.get("scout_site_key") or ("mountain:gate" if node_key.startswith("mountain:") else "forest:camp")
        )
        if scout_site not in campaign_rules(member.season)["sites"]:
            raise GameError("请选择有效的侦察据点")
        activity["scout_site_key"] = scout_site
        if _intel_query(member, now).filter(payload__target_site_key=scout_site).exists():
            raise GameError("本区域已有有效情报，请查看情报或参与讨伐")
        used = member.intel_records.filter(created_at__date=timezone.localdate(now)).count()
        reserved_discoveries = member.expeditions.filter(
            kind="explore", status__in=("outbound", "gathering"), launched_at__date=timezone.localdate(now)
        ).count()
        if used + reserved_discoveries >= config["explore"]["daily_limit"]:
            raise GameError("今日有效探索次数已用完")
    elif mode == "transport":
        if not member.faction:
            raise GameError("分配阵营后才能运输公共物资")
        if resource_rules(member.season)["enabled"]:
            return _prepare_demand_transport(member, node_key, payload, now)
        gateway = SeasonSite.objects.filter(season=member.season, key=node_key).first()
        if gateway and payload.get("target_site_key") and gateway.owner not in {"", member.faction}:
            raise GameError("渡口被敌方控制，请先夺回通路")
        target_key = str(payload.get("target_site_key", ""))
        if target_key:
            target = SeasonSite.objects.filter(season=member.season, key=target_key, owner=member.faction).first()
            if not target:
                raise GameError("运输目标必须是己方控制的据点")
            if all(
                target.supplies.get(key, 0) >= config["transport"]["supply_capacity"]
                for key in ("grain", "wood", "iron")
            ):
                raise GameError("目标据点补给已满，请选择其他订单")
        elif _used_orders(member, "transport", now, npc_only=True) >= config["transport"]["daily_npc_limit"]:
            raise GameError("今日驿站补给订单已用完")
        activity.update(target_site_key=target_key, npc_order=not bool(target_key))
        cargo = dict(
            campaign_rules(member.season)["expeditions"]["cargo"]
            if target_key
            else config["transport"]["npc_resources"]
        )
        if target_key:
            assert target is not None
            reserved: dict[str, int] = {}
            for convoy in SeasonExpedition.objects.filter(
                member__season=member.season,
                kind="transport",
                status__in=("outbound", "transporting"),
                result__activity__target_site_key=target_key,
            ):
                for resource, count in convoy.cargo.items():
                    reserved[resource] = reserved.get(resource, 0) + count
            cargo = {
                resource: min(
                    amount,
                    max(
                        0,
                        config["transport"]["supply_capacity"]
                        - target.supplies.get(resource, 0)
                        - reserved.get(resource, 0),
                    ),
                )
                for resource, amount in cargo.items()
            }
            if not any(cargo.values()):
                raise GameError("目标需求已由在途补给覆盖，请选择其他订单")
        state = produce_locked(member.season, now)[member.faction]
        if any(state.resources.get(key, 0) < amount for key, amount in cargo.items()):
            raise GameError("公共仓库不足以装载本次补给，可先参与资源收集")
        if not target_key:
            initial = campaign_rules(member.season)["economy"]["initial_resources"]
            if any(
                state.resources.get(key, 0) - amount < initial[key] * config["transport"]["npc_reserve_percent"] // 100
                for key, amount in cargo.items()
            ):
                raise GameError("公共仓库已到战备保底，暂停消耗型驿站订单；可先讨伐补给或运输己方据点")
        state.resources = {key: value - cargo.get(key, 0) for key, value in state.resources.items()}
        state.save(update_fields=["resources"])
    elif mode == "intercept":
        target = hostile_transports(member, node_key, now).filter(pk=payload.get("target_run_id", 0)).first()
        if target is None:
            raise GameError("没有可拦截的敌方运输队，请选择现有目标或参加NPC劫运")
        from .convoy_encounters import is_encounter, validate_join

        if is_encounter(target):
            validate_join(member, target, mode)
            activity["encounter_version"] = 1
        activity["target_run_id"] = target.pk
    elif mode == "escort":
        from .convoy_encounters import validate_join

        target_run_id = payload.get("target_run_id")
        if target_run_id is None:
            raise GameError("请选择仍在运输的本阵营车队")
        target_run = (
            SeasonExpedition.objects.filter(pk=target_run_id, member__season=member.season, due_at__gt=now)
            .select_related("member")
            .first()
        )
        if target_run is None:
            raise GameError("请选择仍在运输的本阵营车队")
        validate_join(member, target_run, mode)
        activity["encounter_version"] = 1
        activity["target_run_id"] = target_run.pk
    elif mode == "npc_intercept":
        if _used_orders(member, mode, now) >= config["intercept"]["daily_npc_limit"]:
            raise GameError("今日NPC劫运次数已用完")
        activity["npc_order"] = True
    return {"cargo": cargo, "result": {"activity": activity}}


def settle_exploration_locked(run: Any, now: datetime) -> dict[str, Any]:
    config = feature_rules(run.member.season, "activities")["explore"]
    target_key = run.result.get("activity", {}).get("scout_site_key") or (
        "mountain:gate" if run.node_key.startswith("mountain:") else "forest:camp"
    )
    if _intel_query(run.member, now).filter(payload__target_site_key=target_key).exists():
        # 友军已完成相同情报时，本队没有执行新的有效探索。
        # 保持实际行军与返程，归队后由现有幂等账本退回出征和领兵额度。
        refundable = not bool(run.report_id)
        if refundable:
            run.result = {**run.result, "refund_quota": True}
            from gameplay.services.utils.messages import create_message

            _, created = run.member.claims.get_or_create(key=f"exploration_duplicate_mail:{run.pk}")
            if created:
                target_name = campaign_rules(run.member.season)["sites"].get(target_key, {}).get("name", "目标据点")
                create_message(
                    manor=run.member.manor,
                    kind="system",
                    title="阵营任务-探索结果",
                    body=(
                        f"{target_name}已由友军完成探索，情报已共享，本次不重复产出情报或奖励。"
                        "队伍正在返程，实际行军消耗保留；归队后退回本次出征和领兵额度。"
                    ),
                )
        return {
            "merit": 0,
            "construction": 0,
            "resources": {},
            "message": (
                "友军已完成本区域探索，情报已共享；未产生新情报，归队后退回出征及领兵额度"
                if refundable
                else "友军已完成本区域探索，情报已共享，本次不重复领取奖励"
            ),
        }
    target = SeasonSite.objects.filter(season=run.member.season, key=target_key).first()
    garrisons = SeasonExpedition.objects.filter(
        member__season=run.member.season, node_key=target_key, kind="garrison", status="stationed", due_at__gt=now
    )
    payload = {
        "target_site_key": target_key,
        "target_site_name": campaign_rules(run.member.season)["sites"].get(target_key, {}).get("name", "目标据点"),
        "owner": target.owner if target else "",
        "guards": target.guards if target else 0,
        "durability": target.durability if target else 0,
        "garrisons": [
            {
                "name": g.member.manor.display_name,
                "troops": dict(g.troops),
                "guests": [s["display_name"] for s in g.lineup.get("guests", [])],
            }
            for g in garrisons
        ],
        "bonus_available": True,
        "bonus_percent": config["bonus_percent"],
        "discovery": "NPC物资营地" if not target or not target.owner else "驻防情报",
    }
    intel = SeasonIntel.objects.create(
        season=run.member.season,
        member=run.member,
        faction=run.member.faction,
        node_key=run.node_key,
        kind="exploration",
        payload=payload,
        expires_at=now + timedelta(seconds=config["intel_seconds"]),
    )
    resources = (
        resource_rules(run.member.season)["pve"]["exploration_resources"]
        if resource_rules(run.member.season)["enabled"]
        else config["resources"]
    )
    gains = grant_activity_resources_locked(run.member, resources, now)
    return {
        "merit": config["merit"],
        "construction": config["construction"],
        "resources": gains,
        "intel_id": intel.pk,
        "message": f"探索完成，情报共享{config['intel_seconds']}秒；下一次本区域讨伐可获得额外物资",
    }


def settle_transport_locked(run: Any, now: datetime) -> dict[str, Any]:
    from .recovery import settle_auto_repairs_locked

    settle_auto_repairs_locked(run.member.season, now)
    config = feature_rules(run.member.season, "activities")["transport"]
    activity = run.result.get("activity", {})
    target_key = activity.get("target_site_key", "")
    delivered = dict(run.cargo)
    if run.result.get("activity", {}).get("resource_version") == 3:
        return _settle_demand_transport(run, now)
    if target_key:
        target = SeasonSite.objects.filter(season=run.member.season, key=target_key, owner=run.member.faction).first()
        if target is None:
            raise GameError("目标据点已易主，未交付物资原额退回")
        accepted = {
            key: min(max(0, config["supply_capacity"] - target.supplies.get(key, 0)), amount)
            for key, amount in delivered.items()
        }
        target.supplies = {key: target.supplies.get(key, 0) + accepted.get(key, 0) for key in ("grain", "wood", "iron")}
        target.save(update_fields=["supplies"])
        surplus = {key: amount - accepted.get(key, 0) for key, amount in delivered.items()}
        if any(surplus.values()):
            grant_activity_resources_locked(run.member, surplus, now, refund=True)
        delivered = accepted
    # NPC订单确实消耗已装载物资；不会倍增回到仓库。
    standard = sum(
        (campaign_rules(run.member.season)["expeditions"]["cargo"] if target_key else config["npc_resources"]).values()
    )
    fraction = min(1.0, sum(delivered.values()) / max(1, standard))
    run.cargo = {}
    run.result = {**run.result, "delivered_resources": delivered}
    run.save(update_fields=["cargo", "result"])
    return {
        "merit": int(config["merit"] * fraction * (config["frontline_merit_percent"] / 100 if target_key else 1)),
        "construction": int(config["construction"] * fraction),
        "resources": delivered,
        "message": "补给已实际送达"
        + ("己方据点，进入当地战备库存" if target_key else "驿站订单，物资已消耗用于修复与补给"),
    }


def find_intercept_target_locked(run: Any, now: datetime) -> Any:
    target_id = run.result.get("activity", {}).get("target_run_id")
    return hostile_transports(run.member, run.node_key, now).filter(pk=target_id).first() if target_id else None


def transfer_intercept_locked(attacker: Any, target: Any, won: bool, now: datetime) -> dict[str, Any]:
    if target.intercepted:
        return {"merit": 0, "construction": 0, "loot": {}, "message": "该运输队已被拦截，不重复结算"}
    config = feature_rules(attacker.member.season, "activities")["intercept"]
    target.intercepted = True
    loot = {key: amount * config["loot_percent"] // 100 for key, amount in target.cargo.items()} if won else {}
    target.cargo = {key: amount - loot.get(key, 0) for key, amount in target.cargo.items()}
    target.result = {**target.result, "last_intercept_run_id": attacker.pk}
    target.save(update_fields=["intercepted", "cargo", "result"])
    if loot:
        grant_activity_resources_locked(attacker.member, loot, now, refund=True)
    baseline = (
        sum(campaign_rules(attacker.member.season)["expeditions"]["cargo"].values()) * config["loot_percent"] / 100
    )
    merit = min(config["merit"], int(config["merit"] * sum(loot.values()) / max(1, baseline)))
    return {
        "merit": merit,
        "construction": merit,
        "loot": loot,
        "message": "成功夺取部分货物，其余仍随原运输队保留" if won else "拦截未获胜，未掠夺物资",
    }


def settle_npc_intercept_locked(run: Any, won: bool, now: datetime) -> dict[str, Any]:
    config = feature_rules(run.member.season, "activities")["intercept"]
    if run.result.get("material_reward"):
        from .material_rewards import material_resources_from_snapshot

        amounts = material_resources_from_snapshot(run.result["material_reward"], "npc_intercept", won)
        gains = grant_activity_resources_locked(run.member, amounts, now)
    else:
        gains = grant_activity_resources_locked(run.member, config["npc_resources"], now) if won else {}
    return {
        "merit": config["npc_merit"] if won else config["npc_failure_merit"],
        "construction": config["npc_construction"] if won else config["npc_failure_construction"],
        "resources": gains,
        "message": "NPC辎重讨伐完成，不计玩家对抗战果",
    }


def delivery_reward_policy(season: Any, capacity: int) -> dict[str, int]:
    """同一份规则供派遣冻结与只读预估使用。"""
    rules = campaign_rules(season)["expeditions"]
    activity = feature_rules(season, "activities")["transport"]
    transport = resource_rules(season)["transport"]
    policy = {
        "base_merit": rules.get("transport_reward_base", 0),
        "base_value": rules.get("transport_reward_threshold", 200),
        "full_value": rules.get("transport_reward_full_value", capacity),
        "maximum_merit": activity["merit"] * activity["frontline_merit_percent"] // 100,
    }
    if "participation_full_value" in transport and "participation_merit" in transport:
        policy.update(
            participation_full_value=transport["participation_full_value"],
            participation_merit=transport["participation_merit"],
        )
    return policy


def estimate_delivery_merit(season: Any, cargo: dict[str, int], capacity: int) -> int:
    """仅按预计可交付物资估算；抵达时仍按实际需求与库存重算。"""
    from .transport_rewards import delivery_merit

    weights = resource_rules(season)["transport"]["resource_value_weights"]
    value = sum(max(0, int(amount)) * weights.get(key, 0) for key, amount in cargo.items())
    policy = delivery_reward_policy(season, capacity)
    return delivery_merit(
        value,
        policy["full_value"],
        policy["maximum_merit"],
        policy["base_merit"],
        policy["base_value"],
        participation_full_value=policy.get("participation_full_value", 0),
        participation_merit=policy.get("participation_merit", 0),
    )


def _prepare_demand_transport(member: Any, node_key: str, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    from .logistics import allocate_cargo, convoy_capacity, site_demand
    from .resource_ledger import record_flow

    season = member.season
    from .recovery import settle_auto_repairs_locked

    settle_auto_repairs_locked(season, now)
    target_key = str(payload.get("target_site_key", ""))
    if not target_key:
        raise GameError("已取消消耗型NPC订单，请选择有真实补给需求的己方据点")
    target = SeasonSite.objects.filter(season=season, key=target_key, owner=member.faction).first()
    if target is None:
        raise GameError("运输目标必须是己方控制的据点")
    gateway = SeasonSite.objects.filter(
        season=season, key="river:docks" if node_key == "river:warehouse" else node_key
    ).first()
    if gateway and gateway.owner not in {"", member.faction}:
        raise GameError("渡口被敌方控制，请先夺回通路")
    demand = site_demand(season, target, now)
    if not any(demand.values()):
        raise GameError("目标没有未满足的补给需求，或需求已由在途运输覆盖")
    state = produce_locked(season, now)[member.faction]
    available = {key: min(amount, state.resources.get(key, 0)) for key, amount in demand.items()}
    capacity = convoy_capacity(season, member.faction)
    cargo = allocate_cargo(available, capacity)
    if not any(cargo.values()):
        raise GameError("公共仓库没有所需物资，可先参与资源收集")
    config = resource_rules(season)
    weights = config["transport"].get("resource_value_weights", {"grain": 1, "wood": 2, "iron": 3})
    state.resources = {key: amount - cargo.get(key, 0) for key, amount in state.resources.items()}
    record_flow(state, "transport_out", {key: -value for key, value in cargo.items()}, now)
    state.save(update_fields=["resources", "economy_state"])
    return {
        "cargo": cargo,
        "result": {
            "activity": {
                "type": "transport",
                "resource_version": 3,
                "target_site_key": target_key,
                "npc_order": False,
                "capacity": capacity,
                "value_weights": weights,
                "encounter_version": (
                    1 if campaign_rules(season)["expeditions"].get("convoy_encounters_enabled", False) else 0
                ),
                "encounter_players": campaign_rules(season)["expeditions"].get("max_convoy_players", 5),
                "delivery_reward": delivery_reward_policy(season, capacity),
            }
        },
    }


def _settle_demand_transport(run: Any, now: datetime) -> dict[str, Any]:
    from .logistics import site_demand
    from .operation_costs import mark_courier_delivered_locked

    activity = run.result["activity"]
    target = SeasonSite.objects.filter(
        season=run.member.season, key=activity["target_site_key"], owner=run.member.faction
    ).first()
    if target is None:
        raise GameError("目标据点已易主，未交付物资原额退回")
    # 单支运输直接结算也先清算旧口粮，避免新到粮草追补已发生的断粮时段。
    from .expeditions import _start_return
    from .operation_costs import settle_garrison_cost_locked

    for guard in (
        SeasonExpedition.objects.select_for_update()
        .filter(
            member__season=run.member.season,
            node_key=target.key,
            member__faction=target.owner,
            kind="garrison",
            status="stationed",
        )
        .order_by("launched_at", "pk")
    ):
        if not settle_garrison_cost_locked(guard, target, now):
            _start_return(guard, now, "城内粮草不足，驻军安全返程；请先运输补给后再驻防")
    needed = site_demand(run.member.season, target, now, exclude_run_id=run.pk)
    accepted = {key: min(amount, needed.get(key, 0)) for key, amount in run.cargo.items()}
    surplus = {key: amount - accepted[key] for key, amount in run.cargo.items()}
    if any(surplus.values()):
        grant_activity_resources_locked(run.member, surplus, now, refund=True)
    target.supplies = {
        **target.supplies,
        **{key: target.supplies.get(key, 0) + amount for key, amount in accepted.items()},
    }
    target.save(update_fields=["supplies"])
    run.cargo = {}
    run.result = {**run.result, "delivered_resources": accepted}
    if any(accepted.values()):
        mark_courier_delivered_locked(run)
    else:
        run.result = {**run.result, "refund_quota": not bool(run.report_id)}
    run.save(update_fields=["cargo", "result"])
    config = feature_rules(run.member.season, "activities")["transport"]
    weights = activity["value_weights"]
    value = sum(amount * weights[key] for key, amount in accepted.items())
    fraction = min(1, value / max(1, activity["capacity"]))
    merit = int(config["merit"] * config["frontline_merit_percent"] / 100 * fraction)
    if "delivery_reward" in activity:
        from .transport_rewards import delivery_merit

        policy = activity["delivery_reward"]
        merit = delivery_merit(
            value,
            policy.get("full_value", activity["capacity"]),
            policy["maximum_merit"],
            policy["base_merit"],
            policy["base_value"],
            participation_full_value=policy.get("participation_full_value", 0),
            participation_merit=policy.get("participation_merit", 0),
        )
        fraction = min(1, value / max(1, policy.get("full_value", activity["capacity"])))
    return {
        "merit": merit,
        "construction": int(config["construction"] * fraction),
        "resources": accepted,
        "message": (
            "按真实需求交付前线补给；多余货物原额退回" if value else "据点需求已消失，未交付物资原额退回，不发贡献"
        ),
    }
