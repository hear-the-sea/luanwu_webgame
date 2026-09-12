from __future__ import annotations

import hashlib
import logging
from functools import wraps
from typing import Callable

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpRequest
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from core.utils.http import is_json_request
from core.utils.network import get_client_ip
from core.utils.responses import json_error
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_MEMCACHE_KEY_LIMIT = 250


def _cache_error_response(is_json: bool, error_message: str, request: HttpRequest, redirect_url: str | None = None):
    if is_json:
        return json_error("系统繁忙，请稍后再试", status=503)

    messages.error(request, error_message)
    if redirect_url:
        return redirect(redirect_url)

    referer = request.META.get("HTTP_REFERER", "")
    if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
        return redirect(referer)
    return redirect("/")


def _default_identifier(request: HttpRequest) -> str:
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        return f"user:{user.pk}"
    ip = get_client_ip(request, trust_proxy=True)
    return f"ip:{ip}"


def _validate_rate_limit_options(limit: int, window_seconds: int) -> None:
    if limit <= 0:
        raise AssertionError("rate limit requires positive limit")
    if window_seconds <= 0:
        raise AssertionError("rate limit requires positive window_seconds")


def _should_bypass_rate_limit(request: HttpRequest, include_safe_methods: bool) -> bool:
    if not include_safe_methods and request.method in _SAFE_METHODS:
        return True

    user = getattr(request, "user", None)
    return bool(user and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))


def _safe_identifier(request: HttpRequest, key_func: Callable[[HttpRequest], str] | None = None) -> str:
    raw_identifier = key_func(request) if key_func else _default_identifier(request)
    identifier = str(raw_identifier).strip() if raw_identifier is not None else ""
    if identifier:
        return identifier
    return _default_identifier(request)


def _is_cache_safe_identifier(identifier: str) -> bool:
    return bool(identifier) and all(33 <= ord(ch) <= 126 for ch in identifier)


def _build_cache_key(scope: str, identifier: str) -> str:
    safe_scope = str(scope or "").strip()
    if not _is_cache_safe_identifier(safe_scope):
        safe_scope = "default"

    base = f"rl:{safe_scope}:"
    candidate = f"{base}{identifier}"
    if len(candidate) <= _MEMCACHE_KEY_LIMIT and _is_cache_safe_identifier(identifier):
        return candidate

    digest_source = f"{scope}|{identifier}"
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    scope_prefix = safe_scope[:32] or "default"
    return f"rl:{scope_prefix}:h:{digest}"


def _increment_cache_counter(cache_key: str, window_seconds: int) -> int:
    def _safe_cache_add() -> bool:
        return bool(cache.add(cache_key, 1, timeout=window_seconds))

    def _safe_cache_incr() -> int:
        return int(cache.incr(cache_key))

    try:
        if _safe_cache_add():
            return 1
        return _safe_cache_incr()
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds)
        return 1


def _get_rate_limit_count(cache_key: str, window_seconds: int, log_prefix: str) -> int | None:
    try:
        return _increment_cache_counter(cache_key, window_seconds)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.error("%s cache backend unavailable", log_prefix, exc_info=True)
        return None


def rate_limit_json(
    scope: str,
    *,
    limit: int,
    window_seconds: int,
    key_func: Callable[[HttpRequest], str] | None = None,
    error_message: str = "请求过于频繁，请稍后再试",
    include_safe_methods: bool = False,
) -> Callable:
    """
    Lightweight, cache-backed rate limiting for JSON views.

    This is intended for non-DRF endpoints (plain Django views).
    Fails closed (503) if cache is unavailable to keep critical operations safe.
    """
    _validate_rate_limit_options(limit, window_seconds)

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args, **kwargs):
            if _should_bypass_rate_limit(request, include_safe_methods):
                return view_func(request, *args, **kwargs)

            identifier = _safe_identifier(request, key_func)
            cache_key = _build_cache_key(scope, identifier)
            count = _get_rate_limit_count(cache_key, window_seconds, "Rate limit")
            if count is None:
                return _cache_error_response(True, error_message, request)

            if count > limit:
                return json_error(error_message, status=429)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def rate_limit_redirect(
    scope: str,
    *,
    limit: int,
    window_seconds: int,
    key_func: Callable[[HttpRequest], str] | None = None,
    error_message: str = "操作过于频繁，请稍后再试",
    redirect_url: str | None = None,
    include_safe_methods: bool = False,
) -> Callable:
    """
    Lightweight, cache-backed rate limiting for redirect-based views.

    Returns a safe redirect with error message if cache is unavailable.
    """
    _validate_rate_limit_options(limit, window_seconds)

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args, **kwargs):
            if _should_bypass_rate_limit(request, include_safe_methods):
                return view_func(request, *args, **kwargs)

            wants_json = is_json_request(request)
            identifier = _safe_identifier(request, key_func)
            cache_key = _build_cache_key(scope, identifier)
            count = _get_rate_limit_count(cache_key, window_seconds, "Rate limit redirect")
            if count is None:
                if wants_json:
                    return json_error("系统繁忙，请稍后再试", status=503)
                return _cache_error_response(False, "系统繁忙，请稍后再试", request, redirect_url)

            if count > limit:
                if wants_json:
                    return json_error(error_message, status=429)
                return _cache_error_response(False, error_message, request, redirect_url)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
