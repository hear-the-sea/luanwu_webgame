"""
消息系统视图
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.exceptions import GameError
from core.utils import is_json_request, json_success
from core.utils.validation import safe_redirect_url
from core.utils.view_error_mapping import flash_view_error, json_error_response_for_exception
from gameplay.constants import UIConstants
from gameplay.models import ResourceType
from gameplay.services.manor.core import get_manor
from gameplay.services.resources import project_resource_production_for_read
from gameplay.services.utils.messages import (
    claim_message_attachments,
    delete_all_messages,
    delete_messages,
    list_messages,
    mark_all_messages_read,
    mark_messages_read,
    unread_message_count,
)
from gameplay.views.read_helpers import get_prepared_manor_for_read

logger = logging.getLogger(__name__)


def _safe_unread_message_count(manor: Any) -> int:
    try:
        return int(unread_message_count(manor))
    except DatabaseError as exc:
        logger.warning(
            "Failed to read unread_message_count: manor_id=%s error=%s",
            getattr(manor, "id", None),
            exc,
            exc_info=True,
        )
        return 0


def _build_attachment_details(message: Any) -> dict[str, list[dict[str, Any]]]:
    attachment_details: dict[str, list[dict[str, Any]]] = {"resources": [], "items": []}
    if not message.has_attachments:
        return attachment_details

    attachments = message.attachments or {}
    if message.is_claimed:
        claimed = attachments.get("claimed")
        if isinstance(claimed, dict):
            attachments = claimed

    resources = attachments.get("resources", {})
    resource_labels = dict(ResourceType.choices)
    for key, amount in resources.items():
        attachment_details["resources"].append(
            {
                "key": key,
                "name": resource_labels.get(key, key),
                "amount": amount,
            }
        )

    items = attachments.get("items", {})
    item_keys = list(items.keys())
    if item_keys:
        from gameplay.utils.template_loader import get_item_templates_by_keys

        item_templates_map = get_item_templates_by_keys(item_keys)
    else:
        item_templates_map = {}
    for item_key, quantity in items.items():
        item_template = item_templates_map.get(item_key)
        attachment_details["items"].append(
            {
                "key": item_key,
                "name": item_template.name if item_template else item_key,
                "icon": item_template.icon if item_template else None,
                "image": item_template.image if item_template else None,
                "quantity": quantity,
            }
        )

    return attachment_details


def _resolve_message_action_redirect(request: HttpRequest, default_url_name: str, **kwargs: Any) -> HttpResponse:
    default_url = reverse(default_url_name, kwargs=kwargs) if kwargs else reverse(default_url_name)
    next_url = safe_redirect_url(request, request.POST.get("next"), "")
    if next_url:
        return redirect(next_url)

    referer_url = safe_redirect_url(request, request.META.get("HTTP_REFERER"), "")
    if referer_url:
        return redirect(referer_url)

    return redirect(default_url)


def _messages_list_redirect() -> HttpResponse:
    return redirect("gameplay:messages")


def _run_selected_message_action(
    request: HttpRequest,
    *,
    action: Callable[[Any, list[str]], Any],
    success_message: str,
    empty_message: str,
) -> HttpResponse:
    manor = get_manor(request.user)
    message_ids = request.POST.getlist("message_ids")
    is_json = is_json_request(request)
    if not message_ids:
        if is_json:
            return json_success(message_ids=[], unread_count=_safe_unread_message_count(manor))
        messages.info(request, empty_message)
        return _messages_list_redirect()

    action(manor, message_ids)
    if is_json:
        return json_success(
            message_ids=[int(message_id) for message_id in message_ids if str(message_id).isdigit()],
            unread_count=_safe_unread_message_count(manor),
        )
    messages.success(request, success_message.format(count=len(message_ids)))
    return _messages_list_redirect()


def _claim_attachment_exception_response(
    request: HttpRequest,
    *,
    manor: Any,
    message_id: int,
    is_json: bool,
    exc: Exception,
    log_message: str | None = None,
    log_args: tuple[object, ...] = (),
) -> HttpResponse | None:
    unread_count = _safe_unread_message_count(manor)
    if is_json:
        return json_error_response_for_exception(
            exc,
            log_message=log_message,
            log_args=log_args,
            logger_instance=logger,
            message_id=message_id,
            unread_count=unread_count,
        )
    flash_view_error(
        request,
        exc,
        log_message=log_message,
        log_args=log_args,
        logger_instance=logger,
    )
    return None


def _format_claimed_summary(claimed_summary: dict[str, int]) -> tuple[str, list[dict[str, Any]]]:
    resource_labels = dict(ResourceType.choices)
    parts: list[str] = []
    claimed_payload: list[dict[str, Any]] = []

    # 收集所有物品 key，批量查询
    item_keys_to_lookup = [key[5:] for key in claimed_summary.keys() if key.startswith("item_")]
    if item_keys_to_lookup:
        from gameplay.utils.template_loader import get_item_templates_by_keys

        item_templates_map = get_item_templates_by_keys(item_keys_to_lookup)
    else:
        item_templates_map = {}

    for key, value in claimed_summary.items():
        if key.startswith("item_"):
            item_key = key[5:]  # 移除 "item_" 前缀
            item_template = item_templates_map.get(item_key)
            item_name = item_template.name if item_template else item_key
            parts.append(f"{item_name}×{value}")
            claimed_payload.append({"kind": "item", "key": item_key, "name": item_name, "amount": value})
        else:
            label = resource_labels.get(key, key)
            parts.append(f"{label}×{value}")
            claimed_payload.append({"kind": "resource", "key": key, "name": label, "amount": value})

    summary_text = "、".join(parts) if parts else "附件"
    return summary_text, claimed_payload


class MessageListView(LoginRequiredMixin, TemplateView):
    """消息列表页面"""

    template_name = "gameplay/messages.html"
    MESSAGES_PER_PAGE = UIConstants.MESSAGES_PER_PAGE

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """
        Render the message center where players manage battle/system messages.

        Adds the unread count to the template so the UI can display a
        contextual hint ("当前有 X 条未读通知").
        """
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_resource_production_for_read,
            logger=logger,
            source="messages_view",
        )
        context["manor"] = manor

        all_messages = list_messages(manor)
        paginator = Paginator(all_messages, self.MESSAGES_PER_PAGE)
        page_number: str | int = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["message_list"] = page_obj
        context["page_obj"] = page_obj
        context["unread_count"] = _safe_unread_message_count(manor)
        return context


@login_required
def view_message(request: HttpRequest, pk: int) -> HttpResponse:
    """
    显示消息详情页面

    对于战报消息，跳转到战报详情页；
    对于其他类型消息，显示完整的消息内容和附件信息。

    支持JSON请求：当请求头包含 Accept: application/json 或 X-Requested-With: XMLHttpRequest 时，
    返回JSON格式的响应，包含消息状态和最新的未读消息计数。
    """
    manor = get_manor(request.user)
    message = get_object_or_404(
        manor.messages.select_related("battle_report"),
        pk=pk,
    )

    is_json = is_json_request(request)

    was_unread = not message.is_read

    # 对于JSON请求，返回结构化的响应数据
    if is_json:
        response_data: dict[str, Any] = {
            "message_id": message.pk,
            "was_unread": was_unread,
            "unread_count": _safe_unread_message_count(manor),
        }

        if message.battle_report_id:
            response_data["redirect_url"] = reverse("battle:report_detail", kwargs={"pk": message.battle_report_id})

        return json_success(**response_data)

    # 战报消息跳转到战报详情
    if message.battle_report_id:
        return redirect("battle:report_detail", pk=message.battle_report_id)

    attachment_details = _build_attachment_details(message)

    context = {
        "message": message,
        "attachment_details": attachment_details,
    }

    return render(request, "gameplay/message_detail.html", context)


@login_required
@require_POST
def delete_messages_view(request: HttpRequest) -> HttpResponse:
    """Delete only the messages that have been selected via checkboxes."""
    return _run_selected_message_action(
        request,
        action=delete_messages,
        success_message="已删除 {count} 条消息",
        empty_message="请先选择需要删除的消息",
    )


@login_required
@require_POST
def delete_all_messages_view(request: HttpRequest) -> HttpResponse:
    """Handle the 'one click clear' button."""
    manor = get_manor(request.user)
    delete_all_messages(manor)
    messages.info(request, "所有消息已清空")
    return _messages_list_redirect()


@login_required
@require_POST
def mark_messages_read_view(request: HttpRequest) -> HttpResponse:
    """Mark the selected messages as read without deleting them."""
    return _run_selected_message_action(
        request,
        action=mark_messages_read,
        success_message="已标记 {count} 条消息为已读",
        empty_message="请先选择需要标记的消息",
    )


@login_required
@require_POST
def mark_all_messages_read_view(request: HttpRequest) -> HttpResponse:
    """Mark every stored message as read."""
    manor = get_manor(request.user)
    mark_all_messages_read(manor)
    messages.success(request, "已全部标记为已读")
    return _messages_list_redirect()


@login_required
@require_POST
def claim_attachment_view(request: HttpRequest, pk: int) -> HttpResponse:
    """
    领取消息附件。

    将附件中的资源和道具发放到玩家账户。
    """
    manor = get_manor(request.user)
    message = get_object_or_404(
        manor.messages,
        pk=pk,
    )
    is_json = is_json_request(request)

    try:
        claimed_summary = claim_message_attachments(message)
        summary_text, claimed_payload = _format_claimed_summary(claimed_summary)
        unread_count = _safe_unread_message_count(manor)

        if is_json:
            return json_success(
                message_id=pk,
                summary=summary_text,
                claimed=claimed_payload,
                unread_count=unread_count,
            )

        messages.success(request, f"附件领取成功：{summary_text}")
    except GameError as exc:
        error_response = _claim_attachment_exception_response(
            request,
            manor=manor,
            message_id=pk,
            is_json=is_json,
            exc=exc,
        )
        if error_response is not None:
            return error_response
    except DatabaseError as exc:
        error_response = _claim_attachment_exception_response(
            request,
            manor=manor,
            message_id=pk,
            is_json=is_json,
            exc=exc,
            log_message="Unexpected error in claim_attachment_view: manor_id=%s user_id=%s message_id=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                pk,
            ),
        )
        if error_response is not None:
            return error_response

    return _resolve_message_action_redirect(request, "gameplay:view_message", pk=pk)
