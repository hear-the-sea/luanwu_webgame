from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect

from battle import troops as battle_troops
from core.exceptions import GameError
from core.utils import safe_int
from core.utils.locked_actions import execute_locked_action
from gameplay.models import MissionRun

from . import mission_helpers


def handle_known_mission_exception(
    request: HttpRequest,
    exc: Exception,
    *,
    redirect_func: Callable[[], HttpResponse],
) -> HttpResponse:
    if not isinstance(exc, GameError):
        raise exc
    mission_helpers.handle_known_mission_error(request, exc)
    return redirect_func()


def handle_accept_mission(
    request: HttpRequest,
    *,
    manor: Any,
    mission: Any,
    launch_mission_fn: Callable[[Any, Any, list[int], dict[str, int]], Any],
) -> HttpResponse:
    if mission.is_defense:
        guest_ids: list[int] = []
        raw_loadout: dict[str, int] = {}
    else:
        raw_guest_ids = request.POST.getlist("guest_ids")
        parsed_guest_ids = mission_helpers.parse_positive_ids(raw_guest_ids)
        if parsed_guest_ids is None:
            messages.error(request, "门客选择有误")
            return mission_helpers.mission_tasks_redirect(mission.key)
        guest_ids = parsed_guest_ids
        limit = getattr(manor, "max_squad_size", 5)
        if len(guest_ids) > limit:
            messages.error(request, f"本次出征最多选择 {limit} 名门客")
            return mission_helpers.mission_tasks_redirect(mission.key)
        if mission.guest_only:
            raw_loadout = {}
        else:
            raw_loadout = {}
            for item in battle_troops.troop_template_list():
                raw_value = request.POST.get(f"troop_{item['key']}", 0)
                quantity = safe_int(raw_value, default=None, min_val=0)
                if quantity is None:
                    messages.error(request, "护院配置有误")
                    return mission_helpers.mission_tasks_redirect(mission.key)
                raw_loadout[item["key"]] = quantity

    def _mission_redirect() -> HttpResponse:
        return mission_helpers.mission_tasks_redirect(mission.key)

    def _perform_accept() -> None:
        launch_mission_fn(manor, mission, guest_ids, raw_loadout)
        if mission.is_defense:
            messages.success(request, f"{mission.name} 已进入防守，战报稍后送达。")
        else:
            messages.success(request, f"{mission.name} 已出征，战报稍后送达。")

    def _on_lock_conflict() -> HttpResponse:
        messages.warning(request, "任务请求处理中，请稍候重试")
        return _mission_redirect()

    def _on_known_error(exc: Exception) -> HttpResponse:
        return handle_known_mission_exception(
            request,
            exc,
            redirect_func=_mission_redirect,
        )

    def _on_database_error(exc: DatabaseError) -> HttpResponse:
        mission_helpers.handle_unexpected_mission_error(
            request,
            exc,
            log_message="Unexpected mission accept error: manor_id=%s user_id=%s mission_key=%s mission_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                mission.key,
                getattr(mission, "id", None),
            ),
        )
        return _mission_redirect()

    return execute_locked_action(
        action_name="accept",
        owner_id=int(manor.id),
        scope=mission.key,
        acquire_lock_fn=mission_helpers.acquire_mission_action_lock,
        release_lock_fn=mission_helpers.release_mission_action_lock,
        on_lock_conflict=_on_lock_conflict,
        operation=_perform_accept,
        on_success=lambda _result: _mission_redirect(),
        known_exceptions=(GameError,),
        on_known_error=_on_known_error,
        on_database_error=_on_database_error,
    )


def handle_retreat_mission(
    request: HttpRequest,
    *,
    manor: Any,
    pk: int,
    request_retreat_fn: Callable[[Any], Any],
) -> HttpResponse:
    run = get_object_or_404(
        manor.mission_runs.select_related("mission"),
        pk=pk,
        status=MissionRun.Status.ACTIVE,
    )

    def _dashboard_redirect() -> HttpResponse:
        return redirect("gameplay:dashboard")

    def _perform_retreat() -> None:
        request_retreat_fn(run)
        eta = run.return_at.strftime("%H:%M:%S") if run.return_at else ""
        messages.info(request, f"{run.mission.name} 已撤退，预计返程：{eta}")

    def _on_lock_conflict() -> HttpResponse:
        messages.warning(request, "任务请求处理中，请稍候重试")
        return _dashboard_redirect()

    def _on_known_error(exc: Exception) -> HttpResponse:
        return handle_known_mission_exception(
            request,
            exc,
            redirect_func=_dashboard_redirect,
        )

    def _on_database_error(exc: DatabaseError) -> HttpResponse:
        mission_helpers.handle_unexpected_mission_error(
            request,
            exc,
            log_message="Unexpected mission retreat error: manor_id=%s user_id=%s run_id=%s mission_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
                getattr(run, "mission_id", None),
            ),
        )
        return _dashboard_redirect()

    return execute_locked_action(
        action_name="retreat_mission",
        owner_id=int(manor.id),
        scope=str(pk),
        acquire_lock_fn=mission_helpers.acquire_mission_action_lock,
        release_lock_fn=mission_helpers.release_mission_action_lock,
        on_lock_conflict=_on_lock_conflict,
        operation=_perform_retreat,
        on_success=lambda _result: _dashboard_redirect(),
        known_exceptions=(GameError,),
        on_known_error=_on_known_error,
        on_database_error=_on_database_error,
    )


