from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.utils import safe_int, sanitize_error_message

from ..decorators import require_guild_member
from ..models import Guild, GuildRaidRun
from ..services import guild_dispatch as guild_dispatch_service
from ..services import guild_pvp_queries as guild_pvp_query_service
from ..services import guild_raids as guild_raid_service
from .helpers import execute_guild_action


@login_required
@require_guild_member
def pvp_page(request: Any) -> HttpResponse:
    now = timezone.now()
    context = guild_pvp_query_service.get_guild_pvp_page_context(request.guild_member, now=now)
    return render(request, "guilds/pvp.html", context)


@login_required
@require_guild_member
@require_POST
def launch_guild_raid(request: Any) -> HttpResponse:
    member = request.guild_member
    defender_guild_id = safe_int(request.POST.get("defender_guild_id"), default=None, min_val=1)
    if defender_guild_id is None:
        return redirect("guilds:pvp")
    defender_guild = get_object_or_404(Guild, pk=defender_guild_id, is_active=True)

    execute_guild_action(
        request,
        action=lambda: guild_raid_service.start_guild_raid(
            guild=member.guild,
            operator=request.user,
            defender_guild=defender_guild,
            pool_entry_ids=request.POST.getlist("pool_entry_ids"),
            troop_loadout=guild_dispatch_service.parse_troop_loadout_from_post(request.POST),
        ),
        success_message="帮会部队已出征",
        error_message_formatter=sanitize_error_message,
    )
    return redirect("guilds:pvp")


@login_required
@require_guild_member
@require_POST
def retreat_guild_raid(request: Any) -> HttpResponse:
    member = request.guild_member
    run_id = safe_int(request.POST.get("run_id"), default=None, min_val=1)
    if run_id is None:
        return redirect("guilds:pvp")

    run = get_object_or_404(GuildRaidRun, pk=run_id, attacker_guild=member.guild)
    execute_guild_action(
        request,
        action=lambda: guild_raid_service.request_retreat(run=run, operator=request.user),
        success_message="帮会部队已撤回",
        error_message_formatter=sanitize_error_message,
    )
    return redirect("guilds:pvp")
