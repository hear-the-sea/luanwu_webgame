"""阵营战场开放与据点规则，所有可调值来自赛季配置快照。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.utils.dateparse import parse_datetime

from core.exceptions import GameError
from gameplay.models.season import Season, SeasonSite

from .campaign_rules import campaign_rules
from .development_rules import feature_rules
from .fortifications import breach_capture, reset_defenses, tower_state
from .simplification import simplification_rules

ACTIVE_STATUSES = ("gathering", "outbound", "stationed", "transporting", "returning", "failed")
FIGHTING_MODES = ("attack", "siege", "intercept")


def campaign_open(season: Season, now: datetime) -> bool:
    rules = campaign_rules(season)
    return not season.closed and season.starts_at <= now < min(
        season.ends_at, season.starts_at + timedelta(days=rules["expeditions"]["combat_days"])
    )


def city_open(season: Season, now: datetime) -> bool:
    conf = campaign_rules(season)["expeditions"]
    return now.astimezone(ZoneInfo(conf.get("timezone", "Asia/Shanghai"))).weekday() in conf["city_weekdays"]


def campaign_nodes(season: Season) -> dict[str, dict[str, Any]]:
    from .rules import NODES

    conf = campaign_rules(season)
    nodes = {key: dict(row) for key, row in NODES.items()}
    for key, node in nodes.items():
        if key in conf["sites"]:
            node.update(
                kind="pvp",
                modes=["attack", "garrison", "siege", "repair"],
                **{field: conf["sites"][key][field] for field in ("name", "unlock_day")},
            )
            node["description"] = "派遣真实门客与阵营护院，驻防、攻坚、破城或维修，争夺公共据点。"
            if any(row["site_key"] == key for row in conf["economy"].get("special_troops", {}).values()):
                node["description"] = (
                    "争夺铁骑募训营，控制此地的阵营可持续训练特殊护院；失守后停止新增，已产出的兵力保留。"
                )
            if key == "river:docks":
                node["modes"] += ["pve", "transport", "intercept"]
            elif key == "forest:granary":
                node["modes"] += ["pve"]
            elif key == "river:market":
                node["description"] = (
                    "争夺集市控制权，占领方可兑换定期刷新的绑定货品，并享少量军需调剂优惠；普通阵营商店双方均可使用。"
                )
        elif key == "river:docks":
            node.update(
                modes=["pve", "transport", "intercept"],
                description="护航讨伐、运送公共物资，或拦截敌方争夺运输；每趟运输最多被拦截一次。",
            )
        elif key == "river:market":
            node.update(kind="shop", modes=[])
        elif key == "river:warehouse":
            node.update(kind="economy", modes=[], description="查看阵营后勤资源与建设进度，活动积累建设值后自动发展。")
        elif key == "forest:camp":
            node.update(kind="economy", modes=[], description="查看阵营兵库与出征队伍，推动训练和赛季科技。")
        elif key in conf["economy"]["pve"]:
            node["kind"] = "pve"
            node["modes"] = ["pve"]
        else:
            node.update(kind="preview", modes=[], description="本季尚未配置该建筑活动，可查看其他建筑。")
    activities = feature_rules(season, "activities")
    if activities["enabled"]:
        for key in activities["explore"]["nodes"]:
            nodes[key]["modes"] = ["explore", *nodes[key]["modes"]]
            nodes[key]["description"] = "探索获取临时共享情报与讨伐机会，也可清剿物资营地取得公共资源。"
        nodes["river:docks"]["modes"].append("npc_intercept")
        from .resource_rules import resource_rules

        nodes["river:docks"]["description"] = (
            "将公共物资运至有实际需求的己方据点，保障驻军与维修；也可拦截敌方真实运输队。"
            if resource_rules(season)["enabled"]
            else "护送真实补给至己方据点或驿站订单；可拦截已发现的敌方运输，或讨伐中立辎重队。"
        )
    if simplification_rules(season)["enabled"]:
        for node in nodes.values():
            node["modes"] = [mode for mode in node["modes"] if mode != "npc_intercept"]
        nodes["forest:granary"]["modes"] = [mode for mode in nodes["forest:granary"]["modes"] if mode != "pve"]
        nodes["forest:granary"][
            "description"
        ] = "占领后提高阵营被动粮草产量；驻防守住粮仓，运输补给与维修确保前线稳定。"
        nodes["river:docks"]["modes"] = [mode for mode in nodes["river:docks"]["modes"] if mode != "transport"]
        nodes["river:warehouse"].update(
            kind="logistics",
            modes=["transport"],
            description="阵营补给总管：查看公共资源与兵库，按各城真实缺口派出补给队。",
        )
        nodes["river:market"]["description"] = (
            "占领集市可兑换定期刷新的绑定货品，并享少量军需调剂优惠；普通阵营商店与基础调剂双方均可使用。"
            if "river:market" in conf["sites"]
            else "用个人军资兑换绑定物品；公共资源有富余和短缺时，可在军需调剂中限额换取急需物资。"
        )
        nodes["river:docks"][
            "description"
        ] = "占领渡口提高运输载重；讨伐水匪补充混合物资，或拦截敌方真实补给队。己方运输统一在港口仓库派遣。"
        for key in conf["sites"]:
            nodes[key]["modes"] = [mode for mode in nodes[key]["modes"] if mode != "attack"]
            nodes[key]["description"] = nodes[key]["description"].replace(
                "驻防、攻坚、破城或维修", "集结攻城、驻防或维修"
            )
    if activities["enabled"] and conf["expeditions"].get("convoy_encounters_enabled", False):
        nodes["river:docks"]["modes"].append("escort")
        nodes["river:docks"][
            "description"
        ] = "占领渡口提高运输载重；收集水匪物资，或参加运输队的多人拦截与护送。己方补给在港口仓库派遣。"
    if not conf.get("day_gates_enabled", True):
        for node in nodes.values():
            node["unlock_day"] = 1
    from .occupation import occupation_rules

    occupation = occupation_rules(season)
    if occupation["enabled"] and occupation["automatic_repair"]:
        for node in nodes.values():
            node["modes"] = [mode for mode in node["modes"] if mode != "repair"]
    return nodes


def validate_destination(
    season: Season,
    node_key: str,
    mode: str,
    now: datetime,
    *,
    legacy_modes: bool = False,
    city_arrival_at: datetime | None = None,
) -> dict[str, Any]:
    node = campaign_nodes(season).get(node_key)
    legacy_allowed = (
        legacy_modes
        and simplification_rules(season)["enabled"]
        and (
            (mode == "attack" and node_key in campaign_rules(season)["sites"])
            or (mode == "npc_intercept" and node_key == "river:docks")
            or (mode == "pve" and node_key == "forest:granary")
            or (mode == "transport" and node_key == "river:docks")
        )
    )
    from .occupation import occupation_rules

    legacy_allowed = legacy_allowed or bool(
        legacy_modes
        and mode == "repair"
        and occupation_rules(season)["enabled"]
        and node_key in campaign_rules(season)["sites"]
    )
    if not node or (mode not in node["modes"] and not legacy_allowed):
        raise GameError("该建筑不支持所选行动")
    if not campaign_open(season, now):
        raise GameError("当前不在赛季行动期")
    day = int((now - season.starts_at).total_seconds() // 86400) + 1
    if day < node["unlock_day"]:
        raise GameError(f"该建筑在第 {node['unlock_day']} 天开放")
    if node_key == "river:town" and not city_open(season, city_arrival_at or now):
        conf = campaign_rules(season)["expeditions"]
        weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        days = "、".join(weekdays[index] for index in conf["city_weekdays"])
        clock = conf.get("timezone", "Asia/Shanghai")
        clock_label = "北京时间" if clock == "Asia/Shanghai" else clock
        raise GameError(f"古城仅在{days}全天开放（{clock_label}）")
    return node


def ensure_sites_locked(season: Season, now: datetime) -> dict[str, SeasonSite]:
    from .population import npc_profile

    result = {}
    for key, conf in campaign_rules(season)["sites"].items():
        profile = npc_profile(season, key)
        site, _ = SeasonSite.objects.get_or_create(
            season=season,
            key=key,
            defaults={
                "guards": profile["guards"],
                "durability": profile["durability"],
                "npc_profile": profile,
                "last_score_at": season.starts_at,
            },
        )
        result[key] = site
    return result


def check_ownership(site: SeasonSite, faction: str, mode: str, now: datetime) -> None:
    if mode in {"garrison", "repair"}:
        if site.owner != faction:
            raise GameError("只能驻防或维修本阵营控制的据点")
    elif mode in {"attack", "siege"}:
        if site.owner == faction:
            raise GameError("不能攻击本阵营据点")
        if site.protected_until and now < site.protected_until:
            raise GameError("据点处于占领休整期")
        if mode == "siege" and site.guards and not simplification_rules(site.season)["enabled"]:
            raise GameError("请先击退据点守军，再参与破城")


def capture_locked(site: SeasonSite, faction: str, now: datetime) -> bool:
    campaign = campaign_rules(site.season)
    conf = campaign["sites"][site.key]
    if breach_capture(site.season):
        from .hitpoints import hitpoint_rules

        physical_hp = hitpoint_rules(site.season)["enabled"]
        if site.durability > 0 or (not physical_hp and (site.guards > 0 or tower_state(site)["hp"] > 0)):
            return False
    elif site.control < conf["control_required"]:
        return False
    from .resource_rules import resource_rules

    economic = resource_rules(site.season)["enabled"]
    if economic or any(row["site_key"] == site.key for row in campaign["economy"].get("special_troops", {}).values()):
        from .economy import produce_locked

        # 使用旧归属补齐已到期生产；新主从本次占领后的完整训练周期开始计算。
        produce_locked(site.season, now)
    site.owner = faction
    site.front_line = {}
    site.front_allocations = {}
    site.reinforcement_at = None
    site.attacker = ""
    site.control = 0
    from .population import npc_profile

    profile = npc_profile(site.season, site.key)
    site.guards = profile["guards"]
    site.durability = profile["durability"]
    site.npc_profile = profile
    reset_defenses(site)
    site.capture_cycle += 1
    activity = feature_rules(site.season, "activities")
    if not economic:
        site.supplies = {"grain": activity["transport"]["capture_grain"]} if activity["enabled"] else {}
    site.supply_updated_at = now
    site.protected_until = now + timedelta(seconds=conf["protection_seconds"])
    site.last_score_at = now
    from .occupation import award_occupation_score_locked, initialize_occupation, occupation_rules

    if occupation_rules(site.season)["enabled"]:
        initialize_occupation(site, now)
        award_occupation_score_locked(site, faction, now)
    return True


def _score_tick_groups(
    first: datetime, ticks: int, interval: int, site: dict, expeditions: dict
) -> list[tuple[datetime, int, int]]:
    """将计分时刻按本地日期及规则生效时刻分段；结算边界归前一时段。"""
    normal = site["score_per_tick"]
    weekday = site.get("weekday_score_per_tick")
    if weekday is None:
        return [(first, ticks, normal)]
    effective = parse_datetime(site.get("weekday_score_effective_at", ""))
    zone = ZoneInfo(expeditions.get("timezone", "Asia/Shanghai"))
    open_days = set(expeditions["city_weekdays"])
    remaining = ticks
    cursor = first
    groups = []
    while remaining:
        day = (cursor - timedelta(microseconds=1)).astimezone(zone).date()
        boundary = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        if effective is not None and cursor <= effective:
            boundary = min(boundary, effective)
            points = normal
        else:
            points = normal if day.weekday() in open_days else weekday
        count = min(remaining, max(1, int((boundary - cursor).total_seconds()) // interval + 1))
        groups.append((cursor, count, points))
        remaining -= count
        cursor += timedelta(seconds=count * interval)
    return groups


def score_sites_locked(season: Season, now: datetime) -> None:
    from .economy import ensure_factions_locked

    conf = campaign_rules(season)
    interval = conf["expeditions"].get("score_interval_seconds", 3600)
    cutoff = min(now, season.ends_at, season.starts_at + timedelta(days=conf["expeditions"]["combat_days"]))
    factions = ensure_factions_locked(season, now)
    from .playability_rules import playability_rules
    from .weekly_campaign import activate_weekly_locked, record_site_ticks_locked

    weekly = playability_rules(season)["enabled"]
    activated = activate_weekly_locked(season, factions, now) if weekly else None
    from .occupation import occupation_rules

    occupation = occupation_rules(season)["enabled"]
    for site in ensure_sites_locked(season, now).values():
        if occupation and site.owner and site.protected_until:
            if site.last_score_at < site.protected_until:
                site.last_score_at = site.protected_until
                site.save(update_fields=["last_score_at"])
        ticks = max(0, int((cutoff - site.last_score_at).total_seconds()) // interval)
        if not ticks:
            continue
        # 古城占领积分也可在工作日保留，只有交战限于周末。
        if site.owner:
            state = factions[site.owner]
            from .population import population_profile

            profile = population_profile(season)
            multiplier = profile.get("hotspot_multiplier", 1) if profile.get("hotspot") == site.key else 1
            first_tick = site.last_score_at + timedelta(seconds=interval)
            groups = _score_tick_groups(first_tick, ticks, interval, conf["sites"][site.key], conf["expeditions"])
            for first, count, points in groups:
                points *= multiplier
                if activated is not None:
                    record_site_ticks_locked(season, state, first, count, interval, points, activated)
                state.score += count * points
            state.save(update_fields=["score"])
        site.last_score_at += timedelta(seconds=ticks * interval)
        site.save(update_fields=["last_score_at"])
    season.fiction_score = factions["fiction"].score
    season.history_score = factions["history"].score
    season.save(update_fields=["fiction_score", "history_score"])
