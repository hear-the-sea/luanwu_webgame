from __future__ import annotations

import logging

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction
from django.utils import timezone

import gameplay.services.arena.lifecycle_helpers as arena_lifecycle_helpers
import gameplay.services.arena.match_helpers as arena_match_helpers
from core.exceptions import BattlePreparationError, MessageError
from gameplay.models import ArenaEntry, ArenaTournament
from gameplay.services.manor.core import ensure_manor

User = get_user_model()


def _create_arena_manor(username: str):
    user = User.objects.create_user(username=username, password="pass123", email=f"{username}@test.local")
    return ensure_manor(user)


def test_send_arena_battle_messages_programming_error_bubbles_up(monkeypatch):
    attacker_entry = type("_Entry", (), {"id": 1, "manor": type("_Manor", (), {"display_name": "甲"})()})()
    defender_entry = type("_Entry", (), {"id": 2, "manor": type("_Manor", (), {"display_name": "乙"})()})()
    report = type("_Report", (), {"id": 9})()

    monkeypatch.setattr(
        arena_match_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("broken arena battle message contract")),
    )

    with pytest.raises(AssertionError, match="broken arena battle message contract"):
        arena_match_helpers.send_arena_battle_messages(
            report=report,
            round_number=1,
            attacker_entry=attacker_entry,
            defender_entry=defender_entry,
            winner_entry=attacker_entry,
            logger=logging.getLogger("tests.arena.match_helpers"),
        )


def test_send_arena_battle_messages_keeps_best_effort_on_explicit_message_failure(monkeypatch):
    attacker_entry = type("_Entry", (), {"id": 1, "manor": type("_Manor", (), {"display_name": "甲"})()})()
    defender_entry = type("_Entry", (), {"id": 2, "manor": type("_Manor", (), {"display_name": "乙"})()})()
    report = type("_Report", (), {"id": 10})()

    monkeypatch.setattr(
        arena_match_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
    )

    arena_match_helpers.send_arena_battle_messages(
        report=report,
        round_number=1,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        winner_entry=attacker_entry,
        logger=logging.getLogger("tests.arena.match_helpers"),
    )


def test_send_arena_battle_messages_keeps_best_effort_on_database_failure(monkeypatch):
    attacker_entry = type("_Entry", (), {"id": 1, "manor": type("_Manor", (), {"display_name": "甲"})()})()
    defender_entry = type("_Entry", (), {"id": 2, "manor": type("_Manor", (), {"display_name": "乙"})()})()
    report = type("_Report", (), {"id": 12})()

    monkeypatch.setattr(
        arena_match_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(DatabaseError("message table unavailable")),
    )

    arena_match_helpers.send_arena_battle_messages(
        report=report,
        round_number=1,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        winner_entry=attacker_entry,
        logger=logging.getLogger("tests.arena.match_helpers"),
    )


