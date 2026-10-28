"""阵营后勤生产与兵库操作；调用方须在事务内持有 Season 行锁。"""

from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.exceptions import GameError
from gameplay.models.season import Season, SeasonFaction, SeasonMember, SeasonSite

from .campaign_rules import DEFAULT_AUTO_DEVELOPMENT, campaign_rules
from .population import faction_profile
from .resource_rules import normal_training_potential, resource_caps, resource_rules
from .technology import season_technology_levels


def _require_transaction() -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("阵营后勤写入必须在持有赛季锁的事务中执行")


def ensure_factions_locked(season: Season, now: datetime) -> dict[str, SeasonFaction]:
    _require_transaction()
    campaign = campaign_rules(season)
    rules = campaign["economy"]
    production_start = season.starts_at + timedelta(days=campaign["registration"]["days"])
    result = {}
    new_economy = resource_rules(season)["enabled"]
    for faction in ("fiction", "history"):
        row, _ = SeasonFaction.objects.get_or_create(
            season=season,
            faction=faction,
            defaults={
                "resources": (
                    dict.fromkeys(rules["initial_resources"], 0) if new_economy else dict(rules["initial_resources"])
                ),
                "troops": dict.fromkeys(rules["initial_troops"], 0) if new_economy else dict(rules["initial_troops"]),
                "economy_state": {"new_economy": True, "opened": False} if new_economy else {},
                "levels": {key: 1 for key in rules["buildings"]},
                "technology": {key: 0 for key in rules["technology_keys"]},
                "produced_at": production_start,
            },
        )
        row.season = season
        result[faction] = row
    return result


