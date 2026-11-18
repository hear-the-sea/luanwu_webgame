"""少人数协作的配置快照；未启用的旧赛季保持原结算规则。"""

from copy import deepcopy
from typing import Any

from django.core.exceptions import ImproperlyConfigured

DEFAULT_PLAYABILITY: dict[str, Any] = {
    "enabled": True,
    "balance": {
        "active_days": 7,
        "activity_weight": 45,
        "power_weight": 35,
        "prestige_weight": 20,
        "playtime_options": ["any", "day", "evening", "late"],
    },
    "npc": {
        "level_percent_by_players": {"1": 70, "2": 85, "3": 100, "4": 100, "5": 100},
        "max_guests_by_players": {"1": 1, "2": 2, "3": 3, "4": 3, "5": 3},
    },
    "reservation": {"maximum_hours": 24, "guest_limit": 3},
    "weekly": {
        "win_points": 10,
        "draw_points": 5,
        "minimum_contested_days": 1,
        "goal_per_active": 100,
        "goal_minimum": 200,
        "goal_maximum": 1200,
        "participation_merit": 50,
        "battle_points": {
            "attack_win": 8,
            "attack_loss": 2,
            "defense_win": 6,
            "defense_loss": 2,
            "intercept_win": 4,
            "intercept_loss": 1,
        },
        "frontier_points": 3,
        "personal_items": {"experience_fruit": 3, "xuan_tie_essence": 2},
        "team_items": {"experience_fruit": 2, "xuan_tie_essence": 1},
        "ranking_base_items": {"experience_fruit": 25, "xuan_tie_essence": 20},
        "ranking_bonus_percent": {"elite": 20, "distinguished": 10, "participant": 0},
    },
    "site_effects": {"gate_march_reduction_percent": 10},
    "recommended_battle_hours": [20, 22],
}


def playability_rules(season: Any) -> dict[str, Any]:
    snapshot = season.rules.get("campaign", {}).get("playability") if season else None
    result = deepcopy(DEFAULT_PLAYABILITY)
    if not snapshot:
        result["enabled"] = False
        return result
    for key, value in snapshot.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(deepcopy(value))
        else:
            result[key] = deepcopy(value)
    return result


def validate_playability(config: Any) -> None:
    if config is None:
        return
    if not isinstance(config, dict) or not isinstance(config.get("enabled"), bool):
        raise ImproperlyConfigured("少人数赛季规则必须包含布尔enabled")
    if not config["enabled"]:
        return
    merged = playability_rules(type("Snapshot", (), {"rules": {"campaign": {"playability": config}}})())

    def integer(number: Any, name: str, low: int = 1, high: int = 1000000) -> None:
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise ImproperlyConfigured(f"少人数赛季配置{name}必须为{low}—{high}的整数")

    try:
        balance = merged["balance"]
        integer(balance["active_days"], "balance.active_days", high=30)
        for key in ("activity_weight", "power_weight", "prestige_weight"):
            integer(balance[key], "balance." + key, low=0, high=100)
        if sum(balance[key] for key in ("activity_weight", "power_weight", "prestige_weight")) != 100:
            raise ImproperlyConfigured("阵营平衡权重之和必须为100")
        if balance["playtime_options"] != ["any", "day", "evening", "late"]:
            raise ImproperlyConfigured("参战时段必须使用既有时段选项")
        for key, maximum in (("level_percent_by_players", 100), ("max_guests_by_players", 15)):
            values = merged["npc"][key]
            if not isinstance(values, dict) or set(values) != {"1", "2", "3", "4", "5"}:
                raise ImproperlyConfigured("NPC难度必须覆盖1至5名玩家")
            previous = 0
            for count in range(1, 6):
                integer(values[str(count)], "npc." + key, high=maximum)
                if values[str(count)] < previous:
                    raise ImproperlyConfigured("NPC人数档位必须单调递增")
                previous = values[str(count)]
        integer(merged["reservation"]["maximum_hours"], "reservation.maximum_hours", high=24)
        integer(merged["reservation"]["guest_limit"], "reservation.guest_limit", high=3)
        weekly = merged["weekly"]
        for key in (
            "win_points",
            "draw_points",
            "minimum_contested_days",
            "goal_per_active",
            "goal_minimum",
            "goal_maximum",
            "participation_merit",
        ):
            integer(weekly[key], "weekly." + key, low=0 if key == "draw_points" else 1)
        if weekly["draw_points"] > weekly["win_points"] or weekly["goal_minimum"] > weekly["goal_maximum"]:
            raise ImproperlyConfigured("周胜点或共同目标上下限无效")
        battle_points = weekly.get("battle_points")
        if not isinstance(battle_points, dict) or not battle_points:
            raise ImproperlyConfigured("周战斗战果必须为非空映射")
        for key, amount in battle_points.items():
            if not isinstance(key, str) or not key:
                raise ImproperlyConfigured("周战斗战果事件名无效")
            integer(amount, "weekly.battle_points." + key, low=0, high=100)
        integer(weekly.get("frontier_points"), "weekly.frontier_points", low=0, high=100)
        raw_weekly = config.get("weekly")
        if isinstance(raw_weekly, dict) and ("battle_points" in raw_weekly or "frontier_points" in raw_weekly):
            # 新赛季快照必须同时声明两个字段；旧快照缺省时保持旧结算语意。
            if "battle_points" not in raw_weekly or "frontier_points" not in raw_weekly:
                raise ImproperlyConfigured("新赛季周战果配置必须同时包含 battle_points 与 frontier_points")
            supported = {
                "attack_win",
                "attack_loss",
                "defense_win",
                "defense_loss",
                "intercept_win",
                "intercept_loss",
            }
            raw_points = raw_weekly["battle_points"]
            if not isinstance(raw_points, dict) or not set(raw_points) <= supported:
                raise ImproperlyConfigured("周战斗战果包含未知事件")
        for key in ("personal_items", "team_items", "ranking_base_items"):
            if not isinstance(weekly[key], dict) or not weekly[key]:
                raise ImproperlyConfigured("周奖励必须为非空物品映射")
            for item, amount in weekly[key].items():
                if not isinstance(item, str) or not item or not item.replace("_", "").isalnum():
                    raise ImproperlyConfigured("周奖励物品键名无效")
                integer(amount, "weekly." + key)
        for amount in weekly["ranking_bonus_percent"].values():
            integer(amount, "weekly.ranking_bonus_percent", low=0, high=20)
        integer(
            merged["site_effects"]["gate_march_reduction_percent"],
            "site_effects.gate_march_reduction_percent",
            low=0,
            high=20,
        )
        hours = merged["recommended_battle_hours"]
        if not isinstance(hours, list) or len(hours) != 2:
            raise ImproperlyConfigured("推荐参战时间须包含开始与结束小时")
        for hour in hours:
            integer(hour, "recommended_battle_hours", low=0, high=23)
        if hours[0] >= hours[1]:
            raise ImproperlyConfigured("推荐参战结束时间须晚于开始时间")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ImproperlyConfigured("少人数赛季配置结构无效") from exc