def test_send_arena_battle_messages_runtime_marker_error_bubbles_up(monkeypatch):
    attacker_entry = type("_Entry", (), {"id": 1, "manor": type("_Manor", (), {"display_name": "甲"})()})()
    defender_entry = type("_Entry", (), {"id": 2, "manor": type("_Manor", (), {"display_name": "乙"})()})()
    report = type("_Report", (), {"id": 11})()

    monkeypatch.setattr(
        arena_match_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        arena_match_helpers.send_arena_battle_messages(
            report=report,
            round_number=1,
            attacker_entry=attacker_entry,
            defender_entry=defender_entry,
            winner_entry=attacker_entry,
            logger=logging.getLogger("tests.arena.match_helpers"),
        )


def test_resolve_match_locked_converts_battle_preparation_error_to_retry(monkeypatch):
    attacker_entry = type("_Entry", (), {"id": 1, "manor": type("_Manor", (), {"display_name": "甲"})()})()
    defender_entry = type("_Entry", (), {"id": 2, "manor": type("_Manor", (), {"display_name": "乙"})()})()
    notes = {}
    match = type(
        "_Match",
        (),
        {
            "notes": "",
            "save": lambda self, *, update_fields: notes.update({"notes": self.notes, "update_fields": update_fields}),
        },
    )()

    monkeypatch.setattr(arena_match_helpers, "load_entry_guests", lambda *_args, **_kwargs: [object()])
    monkeypatch.setattr(
        arena_match_helpers,
        "simulate_report",
        lambda **_kwargs: (_ for _ in ()).throw(BattlePreparationError("guest locked")),
    )

    with pytest.raises(RuntimeError, match="战斗模拟异常，已保留待重试"):
        arena_match_helpers.resolve_match_locked(
            tournament=type("_Tournament", (), {"id": 9})(),
            round_number=1,
            match_index=0,
            attacker_entry=attacker_entry,
            defender_entry=defender_entry,
            now=timezone.now(),
            max_guests_per_entry=3,
            arena_match_resolution_error=RuntimeError,
            match=match,
            logger=logging.getLogger("tests.arena.match_helpers"),
        )

    assert notes == {"notes": "战斗模拟异常，待系统重试", "update_fields": ["notes"]}


def test_resolve_match_locked_programming_error_bubbles_up(monkeypatch):
    attacker_entry = type("_Entry", (), {"id": 1, "manor": type("_Manor", (), {"display_name": "甲"})()})()
    defender_entry = type("_Entry", (), {"id": 2, "manor": type("_Manor", (), {"display_name": "乙"})()})()
    match = type(
        "_Match",
        (),
        {
            "notes": "",
            "save": lambda self, *, update_fields: (_ for _ in ()).throw(
                AssertionError("should not save retry note for programming error")
            ),
        },
    )()

    monkeypatch.setattr(arena_match_helpers, "load_entry_guests", lambda *_args, **_kwargs: [object()])
    monkeypatch.setattr(
        arena_match_helpers,
        "simulate_report",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("broken arena simulate contract")),
    )

    with pytest.raises(AssertionError, match="broken arena simulate contract"):
        arena_match_helpers.resolve_match_locked(
            tournament=type("_Tournament", (), {"id": 10})(),
            round_number=1,
            match_index=0,
            attacker_entry=attacker_entry,
            defender_entry=defender_entry,
            now=timezone.now(),
            max_guests_per_entry=3,
            arena_match_resolution_error=RuntimeError,
            match=match,
            logger=logging.getLogger("tests.arena.match_helpers"),
        )


@pytest.mark.django_db(transaction=True)
def test_finalize_tournament_locked_keeps_success_when_explicit_message_failure(monkeypatch):
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=1,
    )
    winner_manor = _create_arena_manor("arena_settlement_winner")
    loser_manor = _create_arena_manor("arena_settlement_loser")

    winner_entry = ArenaEntry.objects.create(
        tournament=tournament,
        manor=winner_manor,
        status=ArenaEntry.Status.REGISTERED,
    )
    ArenaEntry.objects.create(
        tournament=tournament,
        manor=loser_manor,
        status=ArenaEntry.Status.REGISTERED,
    )

    monkeypatch.setattr(
        arena_lifecycle_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
    )

    arena_lifecycle_helpers.finalize_tournament_locked(
        tournament,
        winner_entry=winner_entry,
        now=timezone.now(),
        calculate_ranked_entries=lambda entries, winner: [winner]
        + [entry for entry in entries if entry.pk != winner.pk],
        reward_for_rank=lambda rank: 100 if rank == 1 else 20,
        logger=logging.getLogger("tests.arena.lifecycle_helpers"),
    )

    tournament.refresh_from_db()
    winner_entry.refresh_from_db()

    assert tournament.status == ArenaTournament.Status.COMPLETED
    assert winner_entry.status == ArenaEntry.Status.WINNER
    assert winner_entry.coin_reward == 100


