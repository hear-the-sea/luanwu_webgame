from __future__ import annotations

import random
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from django.db import transaction

from core.config import GUEST
from core.exceptions import (
    GameError,
    GuestNotFoundError,
    GuestNotIdleError,
    InvalidAllocationError,
    RecruitmentCandidateStateError,
    WorldUniqueGuestError,
)

from ..models import Guest, GuestRarity, GuestSkill, GuestStatus, GuestTemplate, RecruitmentCandidate, RecruitmentRecord
from ..utils.name_generator import generate_random_name
from ..utils.recruitment_variance import apply_recruitment_variance
from . import recruitment_batch as _recruitment_batch
from . import recruitment_finalize_helpers as _recruitment_finalize_helpers
from .recruitment_shared import invalidate_recruitment_hall_cache
from .training import ensure_auto_training

if TYPE_CHECKING:
    from gameplay.models import Manor

_build_guest_from_candidate = _recruitment_finalize_helpers.build_guest_from_candidate
_create_recruitment_record = _recruitment_finalize_helpers.create_recruitment_record
_delete_processed_candidates = _recruitment_finalize_helpers.delete_processed_candidates
_ensure_candidate_template_available = _recruitment_finalize_helpers.ensure_candidate_template_available
_ensure_guest_capacity_available = _recruitment_finalize_helpers.ensure_guest_capacity_available
_ensure_retainer_capacity_available = _recruitment_finalize_helpers.ensure_retainer_capacity_available
_increment_retainer_count_locked = _recruitment_finalize_helpers.increment_retainer_count_locked
_load_locked_retainer_candidate = _recruitment_finalize_helpers.load_locked_retainer_candidate
_remaining_guest_capacity = _recruitment_finalize_helpers.remaining_guest_capacity
_partition_non_repeatable_candidates = _recruitment_finalize_helpers.partition_non_repeatable_candidates
_save_guest_objects = _recruitment_finalize_helpers.save_guest_objects
_split_candidates_by_capacity = _recruitment_finalize_helpers.split_candidates_by_capacity
_validate_retainer_candidate_identity = _recruitment_finalize_helpers.validate_retainer_candidate_identity
_preload_templates = _recruitment_batch.preload_templates


def _ensure_training_started_for_guests(guests: list[Guest]) -> None:
    for guest in guests:
        ensure_auto_training(guest)


def grant_template_skills(guest: Guest) -> None:
    """为门客授予模板预设技能。"""
    initial_skills = list(guest.template.initial_skills.all())
    if not initial_skills:
        return
    existing_skill_ids = set(guest.guest_skills.values_list("skill_id", flat=True))
    capacity_left = int(GUEST.MAX_SKILL_SLOTS) - len(existing_skill_ids)
    if capacity_left <= 0:
        return

    skills_to_create: list[GuestSkill] = []
    for skill in initial_skills:
        if skill.id in existing_skill_ids:
            continue
        if len(skills_to_create) >= capacity_left:
            break
        skills_to_create.append(
            GuestSkill(
                guest=guest,
                skill=skill,
                source=GuestSkill.Source.TEMPLATE,
            )
        )

    if skills_to_create:
        GuestSkill.objects.bulk_create(skills_to_create)


def build_recruitment_custom_name(template: GuestTemplate, rng: random.Random) -> str:
    """为普通黑/灰门客生成玩家招募时使用的正常姓名。"""
    if template.rarity in (GuestRarity.BLACK, GuestRarity.GRAY) and not template.is_hermit:
        return generate_random_name(rng)
    return ""


