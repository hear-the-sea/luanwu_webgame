from __future__ import annotations

from typing import Any

from django.core.paginator import Paginator

from gameplay.models import WorkAssignment, WorkTemplate
from guests.models import GuestStatus

WORKS_PER_PAGE = 4

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
        work.claimable_assignment = claimable_assignment_by_work_template_id.get(work.id)
        work.working_assignment = working_assignment_by_work_template_id.get(work.id)
        work.active_assignment = work.claimable_assignment or work.working_assignment
        work.eligible_idle_guests = [
            guest
            for guest in idle_guests
            if (
                guest.level >= work.required_level
                and guest.force >= work.required_force
                and guest.intellect >= work.required_intellect
            )
        ]

    return {
        "work_tiers": list(WORK_TIERS),
        "current_tier": normalized_tier,
        "current_tier_config": current_tier_config,
        "works": works,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
    }
