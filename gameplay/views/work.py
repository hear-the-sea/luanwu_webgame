"""
打工系统视图
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.decorators import flash_unexpected_view_error
from core.exceptions import GameError
from core.utils import safe_positive_int, safe_redirect_url, sanitize_error_message
from gameplay.models import WorkAssignment, WorkTemplate
from gameplay.selectors.work import get_work_page_context
from gameplay.services.manor.core import get_manor, project_manor_activity_for_read
from gameplay.services.work import (
    assign_guest_to_work,
    claim_work_reward,
    recall_guest_from_work,
    refresh_work_assignments,
)
from gameplay.views.read_helpers import get_prepared_manor_for_read
from guests.models import Guest

logger = logging.getLogger(__name__)


def _handle_unexpected_work_error(
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


def _handle_known_work_error(request: HttpRequest, exc: GameError) -> None:
    messages.error(request, sanitize_error_message(exc))


def _resolve_work_redirect_url(request: HttpRequest) -> str:
    return safe_redirect_url(
        request,
        (request.POST.get("next") or request.GET.get("next") or "").strip(),
        reverse("gameplay:work"),
    )


class WorkView(LoginRequiredMixin, TemplateView):
    """打工页面"""

    template_name = "gameplay/work.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_manor_activity_for_read,
            logger=logger,
            source="work_view",
        )
        page = safe_positive_int(self.request.GET.get("page"), default=1) or 1

        context["manor"] = manor
        context.update(
            get_work_page_context(
                manor,
                current_tier=self.request.GET.get("tier") or "junior",
                page=page,
            )
        )
        return context


@login_required
@require_POST
def assign_work_view(request: HttpRequest) -> HttpResponse:
    """派遣门客打工"""
    redirect_url = _resolve_work_redirect_url(request)
    manor = get_manor(request.user)
    guest_id = safe_positive_int(request.POST.get("guest_id"), default=None)
    work_key = (request.POST.get("work_key") or "").strip()

    if guest_id is None or not work_key:
        messages.error(request, "参数错误")
        return redirect(redirect_url)

    try:
        refresh_work_assignments(manor)
    except DatabaseError as exc:
        _handle_unexpected_work_error(
            request,
            exc,
            log_message="Unexpected work refresh before assign: manor_id=%s user_id=%s guest_id=%s work_key=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                guest_id,
                work_key,
            ),
        )
        return redirect(redirect_url)

    guest = get_object_or_404(Guest, id=guest_id, manor=manor)
    work_template = get_object_or_404(WorkTemplate, key=work_key)

    try:
        assign_guest_to_work(guest, work_template)
        # 计算完成时间（小时）
        hours = work_template.work_duration / 3600
        messages.success(request, f"{guest.display_name} 已前往 {work_template.name} 打工，预计 {hours:.1f} 小时后完成")
    except GameError as exc:
        _handle_known_work_error(request, exc)
    except DatabaseError as exc:
        _handle_unexpected_work_error(
            request,
            exc,
            log_message="Unexpected work assign error: manor_id=%s user_id=%s guest_id=%s work_key=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                guest_id,
                work_key,
            ),
        )

    return redirect(redirect_url)


@login_required
@require_POST
def recall_work_view(request: HttpRequest, pk: int) -> HttpResponse:
    """召回打工中的门客"""
    redirect_url = _resolve_work_redirect_url(request)
    manor = get_manor(request.user)

    try:
        refresh_work_assignments(manor)
    except DatabaseError as exc:
        _handle_unexpected_work_error(
            request,
            exc,
            log_message="Unexpected work refresh before recall: manor_id=%s user_id=%s assignment_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
            ),
        )
        return redirect(redirect_url)

    assignment = get_object_or_404(
        WorkAssignment.objects.select_related("guest", "work_template"),
        id=pk,
        manor=manor,
    )
    if assignment.status == WorkAssignment.Status.COMPLETED and not assignment.reward_claimed:
        messages.info(request, f"{assignment.guest.display_name} 的打工已完成，请先领取报酬")
        return redirect(redirect_url)
    if assignment.status != WorkAssignment.Status.WORKING:
        raise Http404("打工任务不存在")

    try:
        recall_guest_from_work(assignment)
        messages.success(
            request, f"{assignment.guest.display_name} 已从 {assignment.work_template.name} 召回（无报酬）"
        )
    except GameError as exc:
        _handle_known_work_error(request, exc)
    except DatabaseError as exc:
        _handle_unexpected_work_error(
            request,
            exc,
            log_message="Unexpected work recall error: manor_id=%s user_id=%s assignment_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
            ),
        )

    return redirect(redirect_url)


@login_required
@require_POST
def claim_work_reward_view(request: HttpRequest, pk: int) -> HttpResponse:
    """领取打工报酬"""
    redirect_url = _resolve_work_redirect_url(request)
    manor = get_manor(request.user)

    try:
        refresh_work_assignments(manor)
    except DatabaseError as exc:
        _handle_unexpected_work_error(
            request,
            exc,
            log_message="Unexpected work refresh before claim: manor_id=%s user_id=%s assignment_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
            ),
        )
        return redirect(redirect_url)

    assignment = get_object_or_404(WorkAssignment, id=pk, manor=manor)

    try:
        reward = claim_work_reward(assignment)
        messages.success(request, f"{assignment.guest.display_name} 完成打工，获得银两 {reward['silver']}")
    except GameError as exc:
        _handle_known_work_error(request, exc)
    except DatabaseError as exc:
        _handle_unexpected_work_error(
            request,
            exc,
            log_message="Unexpected work reward claim error: manor_id=%s user_id=%s assignment_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
            ),
        )

    return redirect(redirect_url)
