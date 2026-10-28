"""显式更新测试季兵种配置，保留已生产、已派遣兵力及个人进度。"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from typing import Any

from django.db import transaction

from core.exceptions import GameError
from gameplay.models import SeasonExpedition

from .campaign_rules import load_campaign_rules, validate_campaign_rules
from .economy import produce_locked
from .warfare import ensure_sites_locked

ELITE_TROOPS = {
    **dict.fromkeys(("dao_ke", "dao_jie", "dao_ba", "dao_sheng"), "dao_sheng"),
    **dict.fromkeys(("qiang_ke", "qiang_hao", "qiang_ba", "qiang_wang"), "qiang_wang"),
    **dict.fromkeys(("jian_shi", "jian_xia", "jian_hao", "jian_sheng"), "jian_sheng"),
    **dict.fromkeys(("quan_shi", "quan_ba", "quan_wang", "quan_sheng"), "quan_sheng"),
    **dict.fromkeys(("archer", "fast_archer", "divine_archer", "arrow_god"), "arrow_god"),
    "bloodthirsty_cavalry": "bloodthirsty_cavalry",
}


def _convert(loadout: dict[str, int], allowed: set[str]) -> dict[str, int]:
    converted: dict[str, int] = defaultdict(int)
    for key, count in loadout.items():
        elite = ELITE_TROOPS.get(key, key)
        if elite not in allowed:
            raise GameError("现有兵库含新配置未支持的兵种，未修改赛季")
        converted[elite] += count
    return dict(converted)


def upgrade_armies_locked(season: Any, now: datetime) -> dict[str, int]:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("更新兵种须在持有赛季锁的事务内执行")
    if season.closed:
        raise GameError("已归档赛季不能更新兵种")
    defaults = load_campaign_rules()
    states = produce_locked(season, now)
    rules = deepcopy(season.rules)
    campaign = rules["campaign"]
    for key in ("initial_troops", "troop_caps", "troop_rates", "special_troops", "technology_keys"):
        campaign["economy"][key] = deepcopy(defaults["economy"][key])
    campaign["sites"]["forest:camp"] = deepcopy(defaults["sites"]["forest:camp"])
    campaign["expeditions"]["enemy_levels"]["forest:camp"] = defaults["expeditions"]["enemy_levels"]["forest:camp"]
    validate_campaign_rules(campaign)
    initial = campaign["economy"]["initial_troops"]
    allowed = set(initial)
    for state in states.values():
        converted = _convert(state.troops, allowed)
        state.troops = {key: converted.get(key, amount) for key, amount in initial.items()}
        tech_level = max(0, state.levels.get("technology", 1) - 1)
        state.technology = {
            key: state.technology.get(key, tech_level) for key in campaign["economy"]["technology_keys"]
        }
        state.save(update_fields=["troops", "technology"])
    updated = 0
    for run in SeasonExpedition.objects.select_for_update().filter(member__season=season).exclude(status="completed"):
        troops = _convert(run.troops, allowed)
        initial_troops = _convert(run.initial_troops, allowed)
        if troops != run.troops or initial_troops != run.initial_troops:
            run.troops = troops
            run.initial_troops = initial_troops
            run.save(update_fields=["troops", "initial_troops"])
            updated += 1
    season.rules = rules
    season.save(update_fields=["rules"])
    ensure_sites_locked(season, now)
    return {"factions": len(states), "expeditions": updated}
