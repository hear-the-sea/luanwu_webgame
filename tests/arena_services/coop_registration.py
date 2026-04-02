from __future__ import annotations

import pytest

from gameplay.models import ArenaCoopEntry, ArenaCoopEvent
from gameplay.services.arena.coop_core import cancel_arena_coop_entry, register_arena_coop_entry
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from tests.arena_services.support import User, create_guest, create_guest_template, fund_manor


@pytest.mark.django_db
def test_register_arena_coop_entry_creates_recruiting_event_and_snapshots():
    user = User.objects.create_user(username="arena_coop_reg", password="pass123", email="arena_coop_reg@test.local")
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_coop_reg_tpl")
    guests = [create_guest(manor, template, suffix) for suffix in ["A", "B", "C"]]

    result = register_arena_coop_entry(manor, [guest.id for guest in guests])

    assert result.entry is not None
    assert result.event.status == ArenaCoopEvent.Status.RECRUITING
    assert result.entry.entry_guests.count() == 3
    assert result.entry_count == 1
    assert result.event.boss_initial_hp == 300000
    assert result.event.boss_remaining_hp == 300000
    assert result.event.daily_rule_snapshot["contribution"]["minimum_share_bps"] > 0


@pytest.mark.django_db
def test_register_arena_coop_entry_fifth_player_moves_event_to_preparing():
    template = create_guest_template("arena_coop_fill_tpl")

    event_id = None
    for idx in range(5):
        user = User.objects.create_user(
            username=f"arena_coop_fill_{idx}",
            password="pass123",
            email=f"arena_coop_fill_{idx}@test.local",
        )
        manor = ensure_manor(user)
        fund_manor(manor)
        guests = [create_guest(manor, template, f"{idx}_{slot}") for slot in ["A", "B", "C"]]
        result = register_arena_coop_entry(manor, [guest.id for guest in guests])
        event_id = result.event.id if event_id is None else event_id
        assert result.event.id == event_id

    event = ArenaCoopEvent.objects.get(pk=event_id)
    assert event.status == ArenaCoopEvent.Status.PREPARING
    assert event.prepare_ends_at is not None


@pytest.mark.django_db
def test_cancel_arena_coop_entry_refunds_daily_counter_before_running():
    user = User.objects.create_user(
        username="arena_coop_cancel",
        password="pass123",
        email="arena_coop_cancel@test.local",
    )
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_coop_cancel_tpl")
    guests = [create_guest(manor, template, suffix) for suffix in ["A", "B", "C"]]

    register_arena_coop_entry(manor, [guest.id for guest in guests])
    manor.refresh_from_db(fields=["arena_coop_participations_today"])
    assert manor.arena_coop_participations_today == 1

    canceled = cancel_arena_coop_entry(manor)

    assert canceled == 1
    manor.refresh_from_db(fields=["arena_coop_participations_today"])
    assert manor.arena_coop_participations_today == 0
    assert not ArenaCoopEntry.objects.filter(
        manor=manor,
        event__status__in=[ArenaCoopEvent.Status.RECRUITING, ArenaCoopEvent.Status.PREPARING],
        status=ArenaCoopEntry.Status.REGISTERED,
    ).exists()
    for guest in guests:
        guest.refresh_from_db(fields=["status"])
        assert guest.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_cancel_arena_coop_entry_downgrades_preparing_event_back_to_recruiting():
    template = create_guest_template("arena_coop_cancel_prepare_tpl")
    manors = []

    for idx in range(5):
        user = User.objects.create_user(
            username=f"arena_coop_cancel_prepare_{idx}",
            password="pass123",
            email=f"arena_coop_cancel_prepare_{idx}@test.local",
        )
        manor = ensure_manor(user)
        fund_manor(manor)
        guests = [create_guest(manor, template, f"{idx}_{slot}") for slot in ["A", "B", "C"]]
        register_arena_coop_entry(manor, [guest.id for guest in guests])
        manors.append(manor)

    first_manor = manors[0]
    event = ArenaCoopEvent.objects.get()
    assert event.status == ArenaCoopEvent.Status.PREPARING

    canceled = cancel_arena_coop_entry(first_manor)

    assert canceled == 1
    event.refresh_from_db()
    assert event.status == ArenaCoopEvent.Status.RECRUITING
    assert event.prepare_ends_at is None
