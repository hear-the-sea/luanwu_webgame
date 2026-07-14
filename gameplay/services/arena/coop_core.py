from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.exceptions import ArenaCancellationError, ArenaEntryStateError, ArenaParticipationLimitError
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, Manor
from gameplay.services.utils.messages import create_message

from . import helpers as _arena_helpers
from .coop_battle import resolve_boss_initial_hp
from .coop_battle import run_coop_battle_locked as _run_coop_battle_locked
from .coop_lifecycle import (
    deduct_registration_silver_locked,
    get_or_create_recruiting_event_locked,
    load_selected_guests_locked,
    move_event_to_preparing_locked,
    release_entry_guest_statuses,
    sync_daily_counter_locked,
    update_daily_counter_locked,
    upsert_entry_with_snapshots_locked,
)
from .coop_rules import load_arena_coop_rules
from .coop_settlement import settle_coop_event_locked
from .virtual_backfill import backfill_coop_event_locked

logger = logging.getLogger(__name__)

_load_positive_int_setting = _arena_helpers.load_positive_int_setting
_normalize_guest_ids = _arena_helpers.normalize_guest_ids
_today_bounds = _arena_helpers.today_bounds
_today_local_date = _arena_helpers.today_local_date


ARENA_COOP_RULES = load_arena_coop_rules()
ARENA_COOP_PLAYER_LIMIT = _load_positive_int_setting(
    "ARENA_COOP_PLAYER_LIMIT",
    ARENA_COOP_RULES["registration"]["player_limit"],
    minimum=2,
)
ARENA_COOP_MAX_GUESTS_PER_ENTRY = _load_positive_int_setting(
    "ARENA_COOP_MAX_GUESTS_PER_ENTRY",
    ARENA_COOP_RULES["registration"]["guest_limit_per_entry"],
    minimum=1,
)
ARENA_COOP_DAILY_PARTICIPATION_LIMIT = _load_positive_int_setting(
    "ARENA_COOP_DAILY_PARTICIPATION_LIMIT",
    ARENA_COOP_RULES["registration"]["daily_participation_limit"],
    minimum=1,
)
ARENA_COOP_PREPARE_DURATION_SECONDS = _load_positive_int_setting(
    "ARENA_COOP_PREPARE_DURATION_SECONDS",
    ARENA_COOP_RULES["registration"]["prepare_duration_seconds"],
    minimum=1,
)
ARENA_COOP_VIRTUAL_FILL_WAIT_SECONDS = int(ARENA_COOP_RULES["runtime"]["virtual_fill_wait_seconds"])
ARENA_COOP_COMPLETED_RETENTION_SECONDS = int(ARENA_COOP_RULES["runtime"]["completed_retention_seconds"])
ARENA_COOP_MINIMUM_SHARE_BPS = int(ARENA_COOP_RULES["contribution"]["minimum_share_bps"])
ARENA_COOP_REGISTRATION_SILVER_COST = int(ARENA_COOP_RULES["registration"]["registration_silver_cost"])
ARENA_COOP_RECRUITING_LOCK_KEY = str(ARENA_COOP_RULES["registration"]["recruiting_lock_key"])
ARENA_COOP_RECRUITING_LOCK_TIMEOUT = _load_positive_int_setting(
    "ARENA_COOP_RECRUITING_LOCK_TIMEOUT",
    ARENA_COOP_RULES["registration"]["recruiting_lock_timeout"],
    minimum=1,
)


def refresh_arena_coop_constants() -> None:
    global ARENA_COOP_RULES
    global ARENA_COOP_PLAYER_LIMIT
    global ARENA_COOP_MAX_GUESTS_PER_ENTRY
    global ARENA_COOP_DAILY_PARTICIPATION_LIMIT
    global ARENA_COOP_PREPARE_DURATION_SECONDS
    global ARENA_COOP_VIRTUAL_FILL_WAIT_SECONDS
    global ARENA_COOP_COMPLETED_RETENTION_SECONDS
    global ARENA_COOP_MINIMUM_SHARE_BPS
    global ARENA_COOP_REGISTRATION_SILVER_COST
    global ARENA_COOP_RECRUITING_LOCK_KEY
    global ARENA_COOP_RECRUITING_LOCK_TIMEOUT

    ARENA_COOP_RULES = load_arena_coop_rules()
    ARENA_COOP_PLAYER_LIMIT = _load_positive_int_setting(
        "ARENA_COOP_PLAYER_LIMIT",
        ARENA_COOP_RULES["registration"]["player_limit"],
        minimum=2,
    )
    ARENA_COOP_MAX_GUESTS_PER_ENTRY = _load_positive_int_setting(
        "ARENA_COOP_MAX_GUESTS_PER_ENTRY",
        ARENA_COOP_RULES["registration"]["guest_limit_per_entry"],
        minimum=1,
    )
    ARENA_COOP_DAILY_PARTICIPATION_LIMIT = _load_positive_int_setting(
        "ARENA_COOP_DAILY_PARTICIPATION_LIMIT",
        ARENA_COOP_RULES["registration"]["daily_participation_limit"],
        minimum=1,
    )
    ARENA_COOP_PREPARE_DURATION_SECONDS = _load_positive_int_setting(
        "ARENA_COOP_PREPARE_DURATION_SECONDS",
        ARENA_COOP_RULES["registration"]["prepare_duration_seconds"],
        minimum=1,
    )
    ARENA_COOP_VIRTUAL_FILL_WAIT_SECONDS = int(ARENA_COOP_RULES["runtime"]["virtual_fill_wait_seconds"])
    ARENA_COOP_COMPLETED_RETENTION_SECONDS = int(ARENA_COOP_RULES["runtime"]["completed_retention_seconds"])
    ARENA_COOP_MINIMUM_SHARE_BPS = int(ARENA_COOP_RULES["contribution"]["minimum_share_bps"])
    ARENA_COOP_REGISTRATION_SILVER_COST = int(ARENA_COOP_RULES["registration"]["registration_silver_cost"])
    ARENA_COOP_RECRUITING_LOCK_KEY = str(ARENA_COOP_RULES["registration"]["recruiting_lock_key"])
    ARENA_COOP_RECRUITING_LOCK_TIMEOUT = _load_positive_int_setting(
        "ARENA_COOP_RECRUITING_LOCK_TIMEOUT",
        ARENA_COOP_RULES["registration"]["recruiting_lock_timeout"],
        minimum=1,
    )


