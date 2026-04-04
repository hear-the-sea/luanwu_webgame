"""
帮会核心视图：大厅、列表、创建、详情
"""

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

import guilds.constants as guild_constants
from core.utils import safe_int, safe_ordering
from core.utils.rate_limit import rate_limit_redirect
from gameplay.models import Manor

from ..decorators import require_guild_leader
from ..models import Guild
from ..services import guild as guild_service
from .contribution import build_guild_resource_context
from .helpers import execute_guild_action, load_guild_leader, load_recent_announcements


@login_required
def guild_hall(request: Any) -> HttpResponse:
    """帮会大厅 - 入口页面"""
    user = request.user

    # 检查是否已加入帮会
    if hasattr(user, "guild_membership") and user.guild_membership.is_active:
        # 已加入帮会，重定向到帮会详情
        return redirect("guilds:detail", guild_id=user.guild_membership.guild.id)

    # 未加入帮会，显示帮会列表（使用 with_member_count 优化 N+1）
    guilds = (
        Guild.objects.with_member_count()
        .filter(is_active=True)
        .order_by("-level", "-created_at")[: guild_constants.GUILD_HALL_DISPLAY_LIMIT]
    )

    context = {
        "guilds": guilds,
        "can_create": True,
    }

    return render(request, "guilds/hall.html", context)


@login_required
def guild_list(request: Any) -> HttpResponse:
    """帮会列表"""
    ordering = safe_ordering(request.GET.get("ordering", "-level"), "-level")
    search = request.GET.get("search", "")
    page = safe_int(request.GET.get("page", 1), default=1, min_val=1)
    page_size = guild_constants.GUILD_LIST_PAGE_SIZE

    guilds = guild_service.get_guild_list(ordering=ordering, search=search, page=page, page_size=page_size)

    context = {
        "guilds": guilds,
        "ordering": ordering,
        "search": search,
        "page": page,
    }

    return render(request, "guilds/list.html", context)


@login_required
def guild_search(request: Any) -> HttpResponse:
    """搜索帮会"""
    query = request.GET.get("q", "")

    if query:
        # 使用 with_member_count 优化 N+1
        guilds = (
            Guild.objects.with_member_count()
            .filter(Q(name__icontains=query) & Q(is_active=True))
            .order_by("-level")[:10]
        )
    else:
        guilds = []

    context = {
        "guilds": guilds,
        "query": query,
    }

    return render(request, "guilds/search.html", context)


@login_required
@rate_limit_redirect("guild_create", limit=3, window_seconds=60)
def create_guild(request: Any) -> HttpResponse:
    """创建帮会"""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        emblem = request.POST.get("emblem", "default")

        outcome = execute_guild_action(
            request,
            action=lambda: guild_service.create_guild(
                user=request.user,
                name=name,
                description=description,
                emblem=emblem,
            ),
            success_message=lambda guild: f"恭喜！帮会【{guild.name}】创建成功！",
        )
        if outcome.succeeded and outcome.result is not None:
            return redirect("guilds:detail", guild_id=outcome.result.id)

    # GET请求，显示创建表单
    manor = get_object_or_404(Manor, user=request.user)

    # 获取金条数量（从仓库）
    from gameplay.models import InventoryItem

    gold_bar_count = (
        InventoryItem.objects.filter(
            manor=manor,
            template__key="gold_bar",
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .values_list("quantity", flat=True)
        .first()
        or 0
    )

    context = {
        "manor": manor,
        "gold_bar_count": gold_bar_count,
        "creation_cost": guild_constants.GUILD_CREATION_COST,
    }

    return render(request, "guilds/create.html", context)


@login_required
def guild_detail(request: Any, guild_id: int) -> HttpResponse:
    """帮会详情页面"""
    guild = get_object_or_404(Guild.objects.with_member_count(), id=guild_id, is_active=True)
    user = request.user

    # 检查是否是本帮会成员
    is_member = False
    member = None
    if hasattr(user, "guild_membership") and user.guild_membership.is_active:
        if user.guild_membership.guild_id == guild.id:
            is_member = True
            member = user.guild_membership

    leader = load_guild_leader(guild)
    announcements = load_recent_announcements(guild, limit=5)

    context = {
        "guild": guild,
        "is_member": is_member,
        "member": member,
        "leader": leader,
        "member_count": guild.current_member_count,
        "announcements": announcements,
    }
    if is_member and member is not None:
        manor = get_object_or_404(Manor, user=request.user)
        context.update(build_guild_resource_context(member, manor=manor))

    return render(request, "guilds/detail.html", context)


@login_required
@require_guild_leader
@rate_limit_redirect("guild_info", limit=10, window_seconds=60)
def guild_info(request: Any, guild_id: int) -> HttpResponse:
    """帮会信息设置"""
    guild = get_object_or_404(Guild, id=guild_id, is_active=True)
    member = request.guild_member

    # 验证是否为本帮会成员（防止跨帮会越权）
    if member.guild_id != guild.id:
        messages.error(request, "您不是该帮会成员")
        return redirect("guilds:detail", guild_id=member.guild_id)

    if request.method == "POST":
        description = request.POST.get("description", "")
        auto_accept = request.POST.get("auto_accept") == "on"

        outcome = execute_guild_action(
            request,
            action=lambda: guild_service.update_guild_info(
                guild=guild,
                operator=request.user,
                description=description,
                auto_accept=auto_accept,
            ),
            success_message="帮会信息已更新",
        )
        if outcome.succeeded:
            return redirect("guilds:detail", guild_id=guild.id)

    context = {
        "guild": guild,
    }

    return render(request, "guilds/info.html", context)
