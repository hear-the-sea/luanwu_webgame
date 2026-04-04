"""
帮会贡献视图：捐献、排名、资源日志
"""

from typing import Any

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

import guilds.constants as guild_constants
from core.utils import safe_int, sanitize_error_message
from core.utils.rate_limit import rate_limit_redirect
from gameplay.models import InventoryItem, Manor, PlayerTroop

from ..decorators import require_guild_member
from ..models import GuildTroopStorage, GuildWarehouse
from ..services import contribution as contribution_service
from .helpers import build_guild_member_context, execute_guild_action, load_donation_logs, load_resource_logs


def _load_red_ruby_count(guild: Any) -> int:
    return int(
        GuildWarehouse.objects.filter(guild=guild, item_key="red_ruby").aggregate(total=Sum("quantity"))["total"] or 0
    )


def _load_troop_storages(guild: Any) -> list[GuildTroopStorage]:
    return list(
        GuildTroopStorage.objects.filter(guild=guild, count__gt=0)
        .select_related("troop_template")
        .order_by("-count", "troop_template__name")
    )


def _load_gold_bar_inventory(manor: Manor) -> int:
    return int(
        InventoryItem.objects.filter(
            manor=manor,
            template__key="gold_bar",
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).aggregate(total=Sum("quantity"))["total"]
        or 0
    )


def _build_resource_page_context(member: Any, *, manor: Manor, page_mode: str) -> dict[str, Any]:
    guild = member.guild
    troop_storages = _load_troop_storages(guild)
    player_troops = list(
        PlayerTroop.objects.filter(manor=manor, count__gt=0).select_related("troop_template").order_by("-count", "id")
    )
    today = timezone.localdate()
    display_daily_donation_silver = int(member.daily_donation_silver)
    display_daily_donation_grain = int(member.daily_donation_grain)
    display_daily_donation_gold_bar = int(member.daily_donation_gold_bar)
    if member.daily_donation_reset_at is None or member.daily_donation_reset_at < today:
        display_daily_donation_silver = 0
        display_daily_donation_grain = 0
        display_daily_donation_gold_bar = 0

    donation_entries = {
        "silver": {
            "label": "银两",
            "rate": guild_constants.CONTRIBUTION_RATES.get("silver", 0),
            "available": int(manor.silver),
            "donated_today": display_daily_donation_silver,
            "daily_limit": int(guild_constants.DAILY_DONATION_LIMITS.get("silver", 0)),
            "resource_type": "silver",
        },
        "grain": {
            "label": "粮食",
            "rate": guild_constants.CONTRIBUTION_RATES.get("grain", 0),
            "available": int(manor.grain),
            "donated_today": display_daily_donation_grain,
            "daily_limit": int(guild_constants.DAILY_DONATION_LIMITS.get("grain", 0)),
            "resource_type": "grain",
        },
        "gold_bar": {
            "label": "金条",
            "rate": guild_constants.CONTRIBUTION_RATES.get("gold_bar", 0),
            "available": _load_gold_bar_inventory(manor),
            "donated_today": display_daily_donation_gold_bar,
            "daily_limit": int(guild_constants.DAILY_DONATION_LIMITS.get("gold_bar", 0)),
            "resource_type": "gold_bar",
        },
    }

    for key, entry in donation_entries.items():
        entry["remaining_today"] = max(0, int(entry["daily_limit"]) - int(entry["donated_today"]))
        entry["max_amount"] = max(0, min(int(entry["remaining_today"]), int(entry["available"])))
        entry["input_id"] = f"{key}_amount"

    troop_total_count = sum(storage.count for storage in troop_storages)
    troop_preview = troop_storages[:3]

    return build_guild_member_context(
        member,
        manor=manor,
        page_mode=page_mode,
        red_ruby_count=_load_red_ruby_count(guild),
        troop_storages=troop_storages,
        player_troops=player_troops,
        troop_overview={
            "total_count": troop_total_count,
            "kinds_count": len(troop_storages),
            "preview": troop_preview,
        },
        donation_entries=donation_entries,
        contribution_rates=guild_constants.CONTRIBUTION_RATES,
        daily_limits=guild_constants.DAILY_DONATION_LIMITS,
    )


def build_guild_resource_context(member: Any, *, manor: Manor, page_mode: str = "detail") -> dict[str, Any]:
    return _build_resource_page_context(member, manor=manor, page_mode=page_mode)


@login_required
@require_guild_member
@rate_limit_redirect("guild_donate", limit=10, window_seconds=60)
def donate_resource(request: Any) -> HttpResponse:
    """捐赠资源"""
    member = request.guild_member

    if request.method != "POST":
        return redirect("guilds:detail", guild_id=member.guild_id)

    resource_type = request.POST.get("resource_type")
    amount = safe_int(request.POST.get("amount", 0), default=0, min_val=0)

    outcome = execute_guild_action(
        request,
        action=lambda: contribution_service.donate_resource(member, resource_type, amount),
        success_message="捐赠成功！您获得了相应的贡献度",
        error_message_formatter=sanitize_error_message,
    )
    if outcome.succeeded:
        return redirect("guilds:detail", guild_id=member.guild_id)

    return redirect("guilds:detail", guild_id=member.guild_id)


@login_required
@require_guild_member
def contribution_ranking(request: Any) -> HttpResponse:
    """贡献排行榜"""
    member = request.guild_member
    guild = member.guild

    ranking_type = request.GET.get("type", "total")  # total 或 weekly
    page = safe_int(request.GET.get("page", 1), default=1, min_val=1) or 1
    page_size = 20

    # 获取所有排名数据
    all_rankings = contribution_service.get_contribution_ranking(guild, ranking_type, limit=None)

    # 使用 Django 分页器
    from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

    paginator = Paginator(all_rankings, page_size)

    try:
        rankings = paginator.page(page)
    except PageNotAnInteger:
        rankings = paginator.page(1)
    except EmptyPage:
        rankings = paginator.page(paginator.num_pages)

    my_rank = contribution_service.get_my_contribution_rank(member, ranking_type)

    context = build_guild_member_context(
        member,
        rankings=rankings,
        my_rank=my_rank,
        ranking_type=ranking_type,
        page=page,
    )

    return render(request, "guilds/contribution_ranking.html", context)


@login_required
@require_guild_member
def resource_status(request: Any) -> HttpResponse:
    """资源状态"""
    member = request.guild_member
    manor = get_object_or_404(Manor, user=request.user)

    context = build_guild_resource_context(member, manor=manor, page_mode="resources")

    return render(request, "guilds/resources.html", context)


@login_required
@require_guild_member
def donation_logs(request: Any) -> HttpResponse:
    """捐赠日志"""
    member = request.guild_member
    context = build_guild_member_context(
        member,
        logs=load_donation_logs(member.guild, limit=50),
    )

    return render(request, "guilds/donation_logs.html", context)


@login_required
@require_guild_member
def resource_logs(request: Any) -> HttpResponse:
    """资源日志"""
    member = request.guild_member
    context = build_guild_member_context(
        member,
        logs=load_resource_logs(member.guild, limit=50),
    )

    return render(request, "guilds/resource_logs.html", context)
