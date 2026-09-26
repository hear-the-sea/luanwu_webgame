"""逍遥山庄门客页面与操作。"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.exceptions import GameError
from core.utils import sanitize_error_message
from gameplay.services.manor.core import get_manor, project_manor_activity_for_read
from gameplay.views.read_helpers import get_prepared_manor_for_read
from guests.models import GuestStatus
from guests.services.vacation import move_guest_to_xiaoyao, return_guest_from_xiaoyao

logger = logging.getLogger(__name__)


class XiaoyaoShanzhuangView(LoginRequiredMixin, TemplateView):
    template_name = "gameplay/xiaoyao_shanzhuang.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_manor_activity_for_read,
            logger=logger,
            source="xiaoyao_shanzhuang_view",
        )
        guests = list(
            manor.guests.filter(status=GuestStatus.VACATION).select_related("template").order_by("-level", "id")
        )
        context.update(
            manor=manor,
            guests=guests,
            capacity=manor.xiaoyao_capacity,
            active_guest_count=manor.active_guest_count,
        )
        return context


@login_required
@require_POST
def move_guest_to_xiaoyao_view(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        name = move_guest_to_xiaoyao(get_manor(request.user), pk)
        messages.success(request, f"{name} 已入住逍遥山庄")
    except GameError as exc:
        messages.error(request, sanitize_error_message(exc))
    except DatabaseError as exc:
        logger.exception("Move guest to xiaoyao failed: user_id=%s guest_id=%s", request.user.pk, pk)
        messages.error(request, sanitize_error_message(exc))
    return redirect("guests:detail", pk=pk)


@login_required
@require_POST
def return_guest_from_xiaoyao_view(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        name = return_guest_from_xiaoyao(get_manor(request.user), pk)
        messages.success(request, f"{name} 已返回庄园")
    except GameError as exc:
        messages.error(request, sanitize_error_message(exc))
    except DatabaseError as exc:
        logger.exception("Return guest from xiaoyao failed: user_id=%s guest_id=%s", request.user.pk, pk)
        messages.error(request, sanitize_error_message(exc))
    return redirect("gameplay:xiaoyao_shanzhuang")
