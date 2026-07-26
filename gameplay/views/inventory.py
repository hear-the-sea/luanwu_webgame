"""
仓库和物品管理视图
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.exceptions import GameError, ItemResourceOverflowConfirmationRequired
from core.utils import is_ajax_request, json_error, safe_int, safe_positive_int
from core.utils.rate_limit import rate_limit_json, rate_limit_redirect
from gameplay.constants import UIConstants
from gameplay.models import InventoryItem
from gameplay.selectors.recruitment import get_recruitment_hall_context
from gameplay.selectors.warehouse import get_warehouse_context
from gameplay.services.inventory.guest_items import (
    use_guest_rarity_upgrade_item,
    use_guest_rebirth_card,
    use_soul_container,
    use_xidianka,
    use_xisuidan,
)
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import get_manor, project_manor_activity_for_read
from gameplay.services.manor.treasury import move_item_to_treasury, move_item_to_warehouse
from gameplay.services.resources import project_resource_production_for_read
from gameplay.views.inventory_action_support import (
    build_inventory_use_success_message,
    inventory_error_response,
    known_inventory_error_response,
    normalize_inventory_success_message,
    parse_positive_quantity,
    resolve_target_guest_item_action,
    success_response,
    unexpected_inventory_error_response,
)
from gameplay.views.read_helpers import get_prepared_manor_for_read
from guests.services.recruitment import refresh_guest_recruitments

from .runtime_refresh_support import run_refresh_api

logger = logging.getLogger(__name__)

RESOURCE_OVERFLOW_CONFIRMATION_SALT = "gameplay.inventory.resource-overflow"
RESOURCE_OVERFLOW_CONFIRMATION_MAX_AGE_SECONDS = 300


def _load_resource_overflow_confirmation(token: str) -> dict[str, object] | None:
    if not token:
        return None
    try:
        snapshot = signing.loads(
            token,
            salt=RESOURCE_OVERFLOW_CONFIRMATION_SALT,
            max_age=RESOURCE_OVERFLOW_CONFIRMATION_MAX_AGE_SECONDS,
        )
    except signing.BadSignature:
        return None
    return snapshot if isinstance(snapshot, dict) else None


def _refresh_recruitment_hall_runtime(manor: Any) -> int:
    return int(refresh_guest_recruitments(manor) or 0)


def _warehouse_item(manor: Any, pk: int) -> InventoryItem:
    return get_object_or_404(
        manor.inventory_items.select_related("template"),
        pk=pk,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


def _move_item_between_storage(
    request: HttpRequest,
    pk: int,
    *,
    move_func: Callable[[Any, int, int], None],
    success_message: Callable[[int], str],
    redirect_url: str,
) -> HttpResponse:
    manor = get_manor(request.user)
    quantity = parse_positive_quantity(request.POST.get("quantity"), default=1)
    is_ajax = is_ajax_request(request)
    if quantity is None:
        return inventory_error_response(request, is_ajax, "数量参数无效", redirect_url=redirect_url)

    try:
        move_func(manor, pk, quantity)
        return success_response(request, is_ajax=is_ajax, message=success_message(quantity), redirect_url=redirect_url)
    except GameError as exc:
        return known_inventory_error_response(request, is_ajax, exc, redirect_url=redirect_url)
    except DatabaseError as exc:
        return unexpected_inventory_error_response(
            request,
            exc,
            is_ajax=is_ajax,
            redirect_url=redirect_url,
            log_message="Unexpected storage move view error: manor_id=%s user_id=%s item_id=%s quantity=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
                quantity,
            ),
            logger_instance=logger,
        )


def _use_target_guest_item(
    request: HttpRequest,
    pk: int,
    *,
    expected_action: str,
    missing_guest_error: str,
    success_fallback_message: Callable[[Mapping[str, Any]], str],
    service_call: Callable[[Any, InventoryItem, int], Mapping[str, Any]],
) -> HttpResponse:
    manor = get_manor(request.user)
    item = _warehouse_item(manor, pk)
    is_ajax = is_ajax_request(request)

    action = resolve_target_guest_item_action(item)
    if action != expected_action:
        return inventory_error_response(request, is_ajax, "物品类型错误")

    guest_id = safe_positive_int(request.POST.get("guest_id"), default=None)
    if guest_id is None:
        return inventory_error_response(request, is_ajax, missing_guest_error)

    try:
        result = service_call(manor, item, guest_id)
        raw_message = result.get("_message")
        if raw_message is None:
            raw_message = success_fallback_message(result)
        message = normalize_inventory_success_message(
            raw_message,
            contract_name="inventory target-guest item success message",
        )
        return success_response(request, is_ajax=is_ajax, message=message)
    except GameError as exc:
        return known_inventory_error_response(request, is_ajax, exc)
    except DatabaseError as exc:
        return unexpected_inventory_error_response(
            request,
            exc,
            is_ajax=is_ajax,
            redirect_url="gameplay:warehouse",
            log_message="Unexpected target-guest item use view error: manor_id=%s user_id=%s item_id=%s guest_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
                guest_id,
            ),
            logger_instance=logger,
        )


class RecruitmentHallView(LoginRequiredMixin, TemplateView):
    """招募大厅页面"""

    template_name = "gameplay/recruitment_hall.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_resource_production_for_read,
            logger=logger,
            source="recruitment_hall_view",
        )
        context.update(get_recruitment_hall_context(manor, UIConstants.RECRUIT_RECORDS_DISPLAY))
        return context


@login_required
@require_POST
@rate_limit_json(
    "guest_recruitment_runtime_refresh", limit=30, window_seconds=60, error_message="状态刷新过于频繁，请稍后再试"
)
def refresh_recruitment_hall_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    return run_refresh_api(
        operation=lambda: _refresh_recruitment_hall_runtime(manor),
        logger_instance=logger,
        log_message="Unexpected guest recruitment refresh error: manor_id=%s user_id=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
        ),
    )


class WarehouseView(LoginRequiredMixin, TemplateView):
    """仓库页面"""

    template_name = "gameplay/warehouse.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_manor_activity_for_read,
            logger=logger,
            source="warehouse_view",
        )
        context["manor"] = manor

        current_tab = self.request.GET.get("tab", "warehouse")
        selected_category = self.request.GET.get("category", "all")
        page = safe_int(self.request.GET.get("page", 1), default=1, min_val=1) or 1
        context.update(get_warehouse_context(manor, current_tab, selected_category, page))
        return context


@login_required
@require_POST
@rate_limit_redirect("use_item", limit=50, window_seconds=60)
def use_item_view(request: HttpRequest, pk: int) -> HttpResponse:
    """使用物品"""
    manor = get_manor(request.user)
    item = _warehouse_item(manor, pk)
    is_ajax = is_ajax_request(request)
    try:
        payload = use_inventory_item(
            item,
            manor=manor,
            resource_overflow_confirmation=_load_resource_overflow_confirmation(
                request.POST.get("resource_overflow_confirmation", "")
            ),
        )
        return success_response(
            request,
            is_ajax=is_ajax,
            message=build_inventory_use_success_message(payload, item_name=item.template.name),
        )
    except ItemResourceOverflowConfirmationRequired as exc:
        if is_ajax:
            return json_error(
                str(exc),
                status=409,
                requires_confirmation=True,
                confirmation_type="resource_overflow",
                confirmation_title="资源溢出确认",
                confirmation_message=str(exc),
                confirmation_ok_text="仍然使用",
                confirmation_token=signing.dumps(
                    exc.confirmation_snapshot,
                    salt=RESOURCE_OVERFLOW_CONFIRMATION_SALT,
                ),
                credited_resources=exc.credited_resources,
                overflow_resources=exc.overflow_resources,
            )
        return known_inventory_error_response(request, is_ajax, exc)
    except GameError as exc:
        return known_inventory_error_response(request, is_ajax, exc)
    except DatabaseError as exc:
        return unexpected_inventory_error_response(
            request,
            exc,
            is_ajax=is_ajax,
            redirect_url="gameplay:warehouse",
            log_message="Unexpected inventory use error: manor_id=%s user_id=%s item_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
            ),
            logger_instance=logger,
        )


@login_required
@require_POST
@rate_limit_redirect("move_to_treasury", limit=30, window_seconds=60)
def move_item_to_treasury_view(request: HttpRequest, pk: int) -> HttpResponse:
    """将物品从仓库移动到藏宝阁"""
    return _move_item_between_storage(
        request,
        pk,
        move_func=move_item_to_treasury,
        success_message=lambda quantity: f"已将 {quantity} 个物品移动到藏宝阁",
        redirect_url=reverse("gameplay:warehouse"),
    )


@login_required
@require_POST
@rate_limit_redirect("move_to_warehouse", limit=30, window_seconds=60)
def move_item_to_warehouse_view(request: HttpRequest, pk: int) -> HttpResponse:
    """将物品从藏宝阁移动到仓库"""
    return _move_item_between_storage(
        request,
        pk,
        move_func=move_item_to_warehouse,
        success_message=lambda quantity: f"已将 {quantity} 个物品移动到仓库",
        redirect_url=f"{reverse('gameplay:warehouse')}?tab=treasury",
    )


@login_required
@require_POST
@rate_limit_redirect("use_rebirth_card", limit=10, window_seconds=60)
def use_guest_rebirth_card_view(request: HttpRequest, pk: int) -> HttpResponse:
    """
    使用门客重生卡（需要选择目标门客）

    Args:
        pk: 物品ID
        guest_id: 目标门客ID（通过POST传入）
    """
    return _use_target_guest_item(
        request,
        pk,
        expected_action="rebirth_guest",
        missing_guest_error="请选择要重生的门客",
        success_fallback_message=lambda result: f"门客 {result.get('guest_name', '')} 已重生为1级",
        service_call=use_guest_rebirth_card,
    )


@login_required
@require_POST
@rate_limit_redirect("use_guest_rarity_upgrade", limit=10, window_seconds=60)
def use_guest_rarity_upgrade_view(request: HttpRequest, pk: int) -> HttpResponse:
    """
    使用门客升阶道具（需要选择目标门客）。

    Args:
        pk: 物品ID
        guest_id: 目标门客ID（通过POST传入）
    """
    return _use_target_guest_item(
        request,
        pk,
        expected_action="upgrade_guest_rarity",
        missing_guest_error="请选择要升阶的门客",
        success_fallback_message=lambda result: f"门客 {result.get('guest_name', '')} 升阶成功",
        service_call=use_guest_rarity_upgrade_item,
    )


@login_required
@require_POST
@rate_limit_redirect("use_soul_container", limit=10, window_seconds=60)
def use_soul_container_view(request: HttpRequest, pk: int) -> HttpResponse:
    """
    使用灵魂容器（需要选择目标门客）。

    Args:
        pk: 物品ID
        guest_id: 目标门客ID（通过POST传入）
    """
    return _use_target_guest_item(
        request,
        pk,
        expected_action="soul_fusion",
        missing_guest_error="请选择要融合的门客",
        success_fallback_message=lambda result: f"门客 {result.get('guest_name', '')} 已完成灵魂融合",
        service_call=use_soul_container,
    )


@login_required
@require_POST
@rate_limit_redirect("use_xisuidan", limit=10, window_seconds=60)
def use_xisuidan_view(request: HttpRequest, pk: int) -> HttpResponse:
    """
    使用洗髓丹（需要选择目标门客）

    Args:
        pk: 物品ID
        guest_id: 目标门客ID（通过POST传入）
    """
    return _use_target_guest_item(
        request,
        pk,
        expected_action="reroll_growth",
        missing_guest_error="请选择要洗髓的门客",
        success_fallback_message=lambda _result: "洗髓完成",
        service_call=use_xisuidan,
    )


@login_required
@require_POST
@rate_limit_redirect("use_xidianka", limit=10, window_seconds=60)
def use_xidianka_view(request: HttpRequest, pk: int) -> HttpResponse:
    """
    使用洗点卡（需要选择目标门客）

    Args:
        pk: 物品ID
        guest_id: 目标门客ID（通过POST传入）
    """
    return _use_target_guest_item(
        request,
        pk,
        expected_action="reset_allocation",
        missing_guest_error="请选择要洗点的门客",
        success_fallback_message=lambda _result: "洗点完成",
        service_call=use_xidianka,
    )
