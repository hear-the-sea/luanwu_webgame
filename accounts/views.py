from __future__ import annotations

import logging
import time
from threading import Lock
from typing import cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import CreateView, TemplateView

from core.config import SECURITY
from core.utils.network import get_client_ip
from core.utils.rate_limit import rate_limit_redirect
from gameplay.models import Manor
from gameplay.services.manor.core import ManorNameConflictError

from .forms import LoginForm, SignUpForm
from .login_runtime import check_login_attempts as runtime_check_login_attempts
from .login_runtime import clear_login_attempts as runtime_clear_login_attempts
from .login_runtime import increment_attempt_counter as runtime_increment_attempt_counter
from .login_runtime import normalize_lock_ttl as runtime_normalize_lock_ttl
from .login_runtime import record_failed_attempt as runtime_record_failed_attempt
from .login_runtime import safe_cache_delete as runtime_safe_cache_delete
from .login_runtime import safe_cache_get as runtime_safe_cache_get
from .login_runtime import safe_cache_set as runtime_safe_cache_set
from .models import User
from .register_runtime import apply_registration_integrity_errors, prepare_signup_user, save_signup_user

# 从 core.config 导入配置
LOGIN_ATTEMPT_LIMIT = SECURITY.LOGIN_ATTEMPT_LIMIT
LOGIN_ATTEMPT_WINDOW = SECURITY.LOGIN_ATTEMPT_WINDOW
LOGIN_LOCKOUT_DURATION = SECURITY.LOGIN_LOCKOUT_DURATION
REGISTRATION_IP_LIMIT = SECURITY.REGISTRATION_IP_LIMIT
REGISTRATION_EMAIL_LIMIT = SECURITY.REGISTRATION_EMAIL_LIMIT
REGISTRATION_RATE_WINDOW = SECURITY.REGISTRATION_RATE_WINDOW
logger = logging.getLogger(__name__)
_LOCAL_LOGIN_CACHE: dict[str, tuple[object, float]] = {}
_LOCAL_LOGIN_CACHE_GUARD = Lock()
_LOCAL_LOGIN_CACHE_MAX_SIZE = 5000
LOGIN_CACHE_INFRASTRUCTURE_EXCEPTIONS = (DatabaseError, ConnectionError, OSError, TimeoutError)


def _get_client_ip(request) -> str:
    """
    获取客户端真实 IP 地址。

    安全说明：
    - 优先使用 REMOTE_ADDR（不可伪造）
    - 仅当配置了可信代理时才使用 X-Forwarded-For
    - 防止攻击者通过伪造 HTTP 头绕过登录限制
    """
    return get_client_ip(request, trust_proxy=True)


def _get_login_attempt_key(request, username: str | None = None) -> tuple[str, str | None]:
    """
    获取登录尝试的缓存 key（基于 IP + 用户名双重限制）。

    Returns:
        (ip_key, username_key) - 两个缓存 key
    """
    ip = _get_client_ip(request)
    ip_key = f"login_attempts:ip:{ip}"
    username_key = f"login_attempts:user:{username}" if username else None
    return ip_key, username_key


def _get_login_lock_key(request, username: str | None = None) -> tuple[str, str | None]:
    """
    获取登录锁缓存 key（基于 IP + 用户名双重限制）。

    Returns:
        (ip_lock_key, username_lock_key)
    """
    ip = _get_client_ip(request)
    ip_lock_key = f"login_lock:ip:{ip}"
    username_lock_key = f"login_lock:user:{username}" if username else None
    return ip_lock_key, username_lock_key


def _cleanup_local_login_cache(now: float) -> None:
    expired_keys = [key for key, (_value, expire_at) in _LOCAL_LOGIN_CACHE.items() if expire_at <= now]
    for key in expired_keys[:1000]:
        _LOCAL_LOGIN_CACHE.pop(key, None)

    if len(_LOCAL_LOGIN_CACHE) <= _LOCAL_LOGIN_CACHE_MAX_SIZE:
        return

    for key, _value in sorted(_LOCAL_LOGIN_CACHE.items(), key=lambda item: item[1][1])[:500]:
        _LOCAL_LOGIN_CACHE.pop(key, None)


