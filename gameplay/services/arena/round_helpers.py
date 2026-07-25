from __future__ import annotations

from typing import Any, Callable


def load_round_entries_for_matches(*, arena_match_model, arena_entry_model, pending_match_ids: list[int]):
    pending_matches = list(
        arena_match_model.objects.select_related(
            "attacker_entry__manor",
            "attacker_entry__manor__user",
            "defender_entry__manor",
            "defender_entry__manor__user",
        )
        .filter(id__in=pending_match_ids)
        .order_by("match_index", "id")
    )
    entry_ids = {
        entry_id
        for match in pending_matches
        for entry_id in [match.attacker_entry_id, match.defender_entry_id]
        if entry_id is not None
    }
    entries = (
        arena_entry_model.objects.select_related("manor", "manor__user")
        .prefetch_related("entry_guests__guest__template", "entry_guests__guest__skills")
        .filter(pk__in=entry_ids)
    )
    return pending_matches, {entry.id: entry for entry in entries}


def resolve_pending_round_matches(
    *,
    pending_matches: list[Any],
    entry_map: dict[int, Any],
    round_number: int,
    now,
    bye_status: str,
    forfeit_status: str,
    save_resolved_match: Callable[..., bool | None],
    resolve_match_locked: Callable[..., Any],
    arena_match_resolution_error: type[Exception],
) -> bool:
    resolution_failed = False
    for pending in pending_matches:
        attacker_entry = entry_map.get(pending.attacker_entry_id)
        if not attacker_entry:
            continue

        if pending.defender_entry_id is None:
            save_resolved_match(
                match=pending,
                winner_entry=attacker_entry,
                status=bye_status,
                note="本轮轮空直接晋级",
                now=now,
            )
            continue

        defender_entry = entry_map.get(pending.defender_entry_id)
        if not defender_entry:
            save_resolved_match(
                match=pending,
                winner_entry=attacker_entry,
                status=forfeit_status,
                note="对手报名数据缺失，自动晋级",
                now=now,
            )
            continue

        try:
            resolve_match_locked(
                tournament=pending.tournament,
                round_number=round_number,
                match_index=pending.match_index,
                attacker_entry=attacker_entry,
                defender_entry=defender_entry,
                now=now,
                match=pending,
            )
        except arena_match_resolution_error:
            resolution_failed = True
    return resolution_failed


def finalize_round_state_locked(
    *,
    arena_tournament_model,
    arena_match_model,
    arena_entry_model,
    arena_entry_guest_model,
    tournament_id: int,
    round_number: int,
    now,
    running_status: str,
    scheduled_status: str,
    registered_status: str,
    eliminated_status: str,
    resolution_failed: bool,
    collect_round_outcome_entry_ids: Callable[[list[Any]], tuple[list[int], list[int]]],
    schedule_round_retry_locked: Callable[..., None],
    finalize_tournament_locked: Callable[..., None],
    schedule_round_locked: Callable[..., bool],
    increase_guest_loyalty_by_ids: Callable[[list[int]], int],
) -> bool:
    tournament = arena_tournament_model.objects.select_for_update().filter(pk=tournament_id).first()
    if not tournament or tournament.status != running_status:
        return False

    round_matches = list(
        arena_match_model.objects.select_for_update()
        .filter(tournament=tournament, round_number=round_number)
        .order_by("match_index", "id")
    )
    unresolved_exists = any(match.status == scheduled_status for match in round_matches)
    if resolution_failed or unresolved_exists:
        schedule_round_retry_locked(tournament, now=now)
        return False

    winner_ids, loser_ids = collect_round_outcome_entry_ids(round_matches)
    if not winner_ids:
        schedule_round_retry_locked(tournament, now=now)
        return False

    if loser_ids:
        arena_entry_model.objects.filter(
            pk__in=loser_ids,
            status=registered_status,
        ).update(
            status=eliminated_status,
            eliminated_round=round_number,
        )
    arena_entry_model.objects.filter(pk__in=winner_ids).update(
        matches_won=__import__("django.db.models", fromlist=["F"]).F("matches_won") + 1
    )

    winner_guest_ids = list(
        arena_entry_guest_model.objects.filter(
            entry_id__in=winner_ids,
            guest_id__isnull=False,
        )
        .values_list("guest_id", flat=True)
        .distinct()
    )
    if winner_guest_ids:
        increase_guest_loyalty_by_ids(winner_guest_ids)

    if len(winner_ids) <= 1:
        winner = None
        if winner_ids:
            winner = (
                arena_entry_model.objects.select_related("manor", "manor__user")
                .select_for_update()
                .filter(pk=winner_ids[0])
                .first()
            )
        finalize_tournament_locked(tournament, winner_entry=winner, now=now)
        return True

    schedule_round_locked(tournament, round_number=round_number + 1, now=now)
    return True
