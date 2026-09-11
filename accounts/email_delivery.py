"""Durable email-verification outbox and provider failover orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS

from .email_providers import alternate_email_provider
from .email_quota import (
    EmailSendReservation,
    reserve_additional_provider_daily_slot,
    reserve_existing_delivery_provider_slot,
)
from .email_verification import EmailVerificationDeliveryError, send_email_verification_message
from .models import EmailVerificationDelivery, User

logger = logging.getLogger(__name__)

EMAIL_DELIVERY_LEASE_SECONDS = 120
EMAIL_DELIVERY_MAX_ATTEMPTS = 4
EMAIL_DELIVERY_RETRY_BASE_SECONDS = 60
EMAIL_DELIVERY_SCAN_BATCH_SIZE = 100


@dataclass(frozen=True)
class _DeliverySnapshot:
    delivery_id: int
    user_id: int
    email: str
    provider: str
    verification_url: str
    fallback_used: bool
    attempts: int


def create_email_verification_delivery(
    *,
    user: User,
    reservation: EmailSendReservation,
    verification_url: str,
) -> EmailVerificationDelivery:
    """Persist one logical verification email inside the caller's transaction."""

    return EmailVerificationDelivery.objects.create(
        user=user,
        provider=reservation.provider,
        verification_url=verification_url,
        quota_month=reservation.month,
        quota_day=reservation.day,
    )


def schedule_email_verification_delivery_after_commit(delivery_id: int) -> None:
    """Dispatch the outbox after commit without making SMTP part of the request."""

    def _dispatch() -> None:
        try:
            if getattr(settings, "EMAIL_BACKEND", "") != "django.core.mail.backends.smtp.EmailBackend":
                # LocMem/console backends are deterministic, local transports. Running
                # them inline keeps hermetic tests and local development observable while
                # real SMTP always stays outside the web request.
                process_email_verification_delivery(delivery_id)
                return

            from .tasks import send_email_verification_delivery_task

            safe_apply_async(
                send_email_verification_delivery_task,
                args=[int(delivery_id)],
                logger=logger,
                log_message="email verification outbox dispatch failed",
                log_extra={"delivery_id": int(delivery_id), "degraded": True},
            )
        except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
            logger.warning(
                "Email verification outbox remains pending after dispatch infrastructure failure: delivery_id=%s",
                delivery_id,
                exc_info=True,
            )
        except Exception:
            # The row is the durable source of truth. A scheduler scan can recover
            # any callback or broker failure without turning a successful signup into
            # a 500 response.
            logger.exception(
                "Unexpected email verification outbox dispatch failure: delivery_id=%s",
                delivery_id,
            )

    transaction.on_commit(_dispatch)


