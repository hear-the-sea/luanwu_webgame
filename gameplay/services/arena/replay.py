from __future__ import annotations

from typing import Any

from django.db import transaction

from battle.random_context import (
    LEGACY_BATTLE_ENGINE_VERSION,
    RNG_STREAM_COMBAT,
    BattleRandomContext,
    current_replay_metadata,
)
from gameplay.models import ArenaCoopEvent, ArenaMatch, ArenaTournament

REPLAY_METADATA_FIELDS = ("base_seed", "rng_version", "battle_engine_version")


def has_replay_metadata(activity: Any) -> bool:
    return (
        int(getattr(activity, "base_seed", 0) or 0) > 0
        and int(getattr(activity, "rng_version", 0) or 0) > 0
        and str(getattr(activity, "battle_engine_version", "") or "") != LEGACY_BATTLE_ENGINE_VERSION
    )


def initialize_replay_metadata_locked(activity: Any) -> bool:
    """Initialize replay fields on an already locked activity exactly once."""

    if has_replay_metadata(activity):
        return False
    metadata = current_replay_metadata()
    for field_name, value in metadata.items():
        setattr(activity, field_name, value)
    activity.save(update_fields=[*REPLAY_METADATA_FIELDS])
    return True


def replay_context(activity: Any) -> BattleRandomContext:
    if not has_replay_metadata(activity):
        raise AssertionError(f"activity {type(activity).__name__} is missing replay metadata")
    return BattleRandomContext.create(
        activity.base_seed,
        rng_version=activity.rng_version,
    )


def derive_match_replay_metadata(
    tournament: ArenaTournament,
    *,
    round_number: int,
    match_index: int,
) -> dict[str, int | str]:
    context = replay_context(tournament)
    discriminator = f"round:{int(round_number)}:match:{int(match_index)}"
    return {
        "base_seed": context.persisted_seed(RNG_STREAM_COMBAT, discriminator=discriminator),
        "rng_version": context.rng_version,
        "battle_engine_version": tournament.battle_engine_version,
    }


@transaction.atomic
def ensure_tournament_replay_metadata(tournament_id: int) -> bool:
    tournament = ArenaTournament.objects.select_for_update().filter(pk=tournament_id).first()
    if tournament is None:
        return False
    initialize_replay_metadata_locked(tournament)
    return True


@transaction.atomic
def ensure_match_replay_metadata(match_id: int) -> bool:
    tournament_id = ArenaMatch.objects.filter(pk=match_id).values_list("tournament_id", flat=True).first()
    if tournament_id is None:
        return False
    tournament = ArenaTournament.objects.select_for_update().get(pk=tournament_id)
    initialize_replay_metadata_locked(tournament)
    match = ArenaMatch.objects.select_for_update().filter(pk=match_id).first()
    if match is None:
        return False
    if not has_replay_metadata(match):
        metadata = derive_match_replay_metadata(
            tournament,
            round_number=match.round_number,
            match_index=match.match_index,
        )
        for field_name, value in metadata.items():
            setattr(match, field_name, value)
        match.save(update_fields=[*REPLAY_METADATA_FIELDS])
    return True


@transaction.atomic
def ensure_coop_event_replay_metadata(event_id: int) -> bool:
    event = ArenaCoopEvent.objects.select_for_update().filter(pk=event_id).first()
    if event is None:
        return False
    initialize_replay_metadata_locked(event)
    return True
