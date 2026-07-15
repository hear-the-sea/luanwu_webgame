from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable

from django.utils import timezone

from core.config import GUEST
from guests.models import Guest


def ensure_training_timer(
    guest: Guest,
    *,
    now: datetime | None = None,
    finalize_guest_training_func: Callable[..., bool],
    ensure_auto_training_func: Callable[[Guest], bool],
) -> bool:
    """
    Ensure the guest has an active training timer.

    Returns True when a pending timer exists after refresh, False otherwise.
    """
    now = now or timezone.now()
    finalize_guest_training_func(guest, now=now)
    if guest.level >= int(GUEST.MAX_LEVEL):
        return False
    if not guest.training_complete_at:
        ensure_auto_training_func(guest)
        guest.refresh_from_db()
    return bool(guest.training_complete_at)


def remaining_training_seconds(guest: Guest, now: datetime | None = None) -> int:
    if not guest.training_complete_at:
        return 0
    now = now or timezone.now()
    remaining = (guest.training_complete_at - now).total_seconds()
    if remaining <= 0:
        return 0
    return int(math.ceil(remaining))


def refresh_guest_state(
    guest: Guest,
    *,
    now: datetime | None = None,
    auto_train: bool = True,
    apply_sets: bool = True,
    recover_hp: bool = True,
    refresh: bool = False,
    finalize_guest_training_func: Callable[..., bool],
    ensure_auto_training_func: Callable[[Guest], bool],
    recover_guest_hp_func: Callable[..., None],
    apply_set_bonuses_func: Callable[[Guest], Any],
) -> Guest:
    now = now or timezone.now()
    finalize_guest_training_func(guest, now=now)
    if auto_train and guest.level < int(GUEST.MAX_LEVEL) and not guest.training_complete_at:
        ensure_auto_training_func(guest)
    if recover_hp:
        recover_guest_hp_func(guest, now=now)
    if apply_sets:
        apply_set_bonuses_func(guest)
    if refresh:
        guest.refresh_from_db()
    return guest
