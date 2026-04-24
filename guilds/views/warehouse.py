"""
帮会仓库视图
"""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.utils import safe_int, sanitize_error_message
from core.utils.rate_limit import rate_limit_redirect

from ..decorators import require_guild_member
from ..services import warehouse as warehouse_service
from .helpers import build_guild_member_context, execute_guild_action

# item_key 格式验证正则（只允许小写字母、数字、下划线）
ITEM_KEY_PATTERN = re.compile(r"^[a-z0-9_]+$")


@login_required
@require_guild_member
def warehouse(request):
    """帮会仓库"""
    member = request.guild_member

    # 获取分页参数
    page = safe_int(request.GET.get("page", 1), default=1, min_val=1)

    # 获取仓库物品列表（分页）
    warehouse_data = warehouse_service.get_warehouse_items(member.guild, page=page, per_page=50, member=member)
    context = build_guild_member_context(
        member,
        warehouse_items=warehouse_data["items"],
        pagination=warehouse_data,
    )

    return render(request, "guilds/warehouse.html", context)


@login_required
@require_guild_member
@require_POST
@rate_limit_redirect("guild_exchange_item", limit=20, window_seconds=60)
def exchange_item(request, item_key):
    """兑换物品"""
    # 验证 item_key 格式（防止注入攻击）
    if not item_key or not ITEM_KEY_PATTERN.match(item_key):
        messages.error(request, "无效的物品标识")
        return redirect("guilds:warehouse")

    member = request.guild_member
    quantity = safe_int(request.POST.get("quantity", 1), default=1, min_val=1, max_val=100)

    execute_guild_action(
        request,
        action=lambda: warehouse_service.exchange_item(member, item_key, quantity),
        success_message="兑换成功！",
        error_message_formatter=sanitize_error_message,
    )

    return redirect("guilds:warehouse")


@login_required
@require_guild_member
def exchange_logs(request):
    """兑换日志"""
    member = request.guild_member
    context = build_guild_member_context(
        member,
        logs=warehouse_service.get_exchange_logs(member.guild, 50),
    )

    return render(request, "guilds/exchange_logs.html", context)
