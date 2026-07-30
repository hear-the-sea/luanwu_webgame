from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

import gameplay.services.arena.core as arena_core
from core.exceptions import ArenaParticipationLimitError
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    ArenaVirtualDemand,
)
from gameplay.services.arena.coop_core import cleanup_expired_arena_coop_events
from gameplay.services.arena.core import cleanup_expired_tournaments, register_arena_entry
from gameplay.services.manor.core import ensure_manor
from tests.arena_services.support import User, create_guest, create_guest_template, fund_manor, snapshot_from_guest


@pytest.mark.django_db
def test_cleanup_expired_tournaments_removes_old_finished_data():
    user = User.objects.create_user(
        username="arena_cleanup_user",
        password="pass123",
        email="arena_cleanup_user@test.local",
    )
    manor = ensure_manor(user)
    template = create_guest_template("arena_cleanup_tpl")
    guest = create_guest(manor, template, "A")

    now = timezone.now()
    stale_tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.COMPLETED,
        player_limit=10,
        round_interval_seconds=600,
        ended_at=now - timedelta(minutes=11),
    )
    stale_entry = ArenaEntry.objects.create(
        tournament=stale_tournament,
        manor=manor,
        status=ArenaEntry.Status.ELIMINATED,
    )
    ArenaEntryGuest.objects.create(entry=stale_entry, guest=guest, snapshot=snapshot_from_guest(guest))
    stale_demand = ArenaVirtualDemand.objects.create(
        tournament=stale_tournament,
        status=ArenaVirtualDemand.Status.CLOSED,
    )

    fresh_tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.COMPLETED,
        player_limit=10,
        round_interval_seconds=600,
        ended_at=now - timedelta(minutes=5),
    )
    ArenaEntry.objects.create(
        tournament=fresh_tournament,
        manor=manor,
        status=ArenaEntry.Status.ELIMINATED,
    )

    cleaned = cleanup_expired_tournaments(now=now, grace_seconds=600, limit=20)
    assert cleaned == 1
    assert not ArenaTournament.objects.filter(id=stale_tournament.id).exists()
    assert not ArenaVirtualDemand.objects.filter(id=stale_demand.id).exists()
    assert ArenaTournament.objects.filter(id=fresh_tournament.id).exists()


@pytest.mark.django_db
def test_daily_participation_counter_not_reset_by_tournament_cleanup():
    user = User.objects.create_user(
        username="arena_counter_cleanup_user",
        password="pass123",
        email="arena_counter_cleanup_user@test.local",
    )
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_counter_cleanup_tpl")
    guest = create_guest(manor, template, "A")

    now = timezone.now()
    manor.arena_participation_date = timezone.localdate(now)
    manor.arena_participations_today = arena_core.ARENA_DAILY_PARTICIPATION_LIMIT
    manor.save(update_fields=["arena_participation_date", "arena_participations_today"])

    stale_tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.COMPLETED,
        player_limit=10,
        round_interval_seconds=600,
        ended_at=now - timedelta(minutes=11),
    )
    stale_entry = ArenaEntry.objects.create(
        tournament=stale_tournament,
        manor=manor,
        status=ArenaEntry.Status.ELIMINATED,
    )
    ArenaEntryGuest.objects.create(entry=stale_entry, guest=guest, snapshot=snapshot_from_guest(guest))

    cleaned = cleanup_expired_tournaments(now=now, grace_seconds=600, limit=20)
    assert cleaned == 1
    assert not ArenaEntry.objects.filter(id=stale_entry.id).exists()

    with pytest.raises(ArenaParticipationLimitError, match="每日最多参加"):
        register_arena_entry(manor, [guest.id])


@pytest.mark.django_db
def test_cleanup_expired_arena_coop_events_removes_old_finished_data():
    user = User.objects.create_user(
        username="arena_coop_cleanup_user",
        password="pass123",
        email="arena_coop_cleanup_user@test.local",
    )
    manor = ensure_manor(user)

    now = timezone.now()
    stale_event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.COMPLETED,
        player_limit=5,
        guest_limit_per_entry=3,
        ended_at=now - timedelta(days=2),
    )
    ArenaCoopEntry.objects.create(
        event=stale_event,
        manor=manor,
        status=ArenaCoopEntry.Status.COMPLETED,
    )
    stale_demand = ArenaVirtualDemand.objects.create(
        coop_event=stale_event,
        status=ArenaVirtualDemand.Status.CLOSED,
    )

    fresh_event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.COMPLETED,
        player_limit=5,
        guest_limit_per_entry=3,
        ended_at=now - timedelta(hours=12),
    )
    ArenaCoopEntry.objects.create(
        event=fresh_event,
        manor=manor,
        status=ArenaCoopEntry.Status.COMPLETED,
    )

    cleaned = cleanup_expired_arena_coop_events(now=now, grace_seconds=86400, limit=20)

    assert cleaned == 1
    assert not ArenaCoopEvent.objects.filter(id=stale_event.id).exists()
    assert not ArenaVirtualDemand.objects.filter(id=stale_demand.id).exists()
    assert ArenaCoopEvent.objects.filter(id=fresh_event.id).exists()
