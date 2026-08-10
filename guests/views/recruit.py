"""
门客招募视图：招募、候选处理、放大镜
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.utils import is_json_request, json_error, safe_positive_int
from core.utils.locked_actions import acquire_scoped_action_lock, release_scoped_action_lock
from core.utils.rate_limit import rate_limit_redirect
from gameplay.services.manor.core import get_manor

from ..forms import RecruitForm
from ..models import RecruitmentCandidate, RecruitmentPool
from ..services.recruitment import start_guest_recruitment, use_magnifying_glass_for_candidates
from ..services.recruitment_guests import bulk_finalize_candidates, discard_candidates, retain_candidates
from ..services.recruitment_quota import add_recruitment_extra_attempt_with_item_cost
from ..services.recruitment_shared import CORE_POOL_TIERS
from .recruit_action_runtime import RECRUIT_ACTION_LOCK_SPEC, run_locked_recruit_action
from .recruit_handlers import (
    handle_candidate_accept,
    handle_magnifying_glass_reveal,
    handle_recruit_draw,
    handle_recruitment_card_use,
)
from .recruit_responses import candidate_action_success_response as _candidate_action_success_response
from .recruit_responses import format_duration as _format_duration
from .recruit_responses import json_recruitment_hall_success as _json_recruitment_hall_success
from .recruit_responses import recruitment_hall_resolution_error_response as _recruitment_hall_resolution_error_response
from .recruit_responses import recruitment_hall_response as _recruitment_hall_response

logger = logging.getLogger(__name__)
ALLOWED_CANDIDATE_ACTIONS = frozenset({"accept", "retain", "discard"})
ALLOWED_CANDIDATE_SCOPES = frozenset({"selected", "all"})
RECRUIT_SUCCESS_NAME_PREVIEW_LIMIT = 12


def _load_selected_candidates(manor: Any, candidate_ids: list[int]) -> tuple[Any, list[RecruitmentCandidate]]:
    queryset = RecruitmentCandidate.objects.filter(manor=manor, id__in=candidate_ids)
    return queryset, list(queryset)


def _parse_positive_candidate_ids(raw_values: list[str]) -> list[int] | None:
    parsed: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        value = safe_positive_int(raw, default=None)
        if value is None:
            return None
        if value in seen:
            continue
        parsed.append(value)
        seen.add(value)
    return parsed


def _finalize_candidates(candidates: list[RecruitmentCandidate]) -> tuple[list[Any], list[Any]]:
    return bulk_finalize_candidates(candidates)


def _acquire_recruit_action_lock(action: str, manor_id: int, scope: str) -> tuple[bool, str, str | None]:
    return acquire_scoped_action_lock(RECRUIT_ACTION_LOCK_SPEC, action, manor_id, scope)


def _release_recruit_action_lock(lock_key: str, lock_token: str | None) -> None:
    release_scoped_action_lock(RECRUIT_ACTION_LOCK_SPEC, lock_key, lock_token)


def _run_locked_recruit_action(**kwargs: Any) -> HttpResponse:
    return run_locked_recruit_action(
        **kwargs,
        recruitment_hall_response=_recruitment_hall_response,
        acquire_lock_fn=_acquire_recruit_action_lock,
        release_lock_fn=_release_recruit_action_lock,
    )


def _normalize_candidate_action(raw_action: str | None) -> str | None:
    if raw_action in (None, ""):
        return "accept"
    if raw_action in ALLOWED_CANDIDATE_ACTIONS:
        return raw_action
    return None


def _normalize_candidate_scope(raw_scope: str | None) -> str | None:
    if raw_scope in (None, "", "selected"):
        return "selected"
    if raw_scope in ALLOWED_CANDIDATE_SCOPES:
        return raw_scope
    return None


@method_decorator(require_POST, name="dispatch")
@method_decorator(rate_limit_redirect("recruit_draw", limit=10, window_seconds=60), name="dispatch")
class RecruitView(LoginRequiredMixin, TemplateView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        manor = get_manor(request.user)
        is_ajax = is_json_request(request)
        form = RecruitForm(request.POST)
        if not form.is_valid():
            if is_ajax:
                return json_error("请选择有效的卡池", status=400)
            messages.error(request, "请选择有效的卡池")
            return redirect("gameplay:recruitment_hall")
        pool = form.cleaned_data["pool"]
        return handle_recruit_draw(
            request=request,
            manor=manor,
            is_ajax=is_ajax,
            pool=pool,
            run_locked_action=_run_locked_recruit_action,
            format_duration=_format_duration,
            json_success_response=_json_recruitment_hall_success,
            start_guest_recruitment_fn=start_guest_recruitment,
        )


@login_required
@require_POST
@rate_limit_redirect("recruitment_card", limit=30, window_seconds=60)
def use_recruitment_card_view(request: HttpRequest) -> HttpResponse:
    """为指定招募卡池增加1次今日招募额度。"""
    manor = get_manor(request.user)
    is_ajax = is_json_request(request)
    pool_id = safe_positive_int(request.POST.get("pool_id"), default=None)
    pool = RecruitmentPool.objects.filter(pk=pool_id, tier__in=CORE_POOL_TIERS).first() if pool_id is not None else None
    if pool is None:
        return _recruitment_hall_response(
            request,
            manor,
            "请选择有效的招募方式。",
            is_ajax=is_ajax,
            status=400,
        )

    return handle_recruitment_card_use(
        request=request,
        manor=manor,
        is_ajax=is_ajax,
        pool=pool,
        run_locked_action=_run_locked_recruit_action,
        recruitment_hall_response=_recruitment_hall_response,
        add_recruitment_extra_attempt_with_item_cost_fn=add_recruitment_extra_attempt_with_item_cost,
    )


@login_required
@require_POST
@rate_limit_redirect("recruit_accept", limit=10, window_seconds=60)
def accept_candidate_view(request: HttpRequest) -> HttpResponse:
    manor = get_manor(request.user)
    is_ajax = is_json_request(request)
    return handle_candidate_accept(
        request=request,
        manor=manor,
        is_ajax=is_ajax,
        raw_scope=request.POST.get("scope"),
        raw_action=request.POST.get("action"),
        raw_candidate_ids=request.POST.getlist("candidate_ids"),
        normalize_scope=_normalize_candidate_scope,
        normalize_action=_normalize_candidate_action,
        parse_positive_candidate_ids=_parse_positive_candidate_ids,
        load_selected_candidates=_load_selected_candidates,
        discard_candidates=discard_candidates,
        retain_candidates=retain_candidates,
        finalize_candidates=_finalize_candidates,
        run_locked_action=_run_locked_recruit_action,
        recruitment_hall_response=_recruitment_hall_response,
        resolution_error_response=_recruitment_hall_resolution_error_response,
        candidate_action_success_response=partial(
            _candidate_action_success_response,
            preview_limit=RECRUIT_SUCCESS_NAME_PREVIEW_LIMIT,
        ),
    )


@login_required
@require_POST
@rate_limit_redirect("recruit_reveal", limit=10, window_seconds=60)
def use_magnifying_glass_view(request: HttpRequest) -> HttpResponse:
    """使用放大镜显现候选门客的稀有度"""
    manor = get_manor(request.user)
    item_id = request.POST.get("item_id")

    is_ajax = is_json_request(request)
    item_id_int = safe_positive_int(item_id, default=None)
    return handle_magnifying_glass_reveal(
        request=request,
        manor=manor,
        is_ajax=is_ajax,
        item_id_int=item_id_int,
        run_locked_action=_run_locked_recruit_action,
        recruitment_hall_response=_recruitment_hall_response,
        use_magnifying_glass_for_candidates_fn=use_magnifying_glass_for_candidates,
    )
