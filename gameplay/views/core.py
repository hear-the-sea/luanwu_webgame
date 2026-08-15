"""
核心页面视图：首页、仪表盘、设置、排行榜
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.decorators import flash_unexpected_view_error
from core.exceptions import GameError
from core.utils import sanitize_error_message
from gameplay.selectors.core import get_dashboard_context, get_ranking_page_context, get_settings_page_context
from gameplay.selectors.home import get_home_context
from gameplay.services.action_points import ACTION_POINTS_MAX, get_current_action_points
from gameplay.services.manor.core import get_manor, project_manor_activity_for_read, rename_manor
from gameplay.services.resources import ResourceProductionBasis, project_resource_production_for_read
from gameplay.utils.resource_calculator import get_hourly_rates
from gameplay.views.read_helpers import get_prepared_manor_for_read, get_prepared_manor_for_read_result

logger = logging.getLogger(__name__)


def _handle_unexpected_core_error(
    request: HttpRequest,
    exc: Exception,
    *,
    log_message: str,
    log_args: tuple[object, ...],
) -> None:
    flash_unexpected_view_error(
        request,
        exc,
        log_message=log_message,
        log_args=log_args,
        logger_instance=logger,
    )


def _handle_known_core_error(request: HttpRequest, exc: GameError) -> None:
    messages.error(request, sanitize_error_message(exc))


class DashboardView(LoginRequiredMixin, TemplateView):
    """建筑仪表盘页面"""

    template_name = "gameplay/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_resource_production_for_read,
            logger=logger,
            source="dashboard_view",
        )
        context["manor"] = manor
        context.update(get_dashboard_context(manor, category=self.kwargs.get("category", "resource")))
        return context


class HomeView(TemplateView):
    """游戏首页/着陆页"""

    template_name = "landing.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if user.is_authenticated:
            prepared_manor = get_prepared_manor_for_read_result(
                self.request,
                logger=logger,
                source="home_view",
                project_fn=project_manor_activity_for_read,
            )
            manor = prepared_manor.manor
            production_basis = (
                prepared_manor.projection_result
                if prepared_manor.projection_succeeded
                and isinstance(prepared_manor.projection_result, ResourceProductionBasis)
                else None
            )
            if production_basis is None:
                context.update(get_home_context(manor))
            else:
                context.update(get_home_context(manor, production_basis=production_basis))

        return context


class SettingsView(LoginRequiredMixin, TemplateView):
    """设置页面"""

    template_name = "gameplay/settings.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_manor(self.request.user)

        context["manor"] = manor
        context.update(get_settings_page_context(manor))

        return context


class GuideView(LoginRequiredMixin, TemplateView):
    """游戏攻略页面"""

    template_name = "gameplay/guide.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_resource_production_for_read,
            logger=logger,
            source="guide_view",
        )
        hourly_rates = get_hourly_rates(manor)
        context["manor"] = manor
        context["guide_snapshot"] = {
            "action_points": get_current_action_points(manor),
            "action_points_max": ACTION_POINTS_MAX,
            "guest_count": manor.guests.count(),
            "guest_capacity": manor.guest_capacity,
            "max_squad_size": manor.max_squad_size,
            "hourly_grain": round(float(hourly_rates.get("grain", 0))),
            "hourly_silver": round(float(hourly_rates.get("silver", 0))),
            "protection_label": "已保护" if manor.is_protected else "未保护",
        }
        return context


@login_required
@require_POST
def rename_manor_view(request: HttpRequest) -> HttpResponse:
    """庄园更名"""
    manor = get_manor(request.user)
    new_name = request.POST.get("new_name", "").strip()

    if not new_name:
        messages.error(request, "请输入新名称")
        return redirect("gameplay:settings")

    try:
        rename_manor(manor, new_name)
        messages.success(request, f"庄园已成功更名为「{new_name}」")
    except GameError as exc:
        _handle_known_core_error(request, exc)
    except DatabaseError as exc:
        _handle_unexpected_core_error(
            request,
            exc,
            log_message="Unexpected manor rename error: manor_id=%s user_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
            ),
        )

    return redirect("gameplay:settings")


class RankingView(LoginRequiredMixin, TemplateView):
    """声望排行榜页面"""

    template_name = "gameplay/ranking.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_manor(self.request.user)

        context["manor"] = manor
        context.update(get_ranking_page_context(manor))

        return context
