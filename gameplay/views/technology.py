"""
科技研究视图
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.decorators import flash_unexpected_view_error
from core.exceptions import GameError
from core.utils import safe_redirect_url, sanitize_error_message
from core.utils.rate_limit import rate_limit_json
from gameplay.selectors.technology import (
    get_technology_page_context,
    normalize_martial_troop_class,
    normalize_technology_tab,
)
from gameplay.services.manor.core import get_manor
from gameplay.services.resources import project_resource_production_for_read
from gameplay.services.technology import refresh_technology_upgrades, upgrade_technology
from gameplay.views.read_helpers import get_prepared_manor_for_read

from .runtime_refresh_support import run_refresh_api

logger = logging.getLogger(__name__)


def _build_technology_redirect_url(tab: str, troop: str = "") -> str:
    redirect_url = f"{reverse('gameplay:technology')}?tab={tab}"
    if tab == "martial" and troop:
        redirect_url += f"&troop={troop}"
    return redirect_url


def _handle_known_technology_error(request: HttpRequest, exc: GameError) -> None:
    messages.error(request, sanitize_error_message(exc))


def _handle_unexpected_technology_error(
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


class TechnologyView(LoginRequiredMixin, TemplateView):
    """技术研究页面"""

    template_name = "gameplay/technology.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_resource_production_for_read,
            logger=logger,
            source="technology_view",
        )
        context["manor"] = manor
        context.update(
            get_technology_page_context(
                manor,
                current_tab=self.request.GET.get("tab") or "",
                current_troop_class=self.request.GET.get("troop") or "",
            )
        )
        return context


@login_required
@require_POST
def upgrade_technology_view(request: HttpRequest, tech_key: str) -> HttpResponse:
    """升级技术"""
    manor = get_manor(request.user)
    tab = normalize_technology_tab(request.POST.get("tab"))
    troop = normalize_martial_troop_class(request.POST.get("troop")) if tab == "martial" else ""
    next_url = (request.POST.get("next") or "").strip()

    try:
        result = upgrade_technology(manor, tech_key)
        messages.success(request, result["message"])
    except GameError as exc:
        _handle_known_technology_error(request, exc)
    except DatabaseError as exc:
        _handle_unexpected_technology_error(
            request,
            exc,
            log_message="Unexpected technology upgrade view error: manor_id=%s user_id=%s tech_key=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                tech_key,
            ),
        )

    # 构建重定向URL，保留子分类参数
    redirect_url = _build_technology_redirect_url(tab, troop)
    redirect_url = safe_redirect_url(request, next_url, redirect_url)
    return redirect(redirect_url)


@login_required
@require_POST
@rate_limit_json(
    "technology_runtime_refresh", limit=30, window_seconds=60, error_message="状态刷新过于频繁，请稍后再试"
)
def refresh_technology_upgrades_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    return run_refresh_api(
        operation=lambda: refresh_technology_upgrades(manor),
        logger_instance=logger,
        log_message="Unexpected technology refresh error: manor_id=%s user_id=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
        ),
    )