def _local_login_cache_get(key: str, default=None):
    now = time.monotonic()
    with _LOCAL_LOGIN_CACHE_GUARD:
        record = _LOCAL_LOGIN_CACHE.get(key)
        if record is None:
            return default
        value, expire_at = record
        if expire_at <= now:
            _LOCAL_LOGIN_CACHE.pop(key, None)
            return default
        return value


def _local_login_cache_set(key: str, value, timeout: int) -> None:
    expire_at = time.monotonic() + max(1, int(timeout))
    with _LOCAL_LOGIN_CACHE_GUARD:
        _LOCAL_LOGIN_CACHE[key] = (value, expire_at)
        if len(_LOCAL_LOGIN_CACHE) > _LOCAL_LOGIN_CACHE_MAX_SIZE:
            _cleanup_local_login_cache(time.monotonic())


def _local_login_cache_delete(key: str) -> None:
    with _LOCAL_LOGIN_CACHE_GUARD:
        _LOCAL_LOGIN_CACHE.pop(key, None)


def _local_login_cache_incr(key: str, timeout: int) -> int:
    now = time.monotonic()
    expire_at = now + max(1, int(timeout))
    with _LOCAL_LOGIN_CACHE_GUARD:
        record = _LOCAL_LOGIN_CACHE.get(key)
        if record is None or record[1] <= now:
            _LOCAL_LOGIN_CACHE[key] = (1, expire_at)
            return 1

        current_value, _current_expire_at = record
        if isinstance(current_value, bool):
            next_value = 1
        elif isinstance(current_value, (int, float, str, bytes, bytearray)):
            try:
                next_value = int(current_value) + 1
            except ValueError:
                next_value = 1
        else:
            next_value = 1
        _LOCAL_LOGIN_CACHE[key] = (next_value, expire_at)
        return next_value


def _check_login_attempts(request, username: str = None) -> tuple[bool, int]:
    return runtime_check_login_attempts(
        request,
        username,
        get_login_lock_key=_get_login_lock_key,
        safe_cache_get=_safe_cache_get,
        normalize_lock_ttl=_normalize_lock_ttl,
    )


def _normalize_lock_ttl(lock_key: str) -> int:
    return runtime_normalize_lock_ttl(
        lock_key,
        cache_obj=cache,
        logger=logger,
        infrastructure_exceptions=LOGIN_CACHE_INFRASTRUCTURE_EXCEPTIONS,
        lockout_duration=LOGIN_LOCKOUT_DURATION,
    )


def _increment_attempt_counter(key: str) -> int:
    from core.utils.task_monitoring import increment_degraded_counter

    return runtime_increment_attempt_counter(
        key,
        cache_obj=cache,
        logger=logger,
        settings_obj=settings,
        infrastructure_exceptions=LOGIN_CACHE_INFRASTRUCTURE_EXCEPTIONS,
        login_attempt_limit=LOGIN_ATTEMPT_LIMIT,
        login_attempt_window=LOGIN_ATTEMPT_WINDOW,
        safe_cache_get=_safe_cache_get,
        safe_cache_set=_safe_cache_set,
        increment_degraded_counter=increment_degraded_counter,
    )


def _record_failed_attempt(request, username: str = None) -> int:
    return runtime_record_failed_attempt(
        request,
        username,
        get_login_attempt_key=_get_login_attempt_key,
        get_login_lock_key=_get_login_lock_key,
        increment_attempt_counter=_increment_attempt_counter,
        safe_cache_set=_safe_cache_set,
        login_attempt_limit=LOGIN_ATTEMPT_LIMIT,
        login_lockout_duration=LOGIN_LOCKOUT_DURATION,
    )


def _clear_login_attempts(request, username: str = None, *, clear_ip: bool = True) -> None:
    runtime_clear_login_attempts(
        request,
        username,
        get_login_attempt_key=_get_login_attempt_key,
        get_login_lock_key=_get_login_lock_key,
        safe_cache_delete=_safe_cache_delete,
        clear_ip=clear_ip,
    )


_CACHE_MISS = object()


