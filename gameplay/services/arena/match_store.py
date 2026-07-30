from __future__ import annotations

from gameplay.models import ArenaEntry, ArenaMatch, ArenaTournament

from .replay import derive_match_replay_metadata


def create_scheduled_match(
    *,
    tournament: ArenaTournament,
    round_number: int,
    match_index: int,
    attacker_entry: ArenaEntry,
    defender_entry: ArenaEntry | None,
) -> ArenaMatch:
    """Single write owner for new arena match slots."""

    match = ArenaMatch(
        tournament=tournament,
        round_number=round_number,
        match_index=match_index,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        status=ArenaMatch.Status.SCHEDULED,
        **derive_match_replay_metadata(
            tournament,
            round_number=round_number,
            match_index=match_index,
        ),
    )
    match.full_clean()
    match.save(force_insert=True)
    return match


__all__ = ["create_scheduled_match"]
