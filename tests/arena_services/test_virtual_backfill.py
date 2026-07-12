from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    BotProfile,
)
from gameplay.services.arena.coop_core import start_due_virtual_backfill_coop_events
from gameplay.services.arena.core import start_due_virtual_backfill_tournaments
from gameplay.services.arena.virtual_backfill import backfill_coop_event_locked, backfill_tournament_locked
from gameplay.services.manor.core import ensure_manor
from tests.arena_services.support import User


def _create_manor(username: str):
    return ensure_manor(User.objects.create_user(username=username, password="pass123"))


def _create_bot_profile(username: str, *, state: str = BotProfile.State.ACTIVE):
    manor = _create_manor(username)
    now = timezone.now()
    return BotProfile.objects.create(
        manor=manor,
        state=state,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=manor.id,
        next_growth_at=now,
        abandon_at=now,
        retire_at=now,
    )


def _add_real_arena_entry(tournament: ArenaTournament, username: str, *, attack: int, defense: int, max_hp: int):
    entry = ArenaEntry.objects.create(tournament=tournament, manor=_create_manor(username))
    ArenaEntryGuest.objects.create(
        entry=entry,
        snapshot={
            "display_name": f"{username}-门客",
            "attack": attack,
            "defense": defense,
            "max_hp": max_hp,
            "current_hp": max_hp,
        },
    )
    return entry


def _add_real_coop_entry(event: ArenaCoopEvent, username: str, *, status: str = ArenaCoopEntry.Status.REGISTERED):
    entry = ArenaCoopEntry.objects.create(event=event, manor=_create_manor(username), status=status)
    ArenaCoopEntryGuest.objects.create(
        entry=entry,
        slot_index=0,
        snapshot={"display_name": f"{username}-门客", "attack": 200, "defense": 200, "max_hp": 2000},
    )
    return entry


@pytest.mark.django_db(transaction=True)
def test_migration_0126_backfills_existing_arena_entry_sources_to_player():
    migrate_from = [("gameplay", "0125_worldchatsendattempt_publish_claim")]
    migrate_to = [("gameplay", "0126_virtual_arena_backfill")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Manor = old_apps.get_model("gameplay", "Manor")
        ArenaTournament = old_apps.get_model("gameplay", "ArenaTournament")
        ArenaEntry = old_apps.get_model("gameplay", "ArenaEntry")
        ArenaCoopEvent = old_apps.get_model("gameplay", "ArenaCoopEvent")
        ArenaCoopEntry = old_apps.get_model("gameplay", "ArenaCoopEntry")

        user = User.objects.create(
            username="arena_virtual_backfill",
            email="arena-virtual-backfill@test.local",
            password="unused",
        )
        manor = Manor.objects.create(user_id=user.pk)
        tournament = ArenaTournament.objects.create(status="running")
        arena_entry = ArenaEntry.objects.create(tournament_id=tournament.pk, manor_id=manor.pk)
        event = ArenaCoopEvent.objects.create(status="preparing")
        coop_entry = ArenaCoopEntry.objects.create(event_id=event.pk, manor_id=manor.pk)

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        ArenaEntry = new_apps.get_model("gameplay", "ArenaEntry")
        ArenaCoopEntry = new_apps.get_model("gameplay", "ArenaCoopEntry")

        assert ArenaEntry.objects.get(pk=arena_entry.pk).source == "player"
        assert ArenaCoopEntry.objects.get(pk=coop_entry.pk).source == "player"
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db
def test_arena_entry_defaults_to_player_and_persists_snapshot_only_virtual_guest():
    player = User.objects.create_user(username="arena_virtual_player", password="pass123")
    virtual_player = User.objects.create_user(username="arena_virtual_bot", password="pass123")
    virtual_fill_at = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        virtual_fill_at=virtual_fill_at,
        virtual_fill_completed=True,
    )

    player_entry = ArenaEntry.objects.create(tournament=tournament, manor=ensure_manor(player))
    virtual_entry = ArenaEntry.objects.create(
        tournament=tournament,
        manor=ensure_manor(virtual_player),
        source=ArenaEntry.Source.VIRTUAL,
    )
    virtual_guest = ArenaEntryGuest.objects.create(
        entry=virtual_entry,
        guest=None,
        snapshot={"display_name": "虚拟门客", "force": 120},
    )

    tournament.refresh_from_db()
    assert tournament.virtual_fill_at == virtual_fill_at
    assert tournament.virtual_fill_completed is True
    assert ArenaEntry.objects.get(pk=player_entry.pk).source == ArenaEntry.Source.PLAYER
    assert ArenaEntry.objects.get(pk=virtual_entry.pk).source == ArenaEntry.Source.VIRTUAL
    virtual_guest.refresh_from_db()
    assert virtual_guest.guest_id is None
    assert virtual_guest.snapshot["display_name"] == "虚拟门客"


