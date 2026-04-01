"""帮会任务视图。"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.utils import safe_int, sanitize_error_message

from ..decorators import require_guild_member
from ..models import GuildMissionRun
from ..services import guild_missions as guild_mission_service
from ..services import guild_troops as guild_troop_service
from .helpers import execute_guild_action


@login_required
@require_guild_member
def missions(request: Any) -> HttpResponse:
    selected_mission_key = str(request.GET.get("mission", "")).strip()
    context = guild_mission_service.get_guild_mission_page_context(
        request.guild_member,
        selected_mission_key=selected_mission_key,
    )
    return render(request, "guilds/missions.html", context)


@login_required
@require_guild_member
@require_POST
def launch_mission(request: Any) -> HttpResponse:
    member = request.guild_member
    template_key = str(request.POST.get("template_key", "")).strip()
    pool_entry_ids = request.POST.getlist("pool_entry_ids")
    troop_loadout = guild_mission_service.parse_troop_loadout_from_post(request.POST)

    execute_guild_action(
        request,
        action=lambda: guild_mission_service.launch_guild_mission(
            guild=member.guild,
            operator=request.user,
            template_key=template_key,
            pool_entry_ids=pool_entry_ids,
            troop_loadout=troop_loadout,
        ),
        success_message="帮会任务已出征",
        error_message_formatter=sanitize_error_message,
    )
    return redirect("guilds:missions")


@login_required
@require_guild_member
@require_POST
def retreat_mission(request: Any) -> HttpResponse:
    member = request.guild_member
    run_id = safe_int(request.POST.get("run_id"), default=None, min_val=1)
    if run_id is None:
        messages.error(request, "参数错误")
        return redirect("guilds:missions")

    run = get_object_or_404(GuildMissionRun, pk=run_id, guild=member.guild)
    execute_guild_action(
        request,
        action=lambda: guild_mission_service.request_retreat(run=run, operator=request.user),
        success_message="帮会任务已撤回",
        error_message_formatter=sanitize_error_message,
    )
    return redirect("guilds:missions")


@login_required
@require_guild_member
@require_POST
def donate_troops(request: Any) -> HttpResponse:
    member = request.guild_member
    troop_key = str(request.POST.get("troop_key", "")).strip()
    quantity = safe_int(request.POST.get("quantity"), default=0)
    resolved_quantity = int(quantity or 0)

    execute_guild_action(
        request,
        action=lambda: guild_troop_service.donate_troops(
            member=member,
            troop_key=troop_key,
            quantity=resolved_quantity,
        ),
        success_message="护院已捐赠到帮会护院池",
        error_message_formatter=sanitize_error_message,
    )
    return redirect("guilds:detail", guild_id=member.guild_id)
