from __future__ import annotations

import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from django.db import transaction

from battle.services import simulate_report
from core.exceptions import BattlePreparationError, MessageError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.models import ArenaEntry, ArenaMatch, ArenaTournament, Message
from gameplay.services.utils.messages import create_message
from guests.models import Guest

from .snapshots import ArenaGuestSnapshotProxy, load_entry_guests

if TYPE_CHECKING:
    from datetime import datetime


ARENA_BATTLE_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def send_arena_battle_messages(
    *,
    report,
    round_number: int,
    attacker_entry: ArenaEntry,
    defender_entry: ArenaEntry,
    winner_entry: ArenaEntry,
    logger: logging.Logger,
) -> None:
    title = f"竞技场第 {round_number} 轮战报"
    winner_name = winner_entry.manor.display_name
    body = f"{attacker_entry.manor.display_name} 对阵 {defender_entry.manor.display_name}，本场胜者：{winner_name}。"

    try:
        create_message(
            manor=attacker_entry.manor,
            kind=Message.Kind.BATTLE,
            title=title,
            body=body,
            battle_report=report,
        )
        create_message(
            manor=defender_entry.manor,
            kind=Message.Kind.BATTLE,
            title=title,
            body=body,
            battle_report=report,
        )
    except ARENA_BATTLE_MESSAGE_EXCEPTIONS:
        logger.exception(
            "failed to send arena battle messages: report_id=%s attacker_entry=%s defender_entry=%s",
            getattr(report, "id", None),
            attacker_entry.id,
            defender_entry.id,
        )


def create_forfeit_match(
    *,
    tournament: ArenaTournament,
    round_number: int,
    match_index: int,
    attacker_entry: ArenaEntry,
    defender_entry: ArenaEntry | None,
    winner_entry: ArenaEntry,
    status: str,
    note: str,
    now,
) -> ArenaMatch:
    return ArenaMatch.objects.create(
        tournament=tournament,
        round_number=round_number,
        match_index=match_index,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        winner_entry=winner_entry,
        status=status,
        notes=note[:255],
        resolved_at=now,
    )


def save_resolved_match(
    *,
    match: ArenaMatch,
    winner_entry: ArenaEntry,
    status: str,
    now,
    note: str = "",
    report=None,
) -> bool:
    update_values = {
        "winner_entry": winner_entry,
        "status": status,
        "notes": note[:255],
        "resolved_at": now,
    }
    update_fields = ["winner_entry", "status", "notes", "resolved_at"]
    if report is not None:
        update_values["battle_report"] = report
        update_fields.append("battle_report")

    if getattr(match, "pk", None):
        claimed = ArenaMatch.objects.filter(pk=match.pk, status=ArenaMatch.Status.SCHEDULED).update(**update_values)
        if not claimed:
            return False
        for field in update_fields:
            setattr(match, field, update_values[field])
        return True

    for field in update_fields:
        setattr(match, field, update_values[field])
    match.save(update_fields=update_fields)
    return True


def persist_forfeit_match_resolution(
    *,
    tournament: ArenaTournament,
    round_number: int,
    match_index: int,
    attacker_entry: ArenaEntry,
    defender_entry: ArenaEntry,
    winner_entry: ArenaEntry,
    note: str,
    now,
    match: ArenaMatch | None,
) -> None:
    if match is not None:
        save_resolved_match(
            match=match,
            winner_entry=winner_entry,
            status=ArenaMatch.Status.FORFEIT,
            note=note,
            now=now,
        )
        return
    create_forfeit_match(
        tournament=tournament,
        round_number=round_number,
        match_index=match_index,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        winner_entry=winner_entry,
        status=ArenaMatch.Status.FORFEIT,
        note=note,
        now=now,
    )


def resolve_forfeit_winner(
    *,
    tournament: ArenaTournament,
    round_number: int,
    match_index: int,
    attacker_entry: ArenaEntry,
    defender_entry: ArenaEntry,
    attacker_guests: list[ArenaGuestSnapshotProxy],
    defender_guests: list[ArenaGuestSnapshotProxy],
    now,
    match: ArenaMatch | None,
    random_choice: Callable[[list[ArenaEntry]], ArenaEntry],
) -> ArenaEntry | None:
    if not attacker_guests and not defender_guests:
        winner_entry = random_choice([attacker_entry, defender_entry])
        note = "双方均无可用门客，随机判定胜者"
    elif not attacker_guests:
        winner_entry = defender_entry
        note = "攻击方无可用门客，判负"
    elif not defender_guests:
        winner_entry = attacker_entry
        note = "防守方无可用门客，判负"
    else:
        return None

    persist_forfeit_match_resolution(
        tournament=tournament,
        round_number=round_number,
        match_index=match_index,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        winner_entry=winner_entry,
        note=note,
        now=now,
        match=match,
    )
    return winner_entry


