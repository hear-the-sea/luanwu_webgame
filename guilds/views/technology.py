"""
帮会科技视图
"""

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.utils.rate_limit import rate_limit_redirect

from .. import constants as guild_constants
from ..decorators import require_guild_member
from ..services import technology as technology_service
from ..services.warehouse import get_guild_material_balances
from .helpers import build_guild_member_context, execute_guild_action, load_ordered_technologies

TECH_CATEGORY_TABS = (
    ("production", "生产类科技"),
    ("combat", "战斗类科技"),
    ("welfare", "福利类科技"),
)

VALID_TECH_CATEGORIES = frozenset(key for key, _label in TECH_CATEGORY_TABS)


def _format_upgrade_cost(tech: Any) -> str:
    cost = technology_service.calculate_tech_upgrade_cost(tech.tech_key, tech.level)
    labels = {
        "silver": "银两",
        "grain": "粮食",
        "gold_bar": "金条",
        "red_ruby": "红宝石",
    }
    parts = [
        f"{labels.get(resource_key, resource_key)} x{amount}" for resource_key, amount in cost.items() if amount > 0
    ]
    return "、".join(parts) if parts else "无"


def _resolve_display_max_level(tech: Any) -> int:
    return int(getattr(tech, "effective_max_level", getattr(tech, "max_level", 0)) or 0)


def _format_troop_tactics_effect(level: int, max_level: int) -> str:
    return f"按 {level} / {max_level} 级线性映射个人兵种科技"


def _build_tech_display_meta(tech: Any) -> dict[str, str]:
    max_level = _resolve_display_max_level(tech)

    if tech.tech_key == "equipment_forge":
        return {"description": "每日生产装备道具", "upgrade_cost": _format_upgrade_cost(tech)}
    if tech.tech_key == "guard_armory":
        return {"description": "每日生产护院招募装备箱", "upgrade_cost": _format_upgrade_cost(tech)}
    if tech.tech_key == "experience_refine":
        return {"description": "每日生产技能书箱", "upgrade_cost": _format_upgrade_cost(tech)}
    if tech.tech_key == "resource_supply":
        return {"description": "每日生产资源礼包", "upgrade_cost": _format_upgrade_cost(tech)}
    if tech.tech_key == "troop_tactics":
        return {
            "description": "帮会科技一发功，兵种科技就跟着胡乱长个儿",
            "current_effect": _format_troop_tactics_effect(tech.level, max_level) if tech.level > 0 else "未激活",
            "next_effect": _format_troop_tactics_effect(min(max_level, tech.level + 1), max_level),
            "upgrade_cost": _format_upgrade_cost(tech),
        }
    if tech.tech_key == "guild_lineup_capacity":
        next_capacity = min(
            technology_service.MAX_GUILD_LINEUP_CAPACITY,
            int(guild_constants.GUILD_BATTLE_LINEUP_LIMIT) + min(max_level, tech.level + 1),
        )
        return {
            "description": "提升帮会已上阵名单总容量",
            "current_effect": f"{technology_service.get_guild_lineup_capacity(tech.guild)} 名",
            "next_effect": f"{next_capacity} 名",
            "upgrade_cost": _format_upgrade_cost(tech),
        }
    if tech.tech_key == "guild_dispatch_capacity":
        next_capacity = min(
            technology_service.MAX_GUILD_DISPATCH_CAPACITY,
            int(guild_constants.GUILD_DISPATCH_GUEST_BASE_LIMIT) + min(max_level, tech.level + 1),
        )
        return {
            "description": "提升单次帮会任务最多可派出的门客人数",
            "current_effect": f"{technology_service.get_guild_dispatch_capacity(tech.guild)} 名",
            "next_effect": f"{next_capacity} 名",
            "upgrade_cost": _format_upgrade_cost(tech),
        }
    if tech.tech_key == "resource_boost":
        return {
            "description": "提升庄园资源产出",
            "current_effect": f"+{tech.level * 10}%" if tech.level > 0 else "未激活",
            "next_effect": f"+{(tech.level + 1) * 10}%",
            "upgrade_cost": _format_upgrade_cost(tech),
        }
    if tech.tech_key == "march_speed":
        return {
            "description": "减少行军时间",
            "current_effect": f"-{tech.level * 5}%" if tech.level > 0 else "未激活",
            "next_effect": f"-{(tech.level + 1) * 5}%",
            "upgrade_cost": _format_upgrade_cost(tech),
        }
    return {"description": "科技效果", "upgrade_cost": _format_upgrade_cost(tech)}


@login_required
@require_guild_member
def technology_list(request: Any) -> HttpResponse:
    """科技列表"""
    member = request.guild_member
    technologies = load_ordered_technologies(member.guild)

    current_category = str(request.GET.get("category") or "").strip()
    if current_category not in VALID_TECH_CATEGORIES:
        available_categories = {tech.category for tech in technologies}
        current_category = (
            "production"
            if "production" in available_categories
            else next(
                (key for key, _label in TECH_CATEGORY_TABS if key in available_categories),
                "production",
            )
        )

    filtered_technologies = [tech for tech in technologies if tech.category == current_category]
    context = build_guild_member_context(
        member,
        technologies=technologies,
        filtered_technologies=filtered_technologies,
        current_tech_category=current_category,
        tech_category_tabs=[
            {"key": key, "label": label, "active": key == current_category} for key, label in TECH_CATEGORY_TABS
        ],
        guild_material_balances=get_guild_material_balances(member.guild),
        tech_names=guild_constants.TECH_NAMES,
        tech_display_meta={tech.tech_key: _build_tech_display_meta(tech) for tech in technologies},
    )

    return render(request, "guilds/technology.html", context)


@login_required
@require_guild_member
@require_POST
@rate_limit_redirect("guild_tech_upgrade", limit=10, window_seconds=60)
def upgrade_technology(request: Any, tech_key: str) -> HttpResponse:
    """升级科技"""
    member = request.guild_member

    execute_guild_action(
        request,
        action=lambda: technology_service.upgrade_technology(member.guild, tech_key, request.user),
        success_message=lambda _result: f"{guild_constants.TECH_NAMES.get(tech_key, tech_key)}升级成功！",
    )

    current_category = str(request.GET.get("category") or "").strip()
    if current_category in VALID_TECH_CATEGORIES:
        return redirect(f"{reverse('guilds:technology')}?category={current_category}")

    return redirect("guilds:technology")
