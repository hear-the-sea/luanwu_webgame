"""
任务系统视图：任务面板、出征、撤退
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.utils.rate_limit import rate_limit_json
from gameplay.models import ScoutRecord
from gameplay.services import missions as mission_service
from gameplay.services import raid as raid_service
from gameplay.services.manor.core import get_manor
from gameplay.services.missions_impl.attempts import add_mission_extra_attempt
from gameplay.services.missions_impl.execution import launch_mission, request_retreat

from . import mission_helpers
from .mission_action_handlers import (
    handle_accept_mission,
    handle_retreat_mission,
    handle_retreat_scout,
    handle_use_mission_card,
)
from .mission_page_context import build_task_board_context, build_troop_config
from .runtime_refresh_support import run_refresh_api

logger = logging.getLogger(__name__)


def _refresh_mission_runtime(manor: Any) -> int:
    mission_service.refresh_mission_runs(manor)
    return 0


class TaskBoardView(LoginRequiredMixin, TemplateView):
    """任务面板页面"""

    template_name = "gameplay/tasks.html"

    def _build_troop_config(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        return build_troop_config()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(build_task_board_context(self.request))
        return context


@method_decorator(require_POST, name="dispatch")
class AcceptMissionView(LoginRequiredMixin, TemplateView):
    """接受任务出征"""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        manor = get_manor(request.user)
        mission, redirect_response = mission_helpers.resolve_mission_or_redirect(
            request, request.POST.get("mission_key")
        )
        if redirect_response is not None:
            return redirect_response
        if mission is None:
            return mission_helpers.mission_tasks_redirect()
        return handle_accept_mission(
            request,
            manor=manor,
            mission=mission,
            launch_mission_fn=launch_mission,
        )


@login_required
@require_POST
@rate_limit_json("mission_runtime_refresh", limit=30, window_seconds=60, error_message="状态刷新过于频繁，请稍后再试")
def refresh_mission_runs_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    return run_refresh_api(
        operation=lambda: _refresh_mission_runtime(manor),
        logger_instance=logger,
        log_message="Unexpected mission refresh error: manor_id=%s user_id=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
        ),
    )


@login_required
@require_POST
def retreat_mission_view(request: HttpRequest, pk: int) -> HttpResponse:
    """任务撤退"""
    manor = get_manor(request.user)
    return handle_retreat_mission(request, manor=manor, pk=pk, request_retreat_fn=request_retreat)


@login_required
@require_POST
def retreat_scout_view(request: HttpRequest, pk: int) -> HttpResponse:
    """侦察撤退视图"""
    manor = get_manor(request.user)
    return handle_retreat_scout(
        request,
        manor=manor,
        pk=pk,
        scout_record_model=ScoutRecord,
        request_scout_retreat_fn=raid_service.request_scout_retreat,
    )


@login_required
@require_POST
def use_mission_card_view(request: HttpRequest) -> HttpResponse:
    """
    使用任务卡增加任务次数。

    消耗一张任务卡，为指定任务增加1次今日额外次数。
    """
    manor = get_manor(request.user)
    mission, redirect_response = mission_helpers.resolve_mission_or_redirect(request, request.POST.get("mission_key"))
    if redirect_response is not None:
        return redirect_response
    if mission is None:
        return mission_helpers.mission_tasks_redirect()
    return handle_use_mission_card(
        request,
        manor=manor,
        mission=mission,
        add_mission_extra_attempt_fn=add_mission_extra_attempt,
    )
