from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from gameplay.models import ArenaEntry, ArenaMatch, ArenaTournament
from gameplay.services.manor.core import ensure_manor
from tests.arena_services.support import User


def _create_entry(tournament: ArenaTournament, label: str) -> ArenaEntry:
    user = User.objects.create_user(username=label, password="pass123")
    return ArenaEntry.objects.create(tournament=tournament, manor=ensure_manor(user))


@pytest.mark.django_db
def test_arena_match_clean_enforces_tournament_winner_and_status_invariants():
    first_tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RUNNING)
    second_tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RUNNING)
    attacker = _create_entry(first_tournament, "arena_integrity_attacker")
    defender = _create_entry(first_tournament, "arena_integrity_defender")
    outsider = _create_entry(second_tournament, "arena_integrity_outsider")

    scheduled = ArenaMatch(
        tournament=first_tournament,
        round_number=1,
        attacker_entry=attacker,
        defender_entry=defender,
    )
    assert scheduled.status == ArenaMatch.Status.SCHEDULED
    scheduled.full_clean()

    cross_tournament = ArenaMatch(
        tournament=first_tournament,
        round_number=1,
        attacker_entry=outsider,
        defender_entry=defender,
    )
    with pytest.raises(ValidationError, match="攻击方报名必须属于当前赛事"):
        cross_tournament.full_clean()

    invalid_winner = ArenaMatch(
        tournament=first_tournament,
        round_number=1,
        attacker_entry=attacker,
        defender_entry=defender,
        winner_entry=outsider,
        status=ArenaMatch.Status.COMPLETED,
        resolved_at=timezone.now(),
    )
    with pytest.raises(ValidationError, match="胜者必须是本场攻方或守方"):
        invalid_winner.full_clean()

    unresolved_completed = ArenaMatch(
        tournament=first_tournament,
        round_number=1,
        attacker_entry=attacker,
        defender_entry=defender,
        status=ArenaMatch.Status.COMPLETED,
    )
    with pytest.raises(ValidationError, match="已结算对局"):
        unresolved_completed.full_clean()

    invalid_bye = ArenaMatch(
        tournament=first_tournament,
        round_number=1,
        attacker_entry=attacker,
        defender_entry=defender,
        winner_entry=attacker,
        status=ArenaMatch.Status.BYE,
        resolved_at=timezone.now(),
    )
    with pytest.raises(ValidationError, match="轮空对局不能有防守方"):
        invalid_bye.full_clean()


@pytest.mark.django_db
def test_arena_match_slot_has_database_unique_constraint():
    tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RUNNING)
    attacker = _create_entry(tournament, "arena_unique_slot_attacker")
    defender = _create_entry(tournament, "arena_unique_slot_defender")
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=attacker,
        defender_entry=defender,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        ArenaMatch.objects.create(
            tournament=tournament,
            round_number=1,
            match_index=0,
            attacker_entry=defender,
            defender_entry=attacker,
        )