def create_guest_from_template(
    *,
    manor: Manor,
    template: GuestTemplate,
    rarity: Optional[str] = None,
    archetype: Optional[str] = None,
    custom_name: str = "",
    rng: Optional[random.Random] = None,
    grant_skills: bool = True,
    save: bool = True,
    allow_world_unique: bool = False,
) -> Guest:
    """按模板创建门客（含属性波动、初始HP与技能）。"""
    if getattr(template, "is_world_unique", False) and not allow_world_unique:
        raise WorldUniqueGuestError(
            message=f"{template.name}为全服唯一门客，只能通过专属召唤卷轴获得",
        )

    rng = rng or random.Random()
    effective_rarity = rarity or template.rarity
    effective_archetype = archetype or template.archetype

    gender_choice = template.default_gender
    if not gender_choice or gender_choice == "unknown":
        gender_choice = rng.choice(["male", "female"])
    morality_value = template.default_morality or rng.randint(30, 100)

    template_attrs = {
        "force": template.base_attack,
        "intellect": template.base_intellect,
        "defense": template.base_defense,
        "agility": template.base_agility,
        "luck": template.base_luck,
    }
    varied_attrs = apply_recruitment_variance(
        template_attrs,
        rarity=effective_rarity,
        archetype=effective_archetype,
        rng=rng,
    )

    initial_hp = max(
        int(GUEST.MIN_HP_FLOOR),
        template.base_hp + varied_attrs["defense"] * int(GUEST.DEFENSE_TO_HP_MULTIPLIER),
    )

    guest = Guest(
        manor=manor,
        template=template,
        custom_name=custom_name,
        force=varied_attrs["force"],
        intellect=varied_attrs["intellect"],
        defense_stat=varied_attrs["defense"],
        agility=varied_attrs["agility"],
        luck=varied_attrs["luck"],
        initial_force=varied_attrs["force"],
        initial_intellect=varied_attrs["intellect"],
        initial_defense=varied_attrs["defense"],
        initial_agility=varied_attrs["agility"],
        loyalty=60,
        gender=gender_choice,
        morality=morality_value,
        current_hp=initial_hp,
    )

    if save:
        guest.save()
        if grant_skills:
            grant_template_skills(guest)

    return guest


def _prepare_guest_objects(
    candidates: List[RecruitmentCandidate],
    template_map: Dict[int, GuestTemplate],
    manor: Manor,
    rng: random.Random,
) -> tuple[List[Guest], List[GuestTemplate], List[int]]:
    return _recruitment_batch.prepare_guest_objects(
        candidates,
        template_map,
        manor,
        rng,
        create_guest_func=create_guest_from_template,
    )


def _resolve_bulk_finalize_request(candidates: List[RecruitmentCandidate]) -> tuple[int, list[int]]:
    raw_manor_id = getattr(candidates[0], "manor_id", None)
    if raw_manor_id is None or isinstance(raw_manor_id, bool):
        raise AssertionError(f"invalid recruitment candidate manor id: {raw_manor_id!r}")
    try:
        manor_id = int(raw_manor_id)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment candidate manor id: {raw_manor_id!r}") from exc
    if manor_id <= 0:
        raise AssertionError(f"invalid recruitment candidate manor id: {raw_manor_id!r}")

    requested_ids: list[int] = []
    seen_candidate_ids: set[int] = set()
    for candidate in candidates:
        raw_candidate_id = getattr(candidate, "id", None)
        raw_candidate_manor_id = getattr(candidate, "manor_id", None)
        if raw_candidate_id is None or isinstance(raw_candidate_id, bool):
            raise AssertionError(f"invalid recruitment candidate id: {raw_candidate_id!r}")
        if raw_candidate_manor_id is None or isinstance(raw_candidate_manor_id, bool):
            raise AssertionError(f"invalid recruitment candidate manor id: {raw_candidate_manor_id!r}")
        try:
            candidate_id = int(raw_candidate_id)
            candidate_manor_id = int(raw_candidate_manor_id)
        except (TypeError, ValueError) as exc:
            raise AssertionError(
                f"invalid recruitment candidate identity: id={raw_candidate_id!r} manor_id={raw_candidate_manor_id!r}"
            ) from exc
        if candidate_id <= 0 or candidate_manor_id <= 0:
            raise AssertionError(
                f"invalid recruitment candidate identity: id={raw_candidate_id!r} manor_id={raw_candidate_manor_id!r}"
            )
        if candidate_manor_id != manor_id:
            raise AssertionError(f"mixed recruitment candidate manor ids: {candidate_manor_id!r} != {manor_id!r}")
        if candidate_id in seen_candidate_ids:
            continue
        requested_ids.append(candidate_id)
        seen_candidate_ids.add(candidate_id)

    return manor_id, requested_ids


