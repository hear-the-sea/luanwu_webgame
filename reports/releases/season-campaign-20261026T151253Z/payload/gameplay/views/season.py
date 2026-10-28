"""赛季页面与显式 POST 命令入口。"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.exceptions import GameError
from core.utils.rate_limit import rate_limit_redirect
from gameplay.selectors.season import get_season_context
from gameplay.services.manor.bootstrap import get_manor
from gameplay.services.season.actions import perform_action

logger = logging.getLogger(__name__)


@login_required
def season_view(request: HttpRequest) -> HttpResponse:
    try:
        season_id = int(request.GET["season_id"]) if request.GET.get("season_id") else None
    except (ValueError, TypeError):
        season_id = None
    return render(request, "gameplay/season.html", get_season_context(get_manor(request.user), season_id))


@login_required
@require_POST
@rate_limit_redirect("season_action", limit=30, window_seconds=60)
def season_action_view(request: HttpRequest) -> HttpResponse:
    season_id = None
    try:
        season_id = int(request.POST.get("season_id", ""))
        payload = {
            "preference": request.POST.get("preference", "random"),
            "registration_playtime": request.POST.get("registration_playtime", "any"),
            "hours": int(request.POST.get("hours", "1")),
            "week_number": int(request.POST.get("week_number", "0")),
            "node_key": request.POST.get("node_key", ""),
            "reward_key": request.POST.get("reward_key", ""),
            "item_key": request.POST.get("item_key", ""),
            "amount": int(request.POST.get("amount", "0")),
            "guest_ids": [int(value) for value in request.POST.getlist("guest_ids")],
            "template_ids": [int(value) for value in request.POST.getlist("template_ids")],
            "mode": request.POST.get("mode", ""),
            "run_id": int(request.POST.get("run_id", "0")),
            "rally_id": int(request.POST.get("rally_id", "0")),
            "station_hours": int(request.POST.get("station_hours", "1")),
            "source_resource": request.POST.get("source_resource", ""),
            "target_resource": request.POST.get("target_resource", ""),
            "target_site_key": request.POST.get("target_site_key", ""),
            "scout_site_key": request.POST.get("scout_site_key", ""),
            "target_run_id": int(request.POST.get("target_run_id", "0")),
            "upgrade_key": request.POST.get("upgrade_key", ""),
            "troops": {
                key.removeprefix("troop_"): int(request.POST[key]) for key in request.POST if key.startswith("troop_")
            },
        }
        if request.POST.get("rally_target_members"):
            payload["rally_target_members"] = int(request.POST["rally_target_members"])
        if request.POST.get("market_period") is not None:
            payload["market_period"] = int(request.POST["market_period"])
        result = perform_action(
            get_manor(request.user),
            season_id,
            request.POST.get("action", ""),
            request.POST.get("action_token", ""),
            payload,
        )
        messages.success(request, result["message"])
    except (ValueError, TypeError):
        messages.error(request, "赛季参数无效，请刷新页面后重试")
    except GameError as exc:
        messages.error(request, str(exc))
    except DatabaseError:
        logger.exception("season action database failure: user=%s", request.user.pk)
        messages.error(request, "操作暂未完成，请稍后重试；重复提交不会重复发奖")
    url = reverse("gameplay:season")
    return redirect(f"{url}?season_id={season_id}" if season_id else url)
