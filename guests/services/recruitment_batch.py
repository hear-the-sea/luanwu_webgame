from __future__ import annotations

import random
from typing import TYPE_CHECKING, Callable

from ..models import Guest, GuestRarity, GuestSkill, GuestTemplate, RecruitmentCandidate, RecruitmentRecord

if TYPE_CHECKING:
    from gameplay.models import Manor


CreateGuestFunc = Callable[..., Guest]


def should_use_candidate_custom_name(candidate: RecruitmentCandidate, template: GuestTemplate) -> bool:
    return candidate.rarity in (GuestRarity.BLACK, GuestRarity.GRAY) and not template.is_hermit


def preload_templates(template_ids: set[int]) -> dict[int, GuestTemplate]:
    return {
        template.id: template
        for template in GuestTemplate.objects.filter(id__in=template_ids).prefetch_related("initial_skills")
    }


def prepare_guest_objects(
    candidates: list[RecruitmentCandidate],
    template_map: dict[int, GuestTemplate],
    manor: Manor,
    rng: random.Random,
    *,
    create_guest_func: CreateGuestFunc,
) -> tuple[list[Guest], list[GuestTemplate], list[int]]:
    guests_to_create: list[Guest] = []
    templates_for_guests: list[GuestTemplate] = []
    candidate_ids_to_delete: list[int] = []

    for candidate in candidates:
        template = template_map.get(candidate.template_id) or candidate.template
        use_custom_name = should_use_candidate_custom_name(candidate, template)
        guest = create_guest_func(
            manor=manor,
            template=template,
            rarity=candidate.rarity,
            archetype=candidate.archetype,
            custom_name=candidate.display_name if use_custom_name else "",
            rng=rng,
            grant_skills=False,
            save=False,
            assign_fallback_avatar=True,
        )
        guests_to_create.append(guest)
        templates_for_guests.append(template)
        candidate_ids_to_delete.append(candidate.id)

    return guests_to_create, templates_for_guests, candidate_ids_to_delete


def build_recruitment_records(
    *,
    manor: Manor,
    candidates: list[RecruitmentCandidate],
    created_guests: list[Guest],
) -> list[RecruitmentRecord]:
    return [
        RecruitmentRecord(
            manor=manor,
            pool=candidate.pool,
            guest=guest,
            rarity=candidate.rarity,
        )
        for candidate, guest in zip(candidates, created_guests)
    ]


def build_template_skill_rows(
    *,
    created_guests: list[Guest],
    templates_for_guests: list[GuestTemplate],
    max_guest_skill_slots: int,
) -> list[GuestSkill]:
    skill_rows: list[GuestSkill] = []
    for guest, template in zip(created_guests, templates_for_guests):
        initial_skills = list(template.initial_skills.all())
        for skill in initial_skills[:max_guest_skill_slots]:
            skill_rows.append(
                GuestSkill(
                    guest=guest,
                    skill=skill,
                    source=GuestSkill.Source.TEMPLATE,
                )
            )
    return skill_rows
