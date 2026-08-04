from __future__ import annotations

from collections.abc import Callable, Iterable

from core.exceptions import (
    ArenaCancellationError,
    ArenaGuestSelectionError,
    InsufficientResourceError,
    InsufficientSilverError,
)
from gameplay.models import ArenaEntry, ArenaEntryGuest, ArenaTournament, Manor, ResourceEvent
from guests.models import Guest, GuestStatus
from guests.services.status import persist_guest_status_transitions


def load_selected_registration_guests_locked(locked_manor: Manor, selected_guest_ids: Iterable[int]) -> list[Guest]:
    requested_guest_ids = [int(guest_id) for guest_id in selected_guest_ids]
    all_selected_guests = list(
        Guest.objects.select_for_update()
        .filter(manor=locked_manor, id__in=requested_guest_ids)
        .select_related("template")
        .prefetch_related("skills")
        .order_by("id")
    )
    if len(all_selected_guests) != len(requested_guest_ids):
        raise ArenaGuestSelectionError("所选门客不存在或不属于当前庄园")

    non_idle_guests = [guest for guest in all_selected_guests if guest.status != GuestStatus.IDLE]
    if non_idle_guests:
        raise ArenaGuestSelectionError("仅空闲门客可报名竞技场")

    selected_guest_order = {guest_id: index for index, guest_id in enumerate(requested_guest_ids)}
    return sorted(all_selected_guests, key=lambda guest: selected_guest_order[guest.id])


def deduct_registration_silver_locked(locked_manor: Manor, *, silver_cost: int) -> None:
    from gameplay.services.resources import spend_resources_locked

    try:
        spend_resources_locked(
            locked_manor,
            {"silver": silver_cost},
            note="竞技场报名",
            reason=ResourceEvent.Reason.UPGRADE_COST,
        )
    except InsufficientResourceError as exc:
        raise InsufficientSilverError(
            silver_cost, int(locked_manor.silver), message=f"银两不足，报名需要 {silver_cost} 银两"
        ) from exc


def create_arena_entry_with_guests_locked(
    *,
    tournament: ArenaTournament,
    locked_manor: Manor,
    selected_guests: list[Guest],
    build_entry_guest_snapshot: Callable[[Guest], dict],
) -> ArenaEntry:
    entry = ArenaEntry.objects.create(tournament=tournament, manor=locked_manor)
    ArenaEntryGuest.objects.bulk_create(
        [
            ArenaEntryGuest(entry=entry, guest=guest, snapshot=build_entry_guest_snapshot(guest))
            for guest in selected_guests
        ]
    )
    persist_guest_status_transitions(
        selected_guests,
        GuestStatus.ARENA,
        source="arena_register",
    )
    return entry


def collect_cancelable_recruiting_entries_locked(locked_manor: Manor) -> tuple[list[ArenaEntry], list[int]]:
    recruiting_entries = list(
        ArenaEntry.objects.select_for_update()
        .select_related("tournament")
        .filter(
            manor=locked_manor,
            status=ArenaEntry.Status.REGISTERED,
            tournament__status=ArenaTournament.Status.RECRUITING,
        )
        .order_by("-joined_at", "-id")
    )
    if not recruiting_entries:
        raise ArenaCancellationError("当前没有可撤销的报名")

    tournament_ids = {entry.tournament_id for entry in recruiting_entries}
    locked_tournaments = list(ArenaTournament.objects.select_for_update().filter(id__in=tournament_ids))
    if any(tournament.status != ArenaTournament.Status.RECRUITING for tournament in locked_tournaments):
        raise ArenaCancellationError("赛事已开赛，当前不可撤销报名")

    entry_ids = [entry.id for entry in recruiting_entries]
    participant_guest_ids = list(
        ArenaEntryGuest.objects.filter(entry_id__in=entry_ids).values_list("guest_id", flat=True).distinct()
    )
    return recruiting_entries, participant_guest_ids
