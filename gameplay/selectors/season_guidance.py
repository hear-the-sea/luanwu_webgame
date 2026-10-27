"""小队赛季的只读行动指引；复用已公开战况，不模拟战斗胜率。"""

from typing import Any


def campaign_guidance(
    nodes: list[dict[str, Any]],
    rallies: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    demands: list[dict[str, Any]],
    faction: str,
    hotspot: str,
    combat_open: bool,
) -> dict[str, Any]:
    if not faction or not combat_open:
        return {}
    by_key = {node["key"]: node for node in nodes}
    joining = next((row for row in rallies if row["can_join"]), None)
    candidates = [
        node
        for node in nodes
        if node.get("rally_modes")
        and node["unlocked"]
        and not node.get("schedule_closed")
        and not (node.get("site") or {}).get("protected_until")
    ]

    def priority(node: dict[str, Any]) -> tuple[int, str]:
        key = node["key"]
        rank = 0 if key == hotspot else 1 if key == "river:town" else 2 if key == "forest:camp" else 3
        return rank, key

    candidates.sort(key=priority)
    target = candidates[0] if candidates else None
    main: dict[str, Any] = {}
    if joining:
        main = {
            "title": f"支援集结 · {joining['node_name']}",
            "detail": "加入队友的现有集结，集中兵力推进。",
            "modal": f"season-rally-{joining['id']}",
            "label": "加入集结",
        }
    elif target:
        benefits = {
            "forest:camp": "占领可解锁嗜血铁骑生产。",
            "river:town": "周末古城正在开放，可争取重要战果。",
            "river:market": "占领可兑换轮换绑定货品，并享军需调剂优惠。",
            "mountain:gate": "占领方可取得关隘战略优势，支援后续行军。",
        }
        main = {
            "title": f"建议主攻 · {target['name']}",
            "detail": benefits.get(target["key"], "集结接力击退守军、摧毁城防并占领据点。"),
            "modal": f"season-node-{target['key']}",
            "label": "查看目标",
        }
    else:
        main = {
            "title": "巩固现有战线",
            "detail": "当前没有可发起的新攻城目标，先查看军务或补充战备。",
            "modal": "season-team-status",
            "label": "查看队伍",
        }
    stocks = {row["key"]: row["amount"] for row in resources}
    shipment = next(
        (
            row
            for row in demands
            if any(min(amount, stocks.get(key, 0)) > 0 for key, amount in row.get("demand", {}).items())
        ),
        None,
    )
    warehouse = by_key.get("river:warehouse", {})
    transport_open = any(
        row["key"] == "transport" and not row["disabled"]
        for row in warehouse.get("personal_modes", warehouse.get("modes", []))
    )
    if demands and warehouse.get("transport_route_blocked"):
        support = {
            "title": "补给受阻 · 渡口被敌方控制",
            "detail": "前线有物资缺口，需先夺回渡口恢复仓库运输通路。",
            "modal": "season-node-river:docks",
            "label": "查看渡口",
        }
    elif shipment and transport_open:
        support = {
            "title": f"补给缺口 · {shipment['name']}",
            "detail": f"尚需{shipment['demand_label']}，已扣除库存和在途补给。",
            "modal": "season-node-river:warehouse",
            "label": "前往运输",
        }
    else:
        needed = {key: sum(row.get("demand", {}).get(key, 0) for row in demands) for key in stocks}
        scarce = min(
            resources,
            key=lambda row: (row["amount"] - needed.get(row["key"], 0))
            / max(1, row.get("daily_rate", row.get("rate", 0))),
            default=None,
        )
        destination = {"grain": "mountain:inn", "wood": "forest:lodge", "iron": "mountain:ruins"}
        node = by_key.get(destination.get(scarce["key"], "")) if scarce else None
        modes = node.get("personal_modes", node.get("modes")) if node else []
        collect_open = modes is None or any(row["key"] == "pve" and not row.get("disabled") for row in modes)
        if (
            scarce
            and node
            and node["unlocked"]
            and collect_open
            and node.get("pve_material_available", True)
            and not node.get("pve_storage_full")
        ):
            support = {
                "title": f"战备建议 · 补充{scarce['name']}",
                "detail": "按公共库存、产量与前线缺口推荐；讨伐所得进入阵营仓库。",
                "modal": f"season-node-{node['key']}",
                "label": "查看讨伐",
            }
        else:
            support = {}
    return {"main": main, "support": support}


def reward_progress(rewards: list[dict[str, Any]], merit: int) -> dict[str, Any]:
    next_reward = next((row for row in sorted(rewards, key=lambda row: row["threshold"]) if not row["claimed"]), None)
    if not next_reward:
        return {}
    threshold = next_reward["threshold"]
    return {
        "name": next_reward["name"],
        "threshold": threshold,
        "value": min(merit, threshold),
        "remaining": max(0, threshold - merit),
        "available": next_reward["available"],
    }
