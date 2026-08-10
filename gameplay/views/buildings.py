"""
建筑升级视图
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.decorators import flash_unexpected_view_error
from core.exceptions import GameError
from core.utils import safe_redirect_url, sanitize_error_message
from core.utils.rate_limit import rate_limit_json
from gameplay.models import Building
from gameplay.services.city_defense import repair_city_defense
from gameplay.services.manor.core import finalize_upgrades, get_manor, start_upgrade

from .runtime_refresh_support import run_refresh_api

logger = logging.getLogger(__name__)


def _refresh_building_upgrades(manor) -> int:
    finalize_upgrades(manor)
    return 0


def _handle_unexpected_building_error(
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


def _handle_known_building_error(request: HttpRequest, exc: GameError) -> None:
    messages.error(request, sanitize_error_message(exc))


@method_decorator(require_POST, name="dispatch")
class UpgradeBuildingView(LoginRequiredMixin, TemplateView):
    """建筑升级视图"""

    http_method_names = ["post"]
    success_url = reverse_lazy("gameplay:dashboard")

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        redirect_url = safe_redirect_url(
            request,
            (request.POST.get("next") or "").strip(),
            str(self.success_url),
        )
        building = get_object_or_404(
            Building.objects.select_related("manor", "manor__user"),
            pk=kwargs["pk"],
            manor__user=request.user,
        )
        try:
            start_upgrade(building)
            eta = (
                timezone.localtime(building.upgrade_complete_at).strftime("%H:%M:%S")
                if building.upgrade_complete_at
                else ""
            )
            messages.success(request, f"{building.building_type.name} 开始升级，完成时间 {eta}")
        except GameError as exc:
            _handle_known_building_error(request, exc)
        except DatabaseError as exc:
            _handle_unexpected_building_error(
                request,
                exc,
                log_message="Unexpected building upgrade view error: manor_id=%s user_id=%s building_id=%s",
                log_args=(
                    getattr(building.manor, "id", None),
                    getattr(request.user, "id", None),
                    getattr(building, "id", None),
                ),
            )
        return redirect(redirect_url)


@method_decorator(require_POST, name="dispatch")
class RepairCityDefenseView(LoginRequiredMixin, TemplateView):
    """城防耐久修复视图"""

    http_method_names = ["post"]
    success_url = reverse_lazy("gameplay:dashboard")

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        redirect_url = safe_redirect_url(
            request,
            (request.POST.get("next") or "").strip(),
            str(self.success_url),
        )
        building = get_object_or_404(
            Building.objects.select_related("manor", "manor__user", "building_type"),
            pk=kwargs["pk"],
            manor__user=request.user,
        )
        try:
            cost = repair_city_defense(building)
            if cost > 0:
                messages.success(request, f"{building.building_type.name} 修复完成，消耗银两 {cost}")
            else:
                messages.info(request, f"{building.building_type.name} 耐久已满")
        except GameError as exc:
            _handle_known_building_error(request, exc)
        except DatabaseError as exc:
            _handle_unexpected_building_error(
                request,
                exc,
                log_message="Unexpected city defense repair view error: manor_id=%s user_id=%s building_id=%s",
                log_args=(
                    getattr(building.manor, "id", None),
                    getattr(request.user, "id", None),
                    getattr(building, "id", None),
                ),
            )
        return redirect(redirect_url)


@login_required
@require_POST
@rate_limit_json("building_runtime_refresh", limit=30, window_seconds=60, error_message="状态刷新过于频繁，请稍后再试")
def refresh_building_upgrades_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    return run_refresh_api(
        operation=lambda: _refresh_building_upgrades(manor),
        logger_instance=logger,
        log_message="Unexpected building refresh error: manor_id=%s user_id=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
        ),
    )
