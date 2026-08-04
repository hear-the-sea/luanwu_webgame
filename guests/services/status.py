"""门客状态切换与训练计时联动。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from ..models import Guest, GuestStatus

GUEST_STATUS_UPDATE_FIELDS = [
    "status",
    "training_complete_at",
    "training_remaining_seconds",
]


@dataclass(frozen=True, slots=True)
class GuestStatusTransition:
    changed: bool
    resumed_training: bool


def _remaining_seconds(training_complete_at: datetime, *, now: datetime) -> int:
    return max(0, math.ceil((training_complete_at - now).total_seconds()))


def pause_guest_training(guest: Guest, *, now: datetime | None = None) -> bool:
    """暂停门客训练并保存剩余秒数；调用方应在事务内持有门客锁。"""
    if not guest.training_complete_at:
        return False

    current_time = now or timezone.now()
    guest.training_remaining_seconds = _remaining_seconds(guest.training_complete_at, now=current_time)
    guest.training_complete_at = None
    return True


def resume_guest_training(guest: Guest, *, now: datetime | None = None) -> bool:
    """恢复已暂停的训练；调用方应在事务内持有门客锁。"""
    if guest.training_remaining_seconds is None or guest.training_complete_at:
        return False

    current_time = now or timezone.now()
    remaining = max(0, int(guest.training_remaining_seconds))
    guest.training_complete_at = current_time + timedelta(seconds=remaining)
    guest.training_remaining_seconds = None
    return True


def prepare_guest_status_transition(
    guest: Guest,
    new_status: str,
    *,
    now: datetime | None = None,
) -> GuestStatusTransition:
    """在内存中准备一次状态切换，并同步训练暂停/恢复字段。"""
    before = (
        guest.status,
        guest.training_complete_at,
        guest.training_remaining_seconds,
    )
    resumed_training = False

    if new_status == GuestStatus.IDLE:
        resumed_training = resume_guest_training(guest, now=now)
    else:
        pause_guest_training(guest, now=now)

    guest.status = new_status
    after = (
        guest.status,
        guest.training_complete_at,
        guest.training_remaining_seconds,
    )
    return GuestStatusTransition(
        changed=before != after,
        resumed_training=resumed_training,
    )


def schedule_resumed_guest_training(guest: Guest, *, source: str) -> None:
    """在状态恢复提交后重新投递训练结算任务。"""
    if not guest.training_complete_at:
        return

    countdown = _remaining_seconds(guest.training_complete_at, now=timezone.now())

    def enqueue_training() -> None:
        from .training import _try_enqueue_complete_guest_training

        _try_enqueue_complete_guest_training(
            guest,
            countdown=countdown,
            source=source,
        )

    transaction.on_commit(enqueue_training)


def schedule_resumed_guest_trainings(guests: Iterable[Guest], *, source: str) -> None:
    for guest in guests:
        schedule_resumed_guest_training(guest, source=source)


def persist_guest_status_transition(
    guest: Guest,
    new_status: str,
    *,
    now: datetime | None = None,
    source: str = "guest_status_transition",
) -> GuestStatusTransition:
    """持久化单个门客状态切换；调用方应在 transaction.atomic() 内持有该 Guest 行锁。"""
    transition = prepare_guest_status_transition(guest, new_status, now=now)
    if not transition.changed:
        return transition

    guest.save(update_fields=GUEST_STATUS_UPDATE_FIELDS)
    if transition.resumed_training:
        schedule_resumed_guest_training(guest, source=source)
    return transition


def persist_guest_status_transitions(
    guests: Iterable[Guest],
    new_status: str,
    *,
    now: datetime | None = None,
    source: str = "guest_status_transition",
) -> int:
    """批量持久化同一目标状态的门客，并恢复其训练任务。

    调用方必须在 transaction.atomic() 内持有所有 Guest 行锁；bulk_update()
    不会自行加锁或检测锁，不能依赖本函数代替并发控制。
    """
    changed_guests: list[Guest] = []
    resumed_guests: list[Guest] = []
    for guest in guests:
        transition = prepare_guest_status_transition(guest, new_status, now=now)
        if transition.changed:
            changed_guests.append(guest)
        if transition.resumed_training:
            resumed_guests.append(guest)

    if not changed_guests:
        return 0

    Guest.objects.bulk_update(changed_guests, GUEST_STATUS_UPDATE_FIELDS)
    schedule_resumed_guest_trainings(resumed_guests, source=source)
    return len(changed_guests)