@transaction.atomic
def finalize_candidate(candidate: RecruitmentCandidate) -> Guest:
    """确认招募候选门客，将其转为正式门客。"""
    from gameplay.models import Manor

    candidate_id, manor_id = _validate_retainer_candidate_identity(candidate)
    manor = Manor.objects.select_for_update().get(pk=manor_id)
    locked_candidate = (
        RecruitmentCandidate.objects.select_for_update()
        .select_related("template", "pool")
        .filter(pk=candidate_id, manor_id=manor_id)
        .first()
    )
    if locked_candidate is None:
        raise RecruitmentCandidateStateError()

    _ensure_candidate_template_available(manor, locked_candidate)
    _ensure_guest_capacity_available(manor)
    guest = _build_guest_from_candidate(
        candidate=locked_candidate,
        manor=manor,
        rng=random.Random(),
        create_guest_func=create_guest_from_template,
        should_use_candidate_custom_name=_recruitment_batch.should_use_candidate_custom_name,
    )
    _create_recruitment_record(
        recruitment_record_model=RecruitmentRecord,
        manor=manor,
        candidate=locked_candidate,
        guest=guest,
    )
    _ensure_training_started_for_guests([guest])
    locked_candidate.delete()
    invalidate_recruitment_hall_cache(getattr(manor, "id", None))
    return guest


@transaction.atomic
def bulk_finalize_candidates(
    candidates: List[RecruitmentCandidate],
) -> Tuple[List[Guest], List[RecruitmentCandidate]]:
    """批量确认招募候选门客，将其转为正式门客。"""
    if not candidates:
        return [], []

    from gameplay.models import Manor

    manor_id, requested_ids = _resolve_bulk_finalize_request(candidates)
    manor = Manor.objects.select_for_update().get(pk=manor_id)

    locked_candidates_map = {
        candidate.id: candidate
        for candidate in RecruitmentCandidate.objects.select_for_update()
        .filter(manor_id=manor.id, id__in=requested_ids)
        .order_by("id")
    }
    locked_candidates = [
        locked_candidates_map[candidate_id] for candidate_id in requested_ids if candidate_id in locked_candidates_map
    ]
    stale_candidates: list[RecruitmentCandidate] = []
    seen_stale_candidate_ids: set[int | None] = set()
    for candidate in candidates:
        candidate_id = getattr(candidate, "id", None)
        if candidate_id in locked_candidates_map or candidate_id in seen_stale_candidate_ids:
            continue
        stale_candidates.append(candidate)
        seen_stale_candidate_ids.add(candidate_id)
    if not locked_candidates:
        return [], stale_candidates or candidates

    eligible_candidates, uniqueness_failed = _partition_non_repeatable_candidates(manor, locked_candidates)

    available_slots = _remaining_guest_capacity(manor)
    if available_slots <= 0:
        return [], locked_candidates + stale_candidates

    to_process, capacity_failed = _split_candidates_by_capacity(eligible_candidates, available_slots=available_slots)
    failed = stale_candidates + uniqueness_failed + capacity_failed

    rng = random.Random()
    template_ids = {candidate.template_id for candidate in to_process}
    template_map = _preload_templates(template_ids)

    guests_to_create, templates_for_guests, candidate_ids_to_delete = _prepare_guest_objects(
        to_process, template_map, manor, rng
    )
    created_guests = _save_guest_objects(guests_to_create)

    records_to_create = _recruitment_batch.build_recruitment_records(
        manor=manor,
        candidates=to_process,
        created_guests=created_guests,
    )
    RecruitmentRecord.objects.bulk_create(records_to_create)

    all_skills_to_create = _recruitment_batch.build_template_skill_rows(
        created_guests=created_guests,
        templates_for_guests=templates_for_guests,
        max_guest_skill_slots=int(GUEST.MAX_SKILL_SLOTS),
    )
    if all_skills_to_create:
        GuestSkill.objects.bulk_create(all_skills_to_create)

    _ensure_training_started_for_guests(created_guests)
    _delete_processed_candidates(
        recruitment_candidate_model=RecruitmentCandidate,
        candidate_ids_to_delete=candidate_ids_to_delete,
    )
    invalidate_recruitment_hall_cache(getattr(manor, "id", None))
    return created_guests, failed


