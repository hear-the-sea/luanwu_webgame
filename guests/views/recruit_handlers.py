from __future__ import annotations

from typing import Any, Callable

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from ..models import RecruitmentCandidate
from .recruit_runtime import execute_candidate_action, resolve_candidate_action_request, reveal_candidate_rarities


def handle_recruit_draw(
    *,
    request: HttpRequest,
    manor: Any,
    is_ajax: bool,
    pool: Any,
    run_locked_action: Callable[..., HttpResponse],
    format_duration: Callable[[int], str],
    json_success_response: Callable[..., HttpResponse],
    start_guest_recruitment_fn: Callable[..., Any],
) -> HttpResponse:
    def _perform_draw() -> HttpResponse:
        recruitment = start_guest_recruitment_fn(manor, pool)
        eta_text = format_duration(recruitment.duration_seconds)
        message = f"{pool.name} 已开始招募，预计 {eta_text} 后完成。"
        if is_ajax:
            return json_success_response(request, manor, message, use_cache=False)

        messages.success(request, message)
        return redirect("gameplay:recruitment_hall")

    return run_locked_action(
        request=request,
        manor=manor,
        is_ajax=is_ajax,
        lock_action="draw",
        lock_scope=str(pool.key),
        operation=_perform_draw,
        database_log_message="Unexpected recruit draw database error: manor_id=%s user_id=%s pool_key=%s",
        unexpected_log_message="Unexpected recruit draw error: manor_id=%s user_id=%s pool_key=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
            getattr(pool, "key", None),
        ),
    )


def handle_recruitment_card_use(
    *,
    request: HttpRequest,
    manor: Any,
    is_ajax: bool,
    pool: Any,
    run_locked_action: Callable[..., HttpResponse],
    recruitment_hall_response: Callable[..., HttpResponse],
    add_recruitment_extra_attempt_with_item_cost_fn: Callable[..., int],
) -> HttpResponse:
    def _perform_use() -> HttpResponse:
        extra_count = add_recruitment_extra_attempt_with_item_cost_fn(manor, pool)
        message = f"招募卡已生效：{pool.name} 今日额度 +1！本日已额外加 {extra_count} 次，卡够就继续招，庄里不嫌人多。"
        return recruitment_hall_response(
            request,
            manor,
            message,
            is_ajax=is_ajax,
            use_cache=False,
        )

    return run_locked_action(
        request=request,
        manor=manor,
        is_ajax=is_ajax,
        lock_action="recruitment_card",
        lock_scope=str(pool.key),
        operation=_perform_use,
        database_log_message="Unexpected recruitment card database error: manor_id=%s user_id=%s pool_key=%s",
        unexpected_log_message="Unexpected recruitment card error: manor_id=%s user_id=%s pool_key=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
            getattr(pool, "key", None),
        ),
    )


def handle_candidate_accept(
    *,
    request: HttpRequest,
    manor: Any,
    is_ajax: bool,
    raw_scope: str | None,
    raw_action: str | None,
    raw_candidate_ids: list[str],
    normalize_scope: Callable[[str | None], str | None],
    normalize_action: Callable[[str | None], str | None],
    parse_positive_candidate_ids: Callable[[list[str]], list[int] | None],
    load_selected_candidates: Callable[[Any, list[int]], tuple[Any, list[Any]]],
    discard_candidates: Callable[[list[Any]], int],
    retain_candidates: Callable[[list[Any]], tuple[int, str | None]],
    finalize_candidates: Callable[[list[Any]], tuple[list[Any], list[Any]]],
    run_locked_action: Callable[..., HttpResponse],
    recruitment_hall_response: Callable[..., HttpResponse],
    resolution_error_response: Callable[..., HttpResponse],
    candidate_action_success_response: Callable[..., HttpResponse],
) -> HttpResponse:
    action_request, resolution_error = resolve_candidate_action_request(
        manor=manor,
        raw_scope=raw_scope,
        raw_action=raw_action,
        raw_candidate_ids=raw_candidate_ids,
        normalize_scope=normalize_scope,
        normalize_action=normalize_action,
        parse_positive_candidate_ids=parse_positive_candidate_ids,
        load_selected_candidates=load_selected_candidates,
        candidate_model=RecruitmentCandidate,
    )
    if resolution_error is not None:
        return resolution_error_response(request, manor, resolution_error, is_ajax=is_ajax)
    assert action_request is not None

    def _perform_accept() -> HttpResponse:
        outcome = execute_candidate_action(
            action=action_request.action,
            selection=action_request.selection,
            discard_candidates=discard_candidates,
            retain_candidates=retain_candidates,
            finalize_candidates=finalize_candidates,
        )
        return candidate_action_success_response(request, manor, outcome, is_ajax=is_ajax)

    return run_locked_action(
        request=request,
        manor=manor,
        is_ajax=is_ajax,
        lock_action="candidate_action",
        lock_scope=action_request.lock_scope,
        operation=_perform_accept,
        database_log_message="Unexpected recruit accept database error: manor_id=%s user_id=%s action=%s candidate_count=%s",
        unexpected_log_message="Unexpected recruit accept error: manor_id=%s user_id=%s action=%s candidate_count=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
            action_request.action,
            action_request.target_count,
        ),
    )


def handle_magnifying_glass_reveal(
    *,
    request: HttpRequest,
    manor: Any,
    is_ajax: bool,
    item_id_int: int | None,
    run_locked_action: Callable[..., HttpResponse],
    recruitment_hall_response: Callable[..., HttpResponse],
    use_magnifying_glass_for_candidates_fn: Callable[[Any, int], int],
) -> HttpResponse:
    if item_id_int is None:
        return recruitment_hall_response(request, manor, "未找到放大镜道具", is_ajax=is_ajax, status=400)

    def _perform_reveal() -> HttpResponse:
        count = reveal_candidate_rarities(
            manor=manor,
            item_id=item_id_int,
            use_magnifying_glass_for_candidates=use_magnifying_glass_for_candidates_fn,
        )
        if count > 0:
            return recruitment_hall_response(
                request,
                manor,
                f"使用放大镜成功：显现 {count} 位候选门客的稀有度",
                is_ajax=is_ajax,
                use_cache=False,
            )
        return recruitment_hall_response(
            request,
            manor,
            "当前候选门客的稀有度已全部显现",
            is_ajax=is_ajax,
            status=400,
            message_level="info",
        )

    return run_locked_action(
        request=request,
        manor=manor,
        is_ajax=is_ajax,
        lock_action="reveal",
        lock_scope=str(item_id_int),
        operation=_perform_reveal,
        database_log_message="Unexpected magnifying-glass database error: manor_id=%s user_id=%s item_id=%s",
        unexpected_log_message="Unexpected magnifying-glass error: manor_id=%s user_id=%s item_id=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
            item_id_int,
        ),
    )
