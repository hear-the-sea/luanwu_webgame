from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from django.db.models import Count, F, Q
from django.utils import timezone

from core.exceptions import ArenaBusyError, ArenaEntryStateError
from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock
from gameplay.models import ArenaCoopEntry, ArenaCoopEntryGuest, ArenaCoopEvent, Manor, ResourceEvent
from guests.models import Guest, GuestStatus

from .snapshots import build_entry_guest_snapshot

logger = logging.getLogger(__name__)


def load_selected_guests_locked(locked_manor: Manor, selected_guest_ids: Iterable[int]) -> list[Guest]:
    requested_guest_ids = [int(guest_id) for guest_id in selected_guest_ids]
    all_selected_guests = list(
        Guest.objects.select_for_update()
        .filter(manor=locked_manor, id__in=requested_guest_ids)
        .select_related("template")
        .prefetch_related("skills")
        .order_by("id")
    )
    if len(all_selected_guests) != len(requested_guest_ids):
        raise ArenaEntryStateError("所选门客不存在或不属于当前庄园")

    non_idle_guests = [guest for guest in all_selected_guests if guest.status != GuestStatus.IDLE]
    if non_idle_guests:
        raise ArenaEntryStateError("仅空闲门客可报名围攻光明顶")

    selected_guest_order = {guest_id: index for index, guest_id in enumerate(requested_guest_ids)}
    return sorted(all_selected_guests, key=lambda guest: selected_guest_order[guest.id])


def deduct_registration_silver_locked(locked_manor: Manor, *, silver_cost: int) -> None:
    if silver_cost <= 0:
        return

    from gameplay.services.resources import spend_resources_locked

    spend_resources_locked(
        locked_manor,
        {"silver": silver_cost},
        note="竞技场共斗报名",
        reason=ResourceEvent.Reason.UPGRADE_COST,
    )


def sync_daily_counter_locked(
    locked_manor: Manor,
    *,
    today_local_date_fn: Callable[..., date],
    today_bounds_fn: Callable[..., tuple[datetime, datetime]],
    now: datetime | None = None,
) -> int:
    today = today_local_date_fn(now=now)
    if locked_manor.arena_coop_participation_date == today:
        return max(0, int(locked_manor.arena_coop_participations_today or 0))

    day_start, day_end = today_bounds_fn(now=now)
    today_count = (
        ArenaCoopEntry.objects.filter(
            manor=locked_manor,
            joined_at__gte=day_start,
            joined_at__lt=day_end,
        )
        .exclude(status=ArenaCoopEntry.Status.CANCELLED)
        .count()
    )
    locked_manor.arena_coop_participation_date = today
    locked_manor.arena_coop_participations_today = max(0, int(today_count))
    locked_manor.save(update_fields=["arena_coop_participation_date", "arena_coop_participations_today"])
    return locked_manor.arena_coop_participations_today


def update_daily_counter_locked(
    locked_manor: Manor,
    *,
    delta: int,
    today_local_date_fn: Callable[..., date],
    today_bounds_fn: Callable[..., tuple[datetime, datetime]],
    now: datetime | None = None,
) -> int:
    current = sync_daily_counter_locked(
        locked_manor,
        today_local_date_fn=today_local_date_fn,
        today_bounds_fn=today_bounds_fn,
        now=now,
    )
    updated = max(0, int(current) + int(delta))
    locked_manor.arena_coop_participation_date = today_local_date_fn(now=now)
    locked_manor.arena_coop_participations_today = updated
    locked_manor.save(update_fields=["arena_coop_participation_date", "arena_coop_participations_today"])
    return updated


def build_event_snapshots(base_rules: dict) -> tuple[dict, dict, dict]:
    return (
        copy.deepcopy(base_rules["enemy"]),
        {
            "rewards": copy.deepcopy(base_rules["rewards"]),
            "rare_drop": copy.deepcopy(base_rules["rare_drop"]),
        },
        {
            "registration": copy.deepcopy(base_rules["registration"]),
            "contribution": copy.deepcopy(base_rules["contribution"]),
        },
    )


