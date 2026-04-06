"""
护院募兵视图
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from urllib.parse import urlencode

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
from core.utils import safe_positive_int, sanitize_error_message
from core.utils.rate_limit import rate_limit_json, rate_limit_redirect
from gameplay.selectors.troop_recruitment import get_troop_recruitment_context
from gameplay.services.manor.core import get_manor
from gameplay.services.recruitment.recruitment import refresh_troop_recruitments
from gameplay.services.resources import project_resource_production_for_read
from gameplay.views.read_helpers import get_prepared_manor_for_read

from .runtime_refresh_support import run_refresh_api

logger = logging.getLogger(__name__)


def _recruitment_redirect(category: str | None = None) -> HttpResponse:
    base_url = reverse("gameplay:troop_recruitment")
    normalized_category = (category or "all").strip()
    if normalized_category and normalized_category != "all":
        return redirect(f"{base_url}?{urlencode({'category': normalized_category})}")
    return redirect(base_url)


def _handle_unexpected_recruitment_error(
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


def _handle_known_recruitment_error(request: HttpRequest, exc: GameError) -> None:
    messages.error(request, sanitize_error_message(exc))


class TroopRecruitmentView(LoginRequiredMixin, TemplateView):
    """护院募兵页面"""

    template_name = "gameplay/troop_recruitment.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_resource_production_for_read,
            logger=logger,
            source="troop_recruitment_view",
        )
        selected_category = (self.request.GET.get("category") or "all").strip() or "all"

        context["manor"] = manor
        context.update(get_troop_recruitment_context(manor, selected_category=selected_category))
        return context


def _troop_bank_post_input(request: HttpRequest) -> tuple[str | None, int | None]:
    troop_key = (request.POST.get("troop_key") or "").strip()
    quantity = safe_positive_int(request.POST.get("quantity"), default=None)
    if not troop_key:
        return None, None
    return troop_key, quantity


def _execute_troop_bank_transfer(
    request: HttpRequest,
    *,
    transfer_action: Callable[[Any, str, int], Any],
    success_message_template: str,
    log_message: str,
) -> HttpResponse:
    manor = get_manor(request.user)
    troop_key, quantity = _troop_bank_post_input(request)

    if not troop_key:
        messages.error(request, "请选择护院类型")
        return redirect("gameplay:troop_recruitment")
    if quantity is None:
        messages.error(request, "数量参数无效")
        return redirect("gameplay:troop_recruitment")

    try:
        result = transfer_action(manor, troop_key, quantity)
        messages.success(request, success_message_template.format(**result))
    except GameError as exc:
        _handle_known_recruitment_error(request, exc)
    except DatabaseError as exc:
        _handle_unexpected_recruitment_error(
            request,
            exc,
            log_message=log_message,
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                troop_key,
                quantity,
            ),
        )
    return redirect("gameplay:troop_recruitment")


@login_required
@require_POST
def start_troop_recruitment_view(request: HttpRequest) -> HttpResponse:
    """开始募兵"""
    manor = get_manor(request.user)
    selected_category = (request.POST.get("category") or "all").strip()
    troop_key = (request.POST.get("troop_key") or "").strip()
    quantity = safe_positive_int(request.POST.get("quantity"), default=None)

    if not troop_key:
        messages.error(request, "请选择兵种")
        return _recruitment_redirect(selected_category)
    if quantity is None:
        messages.error(request, "无效的数量")
        return _recruitment_redirect(selected_category)

    try:
        from gameplay.services.recruitment.recruitment import start_troop_recruitment

        recruitment = start_troop_recruitment(manor, troop_key, quantity)
        quantity_text = f"x{recruitment.quantity}" if recruitment.quantity > 1 else ""
        messages.success(
            request, f"{recruitment.troop_name}{quantity_text} 开始募兵，预计 {recruitment.actual_duration} 秒后完成"
        )
    except GameError as exc:
        _handle_known_recruitment_error(request, exc)
    except DatabaseError as exc:
        _handle_unexpected_recruitment_error(
            request,
            exc,
            log_message="Unexpected troop recruitment start error: manor_id=%s user_id=%s troop_key=%s quantity=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                troop_key,
                request.POST.get("quantity"),
            ),
        )
    return _recruitment_redirect(selected_category)


@login_required
@require_POST
@rate_limit_redirect("deposit_troop_to_bank", limit=30, window_seconds=60)
def deposit_troop_to_bank_view(request: HttpRequest) -> HttpResponse:
    from gameplay.services.manor.troop_bank import deposit_troops_to_bank

    return _execute_troop_bank_transfer(
        request,
        transfer_action=deposit_troops_to_bank,
        success_message_template="已存入 {quantity} 名{troop_name}到钱庄",
        log_message="Unexpected troop bank deposit error: manor_id=%s user_id=%s troop_key=%s quantity=%s",
    )


@login_required
@require_POST
@rate_limit_redirect("withdraw_troop_from_bank", limit=30, window_seconds=60)
def withdraw_troop_from_bank_view(request: HttpRequest) -> HttpResponse:
    from gameplay.services.manor.troop_bank import withdraw_troops_from_bank

    return _execute_troop_bank_transfer(
        request,
        transfer_action=withdraw_troops_from_bank,
        success_message_template="已从钱庄取出 {quantity} 名{troop_name}",
        log_message="Unexpected troop bank withdraw error: manor_id=%s user_id=%s troop_key=%s quantity=%s",
    )


@login_required
@require_POST
@rate_limit_json(
    "troop_recruitment_runtime_refresh", limit=30, window_seconds=60, error_message="状态刷新过于频繁，请稍后再试"
)
def refresh_troop_recruitments_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    return run_refresh_api(
        operation=lambda: refresh_troop_recruitments(manor),
        logger_instance=logger,
        log_message="Unexpected troop recruitment refresh error: manor_id=%s user_id=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
        ),
    )
