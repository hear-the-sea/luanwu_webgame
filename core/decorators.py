"""
Django 视图装饰器

提供统一的错误处理和重定向逻辑。
"""

from __future__ import annotations

import logging
import re
from functools import wraps
from typing import Callable, Optional

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse

from core.exceptions import GameError
from core.utils import json_error
from core.utils.http import accepts_json as _accepts_json_header
from core.utils.http import is_ajax_request as _is_ajax_header
from core.utils.validation import safe_redirect_url, sanitize_error_message
from core.utils.view_error_mapping import action_error_response, flash_view_error

logger = logging.getLogger(__name__)


def _resolve_returned_url(request: HttpRequest, result: str) -> str | None:
    result_str = str(result)
    if re.match(r"^[a-zA-Z0-9_:-]+$", result_str):
        try:
            return reverse(result_str)
        except NoReverseMatch:
            logger.warning("无法解析视图返回的 URL: %s", result)

    safe_result = safe_redirect_url(request, result, "")
    if safe_result:
        return safe_result

    logger.warning("视图返回不安全的 URL: %s，回退到 default", result)
    return None


def _resolve_default_url(request: HttpRequest, default: str | None) -> str | None:
    if not default:
        return None

    default_str = str(default).strip()
    if not default_str:
        return None

    if re.match(r"^[a-zA-Z0-9_:-]+$", default_str):
        try:
            return reverse(default_str)
        except NoReverseMatch:
            logger.warning("无法解析 redirect_url: %s", default)

    safe_default = safe_redirect_url(request, default_str, "")
    if safe_default:
        return safe_default

    logger.warning("redirect_url 不安全，回退到根路径: %s", default)
    return None


def _add_success_message(
    request: HttpRequest,
    success_message: str | Callable[..., str] | None,
    result: HttpResponse | str | None,
) -> None:
    if not success_message:
        return

    if callable(success_message):
        msg = success_message(result)
    else:
        msg = success_message
    messages.success(request, msg)


def _handle_success_response(
    request: HttpRequest,
    result: HttpResponse | str | None,
    redirect_url: str | None,
    success_message: str | Callable[..., str] | None,
) -> HttpResponse | None:
    if isinstance(result, str):
        _add_success_message(request, success_message, result)
        return redirect(get_next_url(request, redirect_url, result=result))

    if result is None:
        _add_success_message(request, success_message, result)
        return redirect(get_next_url(request, redirect_url))

    return None


def _handle_game_exception(
    request: HttpRequest,
    view_func: Callable,
    exc: GameError,
    redirect_url: str | None,
) -> HttpResponse:
    error_msg = sanitize_error_message(exc)

    if is_htmx_request(request):
        return redirect(get_next_url(request, redirect_url))

    if is_ajax_request(request) or expects_json(request):
        return json_error(error_msg, status=400, include_message=True)

    messages.error(request, error_msg)
    return redirect(get_next_url(request, redirect_url))


def flash_unexpected_view_error(
    request: HttpRequest,
    exc: Exception,
    *,
    log_message: str,
    log_args: tuple[object, ...] = (),
    logger_instance: logging.Logger | None = None,
) -> None:
    flash_view_error(
        request,
        exc,
        log_message=log_message,
        log_args=log_args,
        logger_instance=logger_instance or logger,
    )


def unexpected_error_response(
    request: HttpRequest,
    exc: Exception,
    *,
    is_ajax: bool,
    redirect_url: str,
    log_message: str,
    log_args: tuple[object, ...] = (),
    logger_instance: logging.Logger | None = None,
    status: int = 500,
) -> HttpResponse:
    return action_error_response(
        request,
        exc,
        is_ajax=is_ajax,
        redirect_to=redirect_url,
        fallback_status=status,
        log_message=log_message,
        log_args=log_args,
        logger_instance=logger_instance or logger,
    )


