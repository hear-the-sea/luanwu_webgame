from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.utils import timezone

import gameplay.services.arena.match_helpers as arena_match_helpers
from battle.models import BattleReport
from gameplay.models import ArenaEntry, ArenaEntryGuest, ArenaMatch, ArenaTournament
from gameplay.services.arena.core import register_arena_entry, run_due_arena_rounds, start_tournament_if_ready
from gameplay.services.arena.match_helpers import save_resolved_match
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from tests.arena_services.support import User, create_guest, create_guest_template, fund_manor, snapshot_from_guest


@pytest.mark.django_db
def test_arena_uses_guest_snapshot_not_live_guest_state():
    template = create_guest_template("arena_snapshot_tpl")
    user_a = User.objects.create_user(
        username="arena_snapshot_a",
        password="pass123",
        email="arena_snapshot_a@test.local",
    )
    user_b = User.objects.create_user(
        username="arena_snapshot_b",
        password="pass123",
        email="arena_snapshot_b@test.local",
    )
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)
    fund_manor(manor_a)
    fund_manor(manor_b)
    guest_a = create_guest(manor_a, template, "A")
    guest_b = create_guest(manor_b, template, "B")

    first = register_arena_entry(manor_a, [guest_a.id])
    tournament = first.tournament
    tournament.player_limit = 2
    tournament.save(update_fields=["player_limit"])

    register_arena_entry(manor_b, [guest_b.id])
    start_tournament_if_ready(tournament)

    guest_a.level = 99
    guest_a.force = 9999
    guest_a.save(update_fields=["level", "force"])

    now = timezone.now()
    tournament.refresh_from_db()
    tournament.next_round_at = now - timedelta(seconds=1)
    tournament.save(update_fields=["next_round_at"])
    run_due_arena_rounds(now=now, limit=10)

    match = ArenaMatch.objects.filter(tournament=tournament, battle_report__isnull=False).first()
    assert match is not None
    report = match.battle_report
    levels = [member.get("level") for member in (report.attacker_team + report.defender_team)]
    assert 99 not in levels
    guest_a.refresh_from_db(fields=["status"])
    guest_b.refresh_from_db(fields=["status"])
    assert guest_a.status == GuestStatus.IDLE
    assert guest_b.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_arena_no_fallback_when_registered_guest_missing():
    template = create_guest_template("arena_no_fallback_tpl")
    user_a = User.objects.create_user(
        username="arena_no_fallback_a",
        password="pass123",
        email="arena_no_fallback_a@test.local",
    )
    user_b = User.objects.create_user(
        username="arena_no_fallback_b",
        password="pass123",
        email="arena_no_fallback_b@test.local",
    )
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)

    guest_a = create_guest(manor_a, template, "A")
    backup_guest = create_guest(manor_a, template, "A2")
    guest_b = create_guest(manor_b, template, "B")
    backup_guest.level = 88
    backup_guest.save(update_fields=["level"])

    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=0,
        started_at=now,
        next_round_at=now - timedelta(seconds=1),
    )
    entry_a = ArenaEntry.objects.create(tournament=tournament, manor=manor_a)
    entry_b = ArenaEntry.objects.create(tournament=tournament, manor=manor_b)
    ArenaEntryGuest.objects.create(
        entry=entry_a,
        guest=guest_a,
        snapshot=snapshot_from_guest(guest_a),
    )
    ArenaEntryGuest.objects.create(
        entry=entry_b,
        guest=guest_b,
        snapshot=snapshot_from_guest(guest_b),
    )
    ArenaEntryGuest.objects.filter(entry=entry_a).delete()

    run_due_arena_rounds(now=now, limit=10)
    run_due_arena_rounds(now=now + timedelta(seconds=601), limit=10)
    match = ArenaMatch.objects.filter(tournament=tournament).first()
    assert match is not None
    assert match.status == ArenaMatch.Status.FORFEIT
    assert match.winner_entry_id == entry_b.id


@pytest.mark.django_db
def test_save_resolved_match_claims_scheduled_match_only_once():
    user_a = User.objects.create_user(
        username="arena_match_claim_a",
        password="pass123",
        email="arena_match_claim_a@test.local",
    )
    user_b = User.objects.create_user(
        username="arena_match_claim_b",
        password="pass123",
        email="arena_match_claim_b@test.local",
    )
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        current_round=1,
    )
    entry_a = ArenaEntry.objects.create(tournament=tournament, manor=manor_a)
    entry_b = ArenaEntry.objects.create(tournament=tournament, manor=manor_b)
    match = ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=entry_a,
        defender_entry=entry_b,
        status=ArenaMatch.Status.SCHEDULED,
    )

    first_claim = save_resolved_match(
        match=match,
        winner_entry=entry_a,
        status=ArenaMatch.Status.COMPLETED,
        now=timezone.now(),
        note="first",
    )
    second_claim = save_resolved_match(
        match=match,
        winner_entry=entry_b,
        status=ArenaMatch.Status.COMPLETED,
        now=timezone.now(),
        note="second",
    )

    match.refresh_from_db()
    assert first_claim is True
    assert second_claim is False
    assert match.winner_entry_id == entry_a.id
    assert match.notes == "first"


@pytest.mark.django_db(transaction=True)
def test_duplicate_arena_resolution_sends_one_battle_message(monkeypatch):
    user_a = User.objects.create_user(username="arena_duplicate_message_a", password="pass123")
    user_b = User.objects.create_user(username="arena_duplicate_message_b", password="pass123")
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)
    tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RUNNING, player_limit=2, current_round=1)
    entry_a = ArenaEntry.objects.create(tournament=tournament, manor=manor_a)
    entry_b = ArenaEntry.objects.create(tournament=tournament, manor=manor_b)
    snapshot = {
        "display_name": "快照门客",
        "template_key": "arena_duplicate_message_guest",
        "attack": 10,
        "defense": 10,
        "max_hp": 100,
    }
    ArenaEntryGuest.objects.create(entry=entry_a, snapshot=snapshot)
    ArenaEntryGuest.objects.create(entry=entry_b, snapshot=snapshot)
    match = ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=entry_a,
        defender_entry=entry_b,
        status=ArenaMatch.Status.SCHEDULED,
    )
    messages: list[dict] = []
    reports: list[BattleReport] = []

    def _simulate(**_kwargs):
        report = BattleReport.objects.create(
            manor=manor_a,
            opponent_name=manor_b.display_name,
            battle_type="arena",
            winner="attacker",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )
        reports.append(report)
        return report

    monkeypatch.setattr(arena_match_helpers, "simulate_report", _simulate)
    monkeypatch.setattr(arena_match_helpers, "send_arena_battle_messages", lambda **kwargs: messages.append(kwargs))

    for _ in range(2):
        arena_match_helpers.resolve_match_locked(
            tournament=tournament,
            round_number=1,
            match_index=0,
            attacker_entry=entry_a,
            defender_entry=entry_b,
            now=timezone.now(),
            max_guests_per_entry=10,
            arena_match_resolution_error=RuntimeError,
            match=match,
            logger=logging.getLogger("tests.arena_services.snapshot_matches"),
        )

    assert len(messages) == 1
    assert len(reports) == 1
