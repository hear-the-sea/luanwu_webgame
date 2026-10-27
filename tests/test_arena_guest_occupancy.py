from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    ArenaWheelEntry,
    ArenaWheelEntryGuest,
    ArenaWheelEvent,
    RaidRun,
)
from gameplay.services.arena import coop_lifecycle, core, wheel_core
from guests.models import GuestStatus
from tests.arena_services.support import create_guest, create_guest_template

pytestmark = pytest.mark.django_db


@pytest.fixture
def arena_guest(manor_factory):
    manor, _user = manor_factory(username="arena_guest_occupancy")
    guest = create_guest(manor, create_guest_template("arena_guest_occupancy"), "A")
    guest.status = GuestStatus.ARENA
    guest.save(update_fields=["status"])
    return guest


def _wheel_entry(guest, *, event_status, entry_status):
    event = ArenaWheelEvent.objects.create(status=event_status)
    entry = ArenaWheelEntry.objects.create(event=event, manor=guest.manor, status=entry_status)
    ArenaWheelEntryGuest.objects.create(entry=entry, guest=guest, slot_index=0)
    return entry


def _active_entry(guest, activity):
    if activity == "wheel":
        return _wheel_entry(
            guest,
            event_status=ArenaWheelEvent.Status.RUNNING,
            entry_status=ArenaWheelEntry.Status.ACTIVE,
        )
    if activity == "tournament":
        event = ArenaTournament.objects.create(status=ArenaTournament.Status.RUNNING)
        entry = ArenaEntry.objects.create(tournament=event, manor=guest.manor)
        ArenaEntryGuest.objects.create(entry=entry, guest=guest)
        return entry
    event = ArenaCoopEvent.objects.create(status=ArenaCoopEvent.Status.PREPARING)
    entry = ArenaCoopEntry.objects.create(event=event, manor=guest.manor)
    ArenaCoopEntryGuest.objects.create(entry=entry, guest=guest, slot_index=0)
    return entry


@pytest.mark.parametrize("event_status", ["recruiting", "preparing", "running"])
@pytest.mark.parametrize("entry_status", ["registered", "active"])
def test_orphan_cleanup_preserves_live_wheel_guests(arena_guest, event_status, entry_status):
    _wheel_entry(arena_guest, event_status=event_status, entry_status=entry_status)

    assert core._release_orphaned_arena_guests(arena_guest.manor) == 0

    arena_guest.refresh_from_db()
    assert arena_guest.status == GuestStatus.ARENA


@pytest.mark.parametrize("event_status", ["recruiting", "preparing", "running"])
def test_orphan_cleanup_preserves_live_coop_guests(arena_guest, event_status):
    event = ArenaCoopEvent.objects.create(status=event_status)
    entry = ArenaCoopEntry.objects.create(event=event, manor=arena_guest.manor)
    ArenaCoopEntryGuest.objects.create(entry=entry, guest=arena_guest, slot_index=0)

    assert core._release_orphaned_arena_guests(arena_guest.manor) == 0

    arena_guest.refresh_from_db()
    assert arena_guest.status == GuestStatus.ARENA


@pytest.mark.parametrize(
    ("event_status", "entry_status"),
    [("completed", "registered"), ("cancelled", "active"), ("running", "eliminated")],
)
def test_orphan_cleanup_releases_finished_wheel_guests(arena_guest, event_status, entry_status):
    _wheel_entry(arena_guest, event_status=event_status, entry_status=entry_status)

    assert core._release_orphaned_arena_guests(arena_guest.manor) == 1

    arena_guest.refresh_from_db()
    assert arena_guest.status == GuestStatus.IDLE


@pytest.mark.parametrize("activity", ["wheel", "tournament", "coop"])
@pytest.mark.parametrize("guest_status", [GuestStatus.ARENA, GuestStatus.DEPLOYED])
def test_old_wheel_release_preserves_guest_in_new_activity(arena_guest, activity, guest_status):
    old_entry = _wheel_entry(
        arena_guest,
        event_status=ArenaWheelEvent.Status.RUNNING,
        entry_status=ArenaWheelEntry.Status.ELIMINATED,
    )
    _active_entry(arena_guest, activity)
    arena_guest.status = guest_status
    arena_guest.save(update_fields=["status"])

    with transaction.atomic():
        wheel_core._release_entry_guest_statuses(old_entry)

    arena_guest.refresh_from_db()
    assert arena_guest.status == guest_status