def resolve_match_locked(
    *,
    tournament: ArenaTournament,
    round_number: int,
    match_index: int,
    attacker_entry: ArenaEntry,
    defender_entry: ArenaEntry,
    now: datetime,
    max_guests_per_entry: int,
    arena_match_resolution_error: type[Exception],
    match: ArenaMatch | None = None,
    logger: logging.Logger,
) -> ArenaEntry | None:
    if match is None or not getattr(match, "pk", None):
        return _resolve_match_locked(
            tournament=tournament,
            round_number=round_number,
            match_index=match_index,
            attacker_entry=attacker_entry,
            defender_entry=defender_entry,
            now=now,
            max_guests_per_entry=max_guests_per_entry,
            arena_match_resolution_error=arena_match_resolution_error,
            match=match,
            logger=logger,
        )

    with transaction.atomic():
        locked_match = (
            ArenaMatch.objects.select_for_update().filter(pk=match.pk, status=ArenaMatch.Status.SCHEDULED).first()
        )
        if locked_match is None:
            return None
        return _resolve_match_locked(
            tournament=tournament,
            round_number=round_number,
            match_index=match_index,
            attacker_entry=attacker_entry,
            defender_entry=defender_entry,
            now=now,
            max_guests_per_entry=max_guests_per_entry,
            arena_match_resolution_error=arena_match_resolution_error,
            match=locked_match,
            logger=logger,
        )


def _resolve_match_locked(
    *,
    tournament: ArenaTournament,
    round_number: int,
    match_index: int,
    attacker_entry: ArenaEntry,
    defender_entry: ArenaEntry,
    now: datetime,
    max_guests_per_entry: int,
    arena_match_resolution_error: type[Exception],
    match: ArenaMatch | None = None,
    logger: logging.Logger,
) -> ArenaEntry:
    attacker_guests = load_entry_guests(attacker_entry, max_guests_per_entry=max_guests_per_entry)
    defender_guests = load_entry_guests(defender_entry, max_guests_per_entry=max_guests_per_entry)

    forfeit_winner = resolve_forfeit_winner(
        tournament=tournament,
        round_number=round_number,
        match_index=match_index,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        attacker_guests=attacker_guests,
        defender_guests=defender_guests,
        now=now,
        match=match,
        random_choice=random.choice,
    )
    if forfeit_winner is not None:
        return forfeit_winner

    attacker_battle_guests = cast(list[Guest], attacker_guests)
    defender_battle_guests = cast(list[Guest], defender_guests)
    try:
        report = simulate_report(
            manor=attacker_entry.manor,
            battle_type="arena",
            troop_loadout={},
            fill_default_troops=False,
            attacker_guests=attacker_battle_guests,
            defender_guests=defender_battle_guests,
            max_squad=max_guests_per_entry,
            auto_reward=False,
            send_message=False,
            apply_damage=False,
            use_lock=False,
            opponent_name=defender_entry.manor.display_name,
        )
    except BattlePreparationError:
        logger.exception(
            "arena simulate_report failed; defer match for retry: tournament_id=%s round=%s attacker=%s defender=%s",
            tournament.id,
            round_number,
            attacker_entry.id,
            defender_entry.id,
        )
        if match and getattr(match, "pk", None):
            ArenaMatch.objects.filter(pk=match.pk, status=ArenaMatch.Status.SCHEDULED).update(
                notes="战斗模拟异常，待系统重试"
            )
        elif match:
            match.notes = "战斗模拟异常，待系统重试"
            match.save(update_fields=["notes"])
        raise arena_match_resolution_error("战斗模拟异常，已保留待重试")

    if report.winner == "attacker":
        winner_entry = attacker_entry
    elif report.winner == "defender":
        winner_entry = defender_entry
    else:
        winner_entry = random.choice([attacker_entry, defender_entry])

    saved = True
    if match:
        saved = save_resolved_match(
            match=match,
            winner_entry=winner_entry,
            status=ArenaMatch.Status.COMPLETED,
            report=report,
            now=now,
        )
    else:
        ArenaMatch.objects.create(
            tournament=tournament,
            round_number=round_number,
            match_index=match_index,
            attacker_entry=attacker_entry,
            defender_entry=defender_entry,
            winner_entry=winner_entry,
            battle_report=report,
            status=ArenaMatch.Status.COMPLETED,
            resolved_at=now,
        )

    if saved:

        def _send_messages() -> None:
            send_arena_battle_messages(
                report=report,
                round_number=round_number,
                attacker_entry=attacker_entry,
                defender_entry=defender_entry,
                winner_entry=winner_entry,
                logger=logger,
            )

        if match is not None and getattr(match, "pk", None):
            transaction.on_commit(_send_messages)
        else:
            _send_messages()
    return winner_entry