def _produce(
    row: SeasonFaction,
    rules: dict[str, Any],
    cutoff: datetime,
    special_rates: dict[str, tuple[int, datetime]] | None = None,
) -> None:
    seconds = rules["tick_seconds"]
    ticks = max(0, int((cutoff - row.produced_at).total_seconds()) // seconds)
    if not ticks:
        return
    logistics = row.levels.get("logistics", 1)
    training = row.levels.get("training", 1)
    resources = dict(row.resources)
    troops = dict(row.troops)
    rates = dict(rules["troop_rates"])
    special_rates = special_rates or {}
    for key in rules.get("special_troops", {}):
        rates[key] = special_rates[key][0] if key in special_rates else 0
    # 每轮先生产再训练，精确保留粮仓容量与训练耗粮的顺序；只在末尾写一次数据库。
    # 兵库满额后可直接合并剩余资源生产，长时间离线也不会产生逐轮数据库查询。
    for index in range(ticks):
        if all(not rate or troops.get(key, 0) >= rules["troop_caps"][key] for key, rate in rates.items()):
            for key, rate in rules["resource_rates"].items():
                resources[key] = min(
                    max(rules["resource_caps"][key], resources.get(key, 0)),
                    resources.get(key, 0)
                    + min(
                        max(0, rules["resource_caps"][key] - resources.get(key, 0)), rate * logistics * (ticks - index)
                    ),
                )
            break
        for key, rate in rules["resource_rates"].items():
            resources[key] = resources.get(key, 0) + min(
                max(0, rules["resource_caps"][key] - resources.get(key, 0)), rate * logistics
            )
        tick_start = row.produced_at + timedelta(seconds=seconds * index)
        requested = {
            key: (
                min(max(0, rules["troop_caps"][key] - troops.get(key, 0)), rate * training)
                if key not in special_rates or tick_start >= special_rates[key][1]
                else 0
            )
            for key, rate in rates.items()
        }
        wanted = sum(requested.values())
        available = min(wanted, resources.get("grain", 0) // rules["grain_per_troop"])
        if wanted:
            allocations = {key: amount * available // wanted for key, amount in requested.items()}
            remainder = available - sum(allocations.values())
            order = sorted(requested, key=lambda key: (-(requested[key] * available % wanted), key))
            for key in order[:remainder]:
                allocations[key] += 1
            troops = {key: troops.get(key, 0) + allocations[key] for key in requested}
            resources["grain"] -= available * rules["grain_per_troop"]
    row.troops = troops
    row.resources = resources
    row.produced_at += timedelta(seconds=ticks * seconds)
    row.save(update_fields=["resources", "troops", "produced_at"])


def produce_locked(season: Season, now: datetime) -> dict[str, SeasonFaction]:
    _require_transaction()
    from .recovery import settle_auto_repairs_locked

    settle_auto_repairs_locked(season, now)
    rules = campaign_rules(season)
    cutoff = min(now, season.ends_at, season.starts_at + timedelta(days=rules["expeditions"]["combat_days"]))
    states = ensure_factions_locked(season, now)
    special_by_faction: dict[str, dict[str, tuple[int, datetime]]] = {key: {} for key in states}
    special = rules["economy"].get("special_troops", {})
    if special:
        sites = {
            site.key: site
            for site in SeasonSite.objects.filter(season=season, key__in=[row["site_key"] for row in special.values()])
        }
        for key, spec in special.items():
            site = sites.get(spec["site_key"])
            if site is None or site.owner not in states or site.protected_until is None:
                continue
            # 保护截止时刻在易主时固定，可精确还原本轮占领开始，不新增持久字段。
            captured_at = parse_datetime(site.defenses.get("captured_at", "")) or site.protected_until - timedelta(
                seconds=rules["sites"][site.key]["protection_seconds"]
            )
            special_by_faction[site.owner][key] = (spec["rate"], captured_at)
    from copy import deepcopy

    for row in states.values():
        if resource_rules(season)["enabled"]:
            from .resource_production import produce_resources_locked

            produce_resources_locked(row, rules["economy"], cutoff, special_by_faction[row.faction])
            continue
        profile = faction_profile(season, row.faction)
        scaled = deepcopy(rules["economy"])
        for field in ("resource_rates", "troop_rates"):
            scaled[field] = {key: int(value * profile["rate"]) for key, value in scaled[field].items()}
        for field in ("resource_caps", "troop_caps"):
            scaled[field] = {key: max(1, int(value * profile["capacity"])) for key, value in scaled[field].items()}
        specials = (
            {
                key: (max(1, int(rate * profile["rate"])), captured)
                for key, (rate, captured) in special_by_faction[row.faction].items()
            }
            if profile["rate"]
            else {}
        )
        _produce(row, scaled, cutoff, specials)
    return states


def technology_levels(faction: SeasonFaction) -> dict[str, int]:
    return season_technology_levels({str(key): max(0, int(level)) for key, level in faction.technology.items()})


def grant_construction_locked(member: SeasonMember, activity_key: str, now: datetime) -> dict[str, Any]:
    automatic = campaign_rules(member.season)["economy"].get("auto_development", DEFAULT_AUTO_DEVELOPMENT)
    if activity_key not in automatic["activity_points"]:
        raise GameError("未知的阵营建设活动")
    return grant_construction_amount_locked(member, automatic["activity_points"][activity_key], now)


def grant_construction_amount_locked(member: SeasonMember, amount: int, now: datetime) -> dict[str, Any]:
    _require_transaction()
    if member.faction not in {"fiction", "history"}:
        member.refresh_from_db(fields=["pending_construction"])
        member.pending_construction += amount
        member.save(update_fields=["pending_construction"])
        return {
            "construction_points": amount,
            "total_construction_points": member.pending_construction,
            "levels": {},
            "upgraded": [],
            "pending": True,
        }
    rules = campaign_rules(member.season)["economy"]
    automatic = rules.get("auto_development", DEFAULT_AUTO_DEVELOPMENT)
    state = produce_locked(member.season, now)[member.faction]
    state.construction_points += amount
    multiplier = faction_profile(member.season, member.faction)["threshold"]
    thresholds = [int(value * multiplier) for value in automatic["level_thresholds"]]
    target = sum(threshold <= state.construction_points for threshold in thresholds)
    previous = dict(state.levels)
    state.levels = {
        **state.levels,
        **{
            key: max(state.levels.get(key, 1), min(target, building["max_level"]))
            for key, building in rules["buildings"].items()
        },
    }
    tech_level = state.levels["technology"] - 1
    state.technology = {
        **state.technology,
        **{key: max(state.technology.get(key, 0), tech_level) for key in rules["technology_keys"]},
    }
    state.save(update_fields=["construction_points", "levels", "technology"])
    return {
        "construction_points": amount,
        "total_construction_points": state.construction_points,
        "levels": dict(state.levels),
        "upgraded": [key for key in rules["buildings"] if state.levels[key] > previous.get(key, 1)],
    }


def grant_activity_resources_locked(
    member: SeasonMember, resources: dict[str, int], now: datetime, *, construction: int = 0, refund: bool = False
) -> dict[str, int]:
    _require_transaction()
    if member.faction not in {"fiction", "history"}:
        member.refresh_from_db(fields=["pending_resources"])
        member.pending_resources = {
            **member.pending_resources,
            **{key: member.pending_resources.get(key, 0) + amount for key, amount in resources.items()},
        }
        member.save(update_fields=["pending_resources"])
        gains = dict(resources)
    else:
        state = produce_locked(member.season, now)[member.faction]
        caps = campaign_rules(member.season)["economy"]["resource_caps"]
        factor = faction_profile(member.season, member.faction)["capacity"]
        if resource_rules(member.season)["enabled"]:
            caps, factor = resource_caps(member.season, member.faction, state.levels), 1
        gains = {
            key: amount if refund else min(amount, max(0, int(caps[key] * factor) - state.resources.get(key, 0)))
            for key, amount in resources.items()
        }
        state.resources = {key: value + gains.get(key, 0) for key, value in state.resources.items()}
        if resource_rules(member.season)["enabled"]:
            from .resource_ledger import record_flow

            record_flow(state, "refund" if refund else "activity_reward", gains, now)
            state.save(update_fields=["resources", "economy_state"])
        else:
            state.save(update_fields=["resources"])
    if construction:
        grant_construction_amount_locked(member, construction, now)
    return gains


def upgrade_locked(member: SeasonMember, building_key: str, now: datetime) -> dict[str, Any]:
    raise GameError("阵营设施通过活动积累建设值自动升级，无需手动投入公共资源")


def grant_pve_locked(
    member: SeasonMember,
    node_key: str,
    won: bool,
    now: datetime,
    *,
    activity_key: str | None = None,
    event_key: str | None = None,
    material_reward: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from uuid import uuid4

    from .merit import grant_merit_locked

    _require_transaction()
    activity = campaign_rules(member.season)["economy"]["pve"].get(node_key)
    if activity is None:
        raise GameError("该据点没有配置PVE产出")
    frozen_merit = (material_reward or {}).get("merit_by_node", {}).get(node_key, {})
    merit = (
        frozen_merit.get("success", activity["merit"])
        if won
        else frozen_merit.get("failure", activity["failure_merit"])
    )
    effective = bool((material_reward or {}).get("effective_material_merit"))
    intel = None
    material_efficiency = Fraction(1)
    reward_multiplier = Fraction(1)
    if material_reward is not None:
        from .material_rewards import material_resources_from_snapshot

        reward_resources = material_resources_from_snapshot(material_reward, node_key, won)
    else:
        reward_resources = {key: amount if won else amount // 4 for key, amount in activity["resources"].items()}
    if won:
        from .activities import _intel_query
        from .development_rules import feature_rules

        if feature_rules(member.season, "activities")["enabled"]:
            intel = _intel_query(member, now).filter(node_key=node_key, payload__bonus_available=True).first()
            if intel:
                percent = int(intel.payload.get("bonus_percent", 0))
                reward_resources = {key: value * (100 + percent) // 100 for key, value in reward_resources.items()}
    if effective:
        multiplier = Fraction(str((material_reward or {}).get("multiplier", 1)))
        reward_multiplier = multiplier
        merit = int(merit * multiplier)
        if member.faction:
            state = produce_locked(member.season, now)[member.faction]
            caps = resource_caps(member.season, member.faction, state.levels)
            weights = resource_rules(member.season)["transport"]["resource_value_weights"]
            expected = sum(value * weights.get(key, 1) for key, value in reward_resources.items())
            accepted = sum(
                min(value, max(0, caps[key] - state.resources.get(key, 0))) * weights.get(key, 1)
                for key, value in reward_resources.items()
            )
            material_efficiency = Fraction(accepted, expected) if expected else Fraction(0)
            merit = int(Fraction(merit * accepted, expected)) if expected else 0
    receipt = grant_merit_locked(
        member, merit, "logistics", now, event_key=event_key or str(uuid4()), reason="讨伐胜利" if won else "讨伐参与"
    )
    if receipt["duplicate"]:
        return {**receipt, "resources": {}, "message": "该讨伐已结算"}
    if intel:
        intel.payload = {**intel.payload, "bonus_available": False}
        intel.save(update_fields=["payload"])
    gains = grant_activity_resources_locked(member, reward_resources, now)
    if effective:
        automatic = campaign_rules(member.season)["economy"].get("auto_development", DEFAULT_AUTO_DEVELOPMENT)
        base_points = automatic["activity_points"].get(activity_key or ("pve_win" if won else "pve_loss"), 0)
        construction_points = int(base_points * reward_multiplier * material_efficiency) if any(gains.values()) else 0
        construction = (
            grant_construction_amount_locked(member, construction_points, now)
            if construction_points
            else {"construction_points": 0}
        )
    else:
        construction = grant_construction_locked(member, activity_key or ("pve_win" if won else "pve_loss"), now)
    potential = normal_training_potential(gains, resource_rules(member.season)["training"]["normal_cost_per_10"])
    return {
        **receipt,
        **construction,
        "resources": gains,
        "normal_training_potential": potential,
        "message": f"讨伐{'成功' if won else '结束'}，获得 {receipt['merit']} 后勤贡献，阵营建设 +{construction['construction_points']}"
        + ("；物资暂存，分组后入库" if not member.faction else "；物资已进入阵营仓库"),
    }


def reserve_troops_locked(member: SeasonMember, loadout: dict[str, int], now: datetime) -> dict[str, int]:
    _require_transaction()
    if member.faction not in {"fiction", "history"}:
        raise GameError("阵营尚未分配")
    rules = campaign_rules(member.season)["economy"]
    if not isinstance(loadout, dict) or any(
        key not in rules["initial_troops"] or isinstance(amount, bool) or not isinstance(amount, int) or amount < 0
        for key, amount in loadout.items()
    ):
        raise GameError("赛季护院配置无效")
    loadout = {key: amount for key, amount in loadout.items() if amount}
    amount = sum(loadout.values())
    date = timezone.localdate(now)
    drawn = member.troops_drawn if member.troop_date == date else 0
    if drawn + amount > rules["daily_troop_quota"]:
        raise GameError("今日赛季领兵额度不足")
    state = produce_locked(member.season, now)[member.faction]
    if any(state.troops.get(key, 0) < count for key, count in loadout.items()):
        raise GameError("阵营兵库不足，请等待后勤训练")
    state.troops = {key: count - loadout.get(key, 0) for key, count in state.troops.items()}
    state.save(update_fields=["troops"])
    member.troop_date, member.troops_drawn = date, drawn + amount
    member.save(update_fields=["troop_date", "troops_drawn"])
    return loadout


def return_troops_locked(member: SeasonMember, loadout: dict[str, int], now: datetime | None = None) -> None:
    _require_transaction()
    rules = campaign_rules(member.season)["economy"]
    if not isinstance(loadout, dict) or any(
        key not in rules["initial_troops"] or isinstance(count, bool) or not isinstance(count, int) or count < 0
        for key, count in loadout.items()
    ):
        raise GameError("归还护院数据无效")
    # 先按返程前的空缺推进训练，避免先归还导致离线期间应产出的护院被吞掉。
    state = produce_locked(member.season, now or timezone.now())[member.faction]
    # 返程期间兵库可能已补满；幸存护院仍全额归还，超额时暂停该兵种训练。
    state.troops = {key: state.troops.get(key, 0) + loadout.get(key, 0) for key in rules["troop_caps"]}
    state.save(update_fields=["troops"])
