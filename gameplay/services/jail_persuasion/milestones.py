from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from core.exceptions import JailError
from gameplay.models import JailInteractionLog, JailPrisoner, Manor

from .profiles import METHOD_ORDER, clamp, load_jail_persuasion_profiles, render_copy


@dataclass(frozen=True)
class MilestoneChoice:
    key: str
    label: str
    heart_delta: int
    affinity_delta: int


@dataclass(frozen=True)
class PendingMilestone:
    key: str
    title: str
    prompt: str
    method: str
    stage: int
    threshold: int
    choices: tuple[MilestoneChoice, MilestoneChoice]


@dataclass(frozen=True)
class MilestoneResult:
    prisoner: JailPrisoner
    log: JailInteractionLog
    stage: int
    heart_delta: int
    affinity_delta: int
    copy_params: dict[str, object]
    copy_text: str


def pending_milestone_stage(prisoner: JailPrisoner) -> int:
    stage = int(prisoner.milestone_stage or 0)
    affinity = int(prisoner.affinity or 0)
    if stage < 1 and affinity >= 35:
        return 1
    if stage < 2 and affinity >= 70:
        return 2
    return 0


def pending_milestone(prisoner: JailPrisoner) -> PendingMilestone | None:
    stage = pending_milestone_stage(prisoner)
    method = str(prisoner.stance_method or "")
    if stage == 0 or method not in METHOD_ORDER:
        return None
    threshold = 35 if stage == 1 else 70
    profile = load_jail_persuasion_profiles()["milestones"][f"{method}_{threshold}"]
    params: dict[str, object] = {"prisoner_name": prisoner.display_name}
    choices = tuple(
        MilestoneChoice(
            key=choice_key,
            label=str(profile["options"][choice_key]["label"]),
            heart_delta=int(profile["options"][choice_key]["heart_delta"]),
            affinity_delta=int(profile["options"][choice_key]["affinity_delta"]),
        )
        for choice_key in ("aligned", "alternative")
    )
    return PendingMilestone(
        key=str(profile["key"]),
        title=str(profile["title"]),
        prompt=render_copy(str(profile["key"]), params),
        method=method,
        stage=stage,
        threshold=threshold,
        choices=(choices[0], choices[1]),
    )


@transaction.atomic
def resolve_milestone(manor: Manor, prisoner_id: int, *, choice: str) -> MilestoneResult:
    normalized_choice = str(choice or "").strip()
    if normalized_choice not in {"aligned", "alternative"}:
        raise JailError("未知的事件选项")

    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    prisoner = (
        JailPrisoner.objects.select_for_update()
        .select_related("guest_template")
        .filter(pk=prisoner_id, captor=locked_manor, status=JailPrisoner.Status.HELD)
        .first()
    )
    if prisoner is None:
        raise JailError("囚徒不存在或已处理")
    event = pending_milestone(prisoner)
    if event is None:
        raise JailError("当前没有待处理的归心事件")

    profile = load_jail_persuasion_profiles()["milestones"][f"{event.method}_{event.threshold}"]
    option = profile["options"][normalized_choice]
    heart_before = clamp(int(prisoner.loyalty), 0, 100)
    affinity_before = clamp(int(prisoner.affinity), 0, 100)
    heart_after = clamp(heart_before + int(option["heart_delta"]), 0, 100)
    affinity_after = clamp(affinity_before + int(option["affinity_delta"]), 0, 100)
    prisoner.loyalty = heart_after
    prisoner.affinity = affinity_after
    prisoner.milestone_stage = event.stage
    if event.stage == 1:
        prisoner.revealed_level = 3
    prisoner.save(update_fields=["loyalty", "affinity", "milestone_stage", "revealed_level"])

    copy_params = {
        "prisoner_name": prisoner.display_name,
        "heart_delta": abs(heart_after - heart_before),
        "affinity_delta": abs(affinity_after - affinity_before),
    }
    log = JailInteractionLog.objects.create(
        prisoner=prisoner,
        captor=locked_manor,
        method="milestone",
        usage_date=timezone.localdate(),
        heart_before=heart_before,
        heart_after=heart_after,
        affinity_before=affinity_before,
        affinity_after=affinity_after,
        outcome=JailInteractionLog.Outcome.EVENT,
        copy_key=str(option["key"]),
        copy_params=copy_params,
        resource_cost={},
    )
    return MilestoneResult(
        prisoner=prisoner,
        log=log,
        stage=event.stage,
        heart_delta=heart_after - heart_before,
        affinity_delta=affinity_after - affinity_before,
        copy_params=copy_params,
        copy_text=render_copy(str(option["key"]), copy_params),
    )