@dataclass(frozen=True)
class ArenaCoopRegistrationResult:
    entry: ArenaCoopEntry
    event: ArenaCoopEvent
    moved_to_preparing: bool
    entry_count: int


@transaction.atomic
def register_arena_coop_entry(manor: Manor, guest_ids: Iterable[int]) -> ArenaCoopRegistrationResult:
    selected_guest_ids = _normalize_guest_ids(guest_ids, max_guests_per_entry=ARENA_COOP_MAX_GUESTS_PER_ENTRY)
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)

    if (
        sync_daily_counter_locked(
            locked_manor,
            today_local_date_fn=_today_local_date,
            today_bounds_fn=_today_bounds,
        )
        >= ARENA_COOP_DAILY_PARTICIPATION_LIMIT
    ):
        raise ArenaParticipationLimitError(
            ARENA_COOP_DAILY_PARTICIPATION_LIMIT,
            message=f"每日最多参加 {ARENA_COOP_DAILY_PARTICIPATION_LIMIT} 次围攻光明顶",
        )

    if ArenaCoopEntry.objects.filter(
        manor=locked_manor,
        status=ArenaCoopEntry.Status.REGISTERED,
        event__status__in=[
            ArenaCoopEvent.Status.RECRUITING,
            ArenaCoopEvent.Status.PREPARING,
            ArenaCoopEvent.Status.RUNNING,
        ],
    ).exists():
        raise ArenaEntryStateError("您已有进行中的围攻光明顶报名，请等待本场结束")

    selected_guests = load_selected_guests_locked(locked_manor, selected_guest_ids)
    deduct_registration_silver_locked(locked_manor, silver_cost=ARENA_COOP_REGISTRATION_SILVER_COST)
    event = get_or_create_recruiting_event_locked(
        player_limit=ARENA_COOP_PLAYER_LIMIT,
        guest_limit_per_entry=ARENA_COOP_MAX_GUESTS_PER_ENTRY,
        prepare_duration_seconds=ARENA_COOP_PREPARE_DURATION_SECONDS,
        base_rules=ARENA_COOP_RULES,
        recruiting_lock_key=ARENA_COOP_RECRUITING_LOCK_KEY,
        recruiting_lock_timeout=ARENA_COOP_RECRUITING_LOCK_TIMEOUT,
        resolve_boss_initial_hp_fn=resolve_boss_initial_hp,
        virtual_fill_wait_seconds=ARENA_COOP_VIRTUAL_FILL_WAIT_SECONDS,
    )
    entry = upsert_entry_with_snapshots_locked(event, locked_manor, selected_guests)
    entry_count = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED).count()
    moved_to_preparing = False
    if entry_count >= event.player_limit:
        moved_to_preparing = move_event_to_preparing_locked(event)
    update_daily_counter_locked(
        locked_manor,
        delta=1,
        today_local_date_fn=_today_local_date,
        today_bounds_fn=_today_bounds,
    )
    return ArenaCoopRegistrationResult(
        entry=entry,
        event=event,
        moved_to_preparing=moved_to_preparing,
        entry_count=entry_count,
    )


def _lock_coop_cancellation_context(manor_id: int) -> tuple[Manor, ArenaCoopEntry, ArenaCoopEvent]:
    locked_manor = Manor.objects.select_for_update().get(pk=manor_id)
    identity = (
        ArenaCoopEntry.objects.filter(
            manor=locked_manor,
            status=ArenaCoopEntry.Status.REGISTERED,
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ],
        )
        .order_by("-joined_at", "-id")
        .values_list("id", "event_id")
        .first()
    )
    if identity is None:
        raise ArenaCancellationError("当前没有可撤销的共斗报名")

    entry_id, event_id = identity
    event = ArenaCoopEvent.objects.select_for_update().get(pk=event_id)
    entry = (
        ArenaCoopEntry.objects.select_for_update()
        .filter(
            pk=entry_id,
            manor=locked_manor,
            status=ArenaCoopEntry.Status.REGISTERED,
        )
        .first()
    )
    if entry is None:
        raise ArenaCancellationError("当前没有可撤销的共斗报名")
    return locked_manor, entry, event


