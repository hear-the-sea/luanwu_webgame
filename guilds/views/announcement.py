"""
帮会公告视图
"""

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.utils.rate_limit import rate_limit_redirect

from ..decorators import require_guild_leader, require_guild_member
from ..services import guild as guild_service
from .helpers import build_guild_member_context, execute_guild_action

ANNOUNCEMENTS_PER_PAGE = 3


@login_required
@require_guild_member
def announcement_list(request: Any) -> HttpResponse:
    """公告列表"""
    member = request.guild_member
    announcement_queryset = member.guild.announcements.select_related("author__manor").order_by("-created_at", "-id")
    announcement_page = Paginator(announcement_queryset, ANNOUNCEMENTS_PER_PAGE).get_page(request.GET.get("page"))
    context = build_guild_member_context(
        member,
        announcements=list(announcement_page.object_list),
        announcement_page=announcement_page,
    )

    return render(request, "guilds/announcements.html", context)


@login_required
@require_guild_leader
@require_POST
@rate_limit_redirect("guild_announcement", limit=5, window_seconds=60)
def create_announcement(request: Any) -> HttpResponse:
    """创建公告（仅帮主）"""
    member = request.guild_member
    content = request.POST.get("content", "").strip()

    if not content:
        messages.error(request, "公告内容不能为空")
        return redirect("guilds:announcements")

    execute_guild_action(
        request,
        action=lambda: guild_service.create_announcement(member.guild, "leader", content, request.user),
        success_message="公告发布成功",
    )
    return redirect("guilds:announcements")
