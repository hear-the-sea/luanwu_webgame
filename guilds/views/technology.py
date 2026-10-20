"""帮会科技树视图。"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.game_data.cavalry import (
    CAVALRY_BERSERK_CHANCE_PER_LEVEL,
    CAVALRY_BERSERK_MULTI_DAMAGE_MULTIPLIER,
    CAVALRY_BERSERK_SINGLE_DAMAGE_MULTIPLIER,
    CAVALRY_STAT_TECHS,
)
from core.utils.rate_limit import rate_limit_redirect
from gameplay.models import ItemTemplate

from .. import constants as guild_constants
from ..decorators import require_guild_member
from ..models import GuildTechnology
from ..services import technology as technology_service
from ..services.cavalry import finalize_guild_cavalry_training, get_guild_cavalry_recruitment_state
from ..services.warehouse import get_guild_material_balances, get_technology_daily_output_display
from ..technology_tree import PUBLIC_TECHNOLOGIES, TECHNOLOGY_ROUTES, is_technology_route_complete
from .helpers import build_guild_member_context, execute_guild_action, load_ordered_technologies

RESOURCE_LABELS = {
    "silver": "银两",
    "grain": "粮食",
    "gold_bar": "金条",
    "red_ruby": "红宝石",
}


def _format_upgrade_cost(tech_key: str, level: int, guild: Any) -> str:
    cost = technology_service.calculate_tech_upgrade_cost(tech_key, level, guild=guild)
    parts = [f"{RESOURCE_LABELS.get(key, '未知资源')}×{amount}" for key, amount in cost.items() if amount > 0]
    return "、".join(parts) if parts else "无"


def _format_level_effect(node: dict[str, Any], level: int) -> str:
    key = node["key"]
    if key == "troop_tactics":
        return f"帮会战斗中强化护院兵种能力（{level} / 10级）" if level else "未激活"
    if key == "guild_lineup_capacity":
        return f"{technology_service.calculate_guild_lineup_capacity(level)} 名"
    if key == "guild_dispatch_capacity":
        return f"{technology_service.calculate_guild_dispatch_capacity(level)} 名"
    if key == "march_speed":
        return f"行军时间减少 {level * 5}%" if level else "未激活"
    if key in {"sheep_farmland", "sheep_silver_mine"}:
        resource = "粮食" if key == "sheep_farmland" else "银两"
        return f"全体成员庄园{resource}产出 +{level * 5}%" if level else "未激活"
    if key in {"wolf_force", "wolf_defense", "wolf_agility", "wolf_hp"}:
        stat = {
            "wolf_force": "攻击",
            "wolf_defense": "防御",
            "wolf_agility": "敏捷",
            "wolf_hp": "生命",
        }[key]
        percent = round(level * CAVALRY_STAT_TECHS[key][1] * 100)
        return f"嗜血铁骑{stat} +{percent}%" if level else "未激活"
    if key == "wolf_berserk":
        base = round(level * CAVALRY_BERSERK_CHANCE_PER_LEVEL * 100)
        multi_damage = round(CAVALRY_BERSERK_MULTI_DAMAGE_MULTIPLIER * 100)
        single_damage = round(CAVALRY_BERSERK_SINGLE_DAMAGE_MULTIPLIER * 100)
        return (
            f"铁骑冲阵固定触发率 {base}% · 双目标各 {multi_damage}% 伤害 · 仅剩一个目标时 {single_damage}% 伤害"
            if level
            else "未激活"
        )
    if key in {"sheep_metallurgy", "sheep_breeding", "sheep_horsemanship"}:
        return f"速度 +{level * 10}% · 单次上限 +{level * 10}% · 消耗 -{level * 5}%" if level else "未激活"
    if node.get("production"):
        interval_days = max(1, int(node.get("production_interval_days", 1) or 1))
        output_period = f"每{interval_days}天产出" if interval_days > 1 else "每日产出"
        return f"{output_period}已解锁" if level else "未激活"
    if node["max_level"] == 1:
        return "已解锁" if level else "未激活"
    return f"已研究 {level} 级" if level else "未激活"


def _build_display_node(
    *,
    guild: Any,
    node: dict[str, Any],
    tech_by_key: dict[str, GuildTechnology],
    route_key: str | None,
    active_route: str,
) -> dict[str, Any]:
    tech = tech_by_key.get(node["key"])
    level = int(tech.level) if tech is not None else 0
    max_level = int(node["max_level"])
    enabled = node["enabled"]
    unlocked = all(tech is not None and tech.level >= 1 for tech in (tech_by_key.get(key) for key in node["requires"]))
    is_public = route_key is None
    current_effect = _format_level_effect(node, level) if enabled else "暂未开放"
    next_effect = _format_level_effect(node, min(max_level, level + 1)) if enabled and level < max_level else ""
    daily_output = get_technology_daily_output_display(node["key"], level) if enabled and level > 0 else None
    if not enabled:
        upgrade_cost = "暂未开放"
    elif level < max_level:
        upgrade_cost = _format_upgrade_cost(node["key"], level, guild)
    else:
        upgrade_cost = "已满级"
    return {
        "key": node["key"],
        "name": guild_constants.TECH_NAMES.get(node["key"], node["name"]),
        "description": guild_constants.TECH_DESCRIPTIONS.get(node["key"], node["description"]),
        "level": level,
        "max_level": max_level,
        "icon_index": node["icon_index"],
        "requires": node["requires"],
        "unlocked": unlocked,
        "enabled": enabled,
        "available": (enabled and unlocked and (is_public or active_route == route_key) and level < max_level),
        "prerequisite_label": "、".join(TECHNOLOGY_NODE_NAME[key] for key in node["requires"]),
        "current_effect": current_effect,
        "next_effect": next_effect,
        "upgrade_cost": upgrade_cost,
        "daily_output": daily_output,
        "production_interval_days": max(1, int(node.get("production_interval_days", 1) or 1)),
        "route_key": route_key or "public",
    }


TECHNOLOGY_NODE_NAME = {node["key"]: node["name"] for route in TECHNOLOGY_ROUTES for node in route["nodes"]}


@login_required
@require_guild_member
def technology_list(request: Any) -> HttpResponse:
    member = request.guild_member
    guild = member.guild
    finalize_guild_cavalry_training(guild.pk)
    technologies = load_ordered_technologies(guild)
    tech_by_key = {tech.tech_key: tech for tech in technologies}
    active_route = str(guild.active_technology_route or "")
    active_definition = next((route for route in TECHNOLOGY_ROUTES if route["key"] == active_route), None)
    active_route_completed = active_definition is not None and is_technology_route_complete(
        active_definition, {key: tech.level for key, tech in tech_by_key.items()}
    )
    completed_routes = list(guild.completed_technology_routes or [])
    route_multiplier = technology_service.ROUTE_COST_MULTIPLIER ** len(completed_routes)
    route_keys = {route["key"] for route in TECHNOLOGY_ROUTES}
    default_route_key = (active_route if active_route in route_keys else "") or next(
        (route["key"] for route in TECHNOLOGY_ROUTES if route["key"] not in completed_routes),
        TECHNOLOGY_ROUTES[0]["key"] if TECHNOLOGY_ROUTES else "",
    )

    xisuidan_template = ItemTemplate.objects.filter(key="xisuidan").only("image").first()
    xisuidan_image_url = ""
    if xisuidan_template and xisuidan_template.image:
        try:
            xisuidan_image_url = xisuidan_template.image.url
        except ValueError:
            pass

    technology_routes = []
    for route in TECHNOLOGY_ROUTES:
        is_completed = route["key"] in completed_routes
        route_nodes = [
            _build_display_node(
                guild=guild,
                node=node,
                tech_by_key=tech_by_key,
                route_key=route["key"],
                active_route=active_route,
            )
            for node in route["nodes"]
        ]
        technology_routes.append(
            {
                **route,
                "nodes": route_nodes,
                "active": active_route == route["key"],
                "completed": is_completed,
                "selectable": (
                    (not active_route or active_route_completed) and active_route != route["key"] and not is_completed
                ),
                "ready_to_complete": active_route == route["key"] and active_route_completed,
                "resettable": active_route == route["key"] and not active_route_completed,
                "is_default": route["key"] == default_route_key,
                "icon_asset": f"images/guild_technology/route_{route['key']}.png",
                "cost_multiplier": route_multiplier,
            }
        )

    public_technologies = [
        _build_display_node(
            guild=guild,
            node=node,
            tech_by_key=tech_by_key,
            route_key=None,
            active_route=active_route,
        )
        for node in PUBLIC_TECHNOLOGIES
    ]
    context = build_guild_member_context(
        member,
        technologies=technologies,
        technology_routes=technology_routes,
        public_technologies=public_technologies,
        guild_material_balances=get_guild_material_balances(guild),
        completed_technology_routes=completed_routes,
        active_technology_route=active_route,
        cavalry=get_guild_cavalry_recruitment_state(guild, member),
        xisuidan_image_url=xisuidan_image_url,
    )
    return render(request, "guilds/technology.html", context)


@login_required
@require_guild_member
@require_POST
@rate_limit_redirect("guild_tech_upgrade", limit=10, window_seconds=60)
def upgrade_technology(request: Any, tech_key: str) -> HttpResponse:
    member = request.guild_member
    execute_guild_action(
        request,
        action=lambda: technology_service.upgrade_technology(member.guild, tech_key, request.user),
        success_message=lambda _result: f"{guild_constants.TECH_NAMES.get(tech_key, '该科技')}升级成功！",
    )
    return redirect("guilds:technology")


@login_required
@require_guild_member
@require_POST
@rate_limit_redirect("guild_tech_route_select", limit=5, window_seconds=60)
def select_technology_route(request: Any, route_key: str) -> HttpResponse:
    member = request.guild_member
    route = next((item for item in TECHNOLOGY_ROUTES if item["key"] == route_key), None)
    execute_guild_action(
        request,
        action=lambda: technology_service.select_technology_route(member.guild, route_key, request.user),
        success_message=f"已开启{route['title']}科技路线" if route else "科技路线已开启",
    )
    return redirect("guilds:technology")


@login_required
@require_guild_member
@require_POST
@rate_limit_redirect("guild_tech_route_reset", limit=3, window_seconds=60)
def reset_technology_route(request: Any, route_key: str) -> HttpResponse:
    member = request.guild_member
    route = next((item for item in TECHNOLOGY_ROUTES if item["key"] == route_key), None)
    execute_guild_action(
        request,
        action=lambda: technology_service.reset_technology_route(member.guild, route_key, request.user),
        success_message=f"已取消{route['title']}路线，科技等级已清零并返还50%投入资源" if route else "科技路线已取消",
    )
    return redirect("guilds:technology")
