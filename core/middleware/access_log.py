from __future__ import annotations

import logging
import sys
import time
from contextlib import ExitStack
from typing import Any, Callable

from django.conf import settings
from django.db import connections
from django.http import HttpRequest, HttpResponse

from core.utils.network import get_client_ip

logger = logging.getLogger("access")


class _DatabaseExecutionTiming:
    """只累计执行耗时，不保留 SQL 或参数；不包含提交、连接及结果读取耗时。"""

    def __init__(self) -> None:
        self.count = 0
        self.total_seconds = 0.0
        self.max_seconds = 0.0

    def __call__(self, execute: Callable[..., Any], sql: Any, params: Any, many: bool, context: dict[str, Any]) -> Any:
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed = time.perf_counter() - started
            self.count += 1
            self.total_seconds += elapsed
            self.max_seconds = max(self.max_seconds, elapsed)


def _sanitize_log_value(value: Any, *, max_length: int = 2048) -> str:
    text = str(value)
    sanitized = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if len(sanitized) > max_length:
        return sanitized[:max_length]
    return sanitized


class AccessLogMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if getattr(settings, "ACCESS_LOG_ENABLED", True) is False:
            return self.get_response(request)

        path = getattr(request, "path", "")
        if settings.STATIC_URL and path.startswith(settings.STATIC_URL):
            return self.get_response(request)
        if settings.MEDIA_URL and path.startswith(settings.MEDIA_URL):
            return self.get_response(request)

        start = time.monotonic()
        database_timing = _DatabaseExecutionTiming()
        response = None
        try:
            # 注册包装器不会打开连接或增加查询，覆盖同步请求线程内的模板查询。
            with ExitStack() as stack:
                for connection in connections.all():
                    stack.enter_context(connection.execute_wrapper(database_timing))
                response = self.get_response(request)
            return response
        finally:
            exc = sys.exc_info()[1]
            duration_ms = int((time.monotonic() - start) * 1000)
            status_code = getattr(response, "status_code", 500)
            user = getattr(request, "user", None)
            user_id = user.pk if user and getattr(user, "is_authenticated", False) else None
            ip = get_client_ip(request, trust_proxy=getattr(settings, "ACCESS_LOG_TRUST_PROXY", False))
            request_id = getattr(request, "id", "-")
            method = _sanitize_log_value(getattr(request, "method", "UNKNOWN"), max_length=16)
            log_path = _sanitize_log_value(path)
            log_ip = _sanitize_log_value(ip, max_length=128)
            log_request_id = _sanitize_log_value(request_id, max_length=128)
            exc_name = _sanitize_log_value(type(exc).__name__, max_length=64) if exc else ""
            database_details = ""
            if duration_ms >= 1000:
                database_details = (
                    f" db_execute_count={database_timing.count}"
                    f" db_execute_ms={database_timing.total_seconds * 1000:.1f}"
                    f" db_execute_max_ms={database_timing.max_seconds * 1000:.1f}"
                )
            logger.info(
                "method=%s path=%s status=%s duration_ms=%s user_id=%s ip=%s request_id=%s%s%s",
                method,
                log_path,
                status_code,
                duration_ms,
                user_id,
                log_ip,
                log_request_id,
                f" exc={exc_name}" if exc else "",
                database_details,
            )
