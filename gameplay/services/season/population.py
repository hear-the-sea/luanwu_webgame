"""每日冻结活跃规模；规模变化不追溯产出、不改变进行中的战斗。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q

from gameplay.models import SeasonAction, SeasonExpedition, SeasonFaction

from .development_rules import feature_rules
from .resource_rules import resource_rules


def effective_activity_filter() -> Q:
    """唯一贡献凭证计入有效参与；兼容没有凭证关联的旧活动流水。"""
    positive = Q(result__requested_merit__gt=0) | Q(result__merit__gt=0) | Q(result__construction_points__gt=0)
    source = Q(kind="merit_credit") | Q(
        kind__in=("combat", "logistics", "exploration"), result__merit_credit_id__isnull=True
    )
    return positive & source


def population_profile(season: Any) -> dict[str, Any]:
    return dict(season.population) if feature_rules(season, "population")["enabled"] else {}


def faction_profile(season: Any, faction: str) -> dict[str, Any]:
    return {
        "count": 1,
        "rate": 1,
        "capacity": 1,
        "threshold": 1,
        "name": "标准发展",
        **population_profile(season).get("factions", {}).get(faction, {}),
    }


def npc_profile(season: Any, node_key: str) -> dict[str, int]:
    from .campaign_rules import campaign_rules
    from .fortifications import fortification_rules

    fort = fortification_rules(season)
    multiplier = fort["sites"].get(node_key, {}).get("wall_multiplier", 1) if fort["enabled"] else 1
    rules = campaign_rules(season)
    site = rules["sites"].get(node_key, {})
    mode = population_profile(season).get("mode", "standard")
    ratio = {"pioneer": 0.34, "skirmish": 0.67}.get(mode, 1)
    level_ratio = {"pioneer": 0.6, "skirmish": 0.85}.get(mode, 1)
    from .hitpoints import hitpoint_rules

    hp = hitpoint_rules(season)
    durability = max(1, round(site.get("durability", 100) * ratio * multiplier))
    if hp["enabled"]:
        durability = hp["gate_hp"] if node_key in fort["sites"] else site.get("durability", 100) * hp["hp_per_work"]
    return {
        "guards": max(1, round(site.get("guards", 1) * ratio)),
        "durability": durability,
        "level": max(1, round(rules["expeditions"]["enemy_levels"].get(node_key, 3) * level_ratio)),
    }


def refresh_population_locked(season: Any, now: datetime, *, force: bool = False) -> dict[str, Any]:
    from .economy import produce_locked
    from .warfare import ensure_sites_locked

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("人口档位更新须持有赛季锁")
    config = feature_rules(season, "population")
    if not config["enabled"]:
        return {}
    since = now - timedelta(days=config["active_days"])
    active_ids = SeasonAction.objects.filter(
        effective_activity_filter(),
        member__season=season,
        created_at__gte=since,
    ).values("member_id")
    deployed_ids = SeasonExpedition.objects.filter(
        member__season=season,
        launched_at__gte=since,
        status__in=("gathering", "outbound", "stationed", "transporting", "returning"),
    ).values("member_id")
    resource_config = resource_rules(season)
    participant_filter = (
        Q(pk__in=active_ids) if resource_config["enabled"] else Q(pk__in=active_ids) | Q(pk__in=deployed_ids)
    )
    members = season.members.filter(participant_filter).distinct()
    counts = {faction: members.filter(faction=faction).count() for faction in ("fiction", "history")}
    previous = population_profile(season)
    new_faction_started = any(counts[f] and not previous.get("factions", {}).get(f, {}).get("count", 0) for f in counts)
    population_rules = resource_config.get("population", {})
    growth_refresh = resource_config["enabled"] and population_rules.get("growth_refresh_immediate", False)
    increased = any(counts[f] > previous.get("factions", {}).get(f, {}).get("count", 0) for f in counts)
    daily_due = bool(
        season.population_updated_at
        and now >= season.population_updated_at + timedelta(seconds=config["refresh_seconds"])
    )
    if (
        not force
        and season.population_updated_at
        and now < season.population_updated_at + timedelta(seconds=config["refresh_seconds"])
        and not new_faction_started
        and not (growth_refresh and increased)
    ):
        return previous
    if season.population_updated_at:
        from .warfare import score_sites_locked

        produce_locked(season, now)
        score_sites_locked(season, now)
    factions: dict[str, dict[str, Any]] = {}
    for faction, count in counts.items():
        tier = next(row for row in config["tiers"] if count <= row["maximum"])
        factions[faction] = {**tier, "count": count, "rate": tier["rate"] if count else 0}
        if resource_config["enabled"]:
            old_profile = previous.get("factions", {}).get(faction, {})
            old_count = old_profile.get("economic_count", old_profile.get("count", 0))
            if not count:
                economic_count = 0.0
            elif not old_count or "economic_count" not in old_profile:
                economic_count = float(count)
            elif count > old_count and (daily_due or (growth_refresh and count > old_profile.get("count", 0))):
                weight = population_rules.get("growth_previous_weight", population_rules["smoothing_previous_weight"])
                economic_count = round(old_count * weight + count * (1 - weight), 6)
            elif daily_due:
                weight = resource_config["population"]["smoothing_previous_weight"]
                economic_count = round(old_count * weight + count * (1 - weight), 6)
            else:
                economic_count = old_count
            factions[faction]["economic_count"] = economic_count
    least = min(counts.values())
    mode = "pioneer" if least == 0 else "skirmish" if least < 5 else "standard"
    from .campaign_rules import campaign_rules
    from .warfare import city_open

    sites = campaign_rules(season)["sites"]
    candidates = sorted(key for key in sites if key != "river:town" or city_open(season, now))
    hotspot = candidates[now.date().toordinal() % len(candidates)] if candidates and mode != "standard" else ""
    season.population = {
        "factions": factions,
        "hotspot": hotspot,
        "hotspot_name": sites.get(hotspot, {}).get("name", ""),
        "hotspot_multiplier": config.get("hotspot_multiplier", 2) if hotspot else 1,
        "mode": mode,
        "name": {"pioneer": "开拓战局", "skirmish": "小规模争夺", "standard": "军团战争"}[mode],
        "updated_at": now.isoformat(),
    }
    season.population_updated_at = now
    season.save(update_fields=["population", "population_updated_at"])
    for faction in counts:
        if counts[faction] and not previous.get("factions", {}).get(faction, {}).get("count", 0):
            for row in SeasonFaction.objects.filter(season=season, faction=faction):
                if row.produced_at < now:
                    row.produced_at = now
                    row.save(update_fields=["produced_at"])
    engaged_sites = set(
        SeasonExpedition.objects.filter(
            member__season=season, kind__in=("attack", "siege"), status__in=("gathering", "outbound")
        ).values_list("node_key", flat=True)
    )
    for site in ensure_sites_locked(season, now).values():
        old = site.npc_profile or campaign_rules(season)["sites"][site.key]
        if (
            site.key not in engaged_sites
            and not site.owner
            and not site.attacker
            and site.guards == old["guards"]
            and site.durability == old["durability"]
        ):
            profile = npc_profile(season, site.key)
            site.guards, site.durability, site.npc_profile = profile["guards"], profile["durability"], profile
            site.save(update_fields=["guards", "durability", "npc_profile"])
    return season.population


def recommended_focus(season: Any, now: datetime) -> dict[str, Any]:
    """小服聚集玩家的只读推荐；既有积分热点与倍率保持不变。"""
    if not season or season.closed:
        return {}
    from .warfare import campaign_open

    if not campaign_open(season, now):
        return {}
    config = feature_rules(season, "population")
    if not config["enabled"]:
        return {}
    profile = population_profile(season)
    if profile.get("hotspot"):
        return {
            "key": profile["hotspot"],
            "name": profile.get("hotspot_name", ""),
            "scoring_bonus": profile.get("hotspot_multiplier", 1) > 1,
        }
    maximum = config.get("recommendation_max_active", 0)
    counts = profile.get("factions", {})
    active = sum(max(0, int(counts.get(faction, {}).get("count", 0))) for faction in ("fiction", "history"))
    if not maximum or not active or active > maximum:
        return {}
    from zoneinfo import ZoneInfo

    from django.utils import timezone

    from .campaign_rules import campaign_rules
    from .warfare import campaign_nodes, city_open

    sites = campaign_rules(season)["sites"]
    nodes = campaign_nodes(season)
    day = int((now - season.starts_at).total_seconds() // 86400) + 1
    candidates = sorted(
        key for key in sites if day >= nodes[key]["unlock_day"] and (key != "river:town" or city_open(season, now))
    )
    if not candidates:
        return {}
    today = timezone.localdate(now, timezone=ZoneInfo("Asia/Shanghai"))
    key = candidates[today.toordinal() % len(candidates)]
    return {"key": key, "name": sites[key]["name"], "scoring_bonus": False}


def victory_qualified(season: Any) -> bool:
    config = feature_rules(season, "population")
    if not config["enabled"]:
        return True
    from .contests import victory_contested_days

    return all(days >= config["victory_min_days"] for days in victory_contested_days(season).values())
