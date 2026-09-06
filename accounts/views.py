from __future__ import annotations

import logging
import time
import unicodedata
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
from django.views.generic import CreateView, TemplateView, View

from core.config import SECURITY
from core.utils.network import get_client_ip
from core.utils.rate_limit import rate_limit_redirect
from gameplay.models import Manor
from gameplay.services.manor.core import ManorNameConflictError

from .email_providers import EMAIL_PROVIDER_RESEND, alternate_email_provider
from .email_quota import (
    EmailProviderUnavailable,
    EmailQuotaExceeded,
    EmailSendReservation,
    get_email_quota_status,
    is_email_send_quota_exhausted,
    release_email_send_slot,
    reserve_email_send_slot,
)
from .email_verification import (
    EmailVerificationDeliveryError,
    build_email_verification_token,
    get_user_from_email_verification_token,
    send_email_verification_message,
)
from .forms import EmailVerificationRecoveryForm, LoginForm, SignUpForm
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
EMAIL_QUOTA_EXHAUSTED_MESSAGE = "本月注册验证邮件额度已用尽，注册暂时关闭，请下月再试。"
EMAIL_DAILY_QUOTA_EXHAUSTED_MESSAGE = "今日验证邮件额度已用尽，注册暂时关闭，请明日再试。"
EMAIL_PROVIDER_UNAVAILABLE_MESSAGE = "验证邮件服务暂时不可用，请稍后再试。"
EMAIL_VERIFICATION_DELIVERY_ERROR_MESSAGE = "验证邮件发送失败，请稍后重试。"
EMAIL_VERIFICATION_RESEND_COOLDOWN_MESSAGE = "验证邮件刚刚发送过，请稍后再试。"
EMAIL_VERIFICATION_RESEND_ERROR_MESSAGE = "验证邮件发送失败，请稍后重试。"
EMAIL_VERIFICATION_RECOVERY_ERROR_MESSAGE = "系统繁忙，请稍后再试。"
EMAIL_VERIFICATION_RECOVERY_QUOTA_MESSAGE = "本月验证邮件额度已用尽，暂时无法发送，请下月再试。"
EMAIL_VERIFICATION_RESEND_QUOTA_MESSAGE = "本月注册验证邮件额度已用尽，暂时无法重新发送，请下月再试。"
EMAIL_VERIFICATION_RESEND_SESSION_KEY = "pending_email_verification_user_id"
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = max(
    1,
    int(getattr(settings, "EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS", 60)),
)
EMAIL_VERIFICATION_RESEND_IP_LIMIT = max(
    1,
    int(getattr(settings, "EMAIL_VERIFICATION_RESEND_IP_LIMIT", 5)),
)
EMAIL_VERIFICATION_RESEND_IP_WINDOW_SECONDS = max(
    1,
    int(getattr(settings, "EMAIL_VERIFICATION_RESEND_IP_WINDOW_SECONDS", 3600)),
)
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


def _normalize_login_throttle_username(username: str | None) -> str | None:
    normalized = unicodedata.normalize("NFKC", str(username or "")).strip().casefold()
    return normalized or None


def _get_login_attempt_key(request, username: str | None = None) -> tuple[str, str | None]:
    """
    获取登录尝试的缓存 key（基于 IP + 用户名双重限制）。

    Returns:
        (ip_key, username_key) - 两个缓存 key
    """
    ip = _get_client_ip(request)
    username = _normalize_login_throttle_username(username)
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
    username = _normalize_login_throttle_username(username)
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
        # 登录成功只清理用户名桶；IP 桶保留共享出口上的整体攻击信号。
        username = form.cleaned_data.get("username", "")
        _clear_login_attempts(self.request, username, clear_ip=False)
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


def _email_verification_recovery_identifier(request) -> str:
    email = (request.POST.get("email", "") or "").strip().lower()
    if email:
        return f"email:{email}"
    return f"missing-email:{_get_client_ip(request)}"