def _safe_cache_get(key: str, default=None):
    return runtime_safe_cache_get(
        key,
        default,
        local_cache_get=_local_login_cache_get,
        local_cache_set=_local_login_cache_set,
        cache_obj=cache,
        logger=logger,
        infrastructure_exceptions=LOGIN_CACHE_INFRASTRUCTURE_EXCEPTIONS,
        local_cache_timeout=max(LOGIN_ATTEMPT_WINDOW, LOGIN_LOCKOUT_DURATION),
        cache_miss_sentinel=_CACHE_MISS,
    )


def _safe_cache_set(key: str, value, timeout: int) -> None:
    runtime_safe_cache_set(
        key,
        value,
        timeout,
        local_cache_set=_local_login_cache_set,
        cache_obj=cache,
        logger=logger,
        infrastructure_exceptions=LOGIN_CACHE_INFRASTRUCTURE_EXCEPTIONS,
    )


def _safe_cache_delete(key: str) -> None:
    runtime_safe_cache_delete(
        key,
        local_cache_delete=_local_login_cache_delete,
        cache_obj=cache,
        logger=logger,
        infrastructure_exceptions=LOGIN_CACHE_INFRASTRUCTURE_EXCEPTIONS,
    )


class LoginView(DjangoLoginView):
    form_class = LoginForm
    template_name = "registration/login.html"

    def dispatch(self, request, *args, **kwargs):
        """检查是否被锁定"""
        username_hint = None
        if request.method == "POST":
            username_hint = (request.POST.get("username", "") or "").strip() or None
        is_locked, remaining = _check_login_attempts(request, username_hint)
        if is_locked:
            # 安全修复：使用模糊的提示信息，不泄露精确的锁定时间
            messages.error(request, "登录尝试次数过多，请稍后再试")
            return render(request, self.template_name, {"form": self.form_class()})
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # 登录成功后清理用户名与 IP 维度失败记录，避免共享出口 IP 被持续误伤。
        username = form.cleaned_data.get("username", "")
        _clear_login_attempts(self.request, username, clear_ip=True)
        messages.success(self.request, "欢迎回来，领主大人！")
        return super().form_valid(form)

    def form_invalid(self, form):
        # 登录失败，记录尝试次数（基于 IP + 用户名双重限制）
        username = form.cleaned_data.get("username", "")
        attempts = _record_failed_attempt(self.request, username)
        remaining = LOGIN_ATTEMPT_LIMIT - attempts
        if remaining > 0:
            messages.warning(self.request, "用户名或密码错误，请重试")
        else:
            # 安全修复：使用模糊的提示信息
            messages.error(self.request, "登录尝试次数过多，请稍后再试")
        return super().form_invalid(form)


def _registration_email_identifier(request) -> str:
    email = (request.POST.get("email", "") or "").strip().lower()
    if email:
        return f"email:{email}"
    username = (request.POST.get("username", "") or "").strip().lower()
    return f"missing-email:{username or 'anonymous'}"


@method_decorator(
    rate_limit_redirect(
        "registration_ip",
        limit=REGISTRATION_IP_LIMIT,
        window_seconds=REGISTRATION_RATE_WINDOW,
        redirect_url=cast(str, reverse_lazy("accounts:register")),
        error_message="注册请求过于频繁，请稍后再试",
    ),
    name="dispatch",
)
@method_decorator(
    rate_limit_redirect(
        "registration_email",
        limit=REGISTRATION_EMAIL_LIMIT,
        window_seconds=REGISTRATION_RATE_WINDOW,
        key_func=_registration_email_identifier,
        redirect_url=cast(str, reverse_lazy("accounts:register")),
        error_message="注册请求过于频繁，请稍后再试",
    ),
    name="dispatch",
)
class RegisterView(CreateView):
    model = User
    form_class = SignUpForm
    template_name = "accounts/register.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        user = prepare_signup_user(form=form)
        try:
            save_signup_user(user, transaction_atomic=transaction.atomic)
        except ManorNameConflictError:
            form.add_error("manor_name", "该庄园名称已被使用")
            return self.form_invalid(form)
        except IntegrityError:
            apply_registration_integrity_errors(
                form=form,
                user_model=User,
                manor_model=Manor,
            )
            return self.form_invalid(form)
        self.object = user

        login(self.request, self.object)
        messages.success(self.request, "注册成功，已自动登录。")
        return redirect(self.success_url)


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/profile.html"
