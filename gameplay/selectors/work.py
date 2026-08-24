from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.paginator import Paginator

from gameplay.models import WorkAssignment, WorkTemplate
from gameplay.services.work import get_work_action_point_cost
from gameplay.services.work_requirements import (
    WorkEligibility,
    WorkRequirementResult,
    evaluate_work_requirements,
    get_enabled_work_requirements,
)
from guests.models import GuestStatus

WORKS_PER_PAGE = 4


@dataclass(frozen=True, slots=True)
class WorkGuestOption:
    guest: Any
    eligibility: WorkEligibility

    @property
    def guest_id(self) -> int:
        return int(self.guest.pk)

    @property
    def relevant_requirements(self) -> tuple[WorkRequirementResult, ...]:
        return tuple(requirement for requirement in self.eligibility.requirements if requirement.key != "level")

    @property
    def missing_requirements(self) -> tuple[WorkRequirementResult, ...]:
        return self.eligibility.missing_requirements


def _eligible_guest_sort_key(option: WorkGuestOption) -> tuple[int, int, str, int]:
    return (
        option.eligibility.attribute_surplus,
        int(option.guest.level),
        str(option.guest.display_name),
        option.guest_id,
    )


def _ineligible_guest_sort_key(option: WorkGuestOption) -> tuple[int, int, int, int, str, int]:
    eligibility = option.eligibility
    if eligibility.level_missing:
        gap_key = (1, eligibility.level_missing, eligibility.attribute_missing)
    else:
        gap_key = (0, eligibility.attribute_missing, 0)
    return (
        *gap_key,
        int(option.guest.level),
        str(option.guest.display_name),
        option.guest_id,
    )


WORK_TIERS = [
    {
        "key": "junior",
        "name": "初级工作区",
        "tier": WorkTemplate.Tier.JUNIOR,
        "desc": "适合新手门客的基础工作，2小时完成",
    },
    {
        "key": "intermediate",
        "name": "中级工作区",
        "tier": WorkTemplate.Tier.INTERMEDIATE,
        "desc": "需要一定经验的工作，3小时完成",
    },
    {
        "key": "senior",
        "name": "高级工作区",
        "tier": WorkTemplate.Tier.SENIOR,
        "desc": "高难度工作，回报丰厚，4小时完成",
    },
]


def get_work_page_context(manor: Any, *, current_tier: str, page: int) -> dict[str, Any]:
    normalized_tier = (current_tier or "junior").strip() or "junior"
    current_tier_config = next((tier for tier in WORK_TIERS if tier["key"] == normalized_tier), WORK_TIERS[0])
    normalized_tier = current_tier_config["key"]

    paginator = Paginator(
        WorkTemplate.objects.filter(tier=current_tier_config["tier"]).order_by("display_order"),
        WORKS_PER_PAGE,
    )
    page_obj = paginator.get_page(page)
    works = list(page_obj.object_list)

    idle_guests = list(
        manor.guests.filter(status=GuestStatus.IDLE).select_related("template").order_by("-level", "template__name")
    )
    pending_assignments = list(
        WorkAssignment.objects.filter(
            manor=manor,
            status__in=[WorkAssignment.Status.WORKING, WorkAssignment.Status.COMPLETED],
            reward_claimed=False,
        )
        .select_related("guest", "work_template")
        .order_by("work_template_id", "complete_at", "-started_at", "-id")
    )

    claimable_assignment_by_work_template_id: dict[int, WorkAssignment] = {}
    working_assignment_by_work_template_id: dict[int, WorkAssignment] = {}
    for assignment in pending_assignments:
        if assignment.status == WorkAssignment.Status.COMPLETED:
            claimable_assignment_by_work_template_id.setdefault(assignment.work_template_id, assignment)
            continue
        if assignment.status == WorkAssignment.Status.WORKING:
            working_assignment_by_work_template_id.setdefault(assignment.work_template_id, assignment)

    for work in works:
        work.action_point_cost = get_work_action_point_cost(work.tier)
        work.claimable_assignment = claimable_assignment_by_work_template_id.get(work.id)
        work.working_assignment = working_assignment_by_work_template_id.get(work.id)
        work.active_assignment = work.claimable_assignment or work.working_assignment
        options = [
            WorkGuestOption(guest=guest, eligibility=evaluate_work_requirements(guest, work)) for guest in idle_guests
        ]
        work.requirements = get_enabled_work_requirements(work)
        work.eligible_guest_options = sorted(
            (option for option in options if option.eligibility.requirements_met),
            key=_eligible_guest_sort_key,
        )
        work.ineligible_guest_options = sorted(
            (option for option in options if not option.eligibility.requirements_met),
            key=_ineligible_guest_sort_key,
        )
        work.closest_ineligible_guests = work.ineligible_guest_options[:3]
        work.eligible_idle_guests = [option.guest for option in work.eligible_guest_options]

    return {
        "work_tiers": list(WORK_TIERS),
        "current_tier": normalized_tier,
        "current_tier_config": current_tier_config,
        "works": works,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
    }
