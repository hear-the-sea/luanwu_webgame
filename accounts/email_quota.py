from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .email_providers import (
    EMAIL_PROVIDER_ORDER,
    get_email_provider_config,
    is_email_provider_configured,
    ordered_email_providers,
)
from .models import EmailProviderDailyQuota, EmailSendQuota


class EmailQuotaExceeded(RuntimeError):
    """Raised when the monthly or all configured provider daily budgets are exhausted."""

    def __init__(self, message: str = "email send quota exhausted", *, scope: str = "monthly") -> None:
        super().__init__(message)
        self.scope = scope


class EmailProviderUnavailable(RuntimeError):
    """Raised when no provider has a complete usable configuration."""


@dataclass(frozen=True)
class EmailSendReservation:
    month: date
    day: date
    provider: str


@dataclass(frozen=True)
class EmailQuotaStatus:
    monthly_exhausted: bool
    provider_configured: bool
    daily_exhausted: bool

    @property
    def exhausted(self) -> bool:
        return self.monthly_exhausted or not self.provider_configured or self.daily_exhausted


def quota_month(now: datetime | None = None) -> date:
    current_date = timezone.localdate(now) if now is not None else timezone.localdate()
    return current_date.replace(day=1)


def _quota_day(now: datetime | None = None) -> date:
    return timezone.localdate(now) if now is not None else timezone.localdate()


def _configured_provider_order(*, preferred_provider: str | None = None) -> tuple[str, ...]:
    configured: list[str] = []
    for provider in ordered_email_providers(preferred_provider=preferred_provider):
        config = get_email_provider_config(provider)
        if config.daily_limit <= 0 or not is_email_provider_configured(provider):
            continue
        configured.append(provider)
    return tuple(configured)


def reserve_email_send_slot(
    *,
    preferred_provider: str | None = None,
    now: datetime | None = None,
) -> EmailSendReservation:
    """Atomically reserve one monthly and one provider-daily email slot.

    The preferred provider is tried first, then the other configured provider.
    This makes the registration path primary-first and lets resend paths start
    with the provider opposite to the previous send.
    """

    month = quota_month(now)
    day = _quota_day(now)
    monthly_limit = max(0, int(getattr(settings, "EMAIL_MONTHLY_SEND_LIMIT", 4000)))
    provider_order = _configured_provider_order(preferred_provider=preferred_provider)
    if not provider_order:
        raise EmailProviderUnavailable("no configured email provider")

    with transaction.atomic():
        monthly_quota, _created = EmailSendQuota.objects.select_for_update().get_or_create(month=month)
        if monthly_quota.sent_count >= monthly_limit:
            raise EmailQuotaExceeded(scope="monthly")

        for provider in provider_order:
            provider_limit = max(0, int(get_email_provider_config(provider).daily_limit))
            daily_quota, _created = EmailProviderDailyQuota.objects.select_for_update().get_or_create(
                provider=provider,
                day=day,
            )
            if daily_quota.sent_count >= provider_limit:
                continue

            monthly_quota.sent_count += 1
            monthly_quota.save(update_fields=("sent_count", "updated_at"))
            daily_quota.sent_count += 1
            daily_quota.save(update_fields=("sent_count", "updated_at"))
            return EmailSendReservation(month=month, day=day, provider=provider)

    raise EmailQuotaExceeded(scope="daily")


def release_email_send_slot(reservation: EmailSendReservation) -> None:
    """Release a reservation when delivery was never attempted."""

    with transaction.atomic():
        monthly_quota = EmailSendQuota.objects.select_for_update().filter(month=reservation.month).first()
        if monthly_quota is not None and monthly_quota.sent_count > 0:
            monthly_quota.sent_count -= 1
            monthly_quota.save(update_fields=("sent_count", "updated_at"))

        daily_quota = (
            EmailProviderDailyQuota.objects.select_for_update()
            .filter(
                provider=reservation.provider,
                day=reservation.day,
            )
            .first()
        )
        if daily_quota is not None and daily_quota.sent_count > 0:
            daily_quota.sent_count -= 1
            daily_quota.save(update_fields=("sent_count", "updated_at"))


def get_email_quota_status(*, now: datetime | None = None) -> EmailQuotaStatus:
    month = quota_month(now)
    day = _quota_day(now)
    monthly_limit = max(0, int(getattr(settings, "EMAIL_MONTHLY_SEND_LIMIT", 4000)))
    monthly_count = EmailSendQuota.objects.filter(month=month).values_list("sent_count", flat=True).first()
    monthly_exhausted = int(monthly_count or 0) >= monthly_limit

    configured_provider_count = 0
    available_provider = False
    for provider in EMAIL_PROVIDER_ORDER:
        config = get_email_provider_config(provider)
        if config.daily_limit <= 0 or not is_email_provider_configured(provider):
            continue
        configured_provider_count += 1
        daily_count = (
            EmailProviderDailyQuota.objects.filter(
                provider=provider,
                day=day,
            )
            .values_list("sent_count", flat=True)
            .first()
        )
        if int(daily_count or 0) < int(config.daily_limit):
            available_provider = True

    return EmailQuotaStatus(
        monthly_exhausted=monthly_exhausted,
        provider_configured=configured_provider_count > 0,
        daily_exhausted=configured_provider_count > 0 and not available_provider,
    )


def is_email_send_quota_exhausted(*, now: datetime | None = None) -> bool:
    return get_email_quota_status(now=now).exhausted