@pytest.mark.django_db
def test_arena_coop_entry_defaults_to_player_and_persists_snapshot_only_virtual_guest():
    player = User.objects.create_user(username="arena_coop_virtual_player", password="pass123")
    virtual_player = User.objects.create_user(username="arena_coop_virtual_bot", password="pass123")
    virtual_fill_at = timezone.now()
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.PREPARING,
        virtual_fill_at=virtual_fill_at,
        virtual_fill_completed=True,
    )

    player_entry = ArenaCoopEntry.objects.create(event=event, manor=ensure_manor(player))
    virtual_entry = ArenaCoopEntry.objects.create(
        event=event,
        manor=ensure_manor(virtual_player),
        source=ArenaCoopEntry.Source.VIRTUAL,
    )
    virtual_guest = ArenaCoopEntryGuest.objects.create(
        entry=virtual_entry,
        guest=None,
        slot_index=0,
        snapshot={"display_name": "虚拟共斗门客", "force": 120},
    )

    event.refresh_from_db()
    assert event.virtual_fill_at == virtual_fill_at
    assert event.virtual_fill_completed is True
    assert ArenaCoopEntry.objects.get(pk=player_entry.pk).source == ArenaCoopEntry.Source.PLAYER
    assert ArenaCoopEntry.objects.get(pk=virtual_entry.pk).source == ArenaCoopEntry.Source.VIRTUAL
    virtual_guest.refresh_from_db()
    assert virtual_guest.guest_id is None
    assert virtual_guest.snapshot["display_name"] == "虚拟共斗门客"


@pytest.mark.django_db
def test_tournament_backfill_uses_median_snapshot_and_skips_bot_in_other_live_tournament():
    tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RECRUITING, player_limit=5)
    _add_real_arena_entry(tournament, "arena_backfill_low", attack=100, defense=100, max_hp=1000)
    _add_real_arena_entry(tournament, "arena_backfill_median", attack=200, defense=200, max_hp=2000)
    _add_real_arena_entry(tournament, "arena_backfill_high", attack=300, defense=300, max_hp=3000)

    reserved_profile = _create_bot_profile("arena_backfill_reserved")
    live_tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RUNNING, player_limit=2)
    ArenaEntry.objects.create(
        tournament=live_tournament,
        manor=reserved_profile.manor,
        source=ArenaEntry.Source.VIRTUAL,
    )
    stale_profile = _create_bot_profile("arena_backfill_stale", state=BotProfile.State.STALE)
    retired_profile = _create_bot_profile("arena_backfill_retired", state=BotProfile.State.RETIRED)
    first_available = _create_bot_profile("arena_backfill_available_one")
    second_available = _create_bot_profile("arena_backfill_available_two")

    created = backfill_tournament_locked(tournament)

    virtual_entries = list(tournament.entries.filter(source=ArenaEntry.Source.VIRTUAL).prefetch_related("entry_guests"))
    assert created == 2
    assert {entry.manor_id for entry in virtual_entries} == {first_available.manor_id, second_available.manor_id}
    assert reserved_profile.manor_id not in {entry.manor_id for entry in virtual_entries}
    assert stale_profile.manor_id not in {entry.manor_id for entry in virtual_entries}
    assert retired_profile.manor_id not in {entry.manor_id for entry in virtual_entries}
    for entry in virtual_entries:
        links = list(entry.entry_guests.all())
        assert len(links) == 1
        assert links[0].guest_id is None
        assert links[0].snapshot["attack"] == 200
        assert links[0].snapshot["defense"] == 200
        assert links[0].snapshot["max_hp"] == 2000


@pytest.mark.django_db
def test_coop_backfill_replaces_only_registered_entry_shortfall():
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=3,
        guest_limit_per_entry=1,
    )
    _add_real_coop_entry(event, "arena_coop_backfill_registered")
    _add_real_coop_entry(
        event,
        "arena_coop_backfill_cancelled",
        status=ArenaCoopEntry.Status.CANCELLED,
    )
    first_available = _create_bot_profile("arena_coop_backfill_available_one")
    second_available = _create_bot_profile("arena_coop_backfill_available_two")

    created = backfill_coop_event_locked(event)

    virtual_entries = list(event.entries.filter(source=ArenaCoopEntry.Source.VIRTUAL).prefetch_related("entry_guests"))
    assert created == 2
    assert {entry.manor_id for entry in virtual_entries} == {first_available.manor_id, second_available.manor_id}
    assert all(entry.entry_guests.get().guest_id is None for entry in virtual_entries)


@pytest.mark.django_db
def test_due_tournament_backfills_once_then_starts():
    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=3,
        virtual_fill_at=now,
    )
    _add_real_arena_entry(tournament, "arena_due_real", attack=200, defense=200, max_hp=2000)
    _create_bot_profile("arena_due_bot_one")
    _create_bot_profile("arena_due_bot_two")

    assert start_due_virtual_backfill_tournaments(now=now) == 1
    assert start_due_virtual_backfill_tournaments(now=now) == 0

    tournament.refresh_from_db()
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert tournament.virtual_fill_completed is True
    assert tournament.entries.filter(source=ArenaEntry.Source.VIRTUAL).count() == 2


