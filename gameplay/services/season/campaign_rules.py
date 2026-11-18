"""阵营战配置读取与严格验证；活动中使用赛季规则快照。"""

from __future__ import annotations

import copy
import logging
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from battle.troops import load_troop_templates_from_yaml
from core.game_data.technology import load_technology_templates
from core.utils.yaml_loader import load_yaml_data

from .rules import NODES

logger = logging.getLogger(__name__)
RESOURCES = ("grain", "wood", "iron")
DEFAULT_COALITION: dict[str, Any] = {
    "enabled": False,
    "rally_seconds": 1800,
    "min_members": 1,
    "max_members": 5,
    "frontline_capacity": 3000,
    "commander_guest_limit": 3,
    "reinforcement_interval_seconds": 60,
    "reinforcement_per_tick": 300,
    "wave_interval_seconds": 60,
    "max_waves": 50,
}
DEFAULT_AUTO_DEVELOPMENT: dict[str, Any] = {
    "level_thresholds": [0, 1000, 3000, 6000, 10000, 15000],
    "activity_points": {
        "pve_win": 20,
        "pve_loss": 5,
        "transport": 30,
        "attack_win": 30,
        "attack_loss": 0,
        "defense_win": 20,
        "defense_loss": 10,
        "intercept_win": 30,
        "intercept_loss": 0,
    },
}


