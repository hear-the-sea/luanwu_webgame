"""门客状态切换与训练计时联动。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from ..models import Guest, GuestStatus

GUEST_STATUS_UPDATE_FIELDS = [
    "status",
    "training_complete_at",
    "training_remaining_seconds",
]

GUEST_TRAINING_STATE_NONE = "none"
GUEST_TRAINING_STATE_ACTIVE = "active"
GUEST_TRAINING_STATE_PAUSED = "paused"


def resolve_guest_training_state(guest: Guest) -> str:
    """Read the persisted training queue state without changing the guest."""

    if guest.training_complete_at is not None:
        return GUEST_TRAINING_STATE_ACTIVE
    if guest.training_remaining_seconds is not None:
        return GUEST_TRAINING_STATE_PAUSED
    return GUEST_TRAINING_STATE_NONE


def resolve_guest_activity_status(guest: Guest) -> str:
    """Return a report-facing status consistent with the training queue.

    ``Guest.status`` remains the availability state used by gameplay rules;
    an active or paused training timer is exposed as ``training`` at the
    reporting boundary so consumers do not mistake a queued guest for idle.
    """

    if resolve_guest_training_state(guest) != GUEST_TRAINING_STATE_NONE:
        return "training"
    return str(guest.status)


@dataclass(frozen=True, slots=True)
class GuestStatusTransition:
    changed: bool
    resumed_training: bool


def status_after_injury_recovery(guest: Guest) -> str:
    """重伤治愈后，仍在出征队伍中的门客继续占用至归队。"""
    if not guest.pk:
        return GuestStatus.IDLE
    from gameplay.models import MissionRun, RaidRun, SeasonExpedition

    raids = RaidRun.objects.filter(
        attacker_id=OuterRef("manor_id"),
        guests__pk=OuterRef("pk"),
        status__in=(
            RaidRun.Status.MARCHING,
            RaidRun.Status.BATTLING,
            RaidRun.Status.RETURNING,
            RaidRun.Status.RETREATED,
        ),
    )
    missions = MissionRun.objects.filter(
        manor_id=OuterRef("manor_id"), status=MissionRun.Status.ACTIVE, guests__pk=OuterRef("pk")
    )
    expeditions = SeasonExpedition.objects.exclude(status="completed").filter(
        member__manor_id=OuterRef("manor_id"), guests__pk=OuterRef("pk")
    )
    occupied = (
        Guest.objects.filter(pk=guest.pk)
        .alias(_active_raid=Exists(raids), _active_mission=Exists(missions), _active_season=Exists(expeditions))
        .filter(Q(_active_raid=True) | Q(_active_mission=True) | Q(_active_season=True))
        .exists()
    )
    return GuestStatus.DEPLOYED if occupied else GuestStatus.IDLE


def active_raid_guest_ids(guest_ids: Iterable[int], *, exclude_run_id: int | None = None) -> set[int]:
    ids = list(guest_ids)
    if not ids:
        return set()
    from gameplay.models import RaidRun

    runs = RaidRun.objects.filter(
        status__in=(
            RaidRun.Status.MARCHING,
            RaidRun.Status.BATTLING,
            RaidRun.Status.RETURNING,
            RaidRun.Status.RETREATED,
        ),
        guests__pk__in=ids,
    )
    if exclude_run_id is not None:
        runs = runs.exclude(pk=exclude_run_id)
    return set(runs.values_list("guests__pk", flat=True))


def guests_available_after_raid(guests: Iterable[Guest], *, run_id: int) -> list[Guest]:
    """旧队伍归队时，不释放仍被其他任务或队伍占用的门客。"""
    deployed = [guest for guest in guests if guest.status == GuestStatus.DEPLOYED]
    if not deployed:
        return []
    from battle.deployment import collect_active_deployment_guest_ids

    occupied = collect_active_deployment_guest_ids([guest.pk for guest in deployed], exclude_raid_run_id=run_id)
    return [guest for guest in deployed if guest.pk not in occupied]


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
