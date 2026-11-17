"""赛季页面查询；不在 GET 路径生产资源、派兵或推进结算。"""

from __future__ import annotations

import copy
import json
import math
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db.models import Count, Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from battle.troops import load_troop_templates
from core.game_data.technology import load_technology_templates
from core.utils.time_scale import scale_duration
from gameplay.models import ItemTemplate, Season, SeasonMember
from gameplay.models.season import SeasonExpedition, SeasonFaction
from gameplay.services.season.campaign_rules import DEFAULT_AUTO_DEVELOPMENT, campaign_rules, coalition_rules
from gameplay.services.season.catalog import (
    available_summon_templates,
    has_summon_catalog,
    reward_catalog,
    shop_catalog,
)
from gameplay.services.season.contests import victory_contested_days
from gameplay.services.season.development_rules import feature_rules
from gameplay.services.season.fortifications import fortification_rules, needs_repair, tower_state
from gameplay.services.season.garrison_merit import garrison_merit_preview
from gameplay.services.season.market import market_offers, market_return_percent
from gameplay.services.season.market_stock import market_stock_context
from gameplay.services.season.material_rewards import material_budget_summary
from gameplay.services.season.merit import daily_merit_summary
from gameplay.services.season.playability_rules import playability_rules
from gameplay.services.season.population import faction_profile, population_profile, recommended_focus
from gameplay.services.season.quotas import daily_dispatches_used, guest_dispatch_counts
from gameplay.services.season.resource_rules import (
    daily_resource_output,
    economic_population,
    normal_training_potential,
    resource_caps,
    resource_rules,
    training_summary,
    training_targets,
)
from gameplay.services.season.rules import FACTION_NAMES, NODES, season_state
from gameplay.services.season.simplification import simplification_rules
from gameplay.services.season.site_benefits import gate_march_bonus
from gameplay.services.season.technology import CAVALRY_TECH_NAMES, season_technology_levels
from gameplay.services.season.warfare import campaign_nodes, campaign_open, city_open
from gameplay.services.season.weekly_campaign import weekly_campaign_context
from guests.guest_combat_stats import resolve_guest_combat_stats
from guests.models import Guest, GuestRarity, GuestStatus

from .season_archive import previous_season_reward
from .season_collection import collection_progress
from .season_guidance import campaign_guidance, reward_progress
from .season_map_info import decorate_map_nodes
from .season_visuals import expedition_portraits, rally_slots

MODE_NAMES = {
    "pve": "资源收集",
    "explore": "探索情报",
    "npc_intercept": "讨伐NPC辎重",
    "transport": "运输战备",
    "intercept": "拦截敌方运输",
    "escort": "随队护送",
    "attack": "攻坚驻军",
    "garrison": "驻防增援",
    "siege": "破城与争夺控制",
    "repair": "维修城防",
}
RESOURCE_NAMES = {"grain": "粮草", "wood": "木材", "iron": "铁料"}


def _duration(seconds: int) -> str:
    return f"{seconds // 60} 分钟" if seconds % 60 == 0 else f"{seconds} 秒"


def _describe_items(items: dict[str, int], names: dict[str, str]) -> str:
    return "、".join(f"{names.get(key, key)} × {quantity}" for key, quantity in items.items())


def _garrison_context(runs: list[SeasonExpedition], player_guest_limit: int) -> dict[str, Any]:
    players = []
    for run in runs:
        defeated = set(run.result.get("defeated_ids", []))
        guests = sorted(
            (
                row
                for row in run.lineup.get("guests", [])
                if row.get("current_hp", 0) > 0 and row.get("guest_id") not in defeated
            ),
            key=lambda row: (-(row["attack"] + row["defense"] + row["current_hp"]), row["guest_id"]),
        )[:player_guest_limit]
        if guests:
            players.append(
                {
                    "name": run.member.manor.display_name,
                    "guest_names": "、".join(row.get("display_name", "门客") for row in guests),
                    "guest_count": len(guests),
                    "troops": sum(run.troops.values()),
                    "fatigue_stacks": run.result.get("fatigue_stacks", 0),
                }
            )
    return {
        "member_count": len(players),
        "total_troops": sum(row["troops"] for row in players),
        "guest_count": sum(row["guest_count"] for row in players),
        "players": players,
    }


