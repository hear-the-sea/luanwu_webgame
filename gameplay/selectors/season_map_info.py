"""地图只展示当前最值得关注的一条状态；复用既有公开查询结果。"""

from typing import Any


def decorate_map_nodes(
    maps: list[dict[str, Any]],
    guidance: dict[str, Any],
    demands: list[dict[str, Any]],
    own_runs: list[Any],
    faction: str,
) -> None:
    shortages = {row["key"]: row.get("demand", {}) for row in demands}
    main_modal = guidance.get("main", {}).get("modal", "")
    functions = {
        "mountain:inn": "粮草收集",
        "mountain:ruins": "铁料收集",
        "forest:lodge": "木材收集",
        "mountain:gate": "关隘行军优势",
        "forest:camp": "嗜血铁骑产地",
        "forest:granary": "粮产加成",
        "river:docks": "水运与拦截",
        "river:market": "占领享货摊",
        "river:warehouse": "运输补给与兵库",
        "river:town": "重要战果据点",
    }
    for region in maps:
        region["joinable_rally_count"] = sum(
            bool(rally.get("can_join")) for node in region["nodes"] for rally in node.get("rallies", [])
        )
        for node in region["nodes"]:
            key = node["key"]
            node.get("personal_modes", []).sort(key=lambda row: row["key"] != "pve")
            if node.get("kind") == "logistics":
                panes = [("orders", "运输补给"), ("stock", "兵库"), ("development", "发展")]
            elif key == "river:market" and node.get("site"):
                panes = [("trade", "占领货摊"), ("war", "攻防"), ("exchange", "调剂")]
                if not faction or node.get("occupation_class") != faction:
                    panes = [("war", "攻防")]
            else:
                panes = []
                if node.get("personal_modes"):
                    panes.append(("personal", "派遣"))
                if node.get("rally_modes") or node.get("rallies"):
                    rally = ("rally", "集结")
                    if node.get("site") and node.get("occupation_class") != faction:
                        panes.insert(0, rally)
                    else:
                        panes.append(rally)
                if node.get("site"):
                    panes.append(("details", "守备"))
                elif not panes:
                    panes.append(("details", "详情"))
            if node.get("rallies") and not any(pane[0] in {"rally", "war"} for pane in panes):
                panes.append(("rally", "集结"))
            node["popup_panes"] = [{"key": key, "name": name} for key, name in panes]
            node["popup_purpose"] = functions.get(key, "阵营活动")
            node["map_hint"], node["map_hint_kind"] = functions.get(key, ""), "function"
            joinable = next((row for row in node.get("rallies", []) if row["can_join"]), None)
            needs = shortages.get(key, {})
            mine = [run for run in own_runs if run.node_key == key]
            site = node.get("site") or {}
            if node.get("schedule_closed"):
                node["map_hint"], node["map_hint_kind"] = "周末开战", "muted"
            elif joinable:
                node["map_hint"] = f"集结 {joinable['member_count']}/{joinable['target_members']} · 可加入"
                node["map_hint_kind"] = "rally"
            elif any(needs.values()):
                node["map_hint"] = "缺粮待援" if needs.get("grain") else "缺料待援"
                node["map_hint_kind"] = "supply"
            elif mine:
                node["map_hint"] = f"我的队伍 {len(mine)} 支"
                node["map_hint_kind"] = "own"
            elif site.get("protected_until"):
                node["map_hint"], node["map_hint_kind"] = "占领休整中", "muted"
            elif main_modal == f"season-node-{key}":
                node["map_hint"], node["map_hint_kind"] = "建议主攻", "target"
            elif (node.get("garrison") or {}).get("member_count") and node.get("occupation_class") == faction:
                node["map_hint"] = f"己方驻军 {node['garrison']['member_count']} 人"
                node["map_hint_kind"] = "own"
