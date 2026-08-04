"""
门客培养视图：训练、经验道具、属性加点
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.exceptions import GameError, GuestItemConfigurationError
from core.utils import is_ajax_request, is_json_request, json_error, json_success, safe_positive_int
from core.utils.rate_limit import rate_limit_json
from core.utils.validation import safe_redirect_url, sanitize_error_message

from ..forms import AllocateSkillPointsForm, TrainGuestForm
from ..models import Guest
from ..services.recruitment_guests import allocate_attribute_points
from ..services.training import finalize_guest_training, train_guest, use_experience_item_for_guest

logger = logging.getLogger(__name__)


def _parse_positive_item_seconds(raw_value: object) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise GuestItemConfigurationError("道具未配置有效时间")
    raw_value_for_int: Any = raw_value
    try:
        parsed_seconds = int(raw_value_for_int)
    except (TypeError, ValueError) as exc:
        raise GuestItemConfigurationError("道具未配置有效时间") from exc
    if parsed_seconds <= 0:
        raise GuestItemConfigurationError("道具未配置有效时间")
    return parsed_seconds


def _resolve_experience_item_seconds(item) -> int:
    payload = item.template.effect_payload
    if not isinstance(payload, dict):
        raise GuestItemConfigurationError("道具未配置有效时间")
    return _parse_positive_item_seconds(payload.get("time"))


def _normalize_experience_item_view_result(raw_result: object) -> dict[str, object]:
    if not isinstance(raw_result, dict):
        raise AssertionError(f"invalid experience item view result payload: {raw_result!r}")
    return raw_result


def _normalize_experience_item_view_result_int(raw_value: object, *, contract_name: str, min_value: int) -> int:
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


def _normalize_experience_item_view_result_datetime(raw_value: object, *, contract_name: str) -> datetime | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, datetime):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return raw_value


@method_decorator(require_POST, name="dispatch")
class TrainView(LoginRequiredMixin, TemplateView):
    """
    门客训练视图（类视图）

    注意：类视图使用手动错误处理，但使用 manager 方法简化查询
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        from gameplay.services.manor.core import get_manor

        manor = get_manor(request.user)
        form = TrainGuestForm(request.POST, manor=manor)
        default_url = "gameplay:recruitment_hall"
        next_url = safe_redirect_url(request, request.POST.get("next"), default_url)
        if not form.is_valid():
            messages.error(request, "培养参数有误")
            return redirect(next_url)
        guest = form.cleaned_data["guest"]
        levels = form.cleaned_data["levels"]
        try:
            updated_guest = train_guest(guest, levels)
            eta = updated_guest.training_complete_at
            eta_str = eta.strftime("%H:%M:%S") if eta else ""
            messages.success(request, f"{updated_guest.display_name} 正在升级，预计 {eta_str} 完成")
        except GameError as exc:
            messages.error(request, sanitize_error_message(exc))
        except DatabaseError as exc:
            logger.exception(
                "Unexpected guest train database error: manor_id=%s user_id=%s guest_id=%s levels=%s",
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                getattr(guest, "id", None),
                levels,
            )
            messages.error(request, sanitize_error_message(exc))
        return redirect(next_url)