@transaction.atomic
def cancel_arena_coop_entry(manor: Manor) -> int:
    locked_manor, entry, event = _lock_coop_cancellation_context(manor.pk)
    if event.status != ArenaCoopEvent.Status.RECRUITING:
        raise ArenaCancellationError("活动已开战，当前不可撤销报名")

    entry.status = ArenaCoopEntry.Status.CANCELLED
    entry.cancelled_at = timezone.now()
    entry.save(update_fields=["status", "cancelled_at"])
    release_entry_guest_statuses(entry)
    update_daily_counter_locked(
        locked_manor,
        delta=-1,
        today_local_date_fn=_today_local_date,
        today_bounds_fn=_today_bounds,
    )
    return 1


def start_due_virtual_backfill_coop_events(*, now: datetime | None = None, limit: int = 20) -> int:
    now = now or timezone.now()
    event_ids = list(
        ArenaCoopEvent.objects.filter(
            status=ArenaCoopEvent.Status.RECRUITING,
            virtual_fill_completed=False,
            virtual_fill_at__lte=now,
        ).values_list("id", flat=True)[: max(1, int(limit))]
    )
    prepared = 0
    for event_id in event_ids:
        with transaction.atomic():
            event = ArenaCoopEvent.objects.select_for_update().filter(pk=event_id).first()
            if not event or event.status != ArenaCoopEvent.Status.RECRUITING:
                continue
            if event.virtual_fill_completed or not event.virtual_fill_at or event.virtual_fill_at > now:
                continue
            if backfill_coop_event_locked(event) <= 0:
                continue
            event.virtual_fill_completed = True
            event.save(update_fields=["virtual_fill_completed", "updated_at"])
            prepared += int(move_event_to_preparing_locked(event, now=now))
    return prepared


def run_due_arena_coop_events(*, now: datetime | None = None, limit: int = 20) -> int:
    current_time = now or timezone.now()
    candidate_ids = list(
        ArenaCoopEvent.objects.filter(
            status=ArenaCoopEvent.Status.PREPARING,
            prepare_ends_at__lte=current_time,
        )
        .order_by("prepare_ends_at", "id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    processed = 0

    for event_id in candidate_ids:
        with transaction.atomic():
            manor_ids = list(
                ArenaCoopEntry.objects.filter(
                    event_id=event_id,
                    status=ArenaCoopEntry.Status.REGISTERED,
                )
                .order_by("manor_id")
                .values_list("manor_id", flat=True)
            )
            list(Manor.objects.select_for_update().filter(pk__in=manor_ids).order_by("pk"))
            locked_event = ArenaCoopEvent.objects.select_for_update().filter(pk=event_id).first()
            if not locked_event:
                continue
            if locked_event.status != ArenaCoopEvent.Status.PREPARING:
                continue
            if not locked_event.prepare_ends_at or locked_event.prepare_ends_at > current_time:
                continue

            registered_entries = list(
                locked_event.entries.select_for_update()
                .filter(status=ArenaCoopEntry.Status.REGISTERED)
                .select_related("manor")
                .order_by("joined_at", "id")
            )
            if len(registered_entries) < locked_event.player_limit:
                locked_event.status = ArenaCoopEvent.Status.RECRUITING
                locked_event.prepare_ends_at = None
                locked_event.save(update_fields=["status", "prepare_ends_at", "updated_at"])
                continue

            locked_event.status = ArenaCoopEvent.Status.RUNNING
            locked_event.started_at = current_time
            locked_event.save(update_fields=["status", "started_at", "updated_at"])

            report = _run_coop_battle_locked(locked_event, current_time)
            settle_coop_event_locked(
                locked_event,
                report,
                registered_entries,
                current_time,
                base_rules=ARENA_COOP_RULES,
                logger=logger,
                create_message_fn=create_message,
            )
            processed += 1

    return processed


def cleanup_expired_arena_coop_events(*, now: datetime, grace_seconds: int, limit: int = 50) -> int:
    retention_seconds = max(0, int(grace_seconds))
    cutoff_time = now - timedelta(seconds=retention_seconds)
    stale_ids = list(
        ArenaCoopEvent.objects.filter(
            status__in=[ArenaCoopEvent.Status.COMPLETED, ArenaCoopEvent.Status.CANCELLED],
            ended_at__isnull=False,
            ended_at__lte=cutoff_time,
        )
        .order_by("ended_at", "id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    if not stale_ids:
        return 0

    ArenaCoopEvent.objects.filter(id__in=stale_ids).delete()
    return len(stale_ids)
