from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Least

from guests.models import Guest, GuestStatus

MAX_GUEST_LOYALTY = 100
VIRTUAL_PLAYER_GUEST_LOYALTY = 60
INJURY_LOYALTY_DECAY_INTERVAL = timedelta(hours=3)


def is_virtual_player_guest(guest: Any) -> bool:
    """Return whether a guest belongs to a system-controlled virtual Manor."""

    marker = getattr(guest, "_virtual_player_guest", None)
    if marker is not None:
        return bool(marker)
    manor_id = getattr(guest, "manor_id", None)
    if manor_id is None:
        manor = getattr(guest, "manor", None)
        manor_id = getattr(manor, "pk", None) or getattr(manor, "id", None)
    if manor_id is None:
        return False
    # Keep the gameplay-model dependency lazy: the guests service is imported
    # by gameplay bootstrap and must not create an import cycle at module load.
    from gameplay.models import BotProfile

    return BotProfile.objects.filter(manor_id=int(manor_id)).exists()


def _normalize_positive_int(raw: Any, *, field_name: str) -> int:
    if raw is None or isinstance(raw, bool):
        raise AssertionError(f"invalid guest loyalty {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid guest loyalty {field_name}: {raw!r}") from exc
    if value <= 0:
        raise AssertionError(f"invalid guest loyalty {field_name}: {raw!r}")
    return value


def extract_guest_ids(guests: Iterable[Any]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for guest in guests:
        guest_id = getattr(guest, "pk", None) or getattr(guest, "id", None)
        if guest_id is None:
            continue
        parsed_id = _normalize_positive_int(guest_id, field_name="guest id")
        if parsed_id in seen:
            continue
        seen.add(parsed_id)
        normalized.append(parsed_id)
    return normalized


def increase_guest_loyalty_by_ids(guest_ids: Iterable[int], *, amount: int = 1) -> int:
    normalized_amount = _normalize_positive_int(amount, field_name="amount")

    normalized_ids: list[int] = []
    seen: set[int] = set()
    for guest_id in guest_ids:
        parsed_id = _normalize_positive_int(guest_id, field_name="guest id")
        if parsed_id in seen:
            continue
        seen.add(parsed_id)
        normalized_ids.append(parsed_id)

    if not normalized_ids:
        return 0

    return Guest.objects.filter(id__in=normalized_ids, manor__bot_profile__isnull=True).update(
        loyalty=Least(MAX_GUEST_LOYALTY, F("loyalty") + normalized_amount)
    )


def grant_battle_victory_loyalty(guests: Iterable[Any], *, amount: int = 1) -> int:
    return increase_guest_loyalty_by_ids(extract_guest_ids(guests), amount=amount)


def start_injury_loyalty_decay(guest: Any, *, now: datetime) -> None:
    guest.injury_loyalty_processed_at = now


def clear_injury_loyalty_decay(guest: Any) -> None:
    guest.injury_loyalty_processed_at = None


def apply_injury_loyalty_decay(guest: Any, *, now: datetime) -> int:
    """按完整重伤周期扣减忠诚度，并推进持久化结算点。"""
    processed_at = getattr(guest, "injury_loyalty_processed_at", None)
    if getattr(guest, "status", None) != GuestStatus.INJURED or processed_at is None:
        return 0
    if is_virtual_player_guest(guest):
        # Virtual-player loyalty is a stable gameplay parameter, not an
        # economy outcome.  The daily normalizer repairs legacy drift; this
        # guard prevents injury recovery from changing it again.  Keep this
        # check after the cheap state guards because passive HP recovery also
        # visits healthy real-player guests.
        return 0

    elapsed = now - processed_at
    intervals = int(elapsed // INJURY_LOYALTY_DECAY_INTERVAL)
    if intervals <= 0:
        return 0

    guest.loyalty = max(0, int(guest.loyalty) - intervals)
    guest.injury_loyalty_processed_at = processed_at + INJURY_LOYALTY_DECAY_INTERVAL * intervals
    return intervals


@transaction.atomic
def process_injury_loyalty_decay_for_guest(guest_id: int, *, now: datetime) -> int:
    guest = Guest.objects.select_for_update().filter(pk=guest_id).first()
    if guest is None:
        return 0

    intervals = apply_injury_loyalty_decay(guest, now=now)
    if intervals > 0:
        guest.save(update_fields=["loyalty", "injury_loyalty_processed_at"])
    return intervals