def handle_retreat_scout(
    request: HttpRequest,
    *,
    manor: Any,
    pk: int,
    scout_record_model: Any,
    request_scout_retreat_fn: Callable[[Any], Any],
) -> HttpResponse:
    record = get_object_or_404(
        scout_record_model.objects.select_related("defender"),
        pk=pk,
        attacker=manor,
        status=scout_record_model.Status.SCOUTING,
    )

    def _home_redirect() -> HttpResponse:
        return redirect("home")

    def _perform_retreat() -> None:
        request_scout_retreat_fn(record)
        messages.info(request, f"侦察 {record.defender.display_name} 已撤退，探子正在返程")

    def _on_lock_conflict() -> HttpResponse:
        messages.warning(request, "任务请求处理中，请稍候重试")
        return _home_redirect()

    def _on_known_error(exc: Exception) -> HttpResponse:
        return handle_known_mission_exception(
            request,
            exc,
            redirect_func=_home_redirect,
        )

    def _on_database_error(exc: DatabaseError) -> HttpResponse:
        mission_helpers.handle_unexpected_mission_error(
            request,
            exc,
            log_message="Unexpected scout retreat error: manor_id=%s user_id=%s record_id=%s defender_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
                getattr(record, "defender_id", None),
            ),
        )
        return _home_redirect()

    return execute_locked_action(
        action_name="retreat_scout",
        owner_id=int(manor.id),
        scope=str(pk),
        acquire_lock_fn=mission_helpers.acquire_mission_action_lock,
        release_lock_fn=mission_helpers.release_mission_action_lock,
        on_lock_conflict=_on_lock_conflict,
        operation=_perform_retreat,
        on_success=lambda _result: _home_redirect(),
        known_exceptions=(GameError,),
        on_known_error=_on_known_error,
        on_database_error=_on_database_error,
    )


def handle_use_mission_card(
    request: HttpRequest,
    *,
    manor: Any,
    mission: Any,
    add_mission_extra_attempt_fn: Callable[..., Any],
) -> HttpResponse:
    def _mission_redirect() -> HttpResponse:
        return mission_helpers.mission_tasks_redirect(mission.key)

    def _perform_use_card() -> None:
        add_mission_extra_attempt_fn(
            manor=manor,
            mission=mission,
            item_key=mission_helpers.MISSION_CARD_KEY,
            count=1,
        )
        messages.success(request, f"使用任务卡成功，{mission.name} 今日次数+1")

    def _on_lock_conflict() -> HttpResponse:
        messages.warning(request, "任务请求处理中，请稍候重试")
        return _mission_redirect()

    def _on_known_error(exc: Exception) -> HttpResponse:
        return handle_known_mission_exception(
            request,
            exc,
            redirect_func=_mission_redirect,
        )

    def _on_database_error(exc: DatabaseError) -> HttpResponse:
        mission_helpers.handle_unexpected_mission_error(
            request,
            exc,
            log_message="Unexpected mission card use error: manor_id=%s user_id=%s mission_key=%s mission_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                mission.key,
                getattr(mission, "id", None),
            ),
        )
        return _mission_redirect()

    return execute_locked_action(
        action_name="use_card",
        owner_id=int(manor.id),
        scope=mission.key,
        acquire_lock_fn=mission_helpers.acquire_mission_action_lock,
        release_lock_fn=mission_helpers.release_mission_action_lock,
        on_lock_conflict=_on_lock_conflict,
        operation=_perform_use_card,
        on_success=lambda _result: _mission_redirect(),
        known_exceptions=(GameError,),
        on_known_error=_on_known_error,
        on_database_error=_on_database_error,
    )