def _get_pending_email_verification_user(request, *, token: str | None = None) -> User | None:
    if token:
        token_user = get_user_from_email_verification_token(token, allow_expired=True)
        if token_user is not None and not token_user.is_active and not token_user.email_verified:
            return token_user

    raw_user_id = request.session.get(EMAIL_VERIFICATION_RESEND_SESSION_KEY)
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        user_id = 0

    if user_id > 0:
        pending_user = User.objects.filter(
            pk=user_id,
            is_active=False,
            email_verified=False,
        ).first()
        if pending_user is not None:
            return pending_user

    return None


def _email_verification_pending_context(user: User, **extra) -> dict[str, object]:
    context: dict[str, object] = {
        "email": user.email,
        "resend_available": True,
        "resend_cooldown_seconds": EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    }
    context.update(extra)
    return context


def _email_verification_resend_cache_key(user_id: int) -> str:
    return f"email-verification-resend:user:{user_id}"


def _email_quota_message(*, registration: bool = False) -> str:
    status = get_email_quota_status()
    if status.monthly_exhausted:
        return EMAIL_QUOTA_EXHAUSTED_MESSAGE if registration else "本月验证邮件额度已用尽，请下月再试。"
    if status.daily_exhausted:
        return EMAIL_DAILY_QUOTA_EXHAUSTED_MESSAGE if registration else "今日验证邮件额度已用尽，请明日再试。"
    return EMAIL_PROVIDER_UNAVAILABLE_MESSAGE


def _email_quota_exception_message(
    exc: EmailQuotaExceeded,
    *,
    registration: bool = False,
    recovery: bool = False,
) -> str:
    if exc.scope == "monthly":
        if registration:
            return EMAIL_QUOTA_EXHAUSTED_MESSAGE
        if recovery:
            return EMAIL_VERIFICATION_RECOVERY_QUOTA_MESSAGE
        return EMAIL_VERIFICATION_RESEND_QUOTA_MESSAGE
    if registration:
        return EMAIL_DAILY_QUOTA_EXHAUSTED_MESSAGE
    if recovery:
        return "今日验证邮件额度已用尽，暂时无法发送，请明日再试。"
    return "今日注册验证邮件额度已用尽，暂时无法重新发送，请明日再试。"


def _remember_email_verification_provider(user: User, provider: str) -> None:
    if user.email_verification_last_provider == provider:
        return
    user.email_verification_last_provider = provider
    user.save(update_fields=("email_verification_last_provider",))


def _preferred_email_provider_for_resend(user: User) -> str:
    return alternate_email_provider(user.email_verification_last_provider)


def _claim_email_verification_resend_cooldown(user_id: int) -> bool | None:
    try:
        return bool(
            cache.add(
                _email_verification_resend_cache_key(user_id),
                "1",
                timeout=EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
            )
        )
    except LOGIN_CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.error("Failed to claim email verification resend cooldown", exc_info=True)
        return None


