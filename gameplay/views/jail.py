"""
监牢与结义林 API 视图
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.utils import json_error, json_success, parse_json_object, safe_positive_int
from core.utils.locked_actions import (
    ActionLockSpec,
    acquire_scoped_action_lock,
    build_scoped_action_lock_key,
    release_scoped_action_lock,
)
from core.utils.rate_limit import rate_limit_json
from gameplay.selectors.jail import build_prisoner_state, get_jail_page_context, get_oath_grove_page_context
from gameplay.services.jail import (
    add_oath_bond,
    draw_pie,
    list_held_prisoners,
    list_oath_bonds,
    recruit_prisoner,
    release_prisoner,
    remove_oath_bond,
)
from gameplay.services.jail_persuasion.interactions import interact_prisoner, observe_prisoner
from gameplay.services.jail_persuasion.milestones import resolve_milestone
from gameplay.services.manor.core import get_manor
from gameplay.services.resources import project_resource_production_for_read
from gameplay.views.jail_action_support import message_redirect, run_locked_json_action, run_locked_redirect_action
from gameplay.views.jail_payloads import build_jail_status_payload, build_oath_status_payload
from gameplay.views.read_helpers import get_prepared_manor_for_read

logger = logging.getLogger(__name__)
JAIL_ACTION_LOCK_SECONDS = 5
JAIL_ACTION_LOCK_NAMESPACE = "jail:view_lock"
JAIL_ACTION_LOCK_SPEC = ActionLockSpec(
    namespace=JAIL_ACTION_LOCK_NAMESPACE,
    timeout_seconds=JAIL_ACTION_LOCK_SECONDS,
    logger=logger,
    log_context="jail action lock",
)


def _jail_action_lock_key(action: str, manor_id: int, scope: str) -> str:
    return build_scoped_action_lock_key(JAIL_ACTION_LOCK_SPEC, action, manor_id, scope)


def _acquire_jail_action_lock(action: str, manor_id: int, scope: str) -> tuple[bool, str, str | None]:
    return acquire_scoped_action_lock(JAIL_ACTION_LOCK_SPEC, action, manor_id, scope)


def _release_jail_action_lock(lock_key: str, lock_token: str | None) -> None:
    release_scoped_action_lock(JAIL_ACTION_LOCK_SPEC, lock_key, lock_token)


def _oath_guest_id_from_json_or_error(request: HttpRequest) -> tuple[int | None, JsonResponse | None]:
    data = parse_json_object(request.body, empty_as_object=True)
    if data is None:
        return None, json_error("无效的请求数据")
    guest_id = safe_positive_int(data.get("guest_id"), default=None)
    if guest_id is None:
        return None, json_error("请指定门客")
    return guest_id, None


def _json_body_or_error(request: HttpRequest) -> tuple[dict[str, object] | None, JsonResponse | None]:
    data = parse_json_object(request.body, empty_as_object=True)
    if data is None:
        return None, json_error("无效的请求数据")
    return data, None


def _interaction_result_payload(result: object) -> dict[str, object]:
    log = getattr(result, "log", None)
    return {
        "outcome": str(getattr(result, "outcome", "")),
        "heart_delta": int(getattr(result, "heart_delta", 0) or 0),
        "affinity_delta": int(getattr(result, "affinity_delta", 0) or 0),
        "speaker_loyalty_delta": int(getattr(result, "speaker_loyalty_delta", 0) or 0),
        "speaker_loyalty": getattr(result, "speaker_loyalty", None),
        "copy_key": str(getattr(log, "copy_key", "") or ""),
        "copy_params": dict(getattr(result, "copy_params", {}) or {}),
        "text": str(getattr(result, "copy_text", "") or ""),
    }


def _milestone_result_payload(result: object) -> dict[str, object]:
    log = getattr(result, "log", None)
    return {
        "outcome": "event",
        "stage": int(getattr(result, "stage", 0) or 0),
        "heart_delta": int(getattr(result, "heart_delta", 0) or 0),
        "affinity_delta": int(getattr(result, "affinity_delta", 0) or 0),
        "speaker_loyalty_delta": 0,
        "speaker_loyalty": None,
        "copy_key": str(getattr(log, "copy_key", "") or ""),
        "copy_params": dict(getattr(result, "copy_params", {}) or {}),
        "text": str(getattr(result, "copy_text", "") or ""),
    }


class JailView(LoginRequiredMixin, TemplateView):
    template_name = "gameplay/jail.html"

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_resource_production_for_read,
            logger=logger,
            source="jail_view",
        )
        context["manor"] = manor
        context.update(get_jail_page_context(manor))
        return context


class OathGroveView(LoginRequiredMixin, TemplateView):
    template_name = "gameplay/oath_grove.html"

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_resource_production_for_read,
            logger=logger,
            source="oath_grove_view",
        )
        context["manor"] = manor
        context.update(get_oath_grove_page_context(manor))
        return context


@login_required
def jail_status_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    prisoners = list_held_prisoners(manor)
    states = [build_prisoner_state(manor, prisoner) for prisoner in prisoners]
    return json_success(jail=build_jail_status_payload(manor, states))


@login_required
def oath_status_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    bonds = list_oath_bonds(manor)
    return json_success(oath_grove=build_oath_status_payload(manor, bonds))


@login_required
@require_POST
def recruit_prisoner_view(request: HttpRequest, prisoner_id: int) -> HttpResponse:
    manor = get_manor(request.user)
    mode = str(request.POST.get("mode") or "standard").strip()
    return run_locked_redirect_action(
        request=request,
        owner_id=int(manor.id),
        action_name="recruit_view",
        scope=str(prisoner_id),
        operation=lambda: recruit_prisoner(manor, int(prisoner_id), mode=mode),
        success_response=lambda guest: message_redirect(
            request,
            "gameplay:jail",
            level=messages.success,
            message=(
                f"{getattr(guest, '_recruitment_copy_text', '')} "
                f"{guest.display_name} 已成为 1 级门客｜初始忠诚 {guest.loyalty}。"
            ).strip(),
        ),
        redirect_name="gameplay:jail",
        log_message="Unexpected jail recruit error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
def draw_pie_view(request: HttpRequest, prisoner_id: int) -> HttpResponse:
    manor = get_manor(request.user)
    return run_locked_redirect_action(
        request=request,
        owner_id=int(manor.id),
        action_name="draw_pie_view",
        scope=str(prisoner_id),
        operation=lambda: draw_pie(manor, int(prisoner_id)),
        success_response=lambda prisoner: message_redirect(
            request,
            "gameplay:jail",
            level=messages.success,
            message=(
                getattr(getattr(prisoner, "_persuasion_result", None), "copy_text", "")
                or f"已向 {prisoner.display_name} 许以重利"
            ),
        ),
        redirect_name="gameplay:jail",
        log_message="Unexpected jail draw_pie error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
def release_prisoner_view(request: HttpRequest, prisoner_id: int) -> HttpResponse:
    manor = get_manor(request.user)
    return run_locked_redirect_action(
        request=request,
        owner_id=int(manor.id),
        action_name="release_view",
        scope=str(prisoner_id),
        operation=lambda: release_prisoner(manor, int(prisoner_id)),
        success_response=lambda prisoner: message_redirect(
            request,
            "gameplay:jail",
            level=messages.success,
            message=f"已释放：{prisoner.display_name}",
        ),
        redirect_name="gameplay:jail",
        log_message="Unexpected jail release error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
def add_oath_bond_view(request: HttpRequest) -> HttpResponse:
    manor = get_manor(request.user)
    guest_id = safe_positive_int(request.POST.get("guest_id"), default=None)
    if guest_id is None:
        messages.error(request, "请指定门客")
        return redirect("gameplay:oath_grove")
    return run_locked_redirect_action(
        request=request,
        owner_id=int(manor.id),
        action_name="oath_add_view",
        scope=str(guest_id),
        operation=lambda: add_oath_bond(manor, guest_id),
        success_response=lambda bond: message_redirect(
            request,
            "gameplay:oath_grove",
            level=messages.success,
            message=f"结义成功：{bond.guest.display_name}",
        ),
        redirect_name="gameplay:oath_grove",
        log_message="Unexpected oath add error: manor_id=%s guest_id=%s",
        log_args=(getattr(manor, "id", None), guest_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
def remove_oath_bond_view(request: HttpRequest, guest_id: int) -> HttpResponse:
    manor = get_manor(request.user)
    return run_locked_redirect_action(
        request=request,
        owner_id=int(manor.id),
        action_name="oath_remove_view",
        scope=str(guest_id),
        operation=lambda: remove_oath_bond(manor, int(guest_id)),
        success_response=lambda deleted: message_redirect(
            request,
            "gameplay:oath_grove",
            level=messages.error if not deleted else messages.success,
            message="该门客未结义" if not deleted else "已解除结义",
        ),
        redirect_name="gameplay:oath_grove",
        log_message="Unexpected oath remove error: manor_id=%s guest_id=%s",
        log_args=(getattr(manor, "id", None), guest_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
@rate_limit_json("jail_recruit", limit=10, window_seconds=60, error_message="操作过于频繁，请稍后再试")
def recruit_prisoner_api(request: HttpRequest, prisoner_id: int) -> JsonResponse:
    manor = get_manor(request.user)
    if request.content_type == "application/json":
        data, error = _json_body_or_error(request)
        if error is not None:
            return error
        mode = str((data or {}).get("mode") or "standard").strip()
    else:
        mode = str(request.POST.get("mode") or "standard").strip()
    return run_locked_json_action(
        owner_id=int(manor.id),
        action_name="recruit_api",
        scope=str(prisoner_id),
        operation=lambda: recruit_prisoner(manor, int(prisoner_id), mode=mode),
        success_response=lambda guest: json_success(
            message=f"已招募：{guest.display_name}",
            guest_id=guest.id,
            mode=str(getattr(guest, "_recruitment_mode", mode)),
            initial_loyalty=int(guest.loyalty),
            gold_cost=int(getattr(guest, "_recruitment_gold_cost", 0) or 0),
            copy_key=str(getattr(guest, "_recruitment_copy_key", "") or ""),
            copy_params=dict(getattr(guest, "_recruitment_copy_params", {}) or {}),
            text=str(getattr(guest, "_recruitment_copy_text", "") or ""),
        ),
        log_message="Unexpected jail recruit API error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
@rate_limit_json("jail_draw_pie", limit=30, window_seconds=60, error_message="操作过于频繁，请稍后再试")
def draw_pie_api(request: HttpRequest, prisoner_id: int) -> JsonResponse:
    manor = get_manor(request.user)
    return run_locked_json_action(
        owner_id=int(manor.id),
        action_name="draw_pie_api",
        scope=str(prisoner_id),
        operation=lambda: draw_pie(manor, int(prisoner_id)),
        success_response=lambda prisoner: json_success(
            message=(
                getattr(getattr(prisoner, "_persuasion_result", None), "copy_text", "")
                or f"已向 {prisoner.display_name} 许以重利"
            ),
            prisoner_id=prisoner.id,
            new_loyalty=prisoner.loyalty,
            reduction=getattr(prisoner, "_reduction", 0),
            result=(
                _interaction_result_payload(getattr(prisoner, "_persuasion_result", None))
                if getattr(prisoner, "_persuasion_result", None) is not None
                else {}
            ),
            prisoner=build_prisoner_state(manor, prisoner),
        ),
        log_message="Unexpected jail draw_pie API error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
@rate_limit_json("jail_observe", limit=30, window_seconds=60, error_message="操作过于频繁，请稍后再试")
def observe_prisoner_api(request: HttpRequest, prisoner_id: int) -> JsonResponse:
    manor = get_manor(request.user)
    return run_locked_json_action(
        owner_id=int(manor.id),
        action_name="observe_api",
        scope=str(prisoner_id),
        operation=lambda: observe_prisoner(manor, int(prisoner_id)),
        success_response=lambda result: json_success(
            message="已完成察言观色",
            prisoner=build_prisoner_state(manor, result.prisoner),
        ),
        log_message="Unexpected jail observe API error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
@rate_limit_json("jail_interact", limit=30, window_seconds=60, error_message="操作过于频繁，请稍后再试")
def interact_prisoner_api(request: HttpRequest, prisoner_id: int) -> JsonResponse:
    manor = get_manor(request.user)
    data, error = _json_body_or_error(request)
    if error is not None:
        return error
    method = str((data or {}).get("method") or "").strip()
    raw_speaker_id = (data or {}).get("speaker_id")
    speaker_id = safe_positive_int(raw_speaker_id, default=None) if raw_speaker_id is not None else None
    if raw_speaker_id is not None and speaker_id is None:
        return json_error("无效的说客参数")
    return run_locked_json_action(
        owner_id=int(manor.id),
        action_name="interact_api",
        scope=str(prisoner_id),
        operation=lambda: interact_prisoner(
            manor,
            int(prisoner_id),
            method=method,
            speaker_id=speaker_id,
        ),
        success_response=lambda result: json_success(
            message="招降交互已结算",
            result=_interaction_result_payload(result),
            prisoner=build_prisoner_state(manor, result.prisoner),
        ),
        log_message="Unexpected jail interaction API error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
@rate_limit_json("jail_milestone", limit=30, window_seconds=60, error_message="操作过于频繁，请稍后再试")
def resolve_jail_milestone_api(request: HttpRequest, prisoner_id: int) -> JsonResponse:
    manor = get_manor(request.user)
    data, error = _json_body_or_error(request)
    if error is not None:
        return error
    choice = str((data or {}).get("choice") or "").strip()
    return run_locked_json_action(
        owner_id=int(manor.id),
        action_name="milestone_api",
        scope=str(prisoner_id),
        operation=lambda: resolve_milestone(manor, int(prisoner_id), choice=choice),
        success_response=lambda result: json_success(
            message="归心事件已处理",
            result=_milestone_result_payload(result),
            prisoner=build_prisoner_state(manor, result.prisoner),
        ),
        log_message="Unexpected jail milestone API error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
@rate_limit_json("jail_release", limit=20, window_seconds=60, error_message="操作过于频繁，请稍后再试")
def release_prisoner_api(request: HttpRequest, prisoner_id: int) -> JsonResponse:
    manor = get_manor(request.user)
    return run_locked_json_action(
        owner_id=int(manor.id),
        action_name="release_api",
        scope=str(prisoner_id),
        operation=lambda: release_prisoner(manor, int(prisoner_id)),
        success_response=lambda prisoner: json_success(
            message=f"已释放：{prisoner.display_name}", prisoner_id=prisoner.id
        ),
        log_message="Unexpected jail release API error: manor_id=%s prisoner_id=%s",
        log_args=(getattr(manor, "id", None), prisoner_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
@rate_limit_json("oath_add", limit=10, window_seconds=60, error_message="操作过于频繁，请稍后再试")
def add_oath_bond_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    guest_id, error = _oath_guest_id_from_json_or_error(request)
    if error is not None:
        return error
    if guest_id is None:
        return json_error("请指定门客")

    return run_locked_json_action(
        owner_id=int(manor.id),
        action_name="oath_add_api",
        scope=str(guest_id),
        operation=lambda: add_oath_bond(manor, guest_id),
        success_response=lambda bond: json_success(message=f"结义成功：{bond.guest.display_name}"),
        log_message="Unexpected oath add API error: manor_id=%s guest_id=%s",
        log_args=(getattr(manor, "id", None), guest_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )


@login_required
@require_POST
@rate_limit_json("oath_remove", limit=10, window_seconds=60, error_message="操作过于频繁，请稍后再试")
def remove_oath_bond_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    guest_id, error = _oath_guest_id_from_json_or_error(request)
    if error is not None:
        return error
    if guest_id is None:
        return json_error("请指定门客")

    return run_locked_json_action(
        owner_id=int(manor.id),
        action_name="oath_remove_api",
        scope=str(guest_id),
        operation=lambda: remove_oath_bond(manor, guest_id),
        success_response=lambda deleted: (
            json_error("该门客未结义") if not deleted else json_success(message="已解除结义")
        ),
        log_message="Unexpected oath remove API error: manor_id=%s guest_id=%s",
        log_args=(getattr(manor, "id", None), guest_id),
        acquire_lock_fn=_acquire_jail_action_lock,
        release_lock_fn=_release_jail_action_lock,
        logger_instance=logger,
    )
