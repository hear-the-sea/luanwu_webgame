"""精简版军务与集市参数；旧季缺少快照时保持原入口。"""

from copy import deepcopy
from typing import Any

from django.core.exceptions import ImproperlyConfigured

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "garrison_carried_food_hours": 1,
    "market": {
        "daily_faction_orders": 5,
        "order_value": 200,
        "return_percent": 80,
        "source_reserve_days": 2,
        "target_shortage_days": 1,
    },
}


def simplification_rules(season: Any) -> dict[str, Any]:
    snapshot = season.rules.get("campaign", {}).get("simplification") if season else None
    result = deepcopy(DEFAULTS)
    result.update(deepcopy(snapshot) if snapshot is not None else {"enabled": False})
    return result


def validate_simplification(campaign: dict[str, Any]) -> None:
    config = campaign.get("simplification")
    if config is None:
        return
    try:
        if not isinstance(config, dict) or not isinstance(config["enabled"], bool):
            raise ValueError
        if not config["enabled"]:
            return
        if not campaign.get("coalition", {}).get("enabled") or not campaign.get("resource_economy", {}).get("enabled"):
            raise ValueError
        if (
            isinstance(config["garrison_carried_food_hours"], bool)
            or not isinstance(config["garrison_carried_food_hours"], int)
            or not 1 <= config["garrison_carried_food_hours"] <= 6
        ):
            raise ValueError
        for key in DEFAULTS["market"]:
            number = config["market"][key]
            if isinstance(number, bool) or not isinstance(number, int) or not 0 < number <= 10000:
                raise ValueError
        if config["market"]["return_percent"] >= 100:
            raise ValueError
        if config["market"]["source_reserve_days"] <= config["market"]["target_shortage_days"]:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ImproperlyConfigured("simplification配置无效：需开启集结和三资源经济，并设置有效调剂限额/损耗") from exc
