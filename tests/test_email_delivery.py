from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from accounts import email_delivery
from accounts.email_delivery import (
    create_email_verification_delivery,
    process_email_verification_delivery,
    schedule_due_email_verification_deliveries,
    schedule_email_verification_delivery_after_commit,
)
from accounts.email_providers import EMAIL_PROVIDER_BREVO, EMAIL_PROVIDER_RESEND
from accounts.email_quota import quota_month, reserve_email_send_slot
from accounts.email_verification import EmailVerificationDeliveryError, send_email_verification_message
from accounts.models import EmailVerificationDelivery

pytestmark = pytest.mark.django_db(transaction=True)


def _pending_delivery(django_user_model, *, username: str = "delivery-user"):
    user = django_user_model.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="StrongPass123!",
    )
    user.is_active = False
    user.email_verified = False
    user.save(update_fields=("is_active", "email_verified"))
    reservation = reserve_email_send_slot(preferred_provider=EMAIL_PROVIDER_RESEND)
    return create_email_verification_delivery(
        user=user,
        reservation=reservation,
        verification_url=f"https://example.test/accounts/verify-email/{username}/",
    )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_RESEND_DAILY_SEND_LIMIT=10,
    EMAIL_BREVO_DAILY_SEND_LIMIT=10,
    EMAIL_MONTHLY_SEND_LIMIT=10,
)
def test_transport_failure_fails_over_once_and_counts_both_attempts(django_user_model, monkeypatch):
    delivery = _pending_delivery(django_user_model, username="delivery-failover")
    providers: list[str] = []

    def _send(*, provider, **_kwargs):
        providers.append(provider)
        if len(providers) == 1:
            raise EmailVerificationDeliveryError(
                "primary transport failed",
                code="provider_transport",
                can_failover=True,
                handoff_unknown=False,
                retryable=True,
            )
        return 1

    monkeypatch.setattr(email_delivery, "send_email_verification_message", _send)

    assert process_email_verification_delivery(delivery.id) is True

    delivery.refresh_from_db()
    assert providers == [EMAIL_PROVIDER_RESEND, EMAIL_PROVIDER_BREVO]
    assert delivery.status == EmailVerificationDelivery.Status.SENT
    assert delivery.provider == EMAIL_PROVIDER_BREVO
    assert delivery.fallback_used is True
    assert delivery.attempts == 2


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_RESEND_DAILY_SEND_LIMIT=10,
    EMAIL_BREVO_DAILY_SEND_LIMIT=10,
    EMAIL_MONTHLY_SEND_LIMIT=10,
)
def test_unknown_smtp_result_is_not_automatically_retried(django_user_model, monkeypatch):
    delivery = _pending_delivery(django_user_model, username="delivery-unknown")
    providers: list[str] = []

    def _send(*, provider, **_kwargs):
        providers.append(provider)
        raise EmailVerificationDeliveryError(
            "server acknowledgement was ambiguous",
            code="provider_unknown",
            handoff_unknown=True,
        )

    monkeypatch.setattr(email_delivery, "send_email_verification_message", _send)

    assert process_email_verification_delivery(delivery.id) is False

    delivery.refresh_from_db()
    assert providers == [EMAIL_PROVIDER_RESEND]
    assert delivery.status == EmailVerificationDelivery.Status.UNKNOWN
    assert delivery.attempts == 1
    assert delivery.last_error_code == "provider_unknown"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_unexpected_email_backend_errors_are_not_reclassified_as_provider_failures(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(
        username="delivery-programming-error",
        email="delivery-programming-error@example.com",
        password="StrongPass123!",
    )

    def _raise_unexpected_error(**_kwargs):
        raise AssertionError("broken email backend contract")

    monkeypatch.setattr("accounts.email_verification.send_mail", _raise_unexpected_error)

    with pytest.raises(AssertionError, match="broken email backend contract"):
        send_email_verification_message(
            user=user,
            verification_url_value="https://example.test/verify/",
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_RESEND_DAILY_SEND_LIMIT=10,
    EMAIL_BREVO_DAILY_SEND_LIMIT=10,
    EMAIL_MONTHLY_SEND_LIMIT=10,
)
def test_delivery_scanner_only_dispatches_due_or_expired_rows(django_user_model, monkeypatch):
    pending = _pending_delivery(django_user_model, username="delivery-pending")
    due_retry = _pending_delivery(django_user_model, username="delivery-retry")
    due_retry.status = EmailVerificationDelivery.Status.RETRY
    due_retry.next_attempt_at = timezone.now() - timedelta(seconds=1)
    due_retry.save(update_fields=("status", "next_attempt_at", "updated_at"))
    future_retry = _pending_delivery(django_user_model, username="delivery-future")
    future_retry.status = EmailVerificationDelivery.Status.RETRY
    future_retry.next_attempt_at = timezone.now() + timedelta(minutes=5)
    future_retry.save(update_fields=("status", "next_attempt_at", "updated_at"))
    expired_processing = _pending_delivery(django_user_model, username="delivery-expired")
    expired_processing.status = EmailVerificationDelivery.Status.PROCESSING
    expired_processing.lease_until = timezone.now() - timedelta(seconds=1)
    expired_processing.save(update_fields=("status", "lease_until", "updated_at"))
    active_processing = _pending_delivery(django_user_model, username="delivery-active")
    active_processing.status = EmailVerificationDelivery.Status.PROCESSING
    active_processing.lease_until = timezone.now() + timedelta(minutes=2)
    active_processing.save(update_fields=("status", "lease_until", "updated_at"))

    dispatched: list[int] = []

    def _dispatch(_task, *, args, **_kwargs):
        dispatched.append(int(args[0]))
        return True

    monkeypatch.setattr(email_delivery, "safe_apply_async", _dispatch)

    assert schedule_due_email_verification_deliveries(limit=10) == 3
    assert set(dispatched) == {pending.id, due_retry.id, expired_processing.id}
    assert future_retry.id not in dispatched
    assert active_processing.id not in dispatched


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    EMAIL_RESEND_HOST="smtp.example.test",
    EMAIL_RESEND_HOST_USER="resend",
    EMAIL_RESEND_HOST_PASSWORD="secret",
)
def test_smtp_delivery_is_dispatched_after_commit(django_user_model, monkeypatch):
    delivery = _pending_delivery(django_user_model, username="delivery-after-commit")
    dispatched: list[int] = []

    def _dispatch(_task, *, args, **_kwargs):
        dispatched.append(int(args[0]))
        return True

    monkeypatch.setattr(email_delivery, "safe_apply_async", _dispatch)

    with transaction.atomic():
        schedule_email_verification_delivery_after_commit(delivery.id)
        assert dispatched == []

    assert dispatched == [delivery.id]
    delivery.refresh_from_db()
    assert delivery.status == EmailVerificationDelivery.Status.PENDING
    assert delivery.quota_month == quota_month()
