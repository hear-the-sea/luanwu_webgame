from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import EmailSendQuota


class EmailQuotaExceeded(RuntimeError):
    """Raised when the monthly registration-email budget is exhausted."""


@dataclass(frozen=True)
class EmailSendReservation:
    month: date


def quota_month(now: datetime | None = None) -> date:
    current_date = timezone.localdate(now) if now is not None else timezone.localdate()
    return current_date.replace(day=1)


def reserve_email_send_slot(*, now: datetime | None = None) -> EmailSendReservation:
    """Atomically reserve one monthly email slot before attempting delivery."""

    month = quota_month(now)
    limit = int(getattr(settings, "EMAIL_MONTHLY_SEND_LIMIT", 3000))
    with transaction.atomic():
        quota, _created = EmailSendQuota.objects.select_for_update().get_or_create(month=month)
        if quota.sent_count >= limit:
            raise EmailQuotaExceeded("monthly email send quota exhausted")
        quota.sent_count += 1
        quota.save(update_fields=("sent_count", "updated_at"))
    return EmailSendReservation(month=month)


def release_email_send_slot(reservation: EmailSendReservation) -> None:
    """Release a reservation when delivery was never attempted."""

    with transaction.atomic():
        quota = EmailSendQuota.objects.select_for_update().filter(month=reservation.month).first()
        if quota is None or quota.sent_count <= 0:
            return
        quota.sent_count -= 1
        quota.save(update_fields=("sent_count", "updated_at"))


def is_email_send_quota_exhausted(*, now: datetime | None = None) -> bool:
    month = quota_month(now)
    quota_count = EmailSendQuota.objects.filter(month=month).values_list("sent_count", flat=True).first()
    return int(quota_count or 0) >= int(getattr(settings, "EMAIL_MONTHLY_SEND_LIMIT", 3000))
