from __future__ import annotations

import logging
import random
from fractions import Fraction
from typing import TYPE_CHECKING, cast

from django.db import transaction

from battle.random_context import RNG_STREAM_TIE_BREAK, BattleRandomContext, current_replay_metadata
from battle.replay_audit import audit_battle_replay_metadata
from battle.report_events import iter_damage_events
from battle.services import simulate_report
from core.exceptions import BattlePreparationError, InvalidBattleSnapshotError, MessageError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.models import ArenaEntry, ArenaMatch, ArenaTournament, Message
from gameplay.services.utils.messages import create_message
from guests.models import Guest

from .match_store import create_scheduled_match
from .replay import ensure_match_replay_metadata
from .snapshots import ArenaGuestSnapshotProxy, load_entry_guests
from .virtual_reserve_pool import release_virtual_reserve_member_for_manor

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
    match = create_scheduled_match(
        tournament=tournament,
        round_number=round_number,
        match_index=match_index,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
    )
    save_resolved_match(
        match=match,
        winner_entry=winner_entry,
        status=status,
        note=note,
        now=now,
    )
    return match


def save_resolved_match(
    *,
    match: ArenaMatch,
    winner_entry: ArenaEntry,
    status: str,
    now,
    note: str = "",
    report=None,
) -> bool:
    candidate = ArenaMatch(
        tournament=match.tournament,
        round_number=match.round_number,
        match_index=match.match_index,
        attacker_entry=match.attacker_entry,
        defender_entry=match.defender_entry,
        winner_entry=winner_entry,
        status=status,
        base_seed=getattr(match, "base_seed", 0),
        rng_version=getattr(match, "rng_version", 0),
        battle_engine_version=getattr(match, "battle_engine_version", "legacy"),
        battle_report=report,
        notes=note[:255],
        resolved_at=now,
    )
    candidate.full_clean(validate_unique=False, validate_constraints=False)
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
    rng: random.Random,
    attacker_snapshot_invalid: bool = False,
    defender_snapshot_invalid: bool = False,
) -> ArenaEntry | None:
    if attacker_snapshot_invalid and defender_snapshot_invalid:
        winner_entry = rng.choice([attacker_entry, defender_entry])
        note = "双方报名快照均无效，使用对局 tie_break 子流裁决"
    elif attacker_snapshot_invalid:
        winner_entry = defender_entry
        note = "攻击方报名快照无效，判定防守方晋级"
    elif defender_snapshot_invalid:
        winner_entry = attacker_entry
        note = "防守方报名快照无效，判定攻击方晋级"
    elif not attacker_guests and not defender_guests:
        winner_entry = rng.choice([attacker_entry, defender_entry])
        note = "双方均无可用门客，使用对局 tie_break 子流裁决"
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


def _team_hp_ratio(team: object) -> Fraction:
    members = team if isinstance(team, list) else []
    initial_hp = 0
    remaining_hp = 0
    for member in members:
        if not isinstance(member, dict):
            continue
        initial = max(0, int(member.get("initial_hp") or member.get("max_hp") or member.get("hp") or 0))
        remaining = max(0, min(initial, int(member.get("remaining_hp") or 0)))
        initial_hp += initial
        remaining_hp += remaining
    return Fraction(remaining_hp, initial_hp) if initial_hp > 0 else Fraction(0, 1)


def _side_combat_metrics(report, side: str) -> tuple[int, int]:
    applied_damage = 0
    kills = 0
    for event in iter_damage_events(getattr(report, "rounds", None)):
        if event.get("side") != side:
            continue
        applied_damage += max(0, int(event.get("applied_damage", event.get("damage", 0)) or 0))
        kills += max(0, int(event.get("kills", 0) or 0))
    return applied_damage, kills


def _remaining_unit_count(team: object) -> int:
    members = team if isinstance(team, list) else []
    return sum(1 for member in members if isinstance(member, dict) and max(0, int(member.get("remaining_hp") or 0)) > 0)


