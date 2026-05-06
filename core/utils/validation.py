"""
输入校验工具模块

提供安全的用户输入解析和验证函数。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, TypeVar, overload
from urllib.parse import unquote

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

from core.exceptions import GameError

T = TypeVar("T")

# 允许的排序字段白名单（防止 SQL 注入）
ALLOWED_ORDERING_FIELDS = {
    # guilds
    "-level",
    "level",
    "-created_at",
    "created_at",
    "name",
    "-name",
    "-total_contribution",
    "total_contribution",
    # trade
    "-listed_at",
    "listed_at",
    "-price",
    "price",
    "-expires_at",
    "expires_at",
    # common
    "-id",
    "id",
    "-updated_at",
    "updated_at",
}


@overload
def safe_int(
    value: Any, default: None = None, min_val: Optional[int] = None, max_val: Optional[int] = None
) -> Optional[int]: ...


@overload
def safe_int(value: Any, default: int = 0, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int: ...


def safe_int(
    value: Any, default: Optional[int] = 0, min_val: Optional[int] = None, max_val: Optional[int] = None
) -> Optional[int]:
    """
    安全地将值转换为整数。

    Args:
        value: 要转换的值
        default: 转换失败时的默认值（可以为 None）
        min_val: 最小值限制
        max_val: 最大值限制

    Returns:
        转换后的整数，或默认值
    """
    try:
        # Bug修复：使用 `is not None` 替代真值判断，避免 value=0 被错误处理
        result = int(value) if value is not None else default
    except (ValueError, TypeError):
        result = default

    if result is not None:
        if min_val is not None:
            result = max(min_val, result)
        if max_val is not None:
            result = min(max_val, result)

    return result


def safe_non_negative_int(value: Any, default: int = 0) -> int:
    """
    安全地将值转换为非负整数（>= 0）。

    Args:
        value: 要转换的值
        default: 转换失败或越界时的默认值

    Returns:
        非负整数或默认值
    """
    result = safe_int(value, default=None)
    if result is None or result < 0:
        return default
    return result


@overload
def safe_positive_int(value: Any, default: None = None) -> Optional[int]: ...


@overload
def safe_positive_int(value: Any, default: int) -> int: ...


def safe_positive_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """
    安全地将值转换为正整数（> 0）。

    Args:
        value: 要转换的值
        default: 转换失败或非正整数时的默认值

    Returns:
        正整数或默认值
    """
    result = safe_int(value, default=None)
    if result is None or result <= 0:
        return default
    return result


def require_positive_int(value: Any, *, contract_name: str) -> int:
    """
    严格要求正整数值（> 0），违规时抛出 GameError。

    用于内部契约检查（如战斗数值校验），与 safe_* 系列不同，
    此函数在输入无效时抛出异常而非返回默认值。

    Args:
        value: 要校验的值
        contract_name: 契约名称（用于错误消息）

    Returns:
        正整数

    Raises:
        GameError: 值无效或非正整数
    """
    if value is None or isinstance(value, bool):
        raise GameError(f"invalid {contract_name}: {value!r}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GameError(f"invalid {contract_name}: {value!r}") from exc
    if parsed <= 0:
        raise GameError(f"invalid {contract_name}: {value!r}")
    return parsed


def parse_json_object(raw: Any, *, empty_as_object: bool = False) -> Optional[Dict[str, Any]]:
    """
    安全解析 JSON 对象。

    Args:
        raw: 原始 JSON 字符串/字节串
        empty_as_object: True 时将空请求体视为 {}

    Returns:
        dict 对象；若解析失败或顶层不是对象则返回 None
    """
    payload = raw
    if empty_as_object and payload in {None, "", b""}:
        payload = "{}"
    try:
        data = json.loads(payload)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


@overload
def safe_float(
    value: Any, default: None = None, min_val: Optional[float] = None, max_val: Optional[float] = None
) -> Optional[float]: ...


@overload
def safe_float(
    value: Any, default: float = 0.0, min_val: Optional[float] = None, max_val: Optional[float] = None
) -> float: ...


def safe_float(
    value: Any, default: Optional[float] = 0.0, min_val: Optional[float] = None, max_val: Optional[float] = None
) -> Optional[float]:
    """
    安全地将值转换为浮点数。

    Args:
        value: 要转换的值
        default: 转换失败时的默认值（可以为 None）
        min_val: 最小值限制
        max_val: 最大值限制

    Returns:
        转换后的浮点数，或默认值
    """
    try:
        # Bug修复：使用 `is not None` 替代真值判断，避免 value=0 被错误处理
        result = float(value) if value is not None else default
    except (ValueError, TypeError):
        result = default

    if result is not None:
        if min_val is not None:
            result = max(min_val, result)
        if max_val is not None:
            result = min(max_val, result)

    return result


def safe_int_list(values: List[str], default: Optional[List[int]] = None) -> List[int]:
    """
    安全地将字符串列表转换为整数列表。

    跳过无法转换的值。

    Args:
        values: 字符串列表
        default: 转换失败或空列表时的默认值

    Returns:
        整数列表
    """
    if default is None:
        default = []

    if not values:
        return default

    result = []
    for v in values:
        try:
            result.append(int(v))
        except (ValueError, TypeError):
            continue

    return result if result else default


def safe_ordering(value: str, default: str, allowed: Optional[set] = None) -> str:
    """
    校验排序字段，防止 SQL 注入。

    Args:
        value: 用户提供的排序字段
        default: 默认排序字段
        allowed: 允许的排序字段集合

    Returns:
        安全的排序字段
    """
    if allowed is None:
        allowed = ALLOWED_ORDERING_FIELDS

    if value and value in allowed:
        return value
    return default


def sanitize_error_message(error: Exception) -> str:
    """
    清理错误消息，避免泄露敏感信息。

    对于 GameError，返回其业务消息。
    对于其他异常，返回通用消息。

    Args:
        error: 异常对象

    Returns:
        安全的错误消息
    """
    if isinstance(error, GameError):
        return error.message
    return "操作失败，请稍后重试"


def safe_redirect_url(request: HttpRequest, url: Optional[str], default: str) -> str:
    """
    验证重定向 URL 是否安全（防止开放重定向漏洞）。

    Args:
        request: HTTP 请求对象
        url: 用户提供的重定向 URL
        default: 默认重定向 URL（必须是安全的内部路径）

    Returns:
        安全的重定向 URL
    """
    candidate = (url or "").strip()

    # 某些代理/客户端会把片段标识符 `#` 编码进路径（如 `/manor/%23building-77`），
    # 甚至出现二次编码（`/manor/%2523building-77`）。
    # 这里在安全校验前最多解码两次以恢复正确的锚点跳转。
    if candidate and "#" not in candidate and "%" in candidate:
        probe = candidate
        for _ in range(2):
            decoded = unquote(probe)
            if decoded == probe:
                break
            if "#" in decoded:
                candidate = decoded
                break
            probe = decoded

    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default