def _integer(value: Any, field: str, minimum: int = 1, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ImproperlyConfigured(f"阵营战配置 {field} 必须为 {minimum}—{maximum} 的整数")
    return value


def _amounts(value: Any, field: str, allowed: set[str] | None = None) -> None:
    if not isinstance(value, dict) or not value or (allowed is not None and not set(value) <= allowed):
        raise ImproperlyConfigured(f"阵营战配置 {field} 的资源映射无效")
    for key, amount in value.items():
        if not isinstance(key, str) or not key or "/" in key or ":" in key:
            raise ImproperlyConfigured(f"阵营战配置 {field} 的键名无效")
        _integer(amount, f"{field}.{key}", minimum=0)


def validate_campaign_rules(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ImproperlyConfigured("阵营战规则必须为映射")
    try:
        coalition = value.get("coalition", DEFAULT_COALITION)
        if not isinstance(coalition, dict) or set(coalition) != set(DEFAULT_COALITION):
            raise ImproperlyConfigured("联合攻防规则必须完整配置集结及前线参数")
        if not isinstance(coalition["enabled"], bool):
            raise ImproperlyConfigured("联合攻防开关必须为布尔值")
        for key in ("rally_seconds", "reinforcement_interval_seconds", "wave_interval_seconds"):
            _integer(coalition[key], "coalition." + key, maximum=86400)
        for key in ("min_members", "max_members"):
            _integer(coalition[key], "coalition." + key, maximum=5)
        if coalition["max_members"] < coalition["min_members"]:
            raise ImproperlyConfigured("集结人数上限不能少于最低出征人数")
        _integer(coalition["frontline_capacity"], "coalition.frontline_capacity", maximum=100000)
        _integer(coalition["commander_guest_limit"], "coalition.commander_guest_limit", maximum=3)
        _integer(coalition["max_waves"], "coalition.max_waves", maximum=1000)
        _integer(
            coalition["reinforcement_per_tick"],
            "coalition.reinforcement_per_tick",
            maximum=coalition["frontline_capacity"],
        )
        if not isinstance(value.get("day_gates_enabled", True), bool):
            raise ImproperlyConfigured("赛季天数解锁开关必须为布尔值")
        _integer(value["version"], "version", minimum=2, maximum=2)
        _integer(value["registration"]["days"], "registration.days", maximum=14)
        economy = value["economy"]
        for key in ("tick_seconds", "grain_per_troop", "daily_troop_quota"):
            _integer(economy[key], "economy." + key)
        for key in ("initial_resources", "resource_caps", "resource_rates"):
            _amounts(economy[key], "economy." + key, set(RESOURCES))
            if set(economy[key]) != set(RESOURCES):
                raise ImproperlyConfigured("阵营战资源必须配置粮草、木材和铁料")
        for key in ("initial_troops", "troop_caps", "troop_rates"):
            _amounts(economy[key], "economy." + key)
        if set(economy["initial_troops"]) != set(economy["troop_caps"]) or set(economy["initial_troops"]) != set(
            economy["troop_rates"]
        ):
            raise ImproperlyConfigured("阵营护院初始库存、产量和容量必须使用相同兵种")
        if not set(economy["initial_troops"]) <= set(load_troop_templates_from_yaml()):
            raise ImproperlyConfigured("阵营护院包含未配置的兵种")
        for key, initial in economy["initial_resources"].items():
            if initial > economy["resource_caps"][key]:
                raise ImproperlyConfigured("阵营初始资源超过容量")
        for key, initial in economy["initial_troops"].items():
            if initial > economy["troop_caps"][key]:
                raise ImproperlyConfigured("阵营初始护院超过容量")
        buildings = economy["buildings"]
        if set(buildings) != {"logistics", "training", "technology"}:
            raise ImproperlyConfigured("阵营建设配置必须包含后勤、募训和科技")
        for key, building in buildings.items():
            if not isinstance(building["name"], str) or not building["name"]:
                raise ImproperlyConfigured("阵营建筑名称不能为空")
            _integer(building["max_level"], key + ".max_level", maximum=10)
        automatic = economy.get("auto_development", DEFAULT_AUTO_DEVELOPMENT)
        if not isinstance(automatic, dict) or set(automatic) != {"level_thresholds", "activity_points"}:
            raise ImproperlyConfigured("自动发展必须配置等级阈值及活动建设值")
        thresholds = automatic["level_thresholds"]
        if (
            not isinstance(thresholds, list)
            or len(thresholds) < max(row["max_level"] for row in buildings.values())
            or len(thresholds) > 10
        ):
            raise ImproperlyConfigured("自动发展等级阈值必须覆盖最高设施等级")
        previous_threshold = -1
        for index, threshold in enumerate(thresholds):
            _integer(threshold, f"auto_development.level_thresholds.{index}", minimum=0, maximum=1_000_000_000)
            if threshold <= previous_threshold:
                raise ImproperlyConfigured("自动发展等级阈值必须严格递增")
            previous_threshold = threshold
        if thresholds[0] != 0:
            raise ImproperlyConfigured("初始设施等级的建设值阈值必须为0")
        points = automatic["activity_points"]
        required_points = set(DEFAULT_AUTO_DEVELOPMENT["activity_points"]) - {"defense_loss"}
        if not isinstance(points, dict) or not required_points <= set(points) <= set(
            DEFAULT_AUTO_DEVELOPMENT["activity_points"]
        ):
            raise ImproperlyConfigured("活动建设值必须完整配置已支持的活动")
        for key, amount in points.items():
            _integer(amount, "auto_development.activity_points." + key, minimum=0)
        if not isinstance(economy["technology_keys"], list) or not economy["technology_keys"]:
            raise ImproperlyConfigured("阵营科技列表不能为空")
        technology_keys = {row["key"] for row in load_technology_templates().get("technologies", [])}
        if any(not isinstance(key, str) or key not in technology_keys for key in economy["technology_keys"]):
            raise ImproperlyConfigured("阵营科技包含未配置的科技")
        for key, pve in economy["pve"].items():
            _amounts(pve["resources"], key + ".resources", set(RESOURCES))
            _integer(pve["merit"], key + ".merit")
            _integer(pve["failure_merit"], key + ".failure_merit", minimum=0)
        expeditions = value["expeditions"]
        _integer(expeditions.get("guest_daily_limit", 0), "expeditions.guest_daily_limit", minimum=0, maximum=20)
        _integer(expeditions.get("garrison_merit_per_hour", 0), "expeditions.garrison_merit_per_hour", minimum=0)
        mode_travel = expeditions.get("mode_travel_seconds", {})
        if not isinstance(mode_travel, dict) or not set(mode_travel) <= {"transport", "intercept", "escort"}:
            raise ImproperlyConfigured("行动专用行军时长仅支持运输、拦截和护送")
        for mode, seconds in mode_travel.items():
            _integer(seconds, "expeditions.mode_travel_seconds." + mode)
        _integer(expeditions.get("transport_reward_base", 0), "expeditions.transport_reward_base", minimum=0)
        _integer(expeditions.get("transport_reward_threshold", 200), "expeditions.transport_reward_threshold")
        _integer(
            expeditions.get("legacy_transport_seconds", expeditions["transport_seconds"]),
            "expeditions.legacy_transport_seconds",
        )
        if not isinstance(expeditions.get("convoy_encounters_enabled", False), bool):
            raise ImproperlyConfigured("多人运输遭遇战开关无效")
        _integer(expeditions.get("max_convoy_players", 5), "expeditions.max_convoy_players", maximum=5)
        _integer(expeditions.get("transport_reward_full_value", 1200), "expeditions.transport_reward_full_value")
        station_hours = expeditions.get("station_hours_options", [1, 2, 4])
        if not isinstance(station_hours, list) or not station_hours:
            raise ImproperlyConfigured("驻军时长选项必须为非空列表")
        for hours in station_hours:
            _integer(hours, "expeditions.station_hours_options", maximum=24)
        if len(set(station_hours)) != len(station_hours):
            raise ImproperlyConfigured("驻军时长选项不能重复")
        for key in (
            "max_active",
            "combat_days",
            "daily_actions",
            "minimum_travel_seconds",
            "station_seconds",
            "max_battles",
            "transport_seconds",
            "attack_merit",
            "failure_merit",
            "defend_merit",
            "repair_amount",
            "demolition_amount",
            "control_amount",
            "score_interval_seconds",
            "garrison_capacity",
        ):
            _integer(expeditions[key], "expeditions." + key)
        if expeditions["combat_days"] > 90:
            raise ImproperlyConfigured("赛季战斗天数不能超过90天")
        if value["registration"]["days"] >= expeditions["combat_days"]:
            raise ImproperlyConfigured("报名期必须早于赛季行动期结束")
        timezone_name = expeditions["timezone"]
        if not isinstance(timezone_name, str) or not timezone_name:
            raise ImproperlyConfigured("古城战场时区必须为有效时区名称")
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ImproperlyConfigured("古城战场时区必须为有效时区名称") from exc
        multiplier = expeditions["delivery_multiplier"]
        if (
            isinstance(multiplier, bool)
            or not isinstance(multiplier, (int, float))
            or not math.isfinite(multiplier)
            or not 1 <= multiplier <= 2
        ):
            raise ImproperlyConfigured("运输收益倍率必须为1—2之间的有限数字")
        enemy_levels = expeditions["enemy_levels"]
        if not isinstance(enemy_levels, dict) or not enemy_levels:
            raise ImproperlyConfigured("敌军等级必须为非空据点映射")
        if not set(enemy_levels) <= set(expeditions["travel_seconds"]):
            raise ImproperlyConfigured("敌军等级包含未配置行军时间的据点")
        if set(expeditions["travel_seconds"]) != set(NODES):
            raise ImproperlyConfigured("行军时间必须完整覆盖当前地图建筑")
        pve_nodes = {key for key, node in NODES.items() if node["kind"] == "pve"}
        site_nodes = {key for key, node in NODES.items() if node["kind"] == "pvp"} | {
            "river:docks",
            "forest:granary",
            "forest:camp",
            "river:market",
        }
        if not set(economy["pve"]) <= pve_nodes or not set(value["sites"]) <= site_nodes:
            raise ImproperlyConfigured("PVE或争夺据点包含不支持该玩法的地图建筑")
        if "river:docks" not in value["sites"] or "river:docks" not in economy["pve"]:
            raise ImproperlyConfigured("运输战备必须配置渡口据点及护航PVE产出")
        if "forest:granary" in value["sites"] and "forest:granary" not in economy["pve"]:
            raise ImproperlyConfigured("粮仓据点必须配置PVE产出")
        if not (set(economy["pve"]) | set(value["sites"])) <= set(enemy_levels):
            raise ImproperlyConfigured("PVE及争夺据点必须配置敌军等级")
        for node_key, level in enemy_levels.items():
            _integer(level, "enemy_levels." + str(node_key), maximum=1000)
        for key, seconds in expeditions["travel_seconds"].items():
            _integer(seconds, "travel_seconds." + key)
        if not isinstance(expeditions["city_weekdays"], list) or not expeditions["city_weekdays"]:
            raise ImproperlyConfigured("古城开放星期不能为空")
        for day in expeditions["city_weekdays"]:
            _integer(day, "city_weekdays", minimum=0, maximum=6)
        _amounts(expeditions["cargo"], "cargo", set(RESOURCES))
        _amounts(expeditions["repair_cost"], "repair_cost", set(RESOURCES))
        for key, site in value["sites"].items():
            if not isinstance(site["name"], str) or not site["name"].strip():
                raise ImproperlyConfigured("争夺据点名称不能为空")
            for field in (
                "unlock_day",
                "guards",
                "durability",
                "control_required",
                "score_per_tick",
                "protection_seconds",
            ):
                _integer(site[field], key + "." + field)
            if "weekday_score_per_tick" in site:
                _integer(site["weekday_score_per_tick"], key + ".weekday_score_per_tick", minimum=0)
            if "weekday_score_effective_at" in site:
                stamp = site["weekday_score_effective_at"]
                try:
                    parsed = parse_datetime(stamp) if isinstance(stamp, str) else None
                except ValueError:
                    parsed = None
                if parsed is None or timezone.is_naive(parsed):
                    raise ImproperlyConfigured(key + ".weekday_score_effective_at 必须为带时区的时间")
            if site["unlock_day"] > expeditions["combat_days"]:
                raise ImproperlyConfigured("争夺据点不能晚于赛季行动期开放")
        special_troops = economy.get("special_troops", {})
        if not isinstance(special_troops, dict):
            raise ImproperlyConfigured("特殊护院必须为兵种配置映射")
        for troop_key, special in special_troops.items():
            if troop_key not in economy["initial_troops"] or not isinstance(special, dict):
                raise ImproperlyConfigured("特殊护院必须存在于阵营兵库配置")
            if special.get("site_key") not in value["sites"]:
                raise ImproperlyConfigured("特殊护院必须绑定可争夺据点")
            if economy["initial_troops"][troop_key] or economy["troop_rates"][troop_key]:
                raise ImproperlyConfigured("特殊护院初始库存及普通产率必须为0")
            _integer(special["rate"], f"special_troops.{troop_key}.rate")
        rewards = value["rewards"]
        _integer(rewards["minimum_merit"], "rewards.minimum_merit")
        previous = 0
        tier_keys: set[str] = set()
        for tier in rewards["tiers"]:
            if not isinstance(tier["name"], str) or not tier["name"].strip():
                raise ImproperlyConfigured("排名奖励名称不能为空")
            percentile = _integer(tier["percentile"], "rewards.percentile", maximum=100)
            if percentile <= previous or not tier["key"] or tier["key"] in tier_keys:
                raise ImproperlyConfigured("排名奖励档位必须按比例递增且键名唯一")
            tier_keys.add(tier["key"])
            previous = percentile
            _amounts(tier["items"], "rewards.tier.items")
        if previous != 100:
            raise ImproperlyConfigured("排名奖励必须覆盖全部达标参与者")
        _amounts(rewards["winner_items"], "winner_items")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ImproperlyConfigured("阵营战配置结构不完整") from exc
    from .development_rules import validate_development_rules

    validate_development_rules(value)
    from .occupation import validate_occupation

    validate_occupation(value.get("occupation"))
    from .simplification import validate_simplification

    validate_simplification(value)
    from .resource_rules import validate_resource_rules

    validate_resource_rules(value)
    from .playability_rules import validate_playability

    validate_playability(value.get("playability"))
    from .npc_encounters import validate_npc_encounters

    validate_npc_encounters(value.get("npc_encounters"), value)
    from .market_stock import validate_market_control

    validate_market_control(value.get("market_control"))
    if (value.get("market_control") or {}).get("enabled") and "river:market" not in value["sites"]:
        raise ImproperlyConfigured("占领集市必须配置沿岸集市争夺据点")
    if (value.get("market_control") or {}).get("enabled"):
        base = value.get("simplification", {}).get("market", {}).get("return_percent", 80)
        if base + value["market_control"]["owner_return_bonus_percent"] >= 100:
            raise ImproperlyConfigured("集市占领优惠后仍须保留资源调剂损耗")
    from .fortifications import validate_fortifications

    validate_fortifications(value.get("fortifications"), value["sites"])
    return value


def load_campaign_rules() -> dict[str, Any]:
    value = load_yaml_data(
        Path(settings.BASE_DIR) / "data/season_campaign.yaml",
        logger=logger,
        context="阵营战规则",
        default={},
        raise_on_error=True,
    )
    return copy.deepcopy(validate_campaign_rules(value))


def campaign_rules(season: Any = None) -> dict[str, Any]:
    snapshot = season.rules.get("campaign") if season is not None else None
    return copy.deepcopy(validate_campaign_rules(snapshot)) if snapshot is not None else load_campaign_rules()


def coalition_rules(season: Any = None) -> dict[str, Any]:
    """旧季缺少联合规则时保留原在途语义，新增赛季遵循自身配置快照。"""
    if season is not None:
        snapshot = season.rules.get("campaign")
        if not isinstance(snapshot, dict) or "coalition" not in snapshot:
            return copy.deepcopy(DEFAULT_COALITION)
    return copy.deepcopy(campaign_rules(season).get("coalition", DEFAULT_COALITION))
