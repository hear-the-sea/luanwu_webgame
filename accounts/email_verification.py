from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.urls import reverse

from .email_providers import EMAIL_PROVIDER_RESEND, get_email_provider_connection
from .models import User

logger = logging.getLogger(__name__)

EMAIL_VERIFICATION_SALT = "accounts.email-verification"


class EmailVerificationDeliveryError(RuntimeError):
    """Raised when the verification message cannot be handed to the email backend."""


def build_email_verification_token(user: User) -> str:
    if not user.pk or not user.email:
        raise ValueError("email verification requires a persisted user with an email")
    signer = TimestampSigner(salt=EMAIL_VERIFICATION_SALT)
    return signer.sign_object({"user_id": int(user.pk), "email": str(user.email)})


def get_user_from_email_verification_token(token: str, *, allow_expired: bool = False) -> User | None:
    """Resolve a signed token; expiry bypass is only for resend recovery."""

    signer = TimestampSigner(salt=EMAIL_VERIFICATION_SALT)
    try:
        payload: Any = signer.unsign_object(
            token,
            max_age=(
                None if allow_expired else int(getattr(settings, "EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS", 86400))
            ),
        )
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    user_id = payload.get("user_id")
    email = payload.get("email")
    if not isinstance(user_id, int) or user_id <= 0 or not isinstance(email, str) or not email:
        return None
    return User.objects.filter(pk=user_id, email=email).first()


def verification_url(request, token: str) -> str:
    return request.build_absolute_uri(reverse("accounts:verify_email", kwargs={"token": token}))


def send_email_verification_message(
    *,
    request,
    user: User,
    token: str,
    provider: str = EMAIL_PROVIDER_RESEND,
) -> int:
    if not user.email:
        raise ValueError("email verification requires a user email")

    url = verification_url(request, token)
    age_hours = max(1, int(getattr(settings, "EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS", 86400)) // 3600)
    message = (
        "欢迎来到春秋乱世庄园主！\n\n"
        "请点击下面的链接验证你的邮箱，完成注册：\n"
        f"{url}\n\n"
        f"该链接将在 {age_hours} 小时后失效。\n"
        "如果这不是你的操作，请忽略本邮件。"
    )
    try:
        sent_count = send_mail(
            subject="完成邮箱验证，开启你的春秋乱世之旅",
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
            connection=get_email_provider_connection(provider),
        )
    except Exception as exc:
        logger.warning(
            "Failed to send registration email verification message via provider=%s",
            provider,
            exc_info=True,
        )
        raise EmailVerificationDeliveryError("verification email delivery failed") from exc

    if sent_count != 1:
        raise EmailVerificationDeliveryError("email backend did not accept the verification message")
    return sent_count