def get_season_context(manor: Any, season_id: int | None = None) -> dict[str, Any]:
    now = timezone.now()
    seasons = Season.objects.all()
    season = seasons.filter(pk=season_id).first() if season_id else seasons.filter(starts_at__lte=now).first()
    if season is None and season_id is None:
        season = seasons.first()
    member = SeasonMember.objects.filter(season=season, manor=manor).first() if season else None
    campaign = campaign_rules(season)
    coalition = coalition_rules(season)
    activity_rules = feature_rules(season, "activities")
    resource_economy = resource_rules(season)
    collection_config = resource_economy.get("pve", {}).get("collection_waves", {})
    collection_enabled = bool(resource_economy["enabled"] and collection_config.get("enabled"))
    collection_rules = (
        {
            **collection_config,
            "enabled": True,
            "interval_label": _duration(scale_duration(collection_config["interval_seconds"], minimum=1)),
            "maximum_percent": collection_config["max_waves"] * collection_config["win_percent"],
        }
        if collection_enabled
        else {"enabled": False}
    )
    simplified = simplification_rules(season)
    playability = playability_rules(season)
    fortifications = fortification_rules(season)
    from gameplay.services.season.hitpoints import hitpoint_rules, repair_ready_at

    hitpoints = hitpoint_rules(season)
    from gameplay.services.season.occupation import occupation_rules
    from gameplay.services.season.recovery import recovery_status, resource_reserves

    occupation = occupation_rules(season)
    population = population_profile(season) if season else {}
    scale = (
        faction_profile(season, member.faction)
        if season and member and member.faction
        else {"rate": 1, "capacity": 1, "threshold": 1}
    )
    summary = (
        daily_merit_summary(member, now)
        if member
        else {"earned": 0, "cap": 300, "remaining": 300, "combat": 0, "logistics": 0}
    )
    intels = []
    exploration_remaining = (
        max(
            0,
            activity_rules["explore"]["daily_limit"]
            - member.intel_records.filter(created_at__date=timezone.localdate(now)).count(),
        )
        if member and activity_rules["enabled"]
        else 0
    )
    transport_targets = []
    enemy_transports = []
    friendly_transports = []
    convoy_participants: dict[int, dict[str, int]] = {}
    if season and member and activity_rules["enabled"]:
        from gameplay.services.season.activities import _intel_query

        intels = list(_intel_query(member, now).values("node_key", "payload", "expires_at"))
        transport_targets = (
            [
                {"key": site.key, "name": campaign["sites"][site.key]["name"], "grain": site.supplies.get("grain", 0)}
                for site in season.sites.filter(owner=member.faction)
            ]
            if member.faction
            else []
        )
        if resource_economy["enabled"]:
            from gameplay.services.season.logistics import site_demand

            transport_targets = (
                [
                    {
                        "key": site.key,
                        "name": campaign["sites"][site.key]["name"],
                        "demand": needs,
                        "demand_label": " · ".join(
                            f"{RESOURCE_NAMES[key]}{amount}" for key, amount in needs.items() if amount
                        ),
                    }
                    for site in season.sites.filter(owner=member.faction)
                    if any((needs := site_demand(season, site, now)).values())
                ]
                if member.faction
                else []
            )
        enemy_transports = (
            list(
                SeasonExpedition.objects.filter(
                    member__season=season, kind="transport", status="transporting", intercepted=False, due_at__gt=now
                )
                .exclude(member__faction__in=("", member.faction))
                .select_related("member__manor")
            )
            if member.faction
            else []
        )
        if member.faction and campaign["expeditions"].get("convoy_encounters_enabled", False):
            friendly_transports = list(
                SeasonExpedition.objects.filter(
                    member__season=season,
                    member__faction=member.faction,
                    kind="transport",
                    status="transporting",
                    due_at__gt=now,
                    result__activity__encounter_version=1,
                ).select_related("member__manor")
            )
            convoy_ids = [run.pk for run in [*enemy_transports, *friendly_transports]]
            if convoy_ids:
                for convoy_row in SeasonExpedition.objects.filter(
                    member__season=season,
                    kind__in=("intercept", "escort"),
                    status__in=("outbound", "transporting"),
                    result__activity__target_run_id__in=convoy_ids,
                ).values("kind", "result"):
                    convoy_id = convoy_row["result"].get("activity", {}).get("target_run_id")
                    counts = convoy_participants.setdefault(convoy_id, {"intercept": 0, "escort": 0})
                    counts[convoy_row["kind"]] += 1
    campaign["duration_days"] = int((season.ends_at - season.starts_at).total_seconds() // 86400) if season else 90
    economy, expedition_rules = campaign["economy"], campaign["expeditions"]
    state = (
        season_state(season, now)
        if season
        else {"day": 0, "week": 1, "phase": "筹备中", "combat_open": False, "days_left": 0}
    )
    if season:
        state["combat_open"] = campaign_open(season, now)
        if season.starts_at <= now < season.ends_at:
            if not state["combat_open"]:
                state["phase"] = "赛季尾声"
            else:
                state["phase"] = "阵营对抗"
    registration_ends_at = season.starts_at + timedelta(days=campaign["registration"]["days"]) if season else None
    registration_open = bool(season and registration_ends_at and season.starts_at <= now < registration_ends_at)
    if registration_open:
        state["phase"] = "集中报名"
    faction = member.faction if member and member.faction else "fiction"
    assigned = bool(member and member.faction)
    faction_state = (
        SeasonFaction.objects.filter(season=season, faction=faction).first() if season and assigned else None
    )
    faction_states = {row.faction: row for row in season.faction_states.all()} if season else {}
    resources = dict(faction_state.resources) if faction_state else {}
    levels = dict(faction_state.levels) if faction_state else {}
    from gameplay.services.season.logistics import allocate_cargo, convoy_capacity

    transport_capacity = (
        convoy_capacity(season, member.faction)
        if season and member and assigned
        else resource_economy["transport"]["cargo_capacity"]
    )
    if season and assigned and resource_economy["enabled"]:
        from gameplay.services.season.activities import estimate_delivery_merit

        for target in transport_targets:
            available = {key: min(amount, resources.get(key, 0)) for key, amount in target["demand"].items()}
            target["estimated_cargo"] = allocate_cargo(available, transport_capacity)
            target["expected_merit"] = estimate_delivery_merit(season, target["estimated_cargo"], transport_capacity)
    pve_storage_caps = (
        resource_caps(season, faction, levels) if season and assigned and resource_economy["enabled"] else {}
    )
    troops = dict(faction_state.troops) if faction_state else {}
    daily_quota = economy["daily_troop_quota"]
    drawn = member.troops_drawn if member and member.troop_date == timezone.localdate(now) else 0
    troops_remaining = max(0, daily_quota - drawn) if assigned else 0
    active_runs = (
        list(member.expeditions.exclude(status="completed").select_related("rally").order_by("launched_at", "id"))
        if member
        else []
    )
    active_count = len(active_runs)
    max_active = expedition_rules["max_active"]
    daily_dispatches = daily_dispatches_used(member, timezone.localdate(now)) if member else 0
    dispatches_remaining = max(0, expedition_rules["daily_actions"] - daily_dispatches)
    can_dispatch = bool(
        member
        and (assigned or activity_rules["enabled"])
        and state["combat_open"]
        and active_count < max_active
        and dispatches_remaining
    )
    attack_busy = coalition["enabled"] and any(run.kind not in {"garrison", "escort"} for run in active_runs)
    garrison_busy = coalition["enabled"] and any(run.kind in {"garrison", "escort"} for run in active_runs)
    rallies: list[dict[str, Any]] = []
    rally_models = {}
    if season and member and assigned and coalition["enabled"]:
        active_rallies = (
            season.rallies.filter(leader__faction=faction, status__in=["gathering", "marching"])
            .select_related("leader__manor", "leader__manor__user")
            .prefetch_related(
                Prefetch(
                    "expeditions",
                    queryset=SeasonExpedition.objects.filter(status__in=["gathering", "outbound", "stationed"])
                    .select_related("member__manor", "member__manor__user")
                    .order_by("launched_at", "pk"),
                    to_attr="participating_runs",
                )
            )
            .order_by("gathers_until", "pk")
        )
        for rally_model in active_rallies:
            rally_models[rally_model.pk] = rally_model
            joined = any(run.member_id == member.pk for run in rally_model.participating_runs)
            member_count = len({run.member_id for run in rally_model.participating_runs})
            gathering = rally_model.status == "gathering" and rally_model.gathers_until > now
            deadline = rally_model.gathers_until if rally_model.status == "gathering" else rally_model.due_at
            rallies.append(
                {
                    "id": rally_model.pk,
                    "node_key": rally_model.node_key,
                    "kind": rally_model.kind,
                    "kind_label": (
                        "集结攻城"
                        if rally_model.result.get("unified_siege")
                        else "集结攻坚" if rally_model.kind == "attack" else "集结破城"
                    ),
                    "status_label": (
                        "车轮战中" if rally_model.result.get("wave", 0) else rally_model.get_status_display()
                    ),
                    "leader_name": rally_model.leader.manor.display_name,
                    "member_count": member_count,
                    "carried_troops": sum(
                        sum(run.troops.values()) for run in rally_model.participating_runs if run.status == "gathering"
                    ),
                    "target_members": rally_model.result.get("target_members", coalition["max_members"]),
                    "joined": joined,
                    "is_gathering": gathering,
                    "can_join": bool(
                        gathering
                        and not joined
                        and can_dispatch
                        and not attack_busy
                        and member_count < coalition["max_members"]
                    ),
                    "can_cancel": bool(gathering and rally_model.leader_id == member.pk),
                    "countdown_at": deadline,
                    "remaining_seconds": max(0, math.ceil((deadline - now).total_seconds())) if deadline else 0,
                    "wave": int(rally_model.result.get("wave", 0)),
                    "message": rally_model.result.get("message", ""),
                    "deadline_label": (
                        "距离集结出发"
                        if rally_model.status == "gathering"
                        else "距离下一波" if rally_model.result.get("wave", 0) else "距离抵达"
                    ),
                }
            )
    sites = {site.key: site for site in season.sites.all()} if season else {}
    garrisons: dict[str, dict[str, Any]] = {}
    if season and coalition["enabled"]:
        stationed = list(
            SeasonExpedition.objects.filter(
                member__season=season, kind="garrison", status="stationed", due_at__gt=now
            ).select_related("member__manor", "member__manor__user")
        )
        known_sites = {row["payload"].get("target_site_key") for row in intels}
        for key, held_site in sites.items():
            defending = [run for run in stationed if run.node_key == key and run.member.faction == held_site.owner]
            if (
                activity_rules["enabled"]
                and held_site.owner
                and held_site.owner != (member.faction if member else "")
                and key not in known_sites
            ):
                # 不能只隐藏名单：数量也会通过地图 JSON 和战局详情泄漏。
                garrisons[key] = {
                    "member_count": None,
                    "total_troops": None,
                    "guest_count": None,
                    "players": [],
                    "hidden": True,
                }
            else:
                garrisons[key] = _garrison_context(defending, coalition["commander_guest_limit"])
    node_rules = campaign_nodes(season) if season else NODES
    definitions = load_troop_templates()
    special_rules = economy.get("special_troops", {})
    special_production_options = []
    for troop_key, spec in special_rules.items():
        special_site = sites.get(spec["site_key"])
        owner = special_site.owner if special_site else ""
        owner_state = faction_states.get(owner)
        producing = bool(
            owner_state
            and state["combat_open"]
            and owner_state.produced_at <= now
            and special_site
            and special_site.protected_until
        )
        special_production_options.append(
            {
                "key": troop_key,
                "name": definitions.get(troop_key, {}).get("label", troop_key),
                "site_key": spec["site_key"],
                "site_name": node_rules.get(spec["site_key"], {}).get("name", spec["site_key"]),
                "owner": owner,
                "owner_label": FACTION_NAMES.get(owner, "尚无阵营占领"),
                "rate": spec["rate"] * (owner_state.levels.get("training", 1) if owner_state else 1),
                "daily_rate": (
                    round(
                        economic_population(season, owner)
                        * resource_economy["training"]["cavalry_daily_capacity_per_active"],
                        2,
                    )
                    if resource_economy["enabled"]
                    else None
                ),
                "producing": producing,
                "available": troops.get(troop_key, 0),
            }
        )
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    clock_label = "北京时间" if expedition_rules["timezone"] == "Asia/Shanghai" else expedition_rules["timezone"]
    schedule_label = (
        "、".join(weekdays[index] for index in expedition_rules["city_weekdays"]) + f"全天开放（{clock_label}）"
    )
    material_summary = material_budget_summary(member, now) if member else {}
    maps = copy.deepcopy(
        json.loads((Path(settings.BASE_DIR) / "data/images/pvp/layers-v1/manifest.json").read_text(encoding="utf-8"))[
            "maps"
        ]
    )
    for region in maps:
        region["background"] = "season-assets/" + region["background"].removesuffix(".png") + ".webp"
        for node in region["nodes"]:
            key = region["key"] + ":" + node["id"]
            rule = node_rules[key]
            node.update(rule, key=key, unlocked=bool(season and state["day"] >= rule["unlock_day"]))
            node["image"] = "season-assets/" + node["image"].removesuffix(".png") + ".webp"
            site = sites.get(key)
            node["site"] = (
                {
                    "guards": site.guards,
                    "durability": site.durability,
                    "control": site.control,
                    "max_durability": site.npc_profile.get("durability", campaign["sites"][key]["durability"]),
                    "control_required": campaign["sites"][key]["control_required"],
                    "tower": tower_state(site),
                    "repair_ready_at": repair_ready_at(site),
                    "recovery": (
                        recovery_status(site, now) if member and member.faction and site.owner == member.faction else {}
                    ),
                    "capture_points": campaign["sites"][key]["score_per_tick"] * occupation["capture_score_ticks"],
                    "points_per_tick": campaign["sites"][key]["score_per_tick"]
                    * (population.get("hotspot_multiplier", 1) if population.get("hotspot") == key else 1),
                    "other_day_points_per_tick": (
                        campaign["sites"][key]["weekday_score_per_tick"]
                        * (population.get("hotspot_multiplier", 1) if population.get("hotspot") == key else 1)
                        if "weekday_score_per_tick" in campaign["sites"][key]
                        else None
                    ),
                    "protected_until": (
                        site.protected_until if site.protected_until and site.protected_until > now else None
                    ),
                }
                if site
                else None
            )
            node["owner_label"] = FACTION_NAMES.get(site.owner, "中立守军") if site else ""
            node["occupation_class"] = site.owner or "neutral" if site else ""
            node["occupation_label"] = (
                f"{FACTION_NAMES[site.owner]}占领" if site and site.owner else "中立 · 未占领" if site else ""
            )
            allowed = list(rule.get("modes", []))
            if simplified["enabled"] and key == "river:docks":
                allowed = [mode for mode in allowed if mode != "transport"]
            if site and member and member.faction:
                if site.owner == member.faction:
                    allowed = [mode for mode in allowed if mode not in {"attack", "siege"}]
                    if simplified["enabled"] and not needs_repair(site):
                        allowed = [mode for mode in allowed if mode != "repair"]
                else:
                    allowed = [mode for mode in allowed if mode not in {"garrison", "repair"}]
                    if site.guards and not simplified["enabled"]:
                        allowed = [mode for mode in allowed if mode != "siege"]
                    if site.protected_until and site.protected_until > now:
                        allowed = [mode for mode in allowed if mode not in {"attack", "siege"}]
                    if simplified["enabled"] and not needs_repair(site):
                        allowed = [mode for mode in allowed if mode != "repair"]
            if member and not member.faction:
                allowed = [mode for mode in allowed if mode in {"pve", "explore", "npc_intercept"}]
            node["transport_targets"] = transport_targets
            gateway = sites.get("river:docks" if key == "river:warehouse" else key)
            node["transport_route_blocked"] = bool(
                gateway and member and member.faction and gateway.owner not in {"", member.faction}
            )
            node["enemy_transports"] = [
                {
                    "id": run.pk,
                    "name": run.member.manor.display_name,
                    "count": sum(run.cargo.values()),
                    "remaining_seconds": max(0, int((run.due_at - now).total_seconds())),
                    "remaining_label": _duration(max(0, int((run.due_at - now).total_seconds()))),
                    "encounter": run.result.get("activity", {}).get("encounter_version") == 1,
                    "attackers": convoy_participants.get(run.pk, {}).get("intercept", 0),
                    "escorts": 1 + convoy_participants.get(run.pk, {}).get("escort", 0),
                }
                for run in enemy_transports
                if run.node_key == key or (key == "river:docks" and run.node_key == "river:warehouse")
            ]
            node["friendly_transports"] = [
                {
                    "id": run.pk,
                    "name": run.member.manor.display_name,
                    "count": sum(run.cargo.values()),
                    "remaining_label": _duration(max(0, int((run.due_at - now).total_seconds()))),
                    "attackers": convoy_participants.get(run.pk, {}).get("intercept", 0),
                    "escorts": 1 + convoy_participants.get(run.pk, {}).get("escort", 0),
                }
                for run in friendly_transports
                if member and run.member_id != member.pk and key == "river:docks"
            ]
            node["has_intel"] = any(row["node_key"] == key for row in intels)
            known_sites = {row["payload"].get("target_site_key") for row in intels}
            node["scout_targets"] = [
                {"key": site_key, "name": site_config["name"], "known": site_key in known_sites}
                for site_key, site_config in campaign["sites"].items()
            ]
            # 地图数据也会序列化到页面，库存须与己方据点的展示权限一致。
            node["site_supplies"] = (
                dict(site.supplies) if site and assigned and member and site.owner == member.faction else {}
            )
            if site and hitpoints["enabled"]:
                ready = repair_ready_at(site)
                if ready and now < ready:
                    allowed = [mode for mode in allowed if mode != "repair"]
            node["modes"] = [
                {
                    "key": mode,
                    "name": MODE_NAMES[mode],
                    "travel_label": _duration(
                        expedition_rules.get("mode_travel_seconds", {}).get(
                            mode, expedition_rules["travel_seconds"][key]
                        )
                    ),
                    "disabled": activity_rules["enabled"]
                    and (
                        (mode == "intercept" and not node["enemy_transports"])
                        or (mode == "escort" and not node["friendly_transports"])
                        or (
                            mode == "transport"
                            and resource_economy["enabled"]
                            and (not transport_targets or node["transport_route_blocked"])
                        )
                        or (
                            mode == "explore"
                            and (not exploration_remaining or all(row["known"] for row in node["scout_targets"]))
                        )
                    ),
                }
                for mode in allowed
            ]
            node["rally_modes"] = [
                {
                    "key": mode,
                    "name": "集结攻城" if simplified["enabled"] else "集结攻坚" if mode == "attack" else "集结破城",
                }
                for mode in allowed
                if coalition["enabled"] and not attack_busy and mode in {"attack", "siege"}
            ]
            node["personal_modes"] = [
                row for row in node["modes"] if not coalition["enabled"] or row["key"] not in {"attack", "siege"}
            ]
            if coalition["enabled"]:
                node["personal_modes"] = [
                    row
                    for row in node["personal_modes"]
                    if not (garrison_busy if row["key"] in {"garrison", "escort"} else attack_busy)
                ]
            node["rallies"] = [row for row in rallies if row["node_key"] == key]
            node["garrison"] = garrisons.get(key)
            npc = campaign.get("npc_encounters") or {}
            if collection_enabled and npc.get("enabled"):
                spec = npc.get("collection", {})
                base = spec.get("base_levels", {}).get(key)
                if base is not None:
                    rarity_names = {"green": "绿", "blue": "蓝", "purple": "紫", "orange": "橙", "red": "红"}
                    node["collection_enemies"] = [
                        {
                            "count": row["count"],
                            "rarity": rarity_names.get(row["rarity"], row["rarity"]),
                            "level": base + row["level_bonus"],
                        }
                        for row in spec["waves"][: collection_config["max_waves"]]
                    ]
            pve_amounts = economy["pve"].get(key, {}).get("resources", {})
            if resource_economy["enabled"]:
                kind = {
                    "mountain:inn": "grain_run",
                    "forest:granary": "grain_run",
                    "forest:lodge": "wood_run",
                    "mountain:ruins": "iron_run",
                    "river:docks": "mixed_run",
                }.get(key)
                pve_amounts = resource_economy["pve"].get(kind, {}) if kind else {}
            multiplier = material_summary.get("next_multiplier", 1)
            node["pve_reward_label"] = (
                "全额收益" if multiplier == 1 else "半额收益" if multiplier > 0 else "今日物资奖励已领完"
            )
            node["pve_reward_count"] = (
                material_summary.get("full_remaining", 0)
                if multiplier == 1
                else material_summary.get("half_remaining", 0)
            )
            node["pve_preview"] = [
                {
                    "key": resource,
                    "name": RESOURCE_NAMES[resource],
                    "amount": (
                        int(
                            Fraction(amount)
                            * Fraction(str(multiplier))
                            * Fraction(collection_rules["maximum_percent"], 100)
                        )
                        if collection_enabled
                        else int(amount * multiplier)
                    ),
                }
                for resource, amount in pve_amounts.items()
            ]
            intended = {
                resource: (
                    int(
                        Fraction(amount)
                        * Fraction(str(multiplier))
                        * Fraction(collection_rules["maximum_percent"], 100)
                    )
                    if collection_enabled
                    else int(amount * multiplier)
                )
                for resource, amount in pve_amounts.items()
                if int(amount * multiplier) > 0
            }
            free = {
                resource: max(0, pve_storage_caps.get(resource, 0) - resources.get(resource, 0))
                for resource in intended
            }
            node["pve_material_available"] = bool(intended)
            node["pve_storage_limited"] = bool(
                pve_storage_caps and any(amount > free[resource] for resource, amount in intended.items())
            )
            node["pve_storage_full"] = bool(pve_storage_caps and intended and not any(free.values()))
            node["pve_resources"] = [
                {"key": resource, "name": RESOURCE_NAMES[resource], "amount": amount}
                for resource, amount in pve_amounts.items()
            ]
            node["training_potential"] = (
                normal_training_potential(pve_amounts, resource_economy["training"]["normal_cost_per_10"])
                if resource_economy["enabled"]
                else 0
            )
            node["travel_label"] = _duration(expedition_rules["travel_seconds"][key])
            node["schedule_closed"] = bool(season and key == "river:town" and not city_open(season, now))
            node["schedule_message"] = "古城仅在" + schedule_label + "，当前可查看战况。"
            node["special_production"] = [row for row in special_production_options if row["site_key"] == key]
    node_images = {node["key"]: node["image"] for region in maps for node in region["nodes"]}
    visual_runs = [*active_runs, *(run for rally in rally_models.values() for run in rally.participating_runs)]
    visual_ids = {row.get("guest_id") for run in visual_runs for row in run.lineup.get("guests", [])}
    visual_manors = {run.member.manor_id for run in visual_runs}
    portrait_guests = (
        {
            guest.pk: guest
            for guest in Guest.objects.filter(pk__in=visual_ids, manor_id__in=visual_manors).select_related("template")
        }
        if visual_ids
        else {}
    )
    for rally in rallies:
        model = rally_models[rally["id"]]
        rally["image"] = node_images.get(rally["node_key"], "")
        rally["slots"] = rally_slots(model, model.participating_runs, portrait_guests, rally["target_members"])
        rule = node_rules[rally["node_key"]]
        rally_site = sites.get(rally["node_key"])
        destination_open = bool(
            season is not None
            and state["day"] >= rule["unlock_day"]
            and (rally["node_key"] != "river:town" or city_open(season, now))
            and rally_site
            and rally_site.owner != faction
            and (not rally_site.protected_until or rally_site.protected_until <= now)
            and (rally["kind"] != "siege" or not rally_site.guards or simplified["enabled"])
        )
        rally["can_join"] = bool(rally["can_join"] and destination_open)
        rally["node_name"] = rule["name"]
        rally["node"] = {
            "key": rally["node_key"],
            "travel_label": _duration(expedition_rules["travel_seconds"][rally["node_key"]]),
            "modes": [{"key": rally["kind"], "name": rally["kind_label"]}],
        }
    troop_portraits = {
        "dao_sheng": "daosheng",
        "qiang_wang": "qiangwang",
        "jian_sheng": "jiansheng",
        "quan_sheng": "quansheng",
        "arrow_god": "jianshen",
        "bloodthirsty_cavalry": "shixuetieqi",
    }
    troop_options = [
        {
            "key": key,
            "name": definitions.get(key, {}).get("label", key),
            "avatar": definitions.get(key, {}).get("avatar")
            or ("season-troops/" + troop_portraits[key] + ".webp" if key in troop_portraits else ""),
            "available": troops.get(key, 0),
            "max_draw": min(troops.get(key, 0), troops_remaining),
            "special": key in special_rules,
        }
        for key in dict.fromkeys([*economy["initial_troops"], *special_rules])
    ]
    guest_daily_limit = expedition_rules.get("guest_daily_limit", 0)
    guest_uses = guest_dispatch_counts(season, timezone.localdate(now)) if season and guest_daily_limit else {}
    guests = []
    for guest in Guest.objects.filter(manor=manor, status=GuestStatus.IDLE).select_related("template").order_by("id"):
        stats = resolve_guest_combat_stats(guest)
        guests.append(
            {
                "id": guest.id,
                "name": guest.display_name,
                "avatar": guest.avatar_url,
                "rarity_key": guest.rarity,
                "rarity": dict(GuestRarity.choices).get(guest.rarity, guest.rarity),
                "level": guest.level,
                "hp": guest.current_hp,
                "max_hp": stats.max_hp,
                "capacity": stats.troop_capacity,
                "dispatches_remaining": (
                    max(0, guest_daily_limit - guest_uses.get(guest.pk, 0)) if guest_daily_limit else None
                ),
            }
        )
    available_dispatch_guests = sum(not guest_daily_limit or row["dispatches_remaining"] > 0 for row in guests)
    if not available_dispatch_guests:
        can_dispatch = False
        for rally in rallies:
            rally["can_join"] = False
    resource_options = [
        {
            "key": key,
            "name": RESOURCE_NAMES[key],
            "amount": resources.get(key, 0),
            "cap": int(economy["resource_caps"][key] * scale["capacity"]),
            "rate": int(economy["resource_rates"][key] * scale["rate"]) * levels.get("logistics", 1),
        }
        for key in RESOURCE_NAMES
    ]
    if resource_economy["enabled"] and season and member and member.faction:
        daily_output = daily_resource_output(season, member.faction, levels)
        caps = resource_caps(season, member.faction, levels)
        for resource_option in resource_options:
            resource_option["cap"] = caps[resource_option["key"]]
            resource_option["rate"] = round(
                daily_output[resource_option["key"]] * resource_economy["settlement_seconds"] / 86400, 2
            )
            resource_option["daily_rate"] = round(daily_output[resource_option["key"]], 1)
    construction_points = faction_state.construction_points if faction_state else 0
    thresholds = [
        int(value * scale["threshold"])
        for value in economy.get("auto_development", DEFAULT_AUTO_DEVELOPMENT)["level_thresholds"]
    ]
    upgrade_options = []
    descriptions = {
        "logistics": "随阵营建设值自动提升每周期粮草、木材与铁料产量。",
        "training": (
            "随阵营建设值自动提升补兵速度，训练消耗粮草、木材与铁料。"
            if resource_economy["enabled"]
            else "随阵营建设值自动提升护院训练速度，训练护院仍会消耗粮草。"
        ),
        "technology": "随阵营建设值自动提升本赛季护院的军略科技。",
    }
    for key, building in economy["buildings"].items():
        level = levels.get(key, 1)
        maxed = level >= building["max_level"]
        next_threshold = None if maxed else thresholds[level]
        upgrade_options.append(
            {
                "key": key,
                "name": building["name"],
                "max_level": building["max_level"],
                "level": level,
                "maxed": maxed,
                "description": descriptions[key],
                "next_threshold": next_threshold,
                "remaining_points": max(0, next_threshold - construction_points) if next_threshold is not None else 0,
                "progress_value": min(construction_points, next_threshold) if next_threshold is not None else 0,
            }
        )
    development_next_threshold = min(
        (row["next_threshold"] for row in upgrade_options if row["next_threshold"] is not None), default=None
    )
    rewards = reward_catalog(faction, rules=season.rules) if season and assigned else []
    shop = shop_catalog(faction, rules=season.rules) if season and assigned else []
    claims = {claim.key: claim.quantity for claim in member.claims.all()} if member else {}
    fallback_items = season.rules.get("summon_fallback_items", {}) if season else {}
    final_items = member.final_reward.get("items", {}) if member else {}
    ranking_items = {key for tier in campaign["rewards"]["tiers"] for key in tier["items"]}
    item_keys = (
        ({key for row in [*rewards, *shop] for key in row.get("items", {})} | set(fallback_items) | set(final_items))
        | ranking_items
        | set(campaign["rewards"]["winner_items"])
    )
    item_templates = (
        {
            item.key: item
            for item in ItemTemplate.objects.filter(key__in=item_keys).only("key", "name", "image", "icon", "rarity")
        }
        if item_keys
        else {}
    )
    item_names = {key: item.name for key, item in item_templates.items()}
    loaded_keys = set(item_names)
    from gameplay.services.season.item_visuals import item_visuals

    for reward in rewards:
        reward["goods"] = item_visuals(reward.get("items", {}), item_templates)
        reward["progress"] = min(member.merit if member else 0, reward["threshold"])
        reward["remaining"] = max(0, reward["threshold"] - (member.merit if member else 0))
        reward["claimed"] = "reward:" + reward["key"] in claims
        reward["available"] = bool(
            member
            and member.merit >= reward["threshold"]
            and not reward["claimed"]
            and set(reward.get("items", {})) <= loaded_keys
        )
    for item in shop:
        item["goods"] = item_visuals(item["items"], item_templates)
        item["purchased"] = claims.get("shop:" + item["key"], 0)
        item["available"] = bool(
            member
            and state["day"] >= node_rules["river:market"]["unlock_day"]
            and member.coins >= item["cost"]
            and item["purchased"] < item["limit"]
            and set(item["items"]) <= loaded_keys
        )
    candidates = (
        available_summon_templates(manor, faction, rules=season.rules)
        if season and member and assigned and member.scroll_available and not member.scroll_used
        else []
    )
    populations = (
        {row["faction"]: row["total"] for row in season.members.values("faction").annotate(total=Count("id"))}
        if season
        else {}
    )
    factions = [
        {
            "key": key,
            "name": name,
            "score": faction_states[key].score if key in faction_states else getattr(season, key + "_score", 0),
            "members": populations.get(key, 0),
        }
        for key, name in FACTION_NAMES.items()
    ]
    active_expeditions = []
    for run in active_runs:
        collection = collection_progress(run)
        active_expeditions.append(
            {
                "id": run.pk,
                "kind": run.kind,
                "status": run.status,
                "rally_id": run.rally_id,
                "is_rally_leader": bool(run.rally and member and run.rally.leader_id == member.pk),
                "node_key": run.node_key,
                "node_name": node_rules.get(run.node_key, {}).get("name", run.node_key),
                "image": node_images.get(run.node_key, ""),
                "portraits": expedition_portraits(run, portrait_guests),
                "status_label": (
                    "采集中"
                    if collection and run.status == "stationed"
                    else (
                        "等待遭遇战"
                        if run.kind in {"intercept", "escort"} and run.status == "transporting"
                        else run.get_status_display()
                    )
                ),
                "kind_label": (
                    "集结攻城"
                    if run.rally and run.rally.result.get("unified_siege")
                    else MODE_NAMES.get(run.kind, run.kind)
                ),
                "guest_names": "、".join(row.get("display_name", "门客") for row in run.lineup.get("guests", [])),
                "troop_count": sum(run.troops.values()),
                "garrison_merit_preview": garrison_merit_preview(run, now) if run.status == "stationed" else 0,
                "collection": collection,
                "fatigue_stacks": (
                    min(fortifications["fatigue"]["max_stacks"], max(0, int(run.result.get("fatigue_stacks", 0))))
                    if fortifications["enabled"]
                    else 0
                ),
                "due_at": run.due_at,
                "deadline_label": (
                    "预计归队"
                    if run.status == "returning"
                    else (
                        "下一场战斗"
                        if collection and run.status == "stationed"
                        else (
                            "遭遇战结算"
                            if run.kind in {"intercept", "escort"} and run.status == "transporting"
                            else "下一阶段"
                        )
                    )
                ),
                "can_recall": run.status in {"gathering", "outbound", "stationed", "transporting", "failed"},
                "message": run.result.get("message", "")
                + (
                    f"；拦截 {convoy_participants.get(run.pk, {}).get('intercept', 0)} 人"
                    f" · 护送 {1 + convoy_participants.get(run.pk, {}).get('escort', 0)} 人"
                    if run.kind == "transport"
                    and run.status == "transporting"
                    and run.result.get("activity", {}).get("encounter_version") == 1
                    else ""
                ),
                "operation_expenses": (
                    " · ".join(
                        f"{RESOURCE_NAMES[key]}{sum(line.get('resources', {}).get(key, 0) for line in run.result.get('operation_costs', {}).get('expenses', []) if line['kind'] != 'unused_rations_refund')}"
                        for key in RESOURCE_NAMES
                    )
                    if run.result.get("operation_costs")
                    else ""
                ),
                "rations_reserved": sum(
                    run.result.get("operation_costs", {}).get(key, 0)
                    for key in ("march_balance", "courier_balance", "carried_food_balance")
                ),
            }
        )
    leaderboard = (
        [
            {
                "rank": index,
                "name": row.manor.display_name,
                "merit": row.merit,
                "combat_merit": row.combat_merit,
                "logistics_merit": row.logistics_merit,
                "is_me": bool(member and row.pk == member.pk),
            }
            for index, row in enumerate(
                season.members.filter(faction=faction)
                .select_related("manor", "manor__user")
                .order_by("-merit", "joined_at", "id")[:20],
                start=1,
            )
        ]
        if season and assigned
        else []
    )
    site_rows = [
        {
            "key": key,
            "image": node_images.get(key, ""),
            "name": node_rules.get(key, {}).get("name", key),
            "owner_label": FACTION_NAMES.get(site.owner, "中立守军"),
            "guards": site.guards,
            "durability": site.durability,
            "control": site.control,
            "tower": tower_state(site),
            "garrison": garrisons.get(key),
        }
        for key, site in sites.items()
    ]
    final_claimed = "campaign:final" in claims
    technology_names = {row["key"]: row["name"] for row in load_technology_templates().get("technologies", [])}
    technology_options = [
        {
            "key": key,
            "name": technology_names.get(key, key),
            "level": faction_state.technology.get(key, 0) if faction_state else 0,
        }
        for key in economy["technology_keys"]
    ]
    market = (
        market_offers(season, faction_state, now) if season and member and assigned else {"offers": [], "remaining": 0}
    )
    for offer in market["offers"]:
        offer["source_name"] = RESOURCE_NAMES[offer["source"]]
        offer["target_name"] = RESOURCE_NAMES[offer["target"]]
    reservation = dict(member.lineup.get("reservation", {})) if member else {}
    if reservation:
        reservation["target_name"] = node_rules.get(reservation.get("node_key", ""), {}).get("name", "预约据点")
        reservation["expires_at_display"] = parse_datetime(reservation.get("expires_at", ""))
        if (
            reservation.get("status") == "pending"
            and reservation["expires_at_display"]
            and reservation["expires_at_display"] <= now
        ):
            reservation["status"] = "expired"
    weekly_context = weekly_campaign_context(season, member, now) if season else {"enabled": False}
    victory_days = {"fiction": 0, "history": 0}
    population_rules = feature_rules(season, "population") if season else {"enabled": False}
    victory_min_days = population_rules.get("victory_min_days", 0) if population_rules["enabled"] else 0
    if season and victory_min_days:
        victory_days.update(victory_contested_days(season))
    victory_qualified = not victory_min_days or all(days >= victory_min_days for days in victory_days.values())
    frozen_victory = season.population.get("victory_result") if season and season.closed else None
    if isinstance(frozen_victory, dict) and isinstance(frozen_victory.get("qualified"), bool):
        victory_qualified = frozen_victory["qualified"]
        victory_days.update(frozen_victory.get("days", {}))
    season_winner_label = ""
    if season and season.closed:
        season_winner_label = (
            FACTION_NAMES.get(season.winner, "")
            if season.winner
            else "双方战平" if victory_qualified else "有效对抗不足，未产生胜方"
        )
    focus = recommended_focus(season, now) if season else {}
    guidance = campaign_guidance(
        [node for region in maps for node in region["nodes"]],
        rallies,
        resource_options,
        transport_targets,
        member.faction if member else "",
        focus.get("key", ""),
        state["combat_open"],
    )
    if assigned and state["combat_open"] and not can_dispatch:
        title = (
            "今日出征次数已用完"
            if not dispatches_remaining
            else (
                "今日门客次数已用完"
                if guests and not available_dispatch_guests
                else "等待队伍归队" if active_runs else "暂无可派遣门客"
            )
        )
        guidance = {
            "main": {
                "title": title,
                "detail": "可先查看队伍、兵库或领取已达成奖励。",
                "modal": "season-team-status",
                "label": "查看队伍",
            },
            "support": {},
        }
    decorate_map_nodes(maps, guidance, transport_targets, active_runs, member.faction if member else "")
    reservation_targets = [
        {"key": node["key"], "name": node["name"]}
        for region in maps
        for node in region["nodes"]
        if node.get("rally_modes") and node["unlocked"] and not node.get("schedule_closed")
    ]
    for region in maps:
        for node in region["nodes"]:
            node["reservation_targets"] = [target for target in reservation_targets if target["key"] == node["key"]]
            node["reservation_available"] = bool(
                playability["enabled"]
                and coalition["enabled"]
                and assigned
                and state["combat_open"]
                and node["reservation_targets"]
                and not (reservation.get("status") == "pending" and reservation.get("node_key") != node["key"])
            )
            node["reservation"] = reservation if reservation.get("node_key") == node["key"] else {}
    team_slots = []
    for kind, label in (("attack", "进攻队"), ("garrison", "驻防队")):
        runs = [run for run in active_expeditions if (run["kind"] in {"garrison", "escort"}) == (kind == "garrison")]
        team_slots.append(
            {
                "kind": kind,
                "label": label,
                "runs": runs,
                "first_run": runs[0] if runs else {},
                "busy": bool(runs),
                "count": len(runs),
            }
        )
    if weekly_context.get("enabled") and season is not None:
        from gameplay.services.season.weekly_campaign import week_bounds

        weekly_current_number = weekly_context["current_week"]
        weekly_context["current_ends_at"] = week_bounds(season, weekly_current_number)[1]
        for faction_entry in factions:
            faction_entry["victory_points"] = weekly_context.get("victory_points", {}).get(faction_entry["key"], 0)
            faction_row = faction_states.get(faction_entry["key"])
            book = faction_row.economy_state.get("weekly_campaign", {}) if faction_row else {}
            faction_entry["weekly_score"] = int(
                book.get("weeks", {}).get(str(weekly_current_number), {}).get("score", 0)
            )
    ranking_options = []
    for tier in campaign["rewards"]["tiers"]:
        amounts = dict(tier["items"])
        if playability["enabled"]:
            bonus = playability["weekly"]["ranking_bonus_percent"].get(tier["key"], 0)
            amounts = {
                key: count + count * bonus // 100 for key, count in playability["weekly"]["ranking_base_items"].items()
            }
        ranking_options.append(
            {
                "name": tier["name"],
                "percentile": tier["percentile"],
                "description": _describe_items(amounts, item_names),
            }
        )
    return {
        "previous_season_reward": previous_season_reward(manor, season, now),
        "market_stock": market_stock_context(season, member, now) if season and member else {"enabled": False},
        "playability": playability,
        "weekly_campaign": weekly_context,
        "weekly_current": next(
            (row for row in weekly_context.get("weeks", []) if row["number"] == weekly_context.get("current_week")), {}
        ),
        "weekly_claimable_count": sum(
            bool(row["personal_claimable"] or row["team_claimable"]) for row in weekly_context.get("weeks", [])
        ),
        "weekly_personal_description": _describe_items(playability["weekly"]["personal_items"], item_names),
        "weekly_team_description": _describe_items(playability["weekly"]["team_items"], item_names),
        "gate_march_bonus": gate_march_bonus(season, member.faction) if season and member else 0,
        "reservation": reservation,
        "injured_guest_count": Guest.objects.filter(manor=manor, status=GuestStatus.INJURED).count(),
        "reservation_targets": reservation_targets,
        "team_slots": team_slots,
        "joinable_rallies": [rally for rally in rallies if rally["can_join"]],
        "command_guidance": guidance,
        "reward_progress": reward_progress(rewards, member.merit if member else 0),
        "rally_member_options": list(range(coalition["min_members"], coalition["max_members"] + 1)),
        "station_hours_options": expedition_rules.get("station_hours_options", [1, 2, 4]),
        "simplification": simplified,
        "market": market,
        "market_loss_percent": (
            100 - market_return_percent(season, faction) if season else 100 - simplified["market"]["return_percent"]
        ),
        "activity_rules": activity_rules,
        "resource_economy": resource_economy,
        "resource_flow_rows": [
            {
                "reason": {
                    "production": "自动产出",
                    "market_exchange": "军需调剂",
                    "opening_balance": "升级时保留的原库存",
                    "initial": "首次补给",
                    "training": "实际训练",
                    "site_repair": "城防自动维修",
                    "activity_reward": "任务奖励",
                    "refund": "任务退款",
                    "transport_out": "装载出库",
                    "rations_reserve": "行军运营预留",
                    "battle": "交战消耗",
                    "demolition": "攻城器械",
                    "local_surplus_return": "多余当地补给退库",
                    "local_garrison": "当地驻军消耗",
                    "local_battle": "当地交战消耗",
                    "repair": "当地维修消耗",
                }.get(reason, reason),
                "resources": " · ".join(
                    f"{RESOURCE_NAMES[key]} {amount:+d}" for key, amount in amounts.items() if amount
                ),
            }
            for reason, amounts in (
                faction_state.economy_state.get("daily_flows", {}).get(timezone.localdate(now).isoformat(), {})
                if faction_state
                else {}
            ).items()
        ],
        "material_budget_summary": material_summary,
        "collection_rules": collection_rules,
        "economic_population": economic_population(season, member.faction) if season and member else 0,
        "training_targets": training_targets(season, member.faction) if season and member else {},
        "training_summary": (
            training_summary(season, member.faction, faction_state.levels if faction_state else {})
            if season and member
            else {}
        ),
        "exploration_remaining": exploration_remaining,
        "population": population,
        "recommended_focus": focus,
        "victory_min_days": victory_min_days,
        "victory_days": victory_days,
        "merit_summary": summary,
        "intel_records": intels,
        "manor": manor,
        "season": season,
        "season_state": state,
        "member": member,
        "campaign": campaign,
        "faction_label": FACTION_NAMES.get(member.faction, "等待分组") if member else "尚未报名",
        "factions": factions,
        "season_maps": maps,
        # Django模板每次解析可调用变量都会调用它；各表单独立凭证，无需浏览器安全上下文。
        "action_token": uuid4,
        "guests": guests,
        "available_dispatch_guests": available_dispatch_guests,
        "guest_daily_limit": guest_daily_limit,
        "max_guests": (
            min(manor.max_squad_size, coalition["commander_guest_limit"])
            if coalition["enabled"]
            else manor.max_squad_size
        ),
        "rewards": rewards,
        "shop_items": shop,
        "summon_candidates": [
            {
                "id": t.id,
                "name": t.name,
                "rarity": t.get_rarity_display(),
                "rarity_key": t.rarity,
                "avatar": t.avatar.url if t.avatar else "",
            }
            for t in candidates
        ],
        "summon_pick_count": min(season.rules.get("summon_pick_count", 3), len(candidates)) if season else 0,
        "summon_fallback_description": _describe_items(fallback_items, item_names),
        "summon_catalog_ready": bool(season and assigned and has_summon_catalog(faction, rules=season.rules)),
        "season_history": list(seasons.values("id", "name")),
        "season_winner_label": season_winner_label,
        "registration_open": registration_open,
        "registration_ends_at": registration_ends_at,
        "faction_state": faction_state,
        "troop_options": troop_options,
        "resource_options": resource_options,
        "upgrade_options": upgrade_options,
        "construction_points": construction_points,
        "development_next_threshold": development_next_threshold,
        "development_remaining_points": (
            max(0, development_next_threshold - construction_points) if development_next_threshold is not None else 0
        ),
        "development_maxed": all(row["maxed"] for row in upgrade_options),
        "production_interval_label": _duration(economy["tick_seconds"]),
        "daily_troop_quota": daily_quota,
        "garrison_merit_per_hour": expedition_rules.get("garrison_merit_per_hour", 0),
        "convoy_encounters_enabled": expedition_rules.get("convoy_encounters_enabled", False),
        "max_convoy_players": expedition_rules.get("max_convoy_players", 5),
        "transport_window_label": _duration(scale_duration(expedition_rules["transport_seconds"], minimum=1)),
        "transport_capacity": transport_capacity,
        "transport_max_merit": activity_rules["transport"]["merit"]
        * activity_rules["transport"]["frontline_merit_percent"]
        // 100,
        "troops_remaining": troops_remaining,
        "active_expeditions": active_expeditions,
        "active_count": active_count,
        "max_expeditions": max_active,
        "can_dispatch": can_dispatch,
        "coalition": coalition,
        "rally_wait_label": _duration(coalition["rally_seconds"]),
        "fortifications": fortifications,
        "hitpoints": hitpoints,
        "occupation": occupation,
        "resource_priority": (
            resource_reserves(season, faction, operating=True) if season and assigned and occupation["enabled"] else {}
        ),
        "score_interval_minutes": expedition_rules["score_interval_seconds"] // 60,
        "rallies": rallies,
        "attack_busy": attack_busy,
        "garrison_busy": garrison_busy,
        "rally_guest_limit": min(manor.max_squad_size, coalition["commander_guest_limit"]),
        "dispatches_remaining": dispatches_remaining,
        "daily_dispatch_limit": expedition_rules["daily_actions"],
        "technology_options": technology_options,
        "cavalry_technology_options": [
            {"name": name, "level": season_technology_levels({})[key]} for key, name in CAVALRY_TECH_NAMES.items()
        ],
        "special_production_options": special_production_options,
        "site_rows": site_rows,
        "siege_schedule_label": schedule_label,
        "leaderboard": leaderboard,
        "final_claimed": final_claimed,
        "can_claim_final": bool(
            season
            and season.closed
            and member
            and member.final_rank
            and final_items
            and not final_claimed
            and set(final_items) <= loaded_keys
        ),
        "final_reward_description": _describe_items(final_items, item_names),
        "final_tier_label": member.final_reward.get("tier_name", "") if member else "",
        "ranking_reward_options": ranking_options,
        "rank_minimum_merit": campaign["rewards"]["minimum_merit"],
        "winner_reward_description": _describe_items(campaign["rewards"]["winner_items"], item_names),
    }