def _release_email_verification_resend_cooldown(user_id: int) -> None:
    try:
        cache.delete(_email_verification_resend_cache_key(user_id))
    except LOGIN_CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.error("Failed to release email verification resend cooldown", exc_info=True)


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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["registration_email_quota_exhausted"] = is_email_send_quota_exhausted()
        context["registration_email_quota_message"] = _email_quota_message(registration=True)
        return context

    @staticmethod
    def _release_unattempted_email_reservation(
        reservation: EmailSendReservation | None,
        *,
        delivery_attempted: bool,
    ) -> None:
        if reservation is None or delivery_attempted:
            return
        try:
            release_email_send_slot(reservation)
        except Exception:
            logger.error("Failed to release unused registration email quota reservation", exc_info=True)

    def form_valid(self, form):
        if is_email_send_quota_exhausted():
            form.add_error(None, EMAIL_QUOTA_EXHAUSTED_MESSAGE)
            return self.form_invalid(form)

        user = prepare_signup_user(form=form)
        user.is_active = False
        user.email_verified = False
        reservation: EmailSendReservation | None = None
        delivery_attempted = False
        try:
            # Reserve the provider budget before the external SMTP call. The
            # reservation is conservative: once delivery is attempted, the
            # slot is kept even if the provider reports a transient failure.
            reservation = reserve_email_send_slot(preferred_provider=EMAIL_PROVIDER_RESEND)
            with transaction.atomic():
                user.email_verification_last_provider = reservation.provider
                save_signup_user(user, transaction_atomic=transaction.atomic)
                token = build_email_verification_token(user)
                delivery_attempted = True
                send_email_verification_message(
                    request=self.request,
                    user=user,
                    token=token,
                    provider=reservation.provider,
                )
        except EmailQuotaExceeded as exc:
            form.add_error(None, _email_quota_exception_message(exc, registration=True))
            return self.form_invalid(form)
        except EmailProviderUnavailable:
            form.add_error(None, EMAIL_PROVIDER_UNAVAILABLE_MESSAGE)
            return self.form_invalid(form)
        except ManorNameConflictError:
            self._release_unattempted_email_reservation(reservation, delivery_attempted=delivery_attempted)
            form.add_error("manor_name", "该庄园名称已被使用")
            return self.form_invalid(form)
        except IntegrityError:
            self._release_unattempted_email_reservation(reservation, delivery_attempted=delivery_attempted)
            apply_registration_integrity_errors(
                form=form,
                user_model=User,
                manor_model=Manor,
            )
            return self.form_invalid(form)
        except EmailVerificationDeliveryError:
            # The user transaction has rolled back. Keep the conservative
            # reservation because an SMTP failure can happen after handoff.
            form.add_error(None, EMAIL_VERIFICATION_DELIVERY_ERROR_MESSAGE)
            return self.form_invalid(form)
        except DatabaseError:
            self._release_unattempted_email_reservation(reservation, delivery_attempted=delivery_attempted)
            logger.error("Failed to create pending email-verified user", exc_info=True)
            form.add_error(None, "注册失败，请稍后重试。")
            return self.form_invalid(form)
        self.object = user
        self.request.session[EMAIL_VERIFICATION_RESEND_SESSION_KEY] = self.object.pk

        return render(
            self.request,
            "accounts/email_verification_pending.html",
            _email_verification_pending_context(self.object, resend_token=token),
        )