@pytest.mark.django_db
def test_due_coop_event_backfills_then_keeps_existing_prepare_period():
    now = timezone.now()
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=3,
        guest_limit_per_entry=1,
        prepare_duration_seconds=120,
        virtual_fill_at=now,
    )
    _add_real_coop_entry(event, "arena_coop_due_real")
    _create_bot_profile("arena_coop_due_bot_one")
    _create_bot_profile("arena_coop_due_bot_two")

    assert start_due_virtual_backfill_coop_events(now=now) == 1
    assert start_due_virtual_backfill_coop_events(now=now) == 0

    event.refresh_from_db()
    assert event.status == ArenaCoopEvent.Status.PREPARING
    assert event.virtual_fill_completed is True
    assert event.prepare_ends_at == now + timedelta(seconds=120)
    assert event.entries.filter(source=ArenaCoopEntry.Source.VIRTUAL).count() == 2


@pytest.mark.django_db
def test_due_empty_tournament_does_not_start_without_a_real_entry():
    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=now,
    )
    _create_bot_profile("arena_due_empty_bot_one")
    _create_bot_profile("arena_due_empty_bot_two")

    assert start_due_virtual_backfill_tournaments(now=now) == 0

    tournament.refresh_from_db()
    assert tournament.status == ArenaTournament.Status.RECRUITING
    assert tournament.virtual_fill_completed is False


@pytest.mark.django_db
def test_tournament_backfill_waits_for_deadline():
    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=3,
        virtual_fill_at=now + timedelta(hours=5),
    )
    _add_real_arena_entry(tournament, "arena_wait_real", attack=200, defense=200, max_hp=2000)
    _create_bot_profile("arena_wait_bot_one")
    _create_bot_profile("arena_wait_bot_two")

    assert start_due_virtual_backfill_tournaments(now=now) == 0
    assert start_due_virtual_backfill_tournaments(now=now + timedelta(hours=5)) == 1

    tournament.refresh_from_db()
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert tournament.entries.filter(source=ArenaEntry.Source.VIRTUAL).count() == 2


@pytest.mark.django_db
def test_tournament_backfill_requires_all_missing_bots():
    tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RECRUITING, player_limit=3)
    _add_real_arena_entry(tournament, "arena_shortage_real", attack=200, defense=200, max_hp=2000)
    _create_bot_profile("arena_shortage_only_bot")

    assert backfill_tournament_locked(tournament) == 0
    assert tournament.entries.filter(source=ArenaEntry.Source.VIRTUAL).count() == 0


@pytest.mark.django_db
def test_tournament_backfill_emits_structured_strength_audit_log(caplog):
    tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RECRUITING, player_limit=2)
    _add_real_arena_entry(tournament, "arena_audit_real", attack=200, defense=200, max_hp=2000)
    _create_bot_profile("arena_audit_bot")
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_backfill")

    assert backfill_tournament_locked(tournament) == 1

    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_backfill_completed"
    )
    assert record.mode == "tournament"
    assert record.event_id == tournament.id
    assert record.real_entry_count == 1
    assert record.virtual_entry_count == 1
    assert record.target_team_power == 600


@pytest.mark.django_db
def test_tournament_backfill_logs_when_eligible_bots_are_insufficient(caplog):
    tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RECRUITING, player_limit=3)
    _add_real_arena_entry(tournament, "arena_audit_shortage_real", attack=200, defense=200, max_hp=2000)
    _create_bot_profile("arena_audit_shortage_bot")
    caplog.set_level(logging.WARNING, logger="gameplay.services.arena.virtual_backfill")

    assert backfill_tournament_locked(tournament) == 0

    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_backfill_deferred"
    )
    assert record.mode == "tournament"
    assert record.event_id == tournament.id
    assert record.reason == "insufficient_eligible_bots"
    assert record.needed_entry_count == 2
    assert record.available_bot_count == 1


@pytest.mark.django_db
def test_due_tournament_backfill_rolls_back_all_virtual_entries_when_snapshot_write_fails(monkeypatch):
    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=now,
    )
    _add_real_arena_entry(tournament, "arena_rollback_real", attack=200, defense=200, max_hp=2000)
    _create_bot_profile("arena_rollback_bot")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_backfill.ArenaEntryGuest.objects.bulk_create",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("snapshot write failed")),
    )

    with pytest.raises(RuntimeError, match="snapshot write failed"):
        start_due_virtual_backfill_tournaments(now=now)

    tournament.refresh_from_db()
    assert tournament.status == ArenaTournament.Status.RECRUITING
    assert tournament.virtual_fill_completed is False
    assert tournament.entries.filter(source=ArenaEntry.Source.VIRTUAL).count() == 0
