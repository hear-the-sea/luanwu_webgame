from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend

EMAIL_PROVIDER_RESEND: Final = "resend"
EMAIL_PROVIDER_BREVO: Final = "brevo"
EMAIL_PROVIDER_ORDER: Final[tuple[str, ...]] = (
    EMAIL_PROVIDER_RESEND,
    EMAIL_PROVIDER_BREVO,
)
EMAIL_PROVIDER_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    (EMAIL_PROVIDER_RESEND, "Resend"),
    (EMAIL_PROVIDER_BREVO, "Brevo"),
)
SMTP_EMAIL_BACKEND: Final = "django.core.mail.backends.smtp.EmailBackend"


@dataclass(frozen=True)
class EmailProviderConfig:
    key: str
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    use_ssl: bool
    daily_limit: int


def get_email_provider_config(provider: str) -> EmailProviderConfig:
    if provider == EMAIL_PROVIDER_RESEND:
        return EmailProviderConfig(
            key=provider,
            host=str(getattr(settings, "EMAIL_RESEND_HOST", getattr(settings, "EMAIL_HOST", "")) or "").strip(),
            port=int(getattr(settings, "EMAIL_RESEND_PORT", getattr(settings, "EMAIL_PORT", 587))),
            username=str(
                getattr(settings, "EMAIL_RESEND_HOST_USER", getattr(settings, "EMAIL_HOST_USER", "")) or ""
            ).strip(),
            password=str(
                getattr(settings, "EMAIL_RESEND_HOST_PASSWORD", getattr(settings, "EMAIL_HOST_PASSWORD", "")) or ""
            ),
            use_tls=bool(getattr(settings, "EMAIL_RESEND_USE_TLS", getattr(settings, "EMAIL_USE_TLS", True))),
            use_ssl=bool(getattr(settings, "EMAIL_RESEND_USE_SSL", getattr(settings, "EMAIL_USE_SSL", False))),
            daily_limit=max(0, int(getattr(settings, "EMAIL_RESEND_DAILY_SEND_LIMIT", 100))),
        )
    if provider == EMAIL_PROVIDER_BREVO:
        return EmailProviderConfig(
            key=provider,
            host=str(getattr(settings, "EMAIL_BREVO_HOST", "") or "").strip(),
            port=int(getattr(settings, "EMAIL_BREVO_PORT", 587)),
            username=str(getattr(settings, "EMAIL_BREVO_HOST_USER", "") or "").strip(),
            password=str(getattr(settings, "EMAIL_BREVO_HOST_PASSWORD", "") or ""),
            use_tls=bool(getattr(settings, "EMAIL_BREVO_USE_TLS", True)),
            use_ssl=bool(getattr(settings, "EMAIL_BREVO_USE_SSL", False)),
            daily_limit=max(0, int(getattr(settings, "EMAIL_BREVO_DAILY_SEND_LIMIT", 300))),
        )
    raise ValueError(f"unsupported email provider: {provider}")


def is_email_provider_configured(provider: str) -> bool:
    config = get_email_provider_config(provider)

    # Hermetic test backends and the console backend do not need real SMTP
    # credentials. This keeps local development and tests provider-agnostic.
    if getattr(settings, "EMAIL_BACKEND", "") != SMTP_EMAIL_BACKEND:
        return True

    return bool(config.host and config.username and config.password)


def ordered_email_providers(*, preferred_provider: str | None = None) -> tuple[str, ...]:
    if preferred_provider not in EMAIL_PROVIDER_ORDER:
        return EMAIL_PROVIDER_ORDER
    return (preferred_provider,) + tuple(
        provider for provider in EMAIL_PROVIDER_ORDER if provider != preferred_provider
    )


def alternate_email_provider(last_provider: str | None) -> str:
    if last_provider == EMAIL_PROVIDER_RESEND:
        return EMAIL_PROVIDER_BREVO
    return EMAIL_PROVIDER_RESEND


def get_email_provider_connection(provider: str) -> BaseEmailBackend:
    config = get_email_provider_config(provider)
    if not is_email_provider_configured(provider):
        raise ValueError(f"email provider is not configured: {provider}")

    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "")
    if backend != SMTP_EMAIL_BACKEND:
        return get_connection(backend=backend, fail_silently=False)

    return get_connection(
        backend=backend,
        fail_silently=False,
        host=config.host,
        port=config.port,
        username=config.username,
        password=config.password,
        use_tls=config.use_tls,
        use_ssl=config.use_ssl,
        timeout=max(1, int(getattr(settings, "EMAIL_TIMEOUT", 10))),
    )
