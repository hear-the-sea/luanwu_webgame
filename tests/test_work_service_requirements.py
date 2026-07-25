from __future__ import annotations

import pytest
from django.utils import timezone

from core.exceptions import GuestNotRequirementError
from gameplay.models import WorkAssignment, WorkTemplate
from gameplay.services import work as work_service
from gameplay.services.work import assign_guest_to_work, get_available_works_for_guest
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate


def _create_guest(manor, suffix: str, **overrides) -> Guest:
    template = GuestTemplate.objects.create(
        key=f"work_requirement_guest_{suffix}_{manor.pk}",
        name=f"资格测试门客{suffix}",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    values = {
        "level": 10,
        "force": 100,
        "intellect": 100,
        "defense_stat": 100,
        "agility": 100,
        "status": GuestStatus.IDLE,
    }
    values.update(overrides)
    return Guest.objects.create(manor=manor, template=template, **values)


def _create_work(manor, suffix: str, **overrides) -> WorkTemplate:
    values = {
        "required_level": 10,
        "required_force": 100,
        "required_intellect": 100,
        "required_defense": 100,
        "required_agility": 100,
        "reward_silver": 100,
        "work_duration": 60,
    }
    values.update(overrides)
    return WorkTemplate.objects.create(
        key=f"work_requirement_{suffix}_{manor.pk}",
        name=f"资格测试工作{suffix}",
        **values,
    )


@pytest.mark.django_db
def test_get_available_works_for_guest_checks_defense_and_agility(manor_with_user):
    manor, _client = manor_with_user
    guest = _create_guest(manor, "available", defense_stat=70, agility=80)
    eligible = _create_work(manor, "eligible", required_defense=70, required_agility=80)
    defense_too_high = _create_work(manor, "defense_high", required_defense=71, required_agility=80)
    agility_too_high = _create_work(manor, "agility_high", required_defense=70, required_agility=81)

    available = get_available_works_for_guest(guest)

    assert eligible in available
    assert defense_too_high not in available
    assert agility_too_high not in available


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("suffix", "guest_overrides", "work_overrides", "expected_requirement"),
    [
        ("level", {"level": 9}, {}, "level"),
        ("force", {"force": 99}, {}, "force"),
        ("intellect", {"intellect": 99}, {}, "intellect"),
        ("defense", {"defense_stat": 99}, {}, "defense"),
        ("agility", {"agility": 99}, {}, "agility"),
        (
            "fixed_order",
            {"force": 99, "intellect": 99, "defense_stat": 99, "agility": 99},
            {},
            "force",
        ),
    ],
)
def test_assign_guest_to_work_raises_first_missing_requirement_before_consuming_action_points(
    manor_with_user,
    suffix,
    guest_overrides,
    work_overrides,
    expected_requirement,
):
    manor, _client = manor_with_user
    manor.action_points = 321
    manor.action_points_updated_at = timezone.now()
    manor.save(update_fields=["action_points", "action_points_updated_at"])
    guest = _create_guest(manor, suffix, **guest_overrides)
    work = _create_work(manor, suffix, **work_overrides)

    with pytest.raises(GuestNotRequirementError) as exc_info:
        assign_guest_to_work(guest, work)

    assert exc_info.value.context["requirement_type"] == expected_requirement
    manor.refresh_from_db()
    guest.refresh_from_db()
    assert manor.action_points == 321
    assert guest.status == GuestStatus.IDLE
    assert WorkAssignment.objects.filter(guest=guest).exists() is False


@pytest.mark.django_db
def test_assign_guest_to_work_accepts_exactly_matching_all_requirements(manor_with_user):
    manor, _client = manor_with_user
    guest = _create_guest(manor, "exact")
    work = _create_work(manor, "exact")

    assignment = assign_guest_to_work(guest, work)

    guest.refresh_from_db()
    assert assignment.guest_id == guest.pk
    assert guest.status == GuestStatus.WORKING


@pytest.mark.django_db
def test_assign_guest_to_work_rechecks_current_attributes_inside_lock(manor_with_user, monkeypatch):
    manor, _client = manor_with_user
    manor.action_points = 321
    manor.action_points_updated_at = timezone.now()
    manor.save(update_fields=["action_points", "action_points_updated_at"])
    guest = _create_guest(manor, "locked")
    work = _create_work(manor, "locked")
    original_evaluate = work_service.evaluate_work_requirements
    calls = 0

    def evaluate_and_change_guest(current_guest, current_work):
        nonlocal calls
        calls += 1
        result = original_evaluate(current_guest, current_work)
        if calls == 1:
            Guest.objects.filter(pk=current_guest.pk).update(agility=99)
        return result

    monkeypatch.setattr(work_service, "evaluate_work_requirements", evaluate_and_change_guest)

    with pytest.raises(GuestNotRequirementError) as exc_info:
        assign_guest_to_work(guest, work)

    assert calls == 2
    assert exc_info.value.context["requirement_type"] == "agility"
    manor.refresh_from_db()
    assert manor.action_points == 321
    assert WorkAssignment.objects.filter(guest=guest).exists() is False
