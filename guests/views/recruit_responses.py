from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect

from core.utils import json_error, json_success
from gameplay.services.resources import project_resource_production_for_read
from gameplay.views.read_helpers import prepare_manor_for_read

from .recruit_runtime import CandidateActionOutcome, RecruitViewResolutionError

logger = logging.getLogger(__name__)


def format_duration(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if sec or not parts:
        parts.append(f"{sec}秒")
    return "".join(parts)


def _project_recruitment_hall_read_model(request: HttpRequest, manor: Any) -> None:
    prepare_manor_for_read(
        manor,
        project_fn=project_resource_production_for_read,
        logger=logger,
        source="recruitment_hall_ajax_payload",
        user_id=getattr(request.user, "id", None),
    )


def format_bulk_recruit_success_message(succeeded_guests: list[Any], *, preview_limit: int) -> str:
    total = len(succeeded_guests)
    if total <= 0:
        return ""

    preview_names = [guest.display_name for guest in succeeded_guests[:preview_limit]]
    preview = ", ".join(preview_names)
    if total > preview_limit:
        remaining = total - preview_limit
        return f"成功招募 {total} 名门客：{preview} 等 {remaining} 名"
    return f"成功招募 {total} 名门客：{preview}"


def build_recruitment_hall_ajax_payload(
    request: HttpRequest,
    manor: Any,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    from django.template.loader import render_to_string

    from gameplay.constants import UIConstants
    from gameplay.selectors.recruitment import get_recruitment_hall_context

    _project_recruitment_hall_read_model(request, manor)
    context = get_recruitment_hall_context(manor, UIConstants.RECRUIT_RECORDS_DISPLAY, use_cache=use_cache)
    return {
        "hall_pools_html": render_to_string(
            "gameplay/partials/recruitment_pools_section.html", context, request=request
        ),
        "hall_candidates_html": render_to_string(
            "gameplay/partials/recruitment_candidates_section.html", context, request=request
        ),
        "hall_records_html": render_to_string(
            "gameplay/partials/recruitment_records_section.html", context, request=request
        ),
        "candidate_count": context.get("candidate_count", 0),
    }


def json_recruitment_hall_success(
    request: HttpRequest,
    manor: Any,
    message: str,
    *,
    message_level: str = "success",
    use_cache: bool = True,
) -> JsonResponse:
    return json_success(
        message=message,
        message_level=message_level,
        **build_recruitment_hall_ajax_payload(request, manor, use_cache=use_cache),
    )


def recruitment_hall_response(
    request: HttpRequest,
    manor: Any,
    message: str,
    *,
    is_ajax: bool,
    status: int = 200,
    message_level: str = "success",
    use_cache: bool = True,
) -> HttpResponse:
    if is_ajax:
        if status >= 400:
            return json_error(message, status=status)
        return json_recruitment_hall_success(
            request,
            manor,
            message,
            message_level=message_level,
            use_cache=use_cache,
        )

    getattr(messages, message_level)(request, message)
    return redirect("gameplay:recruitment_hall")


def recruitment_hall_resolution_error_response(
    request: HttpRequest,
    manor: Any,
    resolution_error: RecruitViewResolutionError,
    *,
    is_ajax: bool,
) -> HttpResponse:
    return recruitment_hall_response(
        request,
        manor,
        resolution_error.message,
        is_ajax=is_ajax,
        status=resolution_error.status,
        message_level=resolution_error.message_level,
    )


def candidate_action_success_response(
    request: HttpRequest,
    manor: Any,
    outcome: CandidateActionOutcome,
    *,
    is_ajax: bool,
    preview_limit: int,
) -> HttpResponse:
    if outcome.action == "discard":
        message = f"已放弃 {outcome.affected_count} 名候选门客。"
        return recruitment_hall_response(
            request,
            manor,
            message,
            is_ajax=is_ajax,
            message_level="warning" if is_ajax else "info",
            use_cache=False,
        )

    if outcome.action == "retain":
        if outcome.affected_count:
            success_message = f"已将 {outcome.affected_count} 名候选收为家丁。"
            if is_ajax:
                message = success_message
                if outcome.error_message:
                    message = f"{message} {outcome.error_message}"
                return recruitment_hall_response(
                    request,
                    manor,
                    message,
                    is_ajax=is_ajax,
                    use_cache=False,
                )
            messages.success(request, success_message)
            if outcome.error_message:
                messages.error(request, outcome.error_message)
            return redirect("gameplay:recruitment_hall")
        if outcome.error_message:
            return recruitment_hall_response(
                request,
                manor,
                outcome.error_message,
                is_ajax=is_ajax,
                status=400,
            )
        return recruitment_hall_response(
            request,
            manor,
            "当前没有可操作的候选门客。",
            is_ajax=is_ajax,
            status=400,
        )

    if outcome.succeeded:
        success_message = format_bulk_recruit_success_message(outcome.succeeded, preview_limit=preview_limit)
        if is_ajax:
            message = success_message
            if outcome.failed:
                message = f"{message}；门客容量不足，{len(outcome.failed)} 名候选未能招募"
            return recruitment_hall_response(
                request,
                manor,
                message,
                is_ajax=is_ajax,
                use_cache=False,
            )
        messages.success(request, success_message)
        if outcome.failed:
            messages.warning(request, f"门客容量不足，{len(outcome.failed)} 名候选未能招募")
        return redirect("gameplay:recruitment_hall")

    if outcome.failed:
        return recruitment_hall_response(
            request,
            manor,
            f"门客容量不足，{len(outcome.failed)} 名候选未能招募",
            is_ajax=is_ajax,
            status=200,
            message_level="warning",
            use_cache=False,
        )

    return recruitment_hall_response(
        request,
        manor,
        "当前没有可操作的候选门客。",
        is_ajax=is_ajax,
        status=400,
    )