@method_decorator(
    rate_limit_redirect(
        "email_verification_recovery_email",
        limit=REGISTRATION_EMAIL_LIMIT,
        window_seconds=REGISTRATION_RATE_WINDOW,
        key_func=_email_verification_recovery_identifier,
        redirect_url=cast(str, reverse_lazy("accounts:email_verification_recovery")),
        error_message="该邮箱的验证邮件请求过于频繁，请稍后再试",
    ),
    name="dispatch",
)
@method_decorator(
    rate_limit_redirect(
        "email_verification_resend_ip",
        limit=EMAIL_VERIFICATION_RESEND_IP_LIMIT,
        window_seconds=EMAIL_VERIFICATION_RESEND_IP_WINDOW_SECONDS,
        redirect_url=cast(str, reverse_lazy("accounts:email_verification_recovery")),
        error_message="验证邮件请求过于频繁，请稍后再试",
    ),
    name="dispatch",
)
class EmailVerificationRecoveryView(View):
    template_name = "accounts/email_verification_recovery.html"

    def _render(self, request, form: EmailVerificationRecoveryForm, *, status: int = 200, **context):
        context.setdefault("resend_cooldown_seconds", EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS)
        context.setdefault("recovery_quota_message", _email_quota_message())
        context["form"] = form
        return render(request, self.template_name, context, status=status)

    def get(self, request):
        return self._render(request, EmailVerificationRecoveryForm())

    def post(self, request):
        form = EmailVerificationRecoveryForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        if is_email_send_quota_exhausted():
            return self._render(
                request,
                EmailVerificationRecoveryForm(),
                recovery_quota_exhausted=True,
                recovery_quota_message=_email_quota_message(),
            )

        email = form.cleaned_data["email"]
        try:
            pending_user = User.objects.filter(
                email__iexact=email,
                is_active=False,
                email_verified=False,
            ).first()
        except DatabaseError:
            logger.error("Failed to look up email verification recovery user", exc_info=True)
            return self._render(
                request,
                form,
                recovery_error=EMAIL_VERIFICATION_RECOVERY_ERROR_MESSAGE,
                status=503,
            )

        if pending_user is not None:
            cooldown_claimed = _claim_email_verification_resend_cooldown(pending_user.pk)
            if cooldown_claimed is None:
                return self._render(
                    request,
                    form,
                    recovery_error=EMAIL_VERIFICATION_RECOVERY_ERROR_MESSAGE,
                    status=503,
                )

            if cooldown_claimed:
                reservation: EmailSendReservation | None = None
                delivery_attempted = False
                try:
                    reservation = reserve_email_send_slot(
                        preferred_provider=_preferred_email_provider_for_resend(pending_user),
                    )
                    verification_token = build_email_verification_token(pending_user)
                    _remember_email_verification_provider(pending_user, reservation.provider)
                    delivery_attempted = True
                    send_email_verification_message(
                        request=request,
                        user=pending_user,
                        token=verification_token,
                        provider=reservation.provider,
                    )
                except EmailQuotaExceeded as exc:
                    _release_email_verification_resend_cooldown(pending_user.pk)
                    return self._render(
                        request,
                        EmailVerificationRecoveryForm(),
                        recovery_quota_exhausted=True,
                        recovery_quota_message=_email_quota_exception_message(exc, recovery=True),
                    )
                except EmailProviderUnavailable:
                    _release_email_verification_resend_cooldown(pending_user.pk)
                    return self._render(
                        request,
                        form,
                        recovery_error=EMAIL_PROVIDER_UNAVAILABLE_MESSAGE,
                        status=503,
                    )
                except EmailVerificationDeliveryError:
                    # Keep the quota reservation and cooldown after an SMTP
                    # attempt because the provider may have accepted the message.
                    logger.warning("Failed to deliver recovered email verification message", exc_info=True)
                except (DatabaseError, ValueError):
                    if not delivery_attempted:
                        RegisterView._release_unattempted_email_reservation(
                            reservation,
                            delivery_attempted=False,
                        )
                        _release_email_verification_resend_cooldown(pending_user.pk)
                    logger.error("Failed to recover email verification message", exc_info=True)
                    return self._render(
                        request,
                        form,
                        recovery_error=EMAIL_VERIFICATION_RECOVERY_ERROR_MESSAGE,
                        status=503,
                    )

        return self._render(
            request,
            EmailVerificationRecoveryForm(),
            recovery_submitted=True,
        )


