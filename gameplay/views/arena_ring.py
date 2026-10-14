from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from core.exceptions import GameError
from core.utils.rate_limit import rate_limit_redirect
from gameplay.selectors.arena.rings import (
    get_arena_rings_context,
    get_ring_event_detail_context,
    get_ring_inspection_context,
)
from gameplay.services.manor.core import get_manor

from .arena import (
    ARENA_TAB_EVENTS,
    ARENA_TAB_RING,
    BaseArenaView,
    _handle_known_arena_error,
    _handle_unexpected_arena_error,
    _parse_guest_ids,
)

logger = logging.getLogger(__name__)


class ArenaRingsView(BaseArenaView):
    active_tab = ARENA_TAB_RING
    template_name = "gameplay/arena/rings.html"
    show_arena_hero_card = False

    def _build_page_context(self, manor):
        return get_arena_rings_context(manor)


class ArenaRingEventDetailView(BaseArenaView):
    active_tab = ARENA_TAB_EVENTS
    template_name = "gameplay/arena/ring_detail.html"
    show_arena_hero_card = False

    def _build_page_context(self, manor):
        context = get_ring_event_detail_context(
            manor, tenure_id=self.kwargs["tenure_id"], page=self.request.GET.get("page", 1)
        )
        if context is None:
            raise Http404("擂台赛事不存在或已超过 24 小时查看期限")
        return context


class ArenaRingInspectionView(BaseArenaView):
    active_tab = ARENA_TAB_RING
    template_name = "gameplay/arena/ring_inspection.html"
    show_arena_hero_card = False

    def _build_page_context(self, manor):
        context = get_ring_inspection_context(manor, inspection_id=self.kwargs["inspection_id"])
        if context is None:
            raise Http404("阵容查看记录不存在或已过期")
        return context


@login_required
@require_POST
@rate_limit_redirect("arena_ring_action", limit=20, window_seconds=60)
def arena_ring_action_view(request: HttpRequest, ring_key: str) -> HttpResponse:
    from gameplay.services.arena import ring as ring_core

    manor = get_manor(request.user)
    kind = request.POST.get("action", "")
    try:
        try:
            version = int(request.POST.get("expected_version", ""))
            tenure_id = int(request.POST.get("expected_tenure_id", "0"))
            if version < 0 or tenure_id < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise GameError("擂台状态无效，请刷新后重试") from None
        operations = {
            "occupy": ring_core.occupy_ring,
            "challenge": ring_core.challenge_ring,
            "update": ring_core.update_ring_lineup,
            "leave": ring_core.leave_ring,
            "inspect": ring_core.inspect_ring,
        }
        operation = operations.get(kind)
        if operation is None:
            raise GameError("擂台操作无效")
        kwargs = {
            "manor": manor,
            "ring_key": ring_key,
            "action_token": request.POST.get("action_token", ""),
            "expected_version": version,
        }
        if kind != "occupy":
            kwargs["expected_tenure_id"] = tenure_id
        if kind in {"occupy", "challenge", "update"}:
            kwargs["guest_ids"] = _parse_guest_ids(request.POST.getlist("guest_ids"))
            kwargs["troop_loadout"] = {
                key.removeprefix("troop_"): request.POST[key] for key in request.POST if key.startswith("troop_")
            }
        result = operation(**kwargs)
        if kind == "inspect":
            if result.get("inspection_id"):
                return redirect("gameplay:arena_ring_inspection", inspection_id=result["inspection_id"])
            return redirect("gameplay:arena_rings")
        labels = {
            "occupy": "已登台守擂。",
            "challenge": "挑战已结束，可在赛事查看中查看结果与战报。",
            "update": "守擂阵容已更新，下场对战使用新阵容。",
            "leave": "已下台，累计奖励已保留。",
        }
        messages.success(request, labels[kind])
    except GameError as exc:
        _handle_known_arena_error(request, exc)
    except DatabaseError as exc:
        _handle_unexpected_arena_error(
            request,
            exc,
            log_message="arena ring action failed: manor_id=%s ring=%s kind=%s",
            log_args=(manor.id, ring_key, kind),
        )
    return redirect("gameplay:arena_rings")