@pytest.mark.django_db(transaction=True)
def test_finalize_tournament_locked_keeps_success_when_database_message_failure(monkeypatch):
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=1,
    )
    winner_manor = _create_arena_manor("arena_settlement_db_winner")
    loser_manor = _create_arena_manor("arena_settlement_db_loser")

    winner_entry = ArenaEntry.objects.create(
        tournament=tournament,
        manor=winner_manor,
        status=ArenaEntry.Status.REGISTERED,
    )
    ArenaEntry.objects.create(
        tournament=tournament,
        manor=loser_manor,
        status=ArenaEntry.Status.REGISTERED,
    )

    monkeypatch.setattr(
        arena_lifecycle_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(DatabaseError("message table unavailable")),
    )

    arena_lifecycle_helpers.finalize_tournament_locked(
        tournament,
        winner_entry=winner_entry,
        now=timezone.now(),
        calculate_ranked_entries=lambda entries, winner: [winner]
        + [entry for entry in entries if entry.pk != winner.pk],
        reward_for_rank=lambda rank: 100 if rank == 1 else 20,
        logger=logging.getLogger("tests.arena.lifecycle_helpers"),
    )

    tournament.refresh_from_db()
    winner_entry.refresh_from_db()

    assert tournament.status == ArenaTournament.Status.COMPLETED
    assert winner_entry.status == ArenaEntry.Status.WINNER
    assert winner_entry.coin_reward == 100


@pytest.mark.django_db(transaction=True)
def test_finalize_tournament_locked_runtime_marker_error_bubbles_up(monkeypatch):
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=1,
    )
    winner_manor = _create_arena_manor("arena_settlement_runtime_winner")
    loser_manor = _create_arena_manor("arena_settlement_runtime_loser")

    winner_entry = ArenaEntry.objects.create(
        tournament=tournament,
        manor=winner_manor,
        status=ArenaEntry.Status.REGISTERED,
    )
    ArenaEntry.objects.create(
        tournament=tournament,
        manor=loser_manor,
        status=ArenaEntry.Status.REGISTERED,
    )

    monkeypatch.setattr(
        arena_lifecycle_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        with transaction.atomic():
            arena_lifecycle_helpers.finalize_tournament_locked(
                tournament,
                winner_entry=winner_entry,
                now=timezone.now(),
                calculate_ranked_entries=lambda entries, winner: [winner]
                + [entry for entry in entries if entry.pk != winner.pk],
                reward_for_rank=lambda rank: 100 if rank == 1 else 20,
                logger=logging.getLogger("tests.arena.lifecycle_helpers"),
            )


@pytest.mark.django_db(transaction=True)
def test_finalize_tournament_locked_programming_error_bubbles_up(monkeypatch):
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=1,
    )
    winner_manor = _create_arena_manor("arena_settlement_prog_winner")
    loser_manor = _create_arena_manor("arena_settlement_prog_loser")

    winner_entry = ArenaEntry.objects.create(
        tournament=tournament,
        manor=winner_manor,
        status=ArenaEntry.Status.REGISTERED,
    )
    ArenaEntry.objects.create(
        tournament=tournament,
        manor=loser_manor,
        status=ArenaEntry.Status.REGISTERED,
    )

    monkeypatch.setattr(
        arena_lifecycle_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("broken arena settlement message contract")),
    )

    with pytest.raises(AssertionError, match="broken arena settlement message contract"):
        with transaction.atomic():
            arena_lifecycle_helpers.finalize_tournament_locked(
                tournament,
                winner_entry=winner_entry,
                now=timezone.now(),
                calculate_ranked_entries=lambda entries, winner: [winner]
                + [entry for entry in entries if entry.pk != winner.pk],
                reward_for_rank=lambda rank: 100 if rank == 1 else 20,
                logger=logging.getLogger("tests.arena.lifecycle_helpers"),
            )

    tournament.refresh_from_db()
    winner_entry.refresh_from_db()

    assert tournament.status == ArenaTournament.Status.RUNNING
    assert winner_entry.status == ArenaEntry.Status.REGISTERED