@login_required
@require_POST
def use_experience_item_view(request, pk: int):
    """
    使用经验道具视图

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
        error_msg = "请选择经验道具"
        if is_ajax:
            return json_error(error_msg, status=400, include_message=True)
        messages.error(request, error_msg)
        return redirect(next_url)

    item = get_object_or_404(
        manor.inventory_items.select_related("template"),
        pk=item_id_int,
        template__effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    try:
        reduce_seconds = _resolve_experience_item_seconds(item)
        result = _normalize_experience_item_view_result(
            use_experience_item_for_guest(manor, guest, item.pk, reduce_seconds)
        )
        new_quantity = _normalize_experience_item_view_result_int(
            result.get("remaining_item_quantity"),
            contract_name="experience item view result remaining_item_quantity",
            min_value=0,
        )
        reduced_seconds = _normalize_experience_item_view_result_int(
            result.get("time_reduced"),
            contract_name="experience item view result time_reduced",
            min_value=0,
        )
        reduced_hours = round(reduced_seconds / 3600, 2)
        eta = _normalize_experience_item_view_result_datetime(
            result.get("next_eta"),
            contract_name="experience item view result next_eta",
        )
        new_level = _normalize_experience_item_view_result_int(
            result.get("new_level"),
            contract_name="experience item view result new_level",
            min_value=1,
        )
        current_hp = _normalize_experience_item_view_result_int(
            result.get("current_hp"),
            contract_name="experience item view result current_hp",
            min_value=0,
        )
        max_hp = _normalize_experience_item_view_result_int(
            result.get("max_hp"),
            contract_name="experience item view result max_hp",
            min_value=1,
        )
        eta_str = eta.strftime("%H:%M:%S") if eta else "已完成升级"
        msg = f"{item.template.name} 已使用，缩短 {reduced_hours} 小时。预计完成：{eta_str}"
        if is_ajax:
            return json_success(
                message=msg,
                item_id=item.pk,
                new_quantity=new_quantity,
                guest_id=guest.pk,
                new_level=new_level,
                current_hp=current_hp,
                max_hp=max_hp,
                training_eta=eta.isoformat() if eta else None,
            )
        messages.success(request, msg)
    except GameError as exc:
        error_msg = sanitize_error_message(exc)
        if is_ajax:
            return json_error(error_msg, status=400, include_message=True)
        messages.error(request, error_msg)
    except DatabaseError as exc:
        logger.exception(
            "Unexpected experience-item use database error: manor_id=%s user_id=%s guest_id=%s item_id=%s",
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
@rate_limit_json("training_check", limit=60, window_seconds=60, error_message="检查过于频繁，请稍后再试")
def check_training_view(request, pk: int):
    """
    检查门客训练状态，如果已完成则结算训练并返回最新数据。

    用于前端倒计时结束后主动检查，避免页面刷新时数据未更新的问题。
    使用 manager 方法简化查询
    """
    from gameplay.services.manor.core import get_manor

    manor = get_manor(request.user)
    # 使用 manager 方法获取门客，避免重复的 select_related
    guest = get_object_or_404(Guest.objects.for_manor(manor).with_template(), pk=pk)

    # 尝试完成训练（如果时间已到）
    finalize_guest_training(guest)
    guest.refresh_from_db()

    # 返回最新的门客状态
    return json_success(
        guest_id=guest.pk,
        level=guest.level,
        current_hp=guest.current_hp,
        max_hp=guest.max_hp,
        training_eta=guest.training_complete_at.isoformat() if guest.training_complete_at else None,
        training_paused=guest.training_is_paused,
        guest_status=guest.status,
        attribute_points=guest.attribute_points,
    )


@login_required
@require_POST
def allocate_points_view(request, pk: int):
    """
    门客属性加点视图
    """
    from gameplay.services.manor.core import get_manor

    manor = get_manor(request.user)
    # 使用 manager 方法获取门客，避免重复的 select_related
    guest = get_object_or_404(Guest.objects.for_manor(manor).with_template(), pk=pk)
    form = AllocateSkillPointsForm(request.POST, manor=manor)
    is_ajax = is_json_request(request)
    default_url = reverse("guests:detail", args=[guest.pk])
    next_url = safe_redirect_url(request, request.POST.get("next"), default_url)

    if not form.is_valid():
        errors: list[str] = []
        for field_errors in form.errors.values():
            errors.extend(str(error) for error in field_errors)
        error_msg = "; ".join(errors) or "加点参数有误"
        if is_ajax:
            return json_error(error_msg, status=400, include_message=True)
        messages.error(request, error_msg)
        return redirect(next_url)

    # 验证门客 ID 一致性
    form_guest = form.cleaned_data["guest"]
    if form_guest.pk != guest.pk:
        error_msg = "非法的加点请求"
        if is_ajax:
            return json_error(error_msg, status=400, include_message=True)
        messages.error(request, error_msg)
        return redirect(next_url)

    attribute = form.cleaned_data["attribute"]
    points = form.cleaned_data["points"]

    try:
        allocate_attribute_points(guest, attribute, points)
        guest.refresh_from_db(fields=["force", "intellect", "defense_stat", "agility", "luck", "attribute_points"])

        if is_ajax:
            refreshed_form = AllocateSkillPointsForm(manor=manor, initial={"guest": guest})
            guest_field = cast(forms.ModelChoiceField, refreshed_form.fields["guest"])
            guest_field.queryset = manor.guests.filter(pk=guest.pk)
            attribute_panel_html = render_to_string(
                "guests/partials/attribute_panel.html",
                {"guest": guest, "skill_point_form": refreshed_form},
                request=request,
            )
            return json_success(
                message=f"{guest.display_name} 属性加点成功",
                attribute_points=guest.attribute_points,
                force=guest.force,
                intellect=guest.intellect,
                defense=guest.defense_stat,
                agility=guest.agility,
                luck=guest.luck,
                attribute_panel_html=attribute_panel_html,
            )
    except GameError as exc:
        error_msg = sanitize_error_message(exc)
        if is_ajax:
            return json_error(error_msg, status=400, include_message=True)
        messages.error(request, error_msg)
        return redirect(next_url)
    except DatabaseError as exc:
        logger.exception(
            "Unexpected allocate-points database error: manor_id=%s user_id=%s guest_id=%s attribute=%s points=%s",
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
            getattr(guest, "id", None),
            attribute,
            points,
        )
        error_msg = sanitize_error_message(exc)
        if is_ajax:
            return json_error(error_msg, status=500, include_message=True)
        messages.error(request, error_msg)
        return redirect(next_url)

    # 成功消息由装饰器的 success_message 参数处理
    messages.success(request, f"{guest.display_name} 属性加点成功")

    return redirect(next_url)
