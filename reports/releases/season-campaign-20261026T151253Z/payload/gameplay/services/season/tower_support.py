"""有限箭塔支援：原生城防实体承伤，一次箭雨写入真实战斗过程。"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from battle.combatants_pkg.core import Combatant

TOWER_KEY = "arrow_tower"
SUPPORT_KEY = "season_tower_volley"


def prepare_tower_support(prepared: dict[str, Any], site: Any, defender_faction: str) -> dict[str, Any]:
    """只修改本场准备对象，尚不扣除真实门客或建筑生命；返回可审计的冻结参数。"""
    from .fortifications import fortification_rules, tower_state
    from .hitpoints import hitpoint_rules

    rules = fortification_rules(site.season)
    physical_hp = hitpoint_rules(site.season)["enabled"]
    spec = rules.get("sites", {}).get(site.key)
    if not rules["enabled"] or not spec or site.owner != defender_faction:
        return {}
    state = tower_state(site)
    maximum, hp = state["max_hp"], state["hp"]
    if maximum <= 0 or hp <= 0:
        return {}
    battle = prepared["prepared"]
    if any(unit.template_key == TOWER_KEY for unit in battle.defender_city_defenses):
        raise ValueError("本场已经存在箭塔，不能重复附加支援")
    ratio = min(rules["tower"]["damage_percent"], rules["tower"]["max_damage_percent"]) / 100 * hp / maximum
    affected = 0
    # 门客生命由真实战报统一结算；护院伤亡沿用引擎，不在场外直接扣兵。
    for unit in battle.attacker_guests:
        if int(unit.hp * ratio) <= 0:
            continue
        unit.skills = [
            *unit.skills,
            {
                "key": SUPPORT_KEY,
                "name": "守城箭塔齐射",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "battle_start",
                            "conditions": {"state_absent": SUPPORT_KEY},
                            "effects": [
                                {
                                    "type": "lose_hp_ratio",
                                    "target_scope": "self",
                                    "value": ratio,
                                    "current_hp_based": True,
                                    "nonlethal": True,
                                    "log": True,
                                    "log_name": "守城箭塔齐射",
                                },
                                {"type": "set_state", "target_scope": "self", "key": SUPPORT_KEY, "value": True},
                            ],
                        }
                    ]
                },
            },
        ]
        affected += 1
    battle.defender_city_defenses.append(
        Combatant(
            name="守城箭塔",
            attack=0,
            defense=0,
            hp=hp,
            max_hp=maximum,
            side="defender",
            rarity="city_defense",
            luck=0,
            agility=0,
            priority=0,
            kind="city_defense",
            troop_strength=1,
            initial_troop_strength=1,
            initial_hp=hp,
            unit_hp=maximum,
            template_key=TOWER_KEY,
            battle_modifiers={"skip_turn": True},
        )
    )
    return {
        "site_key": site.key,
        "owner": site.owner,
        "initial_hp": hp,
        "max_hp": maximum,
        "damage_ratio": ratio,
        "affected_guests": affected,
        "wear_per_battle": 0 if physical_hp else rules["tower"]["wear_per_battle"],
    }


def settle_tower_support_locked(site: Any, report: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    """从真实战报取承伤结果；同份战报重复结算不再磨损。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("箭塔结算必须持有赛季锁并处于事务中")
    if not snapshot:
        return {}
    if not report.pk:
        raise ValueError("箭塔结算需要已经保存的真实战报")
    if site.key != snapshot["site_key"] or site.owner != snapshot["owner"]:
        raise ValueError("箭塔结算的建筑或归属发生变化")
    stored = site.defenses.get(TOWER_KEY, {})
    previous = stored.get("last_report_id")
    if previous is not None and previous >= report.pk:
        return stored.get("last_battle", {})
    row = next((row for row in report.defender_city_defenses if row.get("key") == TOWER_KEY), None)
    if row is None:
        raise ValueError("战报缺少实际参战的箭塔生命")
    remaining = min(snapshot["initial_hp"], max(0, int(row["hp"])))
    result = {
        **snapshot,
        "report_id": report.pk,
        "combat_damage": snapshot["initial_hp"] - remaining,
        "wear": min(remaining, snapshot["wear_per_battle"]),
        "remaining_hp": max(0, remaining - snapshot["wear_per_battle"]),
    }
    site.defenses = {
        **site.defenses,
        TOWER_KEY: {
            **stored,
            "hp": result["remaining_hp"],
            "max_hp": snapshot["max_hp"],
            "last_report_id": report.pk,
            "last_battle": result,
        },
    }
    site.save(update_fields=["defenses"])
    return result