def test_orphan_release_resumes_paused_training(arena_guest, monkeypatch, django_capture_on_commit_callbacks):
    arena_guest.training_remaining_seconds = 120
    arena_guest.save(update_fields=["training_remaining_seconds"])
    now = timezone.now()
    enqueued = []
    monkeypatch.setattr("guests.services.status.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda guest, **kwargs: enqueued.append((guest.pk, kwargs)),
    )

    with django_capture_on_commit_callbacks(execute=True):
        assert core._release_orphaned_arena_guests(arena_guest.manor) == 1

    arena_guest.refresh_from_db()
    assert arena_guest.status == GuestStatus.IDLE
    assert arena_guest.training_remaining_seconds is None
    assert arena_guest.training_complete_at == now + timedelta(seconds=120)
    assert enqueued == [(arena_guest.pk, {"countdown": 120, "source": "arena_orphan_release"})]


def test_old_coop_release_preserves_guest_in_new_wheel(arena_guest):
    old_event = ArenaCoopEvent.objects.create(status=ArenaCoopEvent.Status.COMPLETED)
    old_entry = ArenaCoopEntry.objects.create(
        event=old_event,
        manor=arena_guest.manor,
        status=ArenaCoopEntry.Status.COMPLETED,
    )
    ArenaCoopEntryGuest.objects.create(entry=old_entry, guest=arena_guest, slot_index=0)
    _active_entry(arena_guest, "wheel")

    with transaction.atomic():
        coop_lifecycle.release_entry_guest_statuses(old_entry)

    arena_guest.refresh_from_db()
    assert arena_guest.status == GuestStatus.ARENA


def test_old_tournament_settlement_preserves_guest_in_new_wheel(arena_guest, manor_factory):
    old_event = ArenaTournament.objects.create(status=ArenaTournament.Status.RUNNING)
    old_entry = ArenaEntry.objects.create(
        tournament=old_event,
        manor=arena_guest.manor,
        status=ArenaEntry.Status.ELIMINATED,
        eliminated_round=1,
    )
    ArenaEntryGuest.objects.create(entry=old_entry, guest=arena_guest)
    winner_manor, _user = manor_factory(username="arena_guest_occupancy_winner")
    winner_entry = ArenaEntry.objects.create(tournament=old_event, manor=winner_manor)
    _active_entry(arena_guest, "wheel")

    with transaction.atomic():
        core.finalize_tournament_locked(
            old_event,
            winner_entry=winner_entry,
            now=timezone.now(),
            calculate_ranked_entries=core._arena_helpers.calculate_ranked_entries,
            reward_for_rank=core._reward_for_rank,
            logger=core.logger,
        )

    old_event.refresh_from_db()
    assert old_event.status == ArenaTournament.Status.COMPLETED
    arena_guest.refresh_from_db()
    assert arena_guest.status == GuestStatus.ARENA


@pytest.mark.parametrize("old_activity", ["wheel", "tournament", "coop"])
def test_old_arena_settlement_preserves_guest_on_new_raid(arena_guest, manor_factory, old_activity):
    defender, _user = manor_factory(username="arena_guest_occupancy_raid_defender")
    raid = RaidRun.objects.create(
        attacker=arena_guest.manor,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        battle_at=timezone.now() + timedelta(minutes=10),
    )
    raid.guests.add(arena_guest)
    arena_guest.status = GuestStatus.DEPLOYED
    arena_guest.save(update_fields=["status"])

    with transaction.atomic():
        if old_activity == "wheel":
            old_entry = _wheel_entry(
                arena_guest,
                event_status=ArenaWheelEvent.Status.RUNNING,
                entry_status=ArenaWheelEntry.Status.ELIMINATED,
            )
            wheel_core._settle_event_locked(event=old_entry.event, entries=[old_entry], now=timezone.now())
        elif old_activity == "coop":
            event = ArenaCoopEvent.objects.create(status=ArenaCoopEvent.Status.COMPLETED)
            entry = ArenaCoopEntry.objects.create(
                event=event, manor=arena_guest.manor, status=ArenaCoopEntry.Status.COMPLETED
            )
            ArenaCoopEntryGuest.objects.create(entry=entry, guest=arena_guest, slot_index=0)
            coop_lifecycle.release_entry_guest_statuses(entry)
        else:
            event = ArenaTournament.objects.create(status=ArenaTournament.Status.RUNNING)
            old_entry = ArenaEntry.objects.create(
                tournament=event,
                manor=arena_guest.manor,
                status=ArenaEntry.Status.ELIMINATED,
                eliminated_round=1,
            )
            ArenaEntryGuest.objects.create(entry=old_entry, guest=arena_guest)
            winner = ArenaEntry.objects.create(tournament=event, manor=defender)
            core.finalize_tournament_locked(
                event,
                winner_entry=winner,
                now=timezone.now(),
                calculate_ranked_entries=core._arena_helpers.calculate_ranked_entries,
                reward_for_rank=core._reward_for_rank,
                logger=core.logger,
            )

    arena_guest.refresh_from_db()
    raid.refresh_from_db()
    assert raid.status == RaidRun.Status.MARCHING
    assert arena_guest.status == GuestStatus.DEPLOYED
