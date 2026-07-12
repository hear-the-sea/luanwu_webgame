from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
from core.exceptions import ArenaCancellationError
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
def test_register_arena_coop_entry_persists_eight_hour_virtual_fill_deadline():
    user = User.objects.create_user(username="arena_coop_virtual_deadline", password="pass123")
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_coop_virtual_deadline_tpl")
    guests = [create_guest(manor, template, suffix) for suffix in ["A", "B", "C"]]
    before = timezone.now()

    result = register_arena_coop_entry(manor, [guest.id for guest in guests])

    after = timezone.now()
    assert result.event.virtual_fill_at is not None
    assert (
        before + timedelta(seconds=arena_coop_core.ARENA_COOP_VIRTUAL_FILL_WAIT_SECONDS) <= result.event.virtual_fill_at
    )
    assert result.event.virtual_fill_at <= after + timedelta(
        seconds=arena_coop_core.ARENA_COOP_VIRTUAL_FILL_WAIT_SECONDS
    )


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
def test_register_arena_coop_entry_can_reregister_after_cancel_in_same_event():
    user = User.objects.create_user(
        username="arena_coop_reregister",
        password="pass123",
        email="arena_coop_reregister@test.local",
    )
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_coop_reregister_tpl")
    guests = [create_guest(manor, template, suffix) for suffix in ["A", "B", "C", "D"]]

    first_result = register_arena_coop_entry(manor, [guest.id for guest in guests[:3]])
    first_entry_id = first_result.entry.id
    event_id = first_result.event.id

    canceled = cancel_arena_coop_entry(manor)
    second_result = register_arena_coop_entry(manor, [guest.id for guest in [guests[0], guests[1], guests[3]]])

    assert canceled == 1
    assert second_result.event.id == event_id
    assert second_result.entry.id == first_entry_id
    assert second_result.entry.status == ArenaCoopEntry.Status.REGISTERED
    assert list(second_result.entry.entry_guests.order_by("slot_index").values_list("guest_id", flat=True)) == [
        guests[0].id,
        guests[1].id,
        guests[3].id,
    ]
    assert not second_result.entry.entry_guests.filter(guest_id=guests[2].id).exists()
    for guest in [guests[0], guests[1], guests[3]]:
        guest.refresh_from_db(fields=["status"])
        assert guest.status == GuestStatus.ARENA
    guests[2].refresh_from_db(fields=["status"])
    assert guests[2].status == GuestStatus.IDLE


@pytest.mark.django_db
def test_cancel_arena_coop_entry_rejects_preparing_event():
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

    with pytest.raises(ArenaCancellationError, match="活动已开战，当前不可撤销报名"):
        cancel_arena_coop_entry(first_manor)

    event.refresh_from_db()
    assert event.status == ArenaCoopEvent.Status.PREPARING
    assert event.prepare_ends_at is not None
