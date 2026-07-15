from __future__ import annotations

import random
from collections.abc import Callable
from typing import TYPE_CHECKING

from core.exceptions import (
    GuestAlreadyOwnedError,
    GuestCapacityFullError,
    RecruitmentCandidateStateError,
    RetainerCapacityFullError,
)

from .recruitment_shared import NON_REPEATABLE_RARITIES

if TYPE_CHECKING:
    from gameplay.models import Manor

    from ..models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentRecord

if TYPE_CHECKING:
    CreateGuestFunc = Callable[..., Guest]
    ShouldUseCustomNameFunc = Callable[[RecruitmentCandidate, GuestTemplate], bool]
else:
    CreateGuestFunc = Callable[..., object]
    ShouldUseCustomNameFunc = Callable[[object, object], bool]


def remaining_guest_capacity(manor: Manor) -> int:
    raw_capacity = getattr(manor, "guest_capacity", None)
    if raw_capacity is None or isinstance(raw_capacity, bool):
        raise AssertionError(f"invalid recruitment guest capacity: {raw_capacity!r}")
    try:
        capacity = int(raw_capacity)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment guest capacity: {raw_capacity!r}") from exc
    if capacity < 0:
        raise AssertionError(f"invalid recruitment guest capacity: {raw_capacity!r}")
    raw_guest_count = manor.guests.count()
    if raw_guest_count is None or isinstance(raw_guest_count, bool):
        raise AssertionError(f"invalid recruitment guest occupancy: {raw_guest_count!r}")
    try:
        guest_count = int(raw_guest_count)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment guest occupancy: {raw_guest_count!r}") from exc
    if guest_count < 0 or guest_count > capacity:
        raise AssertionError(f"invalid recruitment guest occupancy: count={guest_count!r} capacity={capacity!r}")
    return capacity - guest_count


def ensure_guest_capacity_available(manor: Manor) -> int:
    remaining = remaining_guest_capacity(manor)
    if remaining <= 0:
        raise GuestCapacityFullError()
    return remaining


def ensure_candidate_template_available(manor: Manor, candidate: RecruitmentCandidate) -> None:
    if candidate.rarity not in NON_REPEATABLE_RARITIES:
        return
    if manor.guests.filter(template_id=candidate.template_id).exists():
        raise GuestAlreadyOwnedError(candidate.template)


def partition_non_repeatable_candidates(
    manor: Manor,
    candidates: list[RecruitmentCandidate],
) -> tuple[list[RecruitmentCandidate], list[RecruitmentCandidate]]:
    owned_template_ids = set(manor.guests.values_list("template_id", flat=True))
    accepted: list[RecruitmentCandidate] = []
    rejected: list[RecruitmentCandidate] = []
    for candidate in candidates:
        is_non_repeatable = candidate.rarity in NON_REPEATABLE_RARITIES
        if is_non_repeatable and candidate.template_id in owned_template_ids:
            rejected.append(candidate)
            continue
        accepted.append(candidate)
        if is_non_repeatable:
            owned_template_ids.add(candidate.template_id)
    return accepted, rejected


def split_candidates_by_capacity(
    candidates: list[RecruitmentCandidate], *, available_slots: int
) -> tuple[list[RecruitmentCandidate], list[RecruitmentCandidate]]:
    try:
        normalized_slots = int(available_slots)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment available slots: {available_slots!r}") from exc
    if normalized_slots < 0:
        raise AssertionError(f"invalid recruitment available slots: {available_slots!r}")
    return candidates[:normalized_slots], candidates[normalized_slots:]


def build_guest_from_candidate(
    *,
    candidate: RecruitmentCandidate,
    manor: Manor,
    rng: random.Random,
    create_guest_func: CreateGuestFunc,
    should_use_candidate_custom_name: ShouldUseCustomNameFunc,
) -> Guest:
    template = candidate.template
    use_custom_name = should_use_candidate_custom_name(candidate, template)
    return create_guest_func(
        manor=manor,
        template=template,
        rarity=candidate.rarity,
        archetype=candidate.archetype,
        custom_name=candidate.display_name if use_custom_name else "",
        rng=rng,
    )


def create_recruitment_record(
    *,
    recruitment_record_model: type[RecruitmentRecord],
    manor: Manor,
    candidate: RecruitmentCandidate,
    guest: Guest,
) -> RecruitmentRecord:
    return recruitment_record_model.objects.create(
        manor=manor,
        pool=candidate.pool,
        guest=guest,
        rarity=candidate.rarity,
    )


def save_guest_objects(guest_objects: list[Guest]) -> list[Guest]:
    created_guests: list[Guest] = []
    for guest_obj in guest_objects:
        guest_obj.save()
        created_guests.append(guest_obj)
    return created_guests


def delete_processed_candidates(
    *, recruitment_candidate_model: type[RecruitmentCandidate], candidate_ids_to_delete: list[int]
) -> None:
    if not candidate_ids_to_delete:
        return
    recruitment_candidate_model.objects.filter(id__in=candidate_ids_to_delete).delete()


def validate_retainer_candidate_identity(candidate: RecruitmentCandidate) -> tuple[int, int]:
    candidate_id = getattr(candidate, "pk", None)
    manor_id = getattr(candidate, "manor_id", None)
    if not candidate_id or not manor_id:
        raise RecruitmentCandidateStateError()
    return int(candidate_id), int(manor_id)


def load_locked_retainer_candidate(
    *, recruitment_candidate_model: type[RecruitmentCandidate], candidate_id: int, manor_id: int
) -> RecruitmentCandidate | None:
    return recruitment_candidate_model.objects.select_for_update().filter(pk=candidate_id, manor_id=manor_id).first()


def ensure_retainer_capacity_available(manor: Manor) -> None:
    raw_retainer_count = getattr(manor, "retainer_count", None)
    raw_retainer_capacity = getattr(manor, "retainer_capacity", None)
    if raw_retainer_count is None or isinstance(raw_retainer_count, bool):
        raise AssertionError(f"invalid retainer count: {raw_retainer_count!r}")
    if raw_retainer_capacity is None or isinstance(raw_retainer_capacity, bool):
        raise AssertionError(f"invalid retainer capacity: {raw_retainer_capacity!r}")
    try:
        retainer_count = int(raw_retainer_count)
        retainer_capacity = int(raw_retainer_capacity)
    except (TypeError, ValueError) as exc:
        raise AssertionError(
            f"invalid retainer capacity state: count={raw_retainer_count!r} capacity={raw_retainer_capacity!r}"
        ) from exc
    if retainer_count < 0 or retainer_capacity < 0:
        raise AssertionError(
            f"invalid retainer capacity state: count={raw_retainer_count!r} capacity={raw_retainer_capacity!r}"
        )
    if retainer_count >= retainer_capacity:
        raise RetainerCapacityFullError()


def increment_retainer_count_locked(manor: Manor) -> None:
    manor.retainer_count += 1
    manor.save(update_fields=["retainer_count"])