@method_decorator(
    rate_limit_redirect(
        "email_verification_resend_ip",
        limit=EMAIL_VERIFICATION_RESEND_IP_LIMIT,
        window_seconds=EMAIL_VERIFICATION_RESEND_IP_WINDOW_SECONDS,
        redirect_url=cast(str, reverse_lazy("accounts:register")),
        error_message="验证邮件请求过于频繁，请稍后再试",
    ),
    name="dispatch",
)
class ResendEmailVerificationView(View):
    def post(self, request):
        token = (request.POST.get("token", "") or "").strip() or None
        pending_user = _get_pending_email_verification_user(request, token=token)
        if pending_user is None:
            request.session.pop(EMAIL_VERIFICATION_RESEND_SESSION_KEY, None)
            messages.info(request, "验证信息已失效，请重新注册。")
            return redirect("accounts:register")

        request.session[EMAIL_VERIFICATION_RESEND_SESSION_KEY] = pending_user.pk
        cooldown_claimed = _claim_email_verification_resend_cooldown(pending_user.pk)
        if cooldown_claimed is None:
            return render(
                request,
                "accounts/email_verification_pending.html",
                _email_verification_pending_context(
                    pending_user,
                    resend_error="系统繁忙，请稍后再试。",
                    resend_token=token,
                ),
                status=503,
            )
        if not cooldown_claimed:
            return render(
                request,
                "accounts/email_verification_pending.html",
                _email_verification_pending_context(
                    pending_user,
                    resend_error=EMAIL_VERIFICATION_RESEND_COOLDOWN_MESSAGE,
                    resend_token=token,
                ),
            )

        reservation: EmailSendReservation | None = None
        delivery_attempted = False
        try:
            reservation = reserve_email_send_slot(
                preferred_provider=_preferred_email_provider_for_resend(pending_user),
            )
            verification_token = build_email_verification_token(pending_user)
            _remember_email_verification_provider(pending_user, reservation.provider)
            delivery_attempted = True
            send_email_verification_message(
                request=request,
                user=pending_user,
                token=verification_token,
                provider=reservation.provider,
            )
        except EmailQuotaExceeded as exc:
            _release_email_verification_resend_cooldown(pending_user.pk)
            return render(
                request,
                "accounts/email_verification_pending.html",
                _email_verification_pending_context(
                    pending_user,
                    resend_quota_exhausted=True,
                    resend_quota_message=_email_quota_exception_message(exc),
                    resend_token=token,
                ),
            )
        except EmailProviderUnavailable:
            self._release_unattempted_resend_state(
                pending_user_id=pending_user.pk,
                reservation=reservation,
                delivery_attempted=delivery_attempted,
            )
            return render(
                request,
                "accounts/email_verification_pending.html",
                _email_verification_pending_context(
                    pending_user,
                    resend_error=EMAIL_PROVIDER_UNAVAILABLE_MESSAGE,
                    resend_token=token,
                ),
                status=503,
            )
        except EmailVerificationDeliveryError:
            # Keep the reservation and cooldown after an SMTP attempt because
            # the provider may have accepted the message before reporting an error.
            return render(
                request,
                "accounts/email_verification_pending.html",
                _email_verification_pending_context(
                    pending_user,
                    resend_error=EMAIL_VERIFICATION_RESEND_ERROR_MESSAGE,
                    resend_token=verification_token,
                ),
            )
        except (DatabaseError, ValueError):
            self._release_unattempted_resend_state(
                pending_user_id=pending_user.pk,
                reservation=reservation,
                delivery_attempted=delivery_attempted,
            )
            logger.error("Failed to resend email verification message", exc_info=True)
            return render(
                request,
                "accounts/email_verification_pending.html",
                _email_verification_pending_context(
                    pending_user,
                    resend_error="发送验证邮件失败，请稍后重试。",
                    resend_token=token,
                ),
            )

        return render(
            request,
            "accounts/email_verification_pending.html",
            _email_verification_pending_context(
                pending_user,
                resend_success=True,
                resend_token=verification_token,
            ),
        )

    @staticmethod
    def _release_unattempted_resend_state(
        *,
        pending_user_id: int,
        reservation: EmailSendReservation | None,
        delivery_attempted: bool,
    ) -> None:
        if delivery_attempted:
            return
        RegisterView._release_unattempted_email_reservation(
            reservation,
            delivery_attempted=False,
        )
        _release_email_verification_resend_cooldown(pending_user_id)


class VerifyEmailView(View):
    def get(self, request, token: str):
        user = get_user_from_email_verification_token(token)
        if user is None:
            resend_user = _get_pending_email_verification_user(request, token=token)
            return render(
                request,
                "accounts/email_verification_result.html",
                {
                    "verified": False,
                    "resend_user": resend_user,
                    "resend_token": token if resend_user is not None else None,
                    "resend_cooldown_seconds": EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
                },
                status=400,
            )

        try:
            with transaction.atomic():
                verified_user = User.objects.select_for_update().get(pk=user.pk)
                if verified_user.email_verified:
                    request.session.pop(EMAIL_VERIFICATION_RESEND_SESSION_KEY, None)
                    return render(
                        request,
                        "accounts/email_verification_result.html",
                        {"already_verified": True},
                    )
                verified_user.email_verified = True
                verified_user.is_active = True
                verified_user.save(update_fields=("email_verified", "is_active"))
        except User.DoesNotExist:
            return render(
                request,
                "accounts/email_verification_result.html",
                {"verified": False},
                status=400,
            )

        request.session.pop(EMAIL_VERIFICATION_RESEND_SESSION_KEY, None)
        login(request, verified_user)
        messages.success(request, "邮箱验证成功，欢迎进入庄园。")
        return redirect("home")


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/profile.html"
