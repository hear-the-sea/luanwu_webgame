"""监牢俘虏有效期与过期释放工具。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from gameplay.constants import PVPConstants
from gameplay.models import JailPrisoner
from guests.services.world_unique import release_world_unique_prisoners

JAIL_MAX_HOLD_DAYS = int(PVPConstants.JAIL_MAX_HOLD_DAYS)
JAIL_MAX_HOLD_DURATION = timedelta(days=JAIL_MAX_HOLD_DAYS)


def normalize_jail_time(value: datetime | None = None, *, field: str = "as_of") -> datetime:
    """Normalize a jail reference time to aware UTC."""
    resolved = timezone.now() if value is None else value
    if not isinstance(resolved, datetime) or timezone.is_naive(resolved):
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return resolved.astimezone(UTC)


def prisoner_expires_at(captured_at: datetime | None) -> datetime | None:
    """Return the exact expiry instant for a prisoner captured at ``captured_at``."""
    if captured_at is None or not isinstance(captured_at, datetime):
        return None
    return normalize_jail_time(captured_at, field="captured_at") + JAIL_MAX_HOLD_DURATION


def jail_expiration_cutoff(as_of: datetime | None = None) -> datetime:
    """Return the latest capture time that has reached the holding limit."""
    return normalize_jail_time(as_of) - JAIL_MAX_HOLD_DURATION


def is_prisoner_expired(prisoner: Any, *, as_of: datetime | None = None) -> bool:
    expires_at = prisoner_expires_at(getattr(prisoner, "captured_at", None))
    return expires_at is not None and expires_at <= normalize_jail_time(as_of)


@transaction.atomic
def release_expired_prisoner_if_needed(prisoner: JailPrisoner, *, as_of: datetime | None = None) -> bool:
    """Release one locked held prisoner if its 30-day holding period has ended."""
    if prisoner.status != JailPrisoner.Status.HELD or not is_prisoner_expired(prisoner, as_of=as_of):
        return False

    prisoner.status = JailPrisoner.Status.RELEASED
    prisoner.save(update_fields=["status"])
    release_world_unique_prisoners([prisoner.pk])
    return True


@transaction.atomic
def release_expired_prisoners_for_captor(captor: Any, *, as_of: datetime | None = None) -> int:
    """Release all currently-held expired prisoners for one captor."""
    cutoff = jail_expiration_cutoff(as_of)
    captor_id = getattr(captor, "pk", captor)
    prisoner_ids = list(
        JailPrisoner.objects.select_for_update()
        .filter(
            captor_id=captor_id,
            status=JailPrisoner.Status.HELD,
            captured_at__lte=cutoff,
        )
        .values_list("pk", flat=True)
    )
    released = JailPrisoner.objects.filter(pk__in=prisoner_ids, status=JailPrisoner.Status.HELD).update(
        status=JailPrisoner.Status.RELEASED
    )
    release_world_unique_prisoners(prisoner_ids)
    return int(released)