@transaction.atomic
def discard_candidates(candidates: List[RecruitmentCandidate]) -> int:
    """批量放弃候选门客。"""
    if not candidates:
        return 0

    manor_id, requested_ids = _resolve_bulk_finalize_request(candidates)
    locked_candidates = list(
        RecruitmentCandidate.objects.select_for_update().filter(manor_id=manor_id, id__in=requested_ids).order_by("id")
    )
    if not locked_candidates:
        return 0

    candidate_ids = [candidate.id for candidate in locked_candidates]
    RecruitmentCandidate.objects.filter(id__in=candidate_ids).delete()
    invalidate_recruitment_hall_cache(manor_id)
    return len(candidate_ids)


def retain_candidates(candidates: List[RecruitmentCandidate]) -> tuple[int, str | None]:
    """批量将候选门客转为家丁，遇到首个业务失败时停止。"""
    retained = 0
    error_message = None
    for candidate in candidates:
        try:
            convert_candidate_to_retainer(candidate)
            retained += 1
        except GameError as exc:
            error_message = str(exc)
            break
    return retained, error_message


@transaction.atomic
def convert_candidate_to_retainer(candidate: RecruitmentCandidate) -> None:
    """将候选门客转为家丁。"""
    candidate_id, manor_id = _validate_retainer_candidate_identity(candidate)

    from gameplay.models import Manor

    manor = Manor.objects.select_for_update().get(pk=manor_id)
    locked_candidate = _load_locked_retainer_candidate(
        recruitment_candidate_model=RecruitmentCandidate,
        candidate_id=candidate_id,
        manor_id=manor_id,
    )
    if locked_candidate is None:
        raise RecruitmentCandidateStateError()

    _ensure_retainer_capacity_available(manor)
    _increment_retainer_count_locked(manor)
    locked_candidate.delete()
    invalidate_recruitment_hall_cache(getattr(manor, "id", None))


def allocate_attribute_points(guest: Guest, attribute: str, points: int) -> Guest:
    """为门客分配属性点到指定属性。"""
    if not getattr(guest, "pk", None):
        raise GuestNotFoundError()

    with transaction.atomic():
        locked_guest = Guest.objects.select_for_update().filter(pk=guest.pk).first()
        if not locked_guest:
            raise GuestNotFoundError()
        if locked_guest.status != GuestStatus.IDLE:
            raise GuestNotIdleError(locked_guest)
        if points <= 0:
            raise InvalidAllocationError("zero_points")
        if locked_guest.attribute_points < points:
            raise InvalidAllocationError("insufficient")

        attr_map = {
            "force": "force",
            "intellect": "intellect",
            "defense": "defense_stat",
            "agility": "agility",
        }
        allocated_map = {
            "force": "allocated_force",
            "intellect": "allocated_intellect",
            "defense": "allocated_defense",
            "agility": "allocated_agility",
        }

        target = attr_map.get(attribute)
        allocated_field = allocated_map.get(attribute)
        if not target or not allocated_field:
            raise InvalidAllocationError("unknown_attribute")

        max_attribute_value = 9999
        current_value = getattr(locked_guest, target)
        if current_value + points > max_attribute_value:
            raise InvalidAllocationError("attribute_overflow")

        locked_guest.attribute_points -= points
        updated_fields = ["attribute_points"]

        setattr(locked_guest, target, getattr(locked_guest, target) + points)
        updated_fields.append(target)

        setattr(locked_guest, allocated_field, getattr(locked_guest, allocated_field) + points)
        updated_fields.append(allocated_field)

        locked_guest.save(update_fields=list(dict.fromkeys(updated_fields)))
    return locked_guest


__all__ = [
    "_prepare_guest_objects",
    "allocate_attribute_points",
    "bulk_finalize_candidates",
    "convert_candidate_to_retainer",
    "build_recruitment_custom_name",
    "create_guest_from_template",
    "discard_candidates",
    "finalize_candidate",
    "grant_template_skills",
    "retain_candidates",
]
