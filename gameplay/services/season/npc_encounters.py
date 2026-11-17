"""配置化 NPC 敌军；出征时冻结，旧队伍沿用原有规则。"""

from copy import deepcopy
from typing import Any

from django.core.exceptions import ImproperlyConfigured


def validate_npc_encounters(value: Any, campaign: dict[str, Any]) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
        raise ImproperlyConfigured("赛季 NPC 阵容配置必须包含 enabled")
    if not value["enabled"]:
        return

    def integer(raw: Any, low: int, high: int) -> bool:
        return type(raw) is int and low <= raw <= high

    def keys(raw: Any, *, empty: bool = True) -> bool:
        return isinstance(raw, list) and (empty or bool(raw)) and all(isinstance(item, str) and item for item in raw)

    from battle.troops import load_troop_templates_from_yaml

    known = load_troop_templates_from_yaml()

    def troop_map(raw: Any) -> bool:
        return isinstance(raw, dict) and all(key in known and integer(n, 0, 3000) for key, n in raw.items())

    try:
        collection, guard, pools = value["collection"], value["garrison"], value["pools"]
        if not isinstance(pools, dict) or not pools or not all(keys(pool, empty=False) for pool in pools.values()):
            raise ValueError
        if any(len(pool) != len(set(pool)) for pool in pools.values()):
            raise ValueError
        if not isinstance(collection["base_levels"], dict) or not isinstance(guard["base_levels"], dict):
            raise ValueError
        if not set(campaign["economy"]["pve"]) <= set(collection["base_levels"]):
            raise ValueError
        if not set(campaign["sites"]) <= set(guard["base_levels"]):
            raise ValueError
        if any(
            not integer(level, 1, 10000)
            for level in (*collection["base_levels"].values(), *guard["base_levels"].values())
        ):
            raise ValueError
        waves = collection["waves"]
        max_waves = campaign.get("resource_economy", {}).get("pve", {}).get("collection_waves", {}).get("max_waves", 5)
        if not isinstance(waves, list) or len(waves) < max_waves:
            raise ValueError
        for row in waves:
            if not (
                integer(row["level_bonus"], 0, 10000)
                and integer(row["count"], 1, 15)
                and row["rarity"] in pools
                and keys(row["skills"])
                and troop_map(row["troops"])
            ):
                raise ValueError
            skill_sets = row.get("skill_sets")
            if "skill_sets" in row and (
                not isinstance(skill_sets, list)
                or len(skill_sets) != row["count"]
                or not all(keys(group) for group in skill_sets)
            ):
                raise ValueError
        if (
            waves[0]["rarity"] != "green"
            or waves[0]["count"] != 1
            or waves[0]["skills"]
            or any(waves[0].get("skill_sets", []))
            or any(waves[0]["troops"].values())
        ):
            raise ValueError
        if not (
            integer(guard["guests_per_player"], 1, 3)
            and integer(guard["max_guests"], 1, 15)
            and guard["rarity"] in pools
            and troop_map(guard["troops_per_player"])
            and keys(guard["skills"])
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ImproperlyConfigured("赛季 NPC 阵容、采集波次或守军配置无效") from exc


def snapshot_npc_encounter(season: Any, node_key: str, kind: str) -> dict[str, Any]:
    value = season.rules.get("campaign", {}).get("npc_encounters", {})
    if not isinstance(value, dict) or not value.get("enabled"):
        return {}
    category = "collection" if kind == "pve" else "garrison" if kind in {"attack", "siege"} else ""
    if not category:
        return {}
    spec = deepcopy(value[category])
    level = spec.pop("base_levels").get(node_key)
    if level is None:
        return {}
    snapshot = {"version": 1, "category": category, "base_level": level, "pools": deepcopy(value["pools"]), **spec}
    # 出征前核对全部可能用到的敌军，避免战至后期才发现数据缺失。
    from battle.combatants_pkg.cache import get_all_guest_templates
    from core.exceptions import GameError
    from guests.models import Skill

    if category == "collection":
        wave_rules = season.rules["campaign"].get("resource_economy", {}).get("pve", {}).get("collection_waves", {})
        rows = spec["waves"][: wave_rules.get("max_waves", len(spec["waves"]))]
    else:
        rows = [spec]
    templates = get_all_guest_templates()
    rarities = {row["rarity"] for row in rows}
    if any(
        key not in templates or templates[key].rarity != rarity
        for rarity in rarities
        for key in snapshot["pools"][rarity]
    ):
        raise GameError("赛季中立守军门客模板未加载或品质不匹配，请联系管理员")
    skills = {skill for row in rows for group in row.get("skill_sets", [row["skills"]]) for skill in group}
    if skills != set(Skill.objects.filter(key__in=skills).values_list("key", flat=True)):
        raise GameError("赛季中立守军技能未加载，请联系管理员")
    return snapshot
