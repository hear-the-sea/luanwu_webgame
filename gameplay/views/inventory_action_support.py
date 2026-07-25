"""
仓库视图动作支持：错误映射、成功消息契约与请求参数归一化
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from django.contrib import messages
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from core.decorators import unexpected_error_response
from core.exceptions import GameError
from core.utils import json_error, json_success, safe_positive_int, sanitize_error_message


def parse_positive_quantity(raw_quantity: str | None, default: int = 1) -> int | None:
    """Parse quantity from user input, allowing empty to fall back to default."""
    if raw_quantity is None or raw_quantity == "":
        return default
    return safe_positive_int(raw_quantity, default=None)


def resolve_target_guest_item_action(item: Any) -> str | None:
    payload = item.template.effect_payload
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise AssertionError(f"invalid inventory target-guest item effect_payload: {payload!r}")
    action = payload.get("action")
    if action is None:
        return None
    if not isinstance(action, str):
        raise AssertionError(f"invalid inventory target-guest item action: {action!r}")
    return action.strip() or None


def normalize_inventory_success_message(raw_value: object, *, contract_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return raw_value.strip()


def build_inventory_use_success_message(payload: Mapping[str, Any], *, item_name: str) -> str:
    if "_message" in payload:
        summary = normalize_inventory_success_message(
            payload["_message"],
            contract_name="inventory use_item success message",
        )
    else:
        summary = "效果已生效"
    return f"{item_name} 使用成功：{summary}"


def inventory_error_response(
    request: HttpRequest,
    is_ajax: bool,
    error: str,
    redirect_url: str = "gameplay:warehouse",
    *,
    status: int = 400,
) -> HttpResponse:
    if is_ajax:
        return json_error(error, status=status)
    messages.error(request, error)
    return redirect(redirect_url)


def known_inventory_error_response(
    request: HttpRequest,
    is_ajax: bool,
    exc: GameError,
    *,
    redirect_url: str = "gameplay:warehouse",
) -> HttpResponse:
    return inventory_error_response(request, is_ajax, sanitize_error_message(exc), redirect_url=redirect_url)


def unexpected_inventory_error_response(
    request: HttpRequest,
    exc: DatabaseError,
    *,
    is_ajax: bool,
    redirect_url: str,
    log_message: str,
    log_args: tuple[object, ...],
    logger_instance: logging.Logger,
) -> HttpResponse:
    return unexpected_error_response(
        request,
        exc,
        is_ajax=is_ajax,
        redirect_url=redirect_url,
        log_message=log_message,
        log_args=log_args,
        logger_instance=logger_instance,
    )


def success_response(
    request: HttpRequest,
    *,
    is_ajax: bool,
    message: str,
    redirect_url: str = "gameplay:warehouse",
) -> HttpResponse:
    if is_ajax:
        return json_success(message=message)
    messages.success(request, message)
    return redirect(redirect_url)