def _next_local_day_start(now: datetime) -> datetime:
    local_now = timezone.localtime(now)
    return (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _retry_at(now: datetime, attempts: int, *, next_day: bool = False) -> datetime:
    if next_day:
        return _next_local_day_start(now)
    delay = EMAIL_DELIVERY_RETRY_BASE_SECONDS * (2 ** max(0, min(attempts - 1, 3)))
    return now + timedelta(seconds=delay)


def _claim_delivery(delivery_id: int, *, now: datetime) -> _DeliverySnapshot | None:
    with transaction.atomic():
        delivery = (
            EmailVerificationDelivery.objects.select_for_update().select_related("user").filter(pk=delivery_id).first()
        )
        if delivery is None:
            return None
        if delivery.status in {
            EmailVerificationDelivery.Status.SENT,
            EmailVerificationDelivery.Status.UNKNOWN,
            EmailVerificationDelivery.Status.FAILED,
        }:
            return None
        if delivery.status == EmailVerificationDelivery.Status.PROCESSING and delivery.lease_until:
            if delivery.lease_until > now:
                return None
        if delivery.next_attempt_at and delivery.next_attempt_at > now:
            return None

        today = timezone.localdate(now)
        provider = delivery.provider
        fallback_used = delivery.fallback_used
        if delivery.quota_day != today:
            next_provider = reserve_existing_delivery_provider_slot(
                preferred_provider=delivery.provider,
                day=today,
            )
            if next_provider is None:
                delivery.status = EmailVerificationDelivery.Status.RETRY
                delivery.next_attempt_at = _next_local_day_start(now)
                delivery.lease_until = None
                delivery.last_error_code = "daily_quota"
                delivery.last_error = "all configured provider daily quotas are exhausted"
                delivery.save(
                    update_fields=(
                        "status",
                        "next_attempt_at",
                        "lease_until",
                        "last_error_code",
                        "last_error",
                        "updated_at",
                    )
                )
                return None
            provider = next_provider
            delivery.provider = provider
            delivery.quota_day = today
            fallback_used = False
            delivery.fallback_used = False

        delivery.status = EmailVerificationDelivery.Status.PROCESSING
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.lease_until = now + timedelta(seconds=EMAIL_DELIVERY_LEASE_SECONDS)
        delivery.next_attempt_at = None
        delivery.save(
            update_fields=(
                "provider",
                "quota_day",
                "fallback_used",
                "status",
                "attempts",
                "last_attempt_at",
                "lease_until",
                "next_attempt_at",
                "updated_at",
            )
        )
        return _DeliverySnapshot(
            delivery_id=delivery.id,
            user_id=delivery.user_id,
            email=str(delivery.user.email or ""),
            provider=provider,
            verification_url=delivery.verification_url,
            fallback_used=fallback_used,
            attempts=delivery.attempts,
        )


def _switch_to_fallback_provider(
    delivery_id: int,
    *,
    current_provider: str,
    now: datetime,
) -> tuple[str, int] | None:
    fallback_provider = alternate_email_provider(current_provider)
    with transaction.atomic():
        delivery = EmailVerificationDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if delivery is None or delivery.status != EmailVerificationDelivery.Status.PROCESSING:
            return None
        if delivery.fallback_used:
            return None
        if delivery.quota_day != timezone.localdate(now):
            return None
        if not reserve_additional_provider_daily_slot(provider=fallback_provider, day=delivery.quota_day):
            return None
        delivery.provider = fallback_provider
        delivery.fallback_used = True
        # The fallback is a second provider attempt for the same logical
        # delivery. Keep the durable attempt count aligned with the snapshot
        # used by retry/failure decisions.
        delivery.attempts += 1
        delivery.save(update_fields=("provider", "fallback_used", "attempts", "updated_at"))
        return fallback_provider, delivery.attempts


def _mark_sent(snapshot: _DeliverySnapshot, *, now: datetime) -> None:
    with transaction.atomic():
        updated = EmailVerificationDelivery.objects.filter(
            pk=snapshot.delivery_id,
            status=EmailVerificationDelivery.Status.PROCESSING,
        ).update(
            status=EmailVerificationDelivery.Status.SENT,
            sent_at=now,
            lease_until=None,
            next_attempt_at=None,
            last_error_code="",
            last_error="",
            updated_at=now,
        )
        if updated:
            User.objects.filter(pk=snapshot.user_id).update(email_verification_last_provider=snapshot.provider)


def _mark_delivery_error(
    snapshot: _DeliverySnapshot,
    error: EmailVerificationDeliveryError,
    *,
    now: datetime,
) -> None:
    if error.handoff_unknown:
        status = EmailVerificationDelivery.Status.UNKNOWN
        next_attempt_at = None
    elif error.code == "provider_configuration":
        status = EmailVerificationDelivery.Status.FAILED
        next_attempt_at = None
    elif error.retryable and snapshot.attempts < EMAIL_DELIVERY_MAX_ATTEMPTS:
        status = EmailVerificationDelivery.Status.RETRY
        next_attempt_at = _retry_at(now, snapshot.attempts)
    elif snapshot.attempts < EMAIL_DELIVERY_MAX_ATTEMPTS and error.can_failover:
        status = EmailVerificationDelivery.Status.RETRY
        next_attempt_at = _retry_at(now, snapshot.attempts, next_day=True)
    else:
        status = EmailVerificationDelivery.Status.FAILED
        next_attempt_at = None

    error_text = f"{type(error).__name__}: {error}"
    EmailVerificationDelivery.objects.filter(
        pk=snapshot.delivery_id,
        status=EmailVerificationDelivery.Status.PROCESSING,
    ).update(
        status=status,
        next_attempt_at=next_attempt_at,
        lease_until=None,
        last_error_code=error.code,
        last_error=error_text[:4000],
        updated_at=now,
    )


def process_email_verification_delivery(delivery_id: int) -> bool:
    """Deliver one outbox row, with one safe provider failover opportunity."""

    now = timezone.now()
    snapshot = _claim_delivery(int(delivery_id), now=now)
    if snapshot is None:
        return False

    while True:
        user = User.objects.filter(pk=snapshot.user_id).first()
        if user is None or user.email_verified or user.is_active:
            # A verified/activated account no longer needs this message. Marking it
            # failed avoids endless retries while retaining the audit record.
            EmailVerificationDelivery.objects.filter(
                pk=snapshot.delivery_id,
                status=EmailVerificationDelivery.Status.PROCESSING,
            ).update(
                status=EmailVerificationDelivery.Status.FAILED,
                lease_until=None,
                last_error_code="user_no_longer_pending",
                last_error="user is already active or email-verified",
                updated_at=timezone.now(),
            )
            return False

        try:
            send_email_verification_message(
                user=user,
                verification_url_value=snapshot.verification_url,
                provider=snapshot.provider,
            )
        except EmailVerificationDeliveryError as exc:
            if exc.can_failover and not snapshot.fallback_used:
                fallback_result = _switch_to_fallback_provider(
                    snapshot.delivery_id,
                    current_provider=snapshot.provider,
                    now=timezone.now(),
                )
                if fallback_result:
                    fallback_provider, fallback_attempts = fallback_result
                    snapshot = _DeliverySnapshot(
                        delivery_id=snapshot.delivery_id,
                        user_id=snapshot.user_id,
                        email=snapshot.email,
                        provider=fallback_provider,
                        verification_url=snapshot.verification_url,
                        fallback_used=True,
                        attempts=fallback_attempts,
                    )
                    continue
            _mark_delivery_error(snapshot, exc, now=timezone.now())
            logger.warning(
                "Email verification delivery did not complete: delivery_id=%s provider=%s code=%s",
                snapshot.delivery_id,
                snapshot.provider,
                exc.code,
                exc_info=True,
            )
            return False

        _mark_sent(snapshot, now=timezone.now())
        logger.info(
            "Email verification delivery completed: delivery_id=%s provider=%s attempts=%s",
            snapshot.delivery_id,
            snapshot.provider,
            snapshot.attempts,
        )
        return True


def schedule_due_email_verification_deliveries(*, limit: int = EMAIL_DELIVERY_SCAN_BATCH_SIZE) -> int:
    """Enqueue due and lease-expired outbox rows for recovery."""

    now = timezone.now()
    normalized_limit = max(1, min(int(limit), 1000))
    delivery_ids = list(
        EmailVerificationDelivery.objects.filter(
            Q(status=EmailVerificationDelivery.Status.PENDING, next_attempt_at__isnull=True)
            | Q(status=EmailVerificationDelivery.Status.RETRY, next_attempt_at__lte=now)
            | Q(status=EmailVerificationDelivery.Status.PROCESSING, lease_until__lte=now)
        )
        .order_by("created_at")
        .values_list("id", flat=True)[:normalized_limit]
    )
    if not delivery_ids:
        return 0

    from .tasks import send_email_verification_delivery_task

    dispatched = 0
    for delivery_id in delivery_ids:
        if safe_apply_async(
            send_email_verification_delivery_task,
            args=[int(delivery_id)],
            logger=logger,
            log_message="email verification recovery dispatch failed",
            log_extra={"delivery_id": int(delivery_id), "degraded": True},
        ):
            dispatched += 1
    return dispatched


__all__ = [
    "EMAIL_DELIVERY_LEASE_SECONDS",
    "EMAIL_DELIVERY_MAX_ATTEMPTS",
    "EMAIL_DELIVERY_RETRY_BASE_SECONDS",
    "EMAIL_DELIVERY_SCAN_BATCH_SIZE",
    "create_email_verification_delivery",
    "process_email_verification_delivery",
    "schedule_due_email_verification_deliveries",
    "schedule_email_verification_delivery_after_commit",
]
