"""
门客物品使用视图：药品等
"""

from __future__ import annotations

import logging
from datetime import datetime
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
from ..services.health import heal_all_guests_with_medicine, resolve_medicine_heal_amount, use_medicine_item_for_guest

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


def _normalize_medicine_view_result_datetime(raw_value: object, *, contract_name: str) -> datetime | None:
    if raw_value is not None and not isinstance(raw_value, datetime):
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
        training_eta = _normalize_medicine_view_result_datetime(
            result.get("training_eta"),
            contract_name="medicine item view result training_eta",
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
                training_eta=training_eta.isoformat() if training_eta else None,
                training_paused=bool(result.get("training_paused", False)),
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


@login_required
@require_POST
def heal_all_guests_view(request):
    """一键使用仓库药品，按优先级治疗所有可治疗的受伤门客。"""

    from gameplay.services.manor.core import get_manor

    manor = None
    try:
        manor = get_manor(request.user)
        result = heal_all_guests_with_medicine(manor)
    except GameError as exc:
        messages.error(request, sanitize_error_message(exc))
        return redirect("guests:roster")
    except DatabaseError:
        logger.exception(
            "Unexpected bulk guest healing view database error: manor_id=%s user_id=%s",
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
        )
        messages.error(request, "操作失败，请稍后重试")
        return redirect("guests:roster")

    requested_count = int(result["requested_count"])
    healed_count = int(result["healed_count"])
    partial_count = int(result["partial_count"])
    unhealed_count = int(result["unhealed_count"])
    consumed_item_count = int(result["consumed_item_count"])
    if requested_count == 0:
        messages.info(request, "当前没有需要疗伤的门客")
    elif healed_count == requested_count:
        messages.success(
            request,
            f"一键疗伤完成：已治愈 {healed_count} 位门客，消耗疗伤道具 {consumed_item_count} 个",
        )
    elif consumed_item_count > 0:
        messages.warning(
            request,
            f"疗伤道具不足：{healed_count} 位门客已满血，{partial_count} 位门客部分恢复，"
            f"仍有 {unhealed_count} 位门客未满血；共消耗 {consumed_item_count} 个道具",
        )
    else:
        messages.warning(request, "仓库没有可用的疗伤道具，暂未治疗任何门客")

    return redirect("guests:roster")