def resolve_report_winner(
    report,
    *,
    attacker_entry: ArenaEntry,
    defender_entry: ArenaEntry,
    rng: random.Random,
) -> tuple[ArenaEntry, str]:
    if report.winner == "attacker":
        return attacker_entry, "战报裁决：攻击方胜利"
    if report.winner == "defender":
        return defender_entry, "战报裁决：防守方胜利"

    attacker_hp_ratio = _team_hp_ratio(getattr(report, "attacker_team", None))
    defender_hp_ratio = _team_hp_ratio(getattr(report, "defender_team", None))
    if attacker_hp_ratio != defender_hp_ratio:
        winner = attacker_entry if attacker_hp_ratio > defender_hp_ratio else defender_entry
        return winner, f"平局裁决：剩余有效HP比例 {attacker_hp_ratio} : {defender_hp_ratio}"

    attacker_damage, attacker_kills = _side_combat_metrics(report, "attacker")
    defender_damage, defender_kills = _side_combat_metrics(report, "defender")
    if attacker_damage != defender_damage:
        winner = attacker_entry if attacker_damage > defender_damage else defender_entry
        return winner, f"平局裁决：有效伤害 {attacker_damage} : {defender_damage}"

    attacker_units = _remaining_unit_count(getattr(report, "attacker_team", None))
    defender_units = _remaining_unit_count(getattr(report, "defender_team", None))
    attacker_stage_three = (attacker_kills, attacker_units)
    defender_stage_three = (defender_kills, defender_units)
    if attacker_stage_three != defender_stage_three:
        winner = attacker_entry if attacker_stage_three > defender_stage_three else defender_entry
        return winner, (
            "平局裁决：击杀/剩余单位 " f"{attacker_kills}/{attacker_units} : {defender_kills}/{defender_units}"
        )

    winner = rng.choice([attacker_entry, defender_entry])
    return winner, "平局裁决：全部确定性指标相同，使用对局 tie_break 子流"


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
    if match is None:
        match = create_scheduled_match(
            tournament=tournament,
            round_number=round_number,
            match_index=match_index,
            attacker_entry=attacker_entry,
            defender_entry=defender_entry,
        )
    elif not getattr(match, "pk", None):
        # Compatibility shim for isolated callers with an unsaved test adapter.
        # Remove once every caller passes a persisted match created by create_scheduled_match.
        if not int(getattr(match, "base_seed", 0) or 0) or not int(getattr(match, "rng_version", 0) or 0):
            metadata = current_replay_metadata()
            for field_name, value in metadata.items():
                setattr(match, field_name, value)
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

    ensure_match_replay_metadata(match.pk)
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
    if match is None:
        raise AssertionError("arena match resolution requires a match write owner")
    random_context = BattleRandomContext.create(
        match.base_seed,
        rng_version=match.rng_version,
    )
    tie_break_rng = random_context.rng(RNG_STREAM_TIE_BREAK)

    attacker_snapshot_invalid = False
    defender_snapshot_invalid = False
    try:
        attacker_guests = load_entry_guests(attacker_entry, max_guests_per_entry=max_guests_per_entry)
    except InvalidBattleSnapshotError as exc:
        attacker_guests = []
        attacker_snapshot_invalid = True
        if attacker_entry.source == ArenaEntry.Source.VIRTUAL:
            release_virtual_reserve_member_for_manor(attacker_entry.manor_id)
        logger.warning(
            "arena_entry_forfeited_invalid_snapshot: match_id=%s entry_id=%s side=attacker error=%s",
            getattr(match, "pk", None),
            attacker_entry.pk,
            exc,
            extra={
                "event": "arena_entry_forfeited_invalid_snapshot",
                "match_id": getattr(match, "pk", None),
                "tournament_id": tournament.pk,
                "entry_id": attacker_entry.pk,
                "side": "attacker",
                "failure_reason": "invalid_guest_snapshot",
                "base_seed": match.base_seed,
                "rng_version": match.rng_version,
                "battle_engine_version": match.battle_engine_version,
            },
        )
    try:
        defender_guests = load_entry_guests(defender_entry, max_guests_per_entry=max_guests_per_entry)
    except InvalidBattleSnapshotError as exc:
        defender_guests = []
        defender_snapshot_invalid = True
        if defender_entry.source == ArenaEntry.Source.VIRTUAL:
            release_virtual_reserve_member_for_manor(defender_entry.manor_id)
        logger.warning(
            "arena_entry_forfeited_invalid_snapshot: match_id=%s entry_id=%s side=defender error=%s",
            getattr(match, "pk", None),
            defender_entry.pk,
            exc,
            extra={
                "event": "arena_entry_forfeited_invalid_snapshot",
                "match_id": getattr(match, "pk", None),
                "tournament_id": tournament.pk,
                "entry_id": defender_entry.pk,
                "side": "defender",
                "failure_reason": "invalid_guest_snapshot",
                "base_seed": match.base_seed,
                "rng_version": match.rng_version,
                "battle_engine_version": match.battle_engine_version,
            },
        )

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
        rng=tie_break_rng,
        attacker_snapshot_invalid=attacker_snapshot_invalid,
        defender_snapshot_invalid=defender_snapshot_invalid,
    )
    if forfeit_winner is not None:
        return forfeit_winner

    attacker_battle_guests = cast(list[Guest], attacker_guests)
    defender_battle_guests = cast(list[Guest], defender_guests)
    try:
        report = simulate_report(
            manor=attacker_entry.manor,
            battle_type="arena",
            seed=match.base_seed,
            rng_version=match.rng_version,
            battle_engine_version=match.battle_engine_version,
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

    audit_battle_replay_metadata(
        match,
        report,
        logger=logger,
        activity_kind="arena_match",
    )

    winner_entry, resolution_note = resolve_report_winner(
        report,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        rng=tie_break_rng,
    )

    saved = True
    if match:
        saved = save_resolved_match(
            match=match,
            winner_entry=winner_entry,
            status=ArenaMatch.Status.COMPLETED,
            report=report,
            now=now,
            note=resolution_note,
        )
    else:
        raise AssertionError("arena match resolution cannot persist outside create_scheduled_match")

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
