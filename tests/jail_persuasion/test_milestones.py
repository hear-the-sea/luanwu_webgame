from __future__ import annotations

import pytest

from core.exceptions import JailError
from gameplay.models import JailInteractionLog
from gameplay.services.jail_persuasion.interactions import observe_prisoner
from gameplay.services.jail_persuasion.milestones import pending_milestone, resolve_milestone

pytestmark = pytest.mark.django_db


def _prepare(world, *, affinity, stage=0):
    observe_prisoner(world.captor, world.prisoner.id)
    prisoner = world.prisoner
    prisoner.refresh_from_db()
    prisoner.affinity = affinity
    prisoner.milestone_stage = stage
    prisoner.save(update_fields=["affinity", "milestone_stage"])
    return prisoner


def test_pending_milestone_uses_stance_and_lower_stage_first(persuasion_world):
    prisoner = _prepare(persuasion_world, affinity=80)
    event = pending_milestone(prisoner)

    assert event is not None
    assert event.stage == 1
    assert event.threshold == 35
    assert event.method == prisoner.stance_method
    assert prisoner.display_name in event.prompt
    assert {choice.key for choice in event.choices} == {"aligned", "alternative"}


def test_aligned_stage_one_increases_affinity_reveals_level_three_and_logs(persuasion_world):
    prisoner = _prepare(persuasion_world, affinity=35)
    before_interactions = prisoner.interactions_today

    result = resolve_milestone(persuasion_world.captor, prisoner.id, choice="aligned")

    result.prisoner.refresh_from_db()
    assert result.prisoner.affinity == 45
    assert result.prisoner.milestone_stage == 1
    assert result.prisoner.revealed_level == 3
    assert result.prisoner.interactions_today == before_interactions
    assert result.log.method == "milestone"
    assert result.log.outcome == "event"
    assert result.copy_text


def test_crossing_both_thresholds_requires_stage_one_before_stage_two(persuasion_world):
    prisoner = _prepare(persuasion_world, affinity=80)

    first = resolve_milestone(persuasion_world.captor, prisoner.id, choice="aligned")
    assert first.prisoner.milestone_stage == 1
    second_pending = pending_milestone(first.prisoner)
    assert second_pending is not None
    assert second_pending.stage == 2
    assert second_pending.threshold == 70

    second = resolve_milestone(persuasion_world.captor, prisoner.id, choice="aligned")
    assert second.prisoner.milestone_stage == 2
    assert pending_milestone(second.prisoner) is None
    assert JailInteractionLog.objects.filter(prisoner=prisoner, method="milestone").count() == 2


def test_alternative_choice_applies_configured_small_penalty(persuasion_world):
    prisoner = _prepare(persuasion_world, affinity=35)
    event = pending_milestone(prisoner)
    alternative = next(choice for choice in event.choices if choice.key == "alternative")

    result = resolve_milestone(persuasion_world.captor, prisoner.id, choice="alternative")

    assert result.heart_delta == alternative.heart_delta
    assert result.affinity_delta == alternative.affinity_delta
    assert result.prisoner.milestone_stage == 1


def test_milestone_rejects_invalid_choice_or_missing_event(persuasion_world):
    prisoner = _prepare(persuasion_world, affinity=35)
    with pytest.raises(JailError, match="未知的事件选项"):
        resolve_milestone(persuasion_world.captor, prisoner.id, choice="unknown")

    prisoner.affinity = 0
    prisoner.save(update_fields=["affinity"])
    with pytest.raises(JailError, match="没有待处理"):
        resolve_milestone(persuasion_world.captor, prisoner.id, choice="aligned")
