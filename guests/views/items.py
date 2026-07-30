"""
门客物品使用视图：药品等
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.exceptions import GameError
from core.utils import is_ajax_request, json_error, json_success, safe_positive_int
from core.utils.validation import safe_redirect_url, sanitize_error_message

from ..models import Guest
from ..services.health import resolve_medicine_heal_amount, use_medicine_item_for_guest

logger = logging.getLogger(__name__)


def _normalize_medicine_view_result(raw_result: object) -> dict[str, object]:
    if not isinstance(raw_result, dict):
        raise AssertionError(f"invalid medicine item view result payload: {raw_result!r}")
    return raw_result


def _normalize_medicine_view_result_int(raw_value: object, *, contract_name: str, min_value: int) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    raw_for_int: Any = raw_value
    try:
        parsed_value = int(raw_for_int)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}") from exc
    if parsed_value < min_value:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return parsed_value


def _normalize_medicine_view_result_string(raw_value: object, *, contract_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return raw_value


def _resolve_medicine_heal_amount(item) -> int:
    return resolve_medicine_heal_amount(item)


@login_required
@require_POST
def use_medicine_item_view(request, pk: int):
    """
    使用药品视图

    注意：此视图有自定义的 AJAX 响应格式，不使用统一装饰器
    但使用 manager 方法简化查询
    """
    from gameplay.models import InventoryItem, ItemTemplate
    from gameplay.services.manor.core import get_manor

    manor = get_manor(request.user)
    # 使用 manager 方法获取门客，避免重复的 select_related
    guest = get_object_or_404(Guest.objects.for_manor(manor).with_template(), pk=pk)
    item_id = request.POST.get("item_id")
    is_ajax = is_ajax_request(request)
    default_url = reverse("guests:detail", args=[guest.pk])
    next_url = safe_redirect_url(request, request.POST.get("next"), default_url)
    item_id_int = safe_positive_int(item_id, default=None)
    if item_id_int is None:
        error_msg = "请选择药品道具"
        if is_ajax:
            return json_error(error_msg, status=400, include_message=True)
        messages.error(request, error_msg)
        return redirect(next_url)

    item = get_object_or_404(
        manor.inventory_items.select_related("template"),
        pk=item_id_int,
        template__effect_type=ItemTemplate.EffectType.MEDICINE,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    try:
        _resolve_medicine_heal_amount(item)
        result = _normalize_medicine_view_result(use_medicine_item_for_guest(manor, guest, item.pk))
        new_quantity = _normalize_medicine_view_result_int(
            result.get("remaining_item_quantity"),
            contract_name="medicine item view result remaining_item_quantity",
            min_value=0,
        )
        healed = _normalize_medicine_view_result_int(
            result.get("healed"),
            contract_name="medicine item view result healed",
            min_value=0,
        )
        current_hp = _normalize_medicine_view_result_int(
            result.get("new_hp"),
            contract_name="medicine item view result new_hp",
            min_value=0,
        )
        max_hp = _normalize_medicine_view_result_int(
            result.get("max_hp"),
            contract_name="medicine item view result max_hp",
            min_value=1,
        )
        guest_status = _normalize_medicine_view_result_string(
            result.get("status"),
            contract_name="medicine item view result status",
        )
        status_display = _normalize_medicine_view_result_string(
            result.get("status_display"),
            contract_name="medicine item view result status_display",
        )
        msg = f"{guest.display_name} 恢复生命 {healed} 点"
        if bool(result.get("injury_cured")):
            msg += "，重伤状态已解除"
        if is_ajax:
            return json_success(
                message=msg,
                item_id=item.pk,
                new_quantity=new_quantity,
                guest_id=guest.pk,
                current_hp=current_hp,
                max_hp=max_hp,
                guest_status=guest_status,
                status_display=status_display,
            )
        messages.success(request, msg)
    except GameError as exc:
        error_msg = sanitize_error_message(exc)
        if is_ajax:
            return json_error(error_msg, status=400, include_message=True)
        messages.error(request, error_msg)
    except DatabaseError as exc:
        logger.exception(
            "Unexpected medicine use view database error: manor_id=%s user_id=%s guest_id=%s item_id=%s",
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
            pk,
            item_id_int,
        )
        error_msg = sanitize_error_message(exc)
        if is_ajax:
            return json_error(error_msg, status=500, include_message=True)
        messages.error(request, error_msg)
    return redirect(next_url)
