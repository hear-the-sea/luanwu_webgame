from __future__ import annotations

from celery import shared_task

from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS

from .email_delivery import process_email_verification_delivery, schedule_due_email_verification_deliveries


@shared_task(
    name="accounts.send_email_verification_delivery",
    autoretry_for=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def send_email_verification_delivery_task(delivery_id: int) -> bool:
    """Send one durable email-verification outbox row."""

    return process_email_verification_delivery(int(delivery_id))


@shared_task(
    name="accounts.scan_email_verification_deliveries",
    autoretry_for=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def scan_email_verification_deliveries_task(limit: int = 100) -> int:
    """Recover outbox rows whose broker dispatch or worker lease was interrupted."""

    return schedule_due_email_verification_deliveries(limit=int(limit))