def get_next_url(request: HttpRequest, default: Optional[str] = None, result: Optional[str] = None) -> str:
    """
    获取安全的重定向 URL。

    优先级：result（视图返回值）> POST.next > GET.next > HTTP_REFERER > default

    Args:
        request: HTTP 请求对象
        default: 默认重定向 URL（可以是 URL 名称，如 "gameplay:dashboard"）
        result: 视图函数返回的 URL 字符串

    Returns:
        安全的重定向 URL

    注意：
        - result 必须是安全的 URL 名称或路径，不应直接使用用户输入
        - 如果 result 无法通过安全验证，会回退到 default
    """
    if result:
        resolved = _resolve_returned_url(request, result)
        if resolved:
            return resolved

    next_param = request.POST.get("next") or request.GET.get("next")
    for candidate in (next_param, request.META.get("HTTP_REFERER")):
        if not candidate:
            continue
        safe_url = safe_redirect_url(request, candidate, "")
        if safe_url:
            return safe_url

    default_url = _resolve_default_url(request, default)
    return default_url or "/"


def is_htmx_request(request: HttpRequest) -> bool:
    """
    检测是否为 HTMX 请求。
    """
    return request.headers.get("HX-Request") == "true"


def is_ajax_request(request: HttpRequest) -> bool:
    """
    检测是否为传统 AJAX 请求（XMLHttpRequest）。

    注意：不包含 HTMX，HTMX 使用 is_htmx_request() 检测
    """
    return _is_ajax_header(request)


def expects_json(request: HttpRequest) -> bool:
    """
    检测客户端是否期望 JSON 响应。
    """
    return _accepts_json_header(request)


def handle_game_errors(
    redirect_url: str | None = None,
    success_message: str | Callable[..., str] | None = None,
):
    """
    统一处理游戏错误的装饰器。

    自动捕获 GameError，并添加错误消息。
    支持 AJAX/HTMX 请求，自动返回 JSON 响应。

    Args:
        redirect_url: 默认重定向 URL（可以是 URL 名称，如 "gameplay:dashboard"）
                      如果视图返回字符串 URL，会优先使用
        success_message: 成功消息（字符串或可调用对象）

    Usage:
        @login_required
        @require_POST
        @handle_game_errors(redirect_url="gameplay:dashboard")
        def my_view(request, pk):
            guest = get_guest_with_template(get_manor(request.user), pk)
            # 调用具体业务服务
            messages.success(request, f"{guest.display_name} 操作成功")
            return redirect("guests:detail", pk=guest.pk)

        # 或返回 URL 字符串，让装饰器处理重定向
        @login_required
        @require_POST
        @handle_game_errors(redirect_url="gameplay:dashboard")
        def my_view(request, pk):
            guest = get_guest_with_template(get_manor(request.user), pk)
            # 调用具体业务服务
            # 返回 URL 字符串，装饰器会处理重定向
            return "guests:detail"

    注意：
        - 装饰器应该在 @login_required 和 @require_POST 之后
        - 如果视图使用 @transaction.atomic，装饰器应放在它之前（外层）
        - success_message 为可调用对象时，会接收视图函数的返回值作为参数
    """

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            try:
                result = view_func(request, *args, **kwargs)

                response = _handle_success_response(request, result, redirect_url, success_message)
                if response is not None:
                    return response
                return result

            except GameError as exc:
                return _handle_game_exception(request, view_func, exc, redirect_url)

        return wrapper

    return decorator


def atomic_handle_game_errors(
    redirect_url: str | None = None,
    success_message: str | Callable[..., str] | None = None,
):
    """
    组合装饰器：自动在事务中执行，并处理游戏错误。

    这是 `@transaction.atomic` 和 `@handle_game_errors` 的组合。

    注意：
        - 装饰器顺序很重要：atomic 在内层，错误处理在外层
        - 这样异常会先触发事务回滚，再被错误处理捕获

    Usage:
        @login_required
        @require_POST
        @atomic_handle_game_errors(redirect_url="gameplay:dashboard")
        def my_view(request, pk):
            # 自动在事务中执行
            guest = get_guest_with_template(get_manor(request.user), pk)
            # 调用需要在事务中执行的业务服务
            # ...
    """

    def decorator(view_func: Callable) -> Callable:
        # 先应用事务装饰器（内层）- 让异常先触发回滚
        atomic_view = transaction.atomic(view_func)
        # 再应用错误处理装饰器（外层）- 捕获异常并转换成响应
        return handle_game_errors(
            redirect_url=redirect_url,
            success_message=success_message,
        )(atomic_view)

    return decorator