def get_or_create_recruiting_event_locked(
    *,
    player_limit: int,
    guest_limit_per_entry: int,
    prepare_duration_seconds: int,
    base_rules: dict,
    recruiting_lock_key: str,
    recruiting_lock_timeout: int,
    resolve_boss_initial_hp_fn: Callable[[str], int],
    virtual_fill_wait_seconds: int,
) -> ArenaCoopEvent:
    event = (
        ArenaCoopEvent.objects.select_for_update()
        .filter(status=ArenaCoopEvent.Status.RECRUITING)
        .annotate(
            registered_entry_count=Count(
                "entries",
                filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED),
            )
        )
        .filter(registered_entry_count__lt=F("player_limit"))
        .order_by("created_at")
        .first()
    )
    if event:
        return event

    acquired, from_cache, lock_token = acquire_best_effort_lock(
        recruiting_lock_key,
        timeout_seconds=recruiting_lock_timeout,
        logger=logger,
        log_context="arena coop recruiting event lock",
        allow_local_fallback=False,
    )
    if not acquired:
        existing = (
            ArenaCoopEvent.objects.filter(status=ArenaCoopEvent.Status.RECRUITING)
            .annotate(
                registered_entry_count=Count(
                    "entries",
                    filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED),
                )
            )
            .filter(registered_entry_count__lt=F("player_limit"))
            .order_by("created_at")
            .first()
        )
        if existing:
            return existing
        raise ArenaBusyError()

    try:
        existing = (
            ArenaCoopEvent.objects.select_for_update()
            .filter(status=ArenaCoopEvent.Status.RECRUITING)
            .annotate(
                registered_entry_count=Count(
                    "entries",
                    filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED),
                )
            )
            .filter(registered_entry_count__lt=F("player_limit"))
            .order_by("created_at")
            .first()
        )
        if existing:
            return existing

        enemy_snapshot, reward_snapshot, daily_rule_snapshot = build_event_snapshots(base_rules)
        boss_template_key = str(base_rules["enemy"]["boss"]["template_key"])
        boss_initial_hp = resolve_boss_initial_hp_fn(boss_template_key)
        return ArenaCoopEvent.objects.create(
            status=ArenaCoopEvent.Status.RECRUITING,
            player_limit=player_limit,
            guest_limit_per_entry=guest_limit_per_entry,
            prepare_duration_seconds=prepare_duration_seconds,
            virtual_fill_at=timezone.now() + timedelta(seconds=max(1, int(virtual_fill_wait_seconds))),
            boss_name=str(base_rules["enemy"]["boss"]["display_name"]),
            boss_template_key=boss_template_key,
            boss_initial_hp=boss_initial_hp,
            boss_remaining_hp=boss_initial_hp,
            enemy_snapshot=enemy_snapshot,
            reward_snapshot=reward_snapshot,
            daily_rule_snapshot=daily_rule_snapshot,
        )
    finally:
        release_best_effort_lock(
            recruiting_lock_key,
            from_cache=from_cache,
            lock_token=lock_token,
            logger=logger,
            log_context="arena coop recruiting event lock",
        )


def move_event_to_preparing_locked(event: ArenaCoopEvent, *, now: datetime | None = None) -> bool:
    if event.status != ArenaCoopEvent.Status.RECRUITING:
        return False

    registered_count = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED).count()
    if registered_count < event.player_limit:
        return False

    current_time = now or timezone.now()
    from .virtual_reserve import reconcile_coop_demand_locked

    reconcile_coop_demand_locked(event, now=current_time)
    event.status = ArenaCoopEvent.Status.PREPARING
    event.virtual_fill_completed = True
    event.prepare_ends_at = current_time + timedelta(seconds=event.prepare_duration_seconds)
    event.save(update_fields=["status", "virtual_fill_completed", "prepare_ends_at", "updated_at"])
    return True


def upsert_entry_with_snapshots_locked(
    event: ArenaCoopEvent,
    locked_manor: Manor,
    selected_guests: list[Guest],
) -> ArenaCoopEntry:
    entry = (
        ArenaCoopEntry.objects.select_for_update()
        .filter(
            event=event,
            manor=locked_manor,
            status=ArenaCoopEntry.Status.CANCELLED,
        )
        .order_by("-joined_at", "-id")
        .first()
    )
    if entry is None:
        entry = ArenaCoopEntry.objects.create(event=event, manor=locked_manor)
    else:
        entry.status = ArenaCoopEntry.Status.REGISTERED
        entry.cancelled_at = None
        entry.joined_at = timezone.now()
        entry.save(update_fields=["status", "cancelled_at", "joined_at"])
        entry.entry_guests.all().delete()

    ArenaCoopEntryGuest.objects.bulk_create(
        [
            ArenaCoopEntryGuest(
                entry=entry,
                guest=guest,
                slot_index=index,
                snapshot=build_entry_guest_snapshot(guest),
            )
            for index, guest in enumerate(selected_guests)
        ]
    )
    for guest in selected_guests:
        guest.status = GuestStatus.ARENA
    Guest.objects.bulk_update(selected_guests, ["status"])
    return entry


def release_entry_guest_statuses(entry: ArenaCoopEntry) -> None:
    guest_ids = list(entry.entry_guests.values_list("guest_id", flat=True))
    if not guest_ids:
        return
    Guest.objects.filter(
        id__in=guest_ids,
        status__in=[GuestStatus.ARENA, GuestStatus.DEPLOYED],
    ).update(status=GuestStatus.IDLE)
